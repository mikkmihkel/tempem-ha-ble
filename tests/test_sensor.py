"""Tests for beacon sensor entities."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from . import (
    ADDRESS,
    find_device,
    inject_service_info,
    make_service_info,
    register_fake_remote_scanner,
)
from custom_components.tempem_ble.coordinator import KEY_BATTERY

TEMP = "sensor.tempem_1234_temperature"
HUM = "sensor.tempem_1234_humidity"
BATTERY = "sensor.tempem_1234_battery"
RSSI = "sensor.tempem_1234_signal_strength"


async def test_entities_from_advertisement(
    hass: HomeAssistant,
    sensor_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Temperature/humidity entities appear and update on advertisements."""
    hass.config_entries.async_update_entry(
        sensor_entry, options={"battery_poll_hours": 0}
    )
    assert await hass.config_entries.async_setup(sensor_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(TEMP) is None

    inject_service_info(hass, make_service_info())
    await hass.async_block_till_done()

    temp = hass.states.get(TEMP)
    assert temp is not None
    assert temp.state == "28.68"
    assert temp.attributes["unit_of_measurement"] == "°C"
    assert temp.attributes["device_class"] == "temperature"
    hum = hass.states.get(HUM)
    assert hum.state == "54.2"
    assert hum.attributes["unit_of_measurement"] == "%"
    # Battery is not advertised, so no entity yet.
    assert hass.states.get(BATTERY) is None
    # RSSI is disabled by default.
    rssi = entity_registry.async_get(RSSI)
    assert rssi is not None
    assert rssi.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert entity_registry.async_get(TEMP).unique_id == f"{ADDRESS}-temperature"

    device = find_device(hass, connection=(dr.CONNECTION_BLUETOOTH, ADDRESS))
    assert device is not None
    assert device.name == "Tempem 1234"
    assert device.manufacturer == "Tempem"
    assert device.model == "ST1"

    # 0x6000 -> 19.04 C, 0x5000 -> 33.1 %
    inject_service_info(
        hass, make_service_info(manufacturer_data={0x0059: bytes.fromhex("0360005000")})
    )
    await hass.async_block_till_done()
    assert hass.states.get(TEMP).state == "19.04"
    assert hass.states.get(HUM).state == "33.1"

    # Garbage from the same address does not clear the values.
    inject_service_info(
        hass, make_service_info(manufacturer_data={0x0059: bytes.fromhex("43")})
    )
    await hass.async_block_till_done()
    assert hass.states.get(TEMP).state == "19.04"

    assert await hass.config_entries.async_unload(sensor_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(TEMP).state == STATE_UNAVAILABLE


async def test_non_connectable_source(
    hass: HomeAssistant, sensor_entry: MockConfigEntry
) -> None:
    """Advertisements from non-connectable remote scanners reach the sensor."""
    assert await hass.config_entries.async_setup(sensor_entry.entry_id)
    await hass.async_block_till_done()

    scanner, unsub = register_fake_remote_scanner(hass)
    scanner.inject()
    await hass.async_block_till_done()
    assert hass.states.get(TEMP).state == "28.68"
    unsub()


async def test_battery_keeps_value_when_beacon_unavailable(
    hass: HomeAssistant, sensor_entry: MockConfigEntry
) -> None:
    """The battery entity keeps showing its (rarely read) value."""
    hass.config_entries.async_update_entry(
        sensor_entry, options={"battery_poll_hours": 0}
    )
    assert await hass.config_entries.async_setup(sensor_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = sensor_entry.runtime_data

    info = make_service_info()
    inject_service_info(hass, info)
    coordinator._async_remote_battery(ADDRESS.lower(), 55)
    await hass.async_block_till_done()
    assert hass.states.get(BATTERY).state == "55"
    assert hass.states.get(TEMP).state == "28.68"

    # Beacon goes out of range.
    coordinator._async_handle_unavailable(info)
    await hass.async_block_till_done()
    assert hass.states.get(TEMP).state == STATE_UNAVAILABLE
    assert hass.states.get(BATTERY).state == "55"

    # A battery report does not make the beacon available again.
    coordinator._async_remote_battery(ADDRESS, 54)
    await hass.async_block_till_done()
    assert hass.states.get(BATTERY).state == "54"
    assert hass.states.get(TEMP).state == STATE_UNAVAILABLE

    # Other beacons' batteries are ignored.
    coordinator._async_remote_battery("11:22:33:44:55:66", 1)
    await hass.async_block_till_done()
    assert hass.states.get(BATTERY).state == "54"


async def test_restore_after_reload(
    hass: HomeAssistant, sensor_entry: MockConfigEntry
) -> None:
    """Processor data (incl. battery) is restored when the entry reloads."""
    hass.config_entries.async_update_entry(
        sensor_entry, options={"battery_poll_hours": 0}
    )
    assert await hass.config_entries.async_setup(sensor_entry.entry_id)
    await hass.async_block_till_done()
    inject_service_info(hass, make_service_info())
    sensor_entry.runtime_data._async_remote_battery(ADDRESS, 77)
    await hass.async_block_till_done()

    assert await hass.config_entries.async_reload(sensor_entry.entry_id)
    await hass.async_block_till_done()

    # No new advertisement, but the entities exist again with their values.
    assert hass.states.get(BATTERY).state == "77"
    assert hass.states.get(TEMP).state == "28.68"
    processor = sensor_entry.runtime_data._processors[0]
    assert processor.entity_data[KEY_BATTERY] == 77
