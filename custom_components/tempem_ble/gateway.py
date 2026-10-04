"""Remote Tempem gateways: ESP32 boards that post advertisements to a webhook.

A remote gateway sits on a different network than Home Assistant (for example
behind a Cloudflare Tunnel) and cannot be reached by HA, so the ESPHome native
API and Bluetooth proxy do not work. Instead it POSTs batches of advertisements
over HTTPS to ``/api/webhook/<webhook_id>``.

Each advertisement is fed into Home Assistant's Bluetooth stack through a
remote scanner, exactly like ESPHome Bluetooth proxies or the Ruuvi Gateway do.
That means Tempem beacons at the remote site are discovered and handled by the
normal Bluetooth discovery flow, with no per-sensor setup on the gateway.
"""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
import json
import logging
import math
import re
import time
from typing import Any

from aiohttp import hdrs, web
import voluptuous as vol

from homeassistant.components import webhook
from homeassistant.components.bluetooth import (
    FALLBACK_MAXIMUM_STALE_ADVERTISEMENT_SECONDS,
    MONOTONIC_TIME,
    BaseHaRemoteScanner,
    async_register_scanner,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_WEBHOOK_ID
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.util import dt as dt_util

from .const import CONF_GATEWAY_ID, DOMAIN, SIGNAL_GATEWAY_STATUS, SIGNAL_REMOTE_BATTERY
from .parser import DUSUN_MANUFACTURER_ID
from .storage import POLL_STORE

_LOGGER = logging.getLogger(__name__)

GATEWAY_MANUFACTURER = "ESPHome"
GATEWAY_MODEL = "Tempem remote gateway"

MAX_BODY_BYTES = 64 * 1024
MAX_ADVERTISEMENTS = 256
# Real advertisements carry one or two manufacturer data entries.
MAX_MANUFACTURER_DATA = 8
# Distinct beacon addresses one gateway may report within
# FALLBACK_MAXIMUM_STALE_ADVERTISEMENT_SECONDS. The firmware tracks up to 40.
# Bounds what someone who knows the webhook URL can push into Home
# Assistant's Bluetooth stack (history, discovery flows).
MAX_ADDRESSES = 1024
# A battery reading reported later than this is still worth showing; it is
# polled weekly.
MAX_BATTERY_AGE_SECONDS = 7 * 24 * 3600

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")
_HEX_RE = re.compile(r"^([0-9A-Fa-f]{2})*$")


def _mac(value: Any) -> str:
    if not isinstance(value, str) or not _MAC_RE.fullmatch(value):
        raise vol.Invalid("invalid MAC address")
    return value.upper().replace("-", ":")


def _hex_bytes(value: Any) -> bytes:
    if (
        not isinstance(value, str)
        or len(value) > 62 * 2
        or not _HEX_RE.fullmatch(value)
    ):
        raise vol.Invalid("invalid hex string")
    return bytes.fromhex(value)


def _manufacturer_data(value: Any) -> dict[int, bytes]:
    if not isinstance(value, dict):
        raise vol.Invalid("expected an object")
    if len(value) > MAX_MANUFACTURER_DATA:
        raise vol.Invalid("too many manufacturer data entries")
    result: dict[int, bytes] = {}
    for key, data in value.items():
        try:
            company_id = int(key)
        except (TypeError, ValueError) as err:
            raise vol.Invalid("company id must be a decimal string") from err
        if not 0 <= company_id <= 0xFFFF:
            raise vol.Invalid("company id out of range")
        result[company_id] = _hex_bytes(data)
    return result


def _int(value: Any) -> int:
    # JSON allows Infinity, and int(inf) raises OverflowError, which
    # vol.Coerce does not catch.
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as err:
        raise vol.Invalid("expected int") from err


def _finite(value: float) -> float:
    if not math.isfinite(value):
        raise vol.Invalid("expected a finite number")
    return value


_AGE = vol.All(vol.Coerce(float), _finite, vol.Range(min=0))

ADVERTISEMENT_SCHEMA = vol.Schema(
    {
        vol.Required("address"): _mac,
        vol.Required("rssi"): vol.All(_int, vol.Range(min=-127, max=20)),
        vol.Optional("name"): vol.Any(None, vol.All(str, vol.Length(max=64))),
        vol.Optional("manufacturer_data", default=dict): _manufacturer_data,
        vol.Optional("age", default=0): _AGE,
    },
    extra=vol.ALLOW_EXTRA,
)

BATTERY_SCHEMA = vol.Schema(
    {
        vol.Required("address"): _mac,
        vol.Required("level"): vol.All(_int, vol.Range(min=0, max=100)),
        vol.Optional("age", default=0): _AGE,
    },
    extra=vol.ALLOW_EXTRA,
)

GATEWAY_SCHEMA = vol.Schema(
    {
        vol.Optional("mac"): vol.Any(None, _mac),
        vol.Optional("name"): vol.Any(None, vol.All(str, vol.Length(max=64))),
        vol.Optional("version"): vol.Any(None, vol.All(str, vol.Length(max=64))),
        vol.Optional("uptime"): vol.Any(None, _AGE),
        vol.Optional("wifi_rssi"): vol.Any(None, _int),
        vol.Optional("free_heap"): vol.Any(None, vol.All(_int, vol.Range(min=0))),
    },
    extra=vol.ALLOW_EXTRA,
)

PAYLOAD_SCHEMA = vol.Schema(
    {
        vol.Optional("v", default=1): _int,
        vol.Optional("gateway", default=dict): GATEWAY_SCHEMA,
        # Entries are validated one by one so a single bad advert does not
        # drop the whole batch.
        vol.Optional("advertisements", default=list): vol.All(
            list, vol.Length(max=MAX_ADVERTISEMENTS)
        ),
        vol.Optional("batteries", default=list): vol.All(
            list, vol.Length(max=MAX_ADVERTISEMENTS)
        ),
    },
    extra=vol.ALLOW_EXTRA,
)


@dataclass
class GatewayStatus:
    """What the gateway last told us about itself."""

    last_seen: Any = None  # datetime
    mac: str | None = None
    name: str | None = None
    version: str | None = None
    uptime: float | None = None
    wifi_rssi: int | None = None
    free_heap: int | None = None
    advertisements: int = 0
    reports: int = 0
    rejected: int = 0


class TempemRemoteScanner(BaseHaRemoteScanner):
    """A non-connectable scanner fed by webhook posts."""

    @callback
    def async_on_advertisement(
        self,
        address: str,
        rssi: int,
        name: str | None,
        manufacturer_data: dict[int, bytes],
        age: float,
    ) -> None:
        """Feed one advertisement into the Bluetooth manager."""
        self._async_on_advertisement(
            address=address,
            rssi=rssi,
            local_name=name,
            service_uuids=[],
            service_data={},
            manufacturer_data=manufacturer_data,
            tx_power=None,
            details={},
            advertisement_monotonic_time=MONOTONIC_TIME() - age,
        )


class TempemGateway:
    """Runtime state of a remote gateway config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the gateway."""
        self.hass = hass
        self.entry = entry
        self.gateway_id: str = entry.data[CONF_GATEWAY_ID]
        self.webhook_id: str = entry.data[CONF_WEBHOOK_ID]
        self.status = GatewayStatus()
        # address -> monotonic time it was last accepted, see MAX_ADDRESSES.
        self._addresses: dict[str, float] = {}
        self._warned_rejected = False
        # habluetooth treats adapter names starting with "hci" as local
        # adapters and parses the rest as an index.
        adapter = entry.title
        if adapter.startswith("hci"):
            adapter = f"Tempem {adapter}"
        self.scanner = TempemRemoteScanner(
            self.gateway_id, adapter, connector=None, connectable=False
        )

    @property
    def status_signal(self) -> str:
        """Dispatcher signal fired after every report."""
        return SIGNAL_GATEWAY_STATUS.format(self.entry.entry_id)

    @callback
    def async_setup(self) -> None:
        """Register scanner and webhook."""
        entry = self.entry
        # Create the gateway device up front so the Bluetooth integration can
        # link the scanner's device to it.
        device = dr.async_get(self.hass).async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, self.gateway_id)},
            name=entry.title,
            manufacturer=GATEWAY_MANUFACTURER,
            model=GATEWAY_MODEL,
        )
        entry.async_on_unload(
            async_register_scanner(
                self.hass,
                self.scanner,
                source_domain=DOMAIN,
                source_model=GATEWAY_MODEL,
                source_config_entry_id=entry.entry_id,
                source_device_id=device.id,
            )
        )
        entry.async_on_unload(self.scanner.async_setup())
        webhook.async_register(
            self.hass,
            DOMAIN,
            entry.title,
            self.webhook_id,
            self._async_handle_webhook,
            local_only=False,
            allowed_methods=[hdrs.METH_POST],
        )
        entry.async_on_unload(
            lambda: webhook.async_unregister(self.hass, self.webhook_id)
        )

    async def _async_handle_webhook(
        self, hass: HomeAssistant, webhook_id: str, request: web.Request
    ) -> web.Response:
        """Handle a report from the gateway."""
        # Read until EOF: StreamReader.read(n) only returns what is buffered,
        # which can be just the first chunk of the body. Works the same for
        # the MockRequest that Home Assistant Cloud webhooks use (which has no
        # content_length and returns a new reader on every .content access).
        content = request.content
        body = b""
        while len(body) <= MAX_BODY_BYTES and (
            chunk := await content.read(MAX_BODY_BYTES + 1 - len(body))
        ):
            body += chunk
        if len(body) > MAX_BODY_BYTES:
            self.status.rejected += 1
            return web.json_response(
                {"ok": False, "error": "payload too large"},
                status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            )
        try:
            # RecursionError: deeply nested JSON.
            payload = PAYLOAD_SCHEMA(json.loads(body))
        except (ValueError, RecursionError, vol.Invalid) as err:
            self.status.rejected += 1
            # Once until the next good report, so a broken gateway (or someone
            # who knows the URL) cannot flood the log.
            _LOGGER.log(
                logging.DEBUG if self._warned_rejected else logging.WARNING,
                "%s: rejected report: %s",
                self.entry.title,
                err,
            )
            self._warned_rejected = True
            return web.json_response(
                {"ok": False, "error": "invalid payload"}, status=HTTPStatus.BAD_REQUEST
            )
        self._warned_rejected = False
        accepted = self.async_process(payload)
        return web.json_response({"ok": True, "accepted": accepted})

    @callback
    def async_process(self, payload: dict[str, Any]) -> int:
        """Process a validated payload. Returns accepted advertisement count."""
        accepted = 0
        now = MONOTONIC_TIME()
        addresses = self._addresses
        if len(addresses) >= MAX_ADDRESSES:
            cutoff = now - FALLBACK_MAXIMUM_STALE_ADVERTISEMENT_SECONDS
            for address in [a for a, seen in addresses.items() if seen < cutoff]:
                del addresses[address]
        for raw in payload["advertisements"]:
            try:
                adv = ADVERTISEMENT_SCHEMA(raw)
            except vol.Invalid as err:
                _LOGGER.debug(
                    "%s: skipping advertisement %s: %s", self.entry.title, raw, err
                )
                continue
            if adv["age"] > FALLBACK_MAXIMUM_STALE_ADVERTISEMENT_SECONDS:
                continue
            # Only Tempem/Dusun data is passed on: whoever knows the webhook URL
            # must not be able to fake advertisements for other integrations.
            if (dusun := adv["manufacturer_data"].get(DUSUN_MANUFACTURER_ID)) is None:
                continue
            if adv["address"] not in addresses and len(addresses) >= MAX_ADDRESSES:
                _LOGGER.debug(
                    "%s: too many distinct addresses, skipping %s",
                    self.entry.title,
                    adv["address"],
                )
                continue
            addresses[adv["address"]] = now
            self.scanner.async_on_advertisement(
                adv["address"],
                adv["rssi"],
                adv.get("name") or None,
                {DUSUN_MANUFACTURER_ID: dusun},
                adv["age"],
            )
            accepted += 1

        for raw in payload["batteries"]:
            try:
                battery = BATTERY_SCHEMA(raw)
            except vol.Invalid as err:
                _LOGGER.debug("%s: skipping battery %s: %s", self.entry.title, raw, err)
                continue
            if battery["age"] > MAX_BATTERY_AGE_SECONDS:
                continue
            # Remembered so a sensor added after this (weekly) report still
            # gets a battery level. A reading older than the cached one (e.g.
            # from a second gateway) is not shown either.
            if not self.hass.data[POLL_STORE].async_set_remote_battery(
                battery["address"], battery["level"], time.time() - battery["age"]
            ):
                continue
            async_dispatcher_send(
                self.hass, SIGNAL_REMOTE_BATTERY, battery["address"], battery["level"]
            )

        gateway = payload["gateway"]
        status = self.status
        status.last_seen = dt_util.utcnow()
        status.reports += 1
        status.advertisements = accepted
        for key in ("mac", "name", "version", "uptime", "wifi_rssi", "free_heap"):
            if gateway.get(key) is not None:
                setattr(status, key, gateway[key])
        async_dispatcher_send(self.hass, self.status_signal)
        return accepted


def async_webhook_url(hass: HomeAssistant, webhook_id: str) -> str:
    """Return the public URL the gateway should post to."""
    try:
        return webhook.async_generate_url(
            hass, webhook_id, allow_internal=False, prefer_external=True
        )
    except NoURLAvailableError:
        return f"https://<your-home-assistant-domain>{webhook.async_generate_path(webhook_id)}"
