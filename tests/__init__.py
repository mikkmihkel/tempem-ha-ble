"""Tests for the Tempem BLE integration."""

from __future__ import annotations

import time
from typing import Any

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from habluetooth import BaseHaRemoteScanner, BluetoothServiceInfoBleak

from homeassistant.components.bluetooth import (
    MONOTONIC_TIME,
    async_get_advertisement_callback,
    async_register_scanner,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import device_registry as dr

ADDRESS = "C0:FF:EE:00:12:34"
OTHER_ADDRESS = "C1:22:33:44:55:66"
# Example payload from the Dusun "beaconsensor data format" PDF.
PDF_PAYLOAD = bytes.fromhex("036e087b34")
TEMPEM_MANUFACTURER_DATA = {0x0059: PDF_PAYLOAD}


def make_service_info(
    address: str = ADDRESS,
    name: str | None = "TempemSens",
    rssi: int = -70,
    manufacturer_data: dict[int, bytes] | None = None,
    source: str = "local",
    connectable: bool = True,
) -> BluetoothServiceInfoBleak:
    """Build a BluetoothServiceInfoBleak like a scanner would."""
    if manufacturer_data is None:
        manufacturer_data = TEMPEM_MANUFACTURER_DATA
    try:
        device = BLEDevice(address, name, {"source": source})
    except TypeError:  # bleak < 1.0 (Home Assistant < 2025.7) also wants rssi
        device = BLEDevice(address, name, {"source": source}, rssi)  # type: ignore[call-arg]
    advertisement = AdvertisementData(
        local_name=name,
        manufacturer_data=manufacturer_data,
        service_data={},
        service_uuids=[],
        rssi=rssi,
        tx_power=-127,
        platform_data=(),
    )
    return BluetoothServiceInfoBleak(
        name=name or address,
        address=address,
        rssi=rssi,
        manufacturer_data=manufacturer_data,
        service_data={},
        service_uuids=[],
        source=source,
        device=device,
        advertisement=advertisement,
        connectable=connectable,
        time=MONOTONIC_TIME(),
        tx_power=None,
    )


def inject_service_info(hass: HomeAssistant, info: BluetoothServiceInfoBleak) -> None:
    """Inject an advertisement into the Bluetooth manager."""
    async_get_advertisement_callback(hass)(info)


class FakeRemoteScanner(BaseHaRemoteScanner):
    """A non-connectable remote scanner (like a passive proxy)."""

    def inject(
        self,
        address: str = ADDRESS,
        rssi: int = -70,
        name: str | None = "TempemSens",
        manufacturer_data: dict[int, bytes] | None = None,
    ) -> None:
        """Inject an advertisement through this scanner."""
        self._async_on_advertisement(
            address,
            rssi,
            name,
            [],
            {},
            TEMPEM_MANUFACTURER_DATA
            if manufacturer_data is None
            else manufacturer_data,
            None,
            {},
            MONOTONIC_TIME(),
        )


def register_fake_remote_scanner(
    hass: HomeAssistant, source: str = "AA:BB:CC:DD:EE:01"
) -> tuple[FakeRemoteScanner, CALLBACK_TYPE]:
    """Register a non-connectable remote scanner."""
    scanner = FakeRemoteScanner(source, "fake proxy", connector=None, connectable=False)
    unsub_register = async_register_scanner(hass, scanner)
    unsub_setup = scanner.async_setup()

    def _unsub() -> None:
        unsub_setup()
        unsub_register()

    return scanner, _unsub


def contract_payload(**overrides: Any) -> dict[str, Any]:
    """Return the payload documented in the gateway contract."""
    payload: dict[str, Any] = {
        "v": 1,
        "gateway": {
            "mac": "AA:BB:CC:DD:EE:FF",
            "name": "tempem-remote",
            "version": "2026.9.1",
            "uptime": 12345,
            "wifi_rssi": -61,
            "free_heap": 123456,
        },
        "advertisements": [
            {
                "address": ADDRESS,
                "rssi": -70,
                "name": "TempemSens",
                "manufacturer_data": {"89": "036e087b34"},
                "age": 3.2,
            }
        ],
        "batteries": [{"address": ADDRESS, "level": 87, "age": 120}],
    }
    payload.update(overrides)
    return payload


def now() -> float:
    """Wall clock, patchable."""
    return time.time()


def find_device(
    hass: HomeAssistant,
    *,
    identifier: tuple[str, str] | None = None,
    connection: tuple[str, str] | None = None,
) -> dr.DeviceEntry | None:
    """Look a device up by identifier or connection, on every supported HA version.

    DeviceRegistry.async_get_device and using DeviceRegistry.devices as a
    mapping are deprecated (errors in tests) since Home Assistant 2026.9, and
    their replacements need a config entry and don't exist in older releases.
    """
    registry = dr.async_get(hass)
    for item in registry.devices:
        # Newer releases iterate DeviceEntry objects, older ones device ids.
        device = registry.async_get(item) if isinstance(item, str) else item
        if device is None:
            continue
        if identifier is not None and identifier in device.identifiers:
            return device
        if connection is not None and connection in device.connections:
            return device
    return None
