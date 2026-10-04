"""Config flow for Tempem BLE."""

from __future__ import annotations

import secrets
from typing import Any

import voluptuous as vol

from homeassistant.components import webhook
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
    async_scanner_by_source,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS, CONF_NAME, CONF_WEBHOOK_ID
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_BATTERY_POLL_HOURS,
    CONF_ENTRY_TYPE,
    CONF_GATEWAY_ID,
    DEFAULT_BATTERY_POLL_HOURS,
    DOMAIN,
    ENTRY_TYPE_GATEWAY,
    ENTRY_TYPE_SENSOR,
)
from .gateway import async_webhook_url
from .parser import is_supported_advertisement, short_address

DEFAULT_GATEWAY_NAME = "Tempem remote gateway"


def _sensor_title(discovery_info: BluetoothServiceInfoBleak) -> str:
    return f"Tempem {short_address(discovery_info.address)}"


class TempemConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tempem BLE."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered: dict[str, str] = {}
        self._gateway_entry_data: dict[str, Any] | None = None
        self._gateway_title: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return TempemOptionsFlow()

    # ---------------------------------------------------------------- sensors

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a beacon found by the Bluetooth integration."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        if not is_supported_advertisement(
            discovery_info.manufacturer_data, discovery_info.name
        ):
            return self.async_abort(reason="not_supported")
        self._discovery_info = discovery_info
        self.context["title_placeholders"] = {"name": _sensor_title(discovery_info)}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm adding a discovered beacon."""
        assert self._discovery_info is not None
        title = _sensor_title(self._discovery_info)
        if user_input is not None:
            return self.async_create_entry(
                title=title, data={CONF_ENTRY_TYPE: ENTRY_TYPE_SENSOR}
            )
        self._set_confirm_only()
        source = self._discovery_info.source
        if scanner := async_scanner_by_source(self.hass, source):
            source = scanner.name
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={
                "name": title,
                "address": self._discovery_info.address,
                "source": source,
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user choose between adding a sensor or a remote gateway."""
        return self.async_show_menu(
            step_id="user", menu_options=["pick_sensor", "gateway"]
        )

    async def async_step_pick_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick one of the beacons Home Assistant currently hears."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            # The entry title becomes the device name; the label shown in the
            # list also has the address and signal strength.
            return self.async_create_entry(
                title=f"Tempem {short_address(address)}",
                data={CONF_ENTRY_TYPE: ENTRY_TYPE_SENSOR},
            )

        current = self._async_current_ids(include_ignore=False)
        for info in async_discovered_service_info(self.hass, connectable=False):
            if info.address in current or info.address in self._discovered:
                continue
            if is_supported_advertisement(info.manufacturer_data, info.name):
                self._discovered[info.address] = (
                    f"{_sensor_title(info)} ({info.address}, {info.rssi} dBm)"
                )
        if not self._discovered:
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(
            step_id="pick_sensor",
            data_schema=vol.Schema(
                {vol.Required(CONF_ADDRESS): vol.In(self._discovered)}
            ),
        )

    # ---------------------------------------------------------------- gateway

    async def async_step_gateway(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create a remote gateway and its webhook."""
        if user_input is not None:
            gateway_id = f"tempem-gw-{secrets.token_hex(4)}"
            await self.async_set_unique_id(gateway_id)
            self._gateway_title = user_input[CONF_NAME]
            self._gateway_entry_data = {
                CONF_ENTRY_TYPE: ENTRY_TYPE_GATEWAY,
                CONF_GATEWAY_ID: gateway_id,
                CONF_WEBHOOK_ID: webhook.async_generate_id(),
            }
            return await self.async_step_gateway_url()
        return self.async_show_form(
            step_id="gateway",
            data_schema=vol.Schema(
                {vol.Required(CONF_NAME, default=DEFAULT_GATEWAY_NAME): str}
            ),
        )

    async def async_step_gateway_url(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the webhook URL to put in the gateway's secrets.yaml."""
        assert self._gateway_entry_data is not None
        assert self._gateway_title is not None
        if user_input is not None:
            return self.async_create_entry(
                title=self._gateway_title, data=self._gateway_entry_data
            )
        return self.async_show_form(
            step_id="gateway_url",
            description_placeholders={
                "webhook_url": async_webhook_url(
                    self.hass, self._gateway_entry_data[CONF_WEBHOOK_ID]
                )
            },
        )


class TempemOptionsFlow(OptionsFlow):
    """Options: battery poll interval for sensors, webhook URL for gateways."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if self.config_entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_GATEWAY:
            return await self.async_step_gateway()
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = self.config_entry.options.get(
            CONF_BATTERY_POLL_HOURS, DEFAULT_BATTERY_POLL_HOURS
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_BATTERY_POLL_HOURS, default=current
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=24 * 90,
                            step=1,
                            unit_of_measurement="h",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
        )

    async def async_step_gateway(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the gateway's webhook URL again."""
        if user_input is not None:
            return self.async_create_entry(data=dict(self.config_entry.options))
        return self.async_show_form(
            step_id="gateway",
            description_placeholders={
                "webhook_url": async_webhook_url(
                    self.hass, self.config_entry.data[CONF_WEBHOOK_ID]
                )
            },
        )
