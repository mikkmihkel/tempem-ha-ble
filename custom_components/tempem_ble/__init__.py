"""The Tempem BLE integration.

Two kinds of config entries live in this domain:

* ``sensor``: one per Tempem beacon (unique id = MAC). Discovered by Home
  Assistant's Bluetooth integration from any source: the local adapter,
  ESPHome Bluetooth proxies, or a remote Tempem gateway.
* ``gateway``: a remote ESP32 gateway that posts advertisements to a webhook
  over the internet and is registered as a Bluetooth scanner.
"""

from __future__ import annotations

from homeassistant.components.bluetooth import async_remove_scanner
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import CONF_ENTRY_TYPE, CONF_GATEWAY_ID, DOMAIN, ENTRY_TYPE_GATEWAY
from .coordinator import TempemCoordinator
from .gateway import TempemGateway
from .storage import POLL_STORE, BatteryPollStore

type TempemConfigEntry = ConfigEntry[TempemCoordinator | TempemGateway]

SENSOR_PLATFORMS: list[Platform] = [Platform.SENSOR]
GATEWAY_PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def is_gateway_entry(entry: ConfigEntry) -> bool:
    """Return True for remote gateway entries."""
    return entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_GATEWAY


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up shared state."""
    store = BatteryPollStore(hass)
    await store.async_load()
    hass.data[POLL_STORE] = store
    return True


async def async_setup_entry(hass: HomeAssistant, entry: TempemConfigEntry) -> bool:
    """Set up a Tempem config entry."""
    if is_gateway_entry(entry):
        gateway = TempemGateway(hass, entry)
        entry.runtime_data = gateway
        gateway.async_setup()
        await hass.config_entries.async_forward_entry_setups(entry, GATEWAY_PLATFORMS)
        return True

    coordinator = TempemCoordinator(hass, entry, hass.data[POLL_STORE])
    entry.runtime_data = coordinator
    coordinator.async_start_remote_battery_listener()
    await hass.config_entries.async_forward_entry_setups(entry, SENSOR_PLATFORMS)
    # Only start after the platforms had a chance to subscribe.
    entry.async_on_unload(coordinator.async_start())
    coordinator.async_restore_remote_battery()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TempemConfigEntry) -> bool:
    """Unload a config entry."""
    platforms = GATEWAY_PLATFORMS if is_gateway_entry(entry) else SENSOR_PLATFORMS
    return await hass.config_entries.async_unload_platforms(entry, platforms)


async def async_remove_entry(hass: HomeAssistant, entry: TempemConfigEntry) -> None:
    """Clean up after a removed entry."""
    if is_gateway_entry(entry):
        # Drops the scanner's advertisement history and the Bluetooth
        # integration's config entry for it.
        async_remove_scanner(hass, entry.data[CONF_GATEWAY_ID])
    elif entry.unique_id and (store := hass.data.get(POLL_STORE)):
        store.async_forget(entry.unique_id)
