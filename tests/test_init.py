"""Tests for setting up, unloading and removing entries."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.bluetooth import async_scanner_by_source
from homeassistant.components.webhook import DOMAIN as WEBHOOK_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from . import ADDRESS, find_device
from custom_components.tempem_ble.const import DOMAIN
from custom_components.tempem_ble.coordinator import TempemCoordinator
from custom_components.tempem_ble.gateway import TempemGateway
from custom_components.tempem_ble.storage import POLL_STORE

GW_ID = "tempem-gw-1a2b3c4d"


def _bluetooth_scanner_entries(hass: HomeAssistant) -> list:
    return [
        entry
        for entry in hass.config_entries.async_entries("bluetooth")
        if entry.unique_id == GW_ID
    ]


async def test_setup_unload_sensor(
    hass: HomeAssistant, sensor_entry: MockConfigEntry
) -> None:
    """A sensor entry sets up a coordinator and unloads cleanly."""
    assert await hass.config_entries.async_setup(sensor_entry.entry_id)
    await hass.async_block_till_done()
    assert sensor_entry.state is ConfigEntryState.LOADED
    assert isinstance(sensor_entry.runtime_data, TempemCoordinator)

    assert await hass.config_entries.async_unload(sensor_entry.entry_id)
    await hass.async_block_till_done()
    assert sensor_entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_unload_gateway(
    hass: HomeAssistant,
    gateway_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A gateway registers a remote scanner and a webhook, and removes both."""
    assert await hass.config_entries.async_setup(gateway_entry.entry_id)
    await hass.async_block_till_done()
    assert gateway_entry.state is ConfigEntryState.LOADED
    assert isinstance(gateway_entry.runtime_data, TempemGateway)

    scanner = async_scanner_by_source(hass, GW_ID)
    assert scanner is gateway_entry.runtime_data.scanner
    assert scanner.connectable is False
    assert "test-webhook-id" in hass.data[WEBHOOK_DOMAIN]

    # HA's Bluetooth integration lists the remote scanner as its own entry,
    # with its device linked to the gateway device.
    bt_entries = _bluetooth_scanner_entries(hass)
    assert len(bt_entries) == 1
    assert bt_entries[0].state is ConfigEntryState.LOADED
    gateway_device = find_device(hass, identifier=(DOMAIN, GW_ID))
    bt_device = find_device(hass, connection=(dr.CONNECTION_BLUETOOTH, GW_ID))
    assert bt_device is not None
    assert bt_device.via_device_id == gateway_device.id

    assert await hass.config_entries.async_unload(gateway_entry.entry_id)
    await hass.async_block_till_done()
    assert gateway_entry.state is ConfigEntryState.NOT_LOADED
    assert async_scanner_by_source(hass, GW_ID) is None
    assert "test-webhook-id" not in hass.data[WEBHOOK_DOMAIN]

    # Setting up again works (no duplicate registrations).
    assert await hass.config_entries.async_setup(gateway_entry.entry_id)
    await hass.async_block_till_done()
    assert gateway_entry.state is ConfigEntryState.LOADED
    assert len(_bluetooth_scanner_entries(hass)) == 1


async def test_remove_gateway(
    hass: HomeAssistant, gateway_entry: MockConfigEntry
) -> None:
    """Removing a gateway also removes HA Bluetooth's entry for its scanner."""
    assert await hass.config_entries.async_setup(gateway_entry.entry_id)
    await hass.async_block_till_done()
    assert len(_bluetooth_scanner_entries(hass)) == 1

    await hass.config_entries.async_remove(gateway_entry.entry_id)
    await hass.async_block_till_done()
    assert _bluetooth_scanner_entries(hass) == []


async def test_remove_sensor_forgets_poll_time(
    hass: HomeAssistant, sensor_entry: MockConfigEntry
) -> None:
    """Removing a sensor drops its poll bookkeeping but keeps gateway reports."""
    assert await hass.config_entries.async_setup(sensor_entry.entry_id)
    await hass.async_block_till_done()
    store = hass.data[POLL_STORE]
    store.async_mark_polled(ADDRESS)
    store.async_set_remote_battery(ADDRESS, 80, 1000.0)

    await hass.config_entries.async_remove(sensor_entry.entry_id)
    await hass.async_block_till_done()
    assert store.last_polled(ADDRESS) == 1000.0
    assert store.remote_battery(ADDRESS) == (80, 1000.0)
