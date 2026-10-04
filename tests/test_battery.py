"""Tests for reading the battery level over GATT."""

from __future__ import annotations

from datetime import timedelta
import itertools
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from bleak import BleakError
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant.core import HomeAssistant

from . import (
    ADDRESS,
    inject_service_info,
    make_service_info,
    register_fake_remote_scanner,
)
from custom_components.tempem_ble.const import (
    BATTERY_LEVEL_CHAR_UUID,
    BATTERY_RETRY_SECONDS,
    STORAGE_KEY,
)
from custom_components.tempem_ble.storage import POLL_STORE

BATTERY = "sensor.tempem_1234_battery"


def _client(level: bytes = b"\x57") -> MagicMock:
    client = MagicMock()
    client.read_gatt_char = AsyncMock(return_value=level)
    client.disconnect = AsyncMock()
    return client


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, **options: Any) -> None:
    if options:
        hass.config_entries.async_update_entry(entry, options=options)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


_counter = itertools.count()


async def _advertise(hass: HomeAssistant, **kwargs: Any) -> None:
    """Inject an advertisement with a new reading.

    The Bluetooth manager does not call back for repeated identical
    advertisements, so every call changes the temperature a little.
    """
    # Let the poll debouncer's cooldown timer run out first.
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    raw = 0x6E08 + next(_counter) % 64
    kwargs.setdefault(
        "manufacturer_data", {0x0059: bytes([0x03]) + raw.to_bytes(2) + b"\x7b\x34"}
    )
    inject_service_info(hass, make_service_info(**kwargs))
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_poll_on_connectable_advertisement(
    hass: HomeAssistant,
    sensor_entry: MockConfigEntry,
    mock_establish_connection: MagicMock,
    hass_storage: dict[str, Any],
    freezer,
) -> None:
    """A connectable advert triggers one battery read, which is persisted."""
    client = _client()
    mock_establish_connection.return_value = client
    await _setup(hass, sensor_entry)

    await _advertise(hass)
    assert mock_establish_connection.call_count == 1
    assert mock_establish_connection.call_args.args[1].address == ADDRESS
    client.read_gatt_char.assert_awaited_once_with(BATTERY_LEVEL_CHAR_UUID)
    client.disconnect.assert_awaited_once()
    assert hass.states.get(BATTERY).state == "87"
    assert hass.data[POLL_STORE].last_polled(ADDRESS) is not None

    # More advertisements within the interval do not reconnect.
    freezer.tick(timedelta(minutes=30))
    await _advertise(hass, rssi=-60)
    freezer.tick(timedelta(hours=100))
    await _advertise(hass, rssi=-61)
    assert mock_establish_connection.call_count == 1

    # The poll time is saved (delayed write).
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert ADDRESS in hass_storage[STORAGE_KEY]["data"]["polled"]

    # After the default week it is read again.
    freezer.tick(timedelta(hours=68))
    await _advertise(hass, rssi=-62)
    assert mock_establish_connection.call_count == 2


async def test_persisted_poll_prevents_reconnect_after_restart(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    sensor_entry: MockConfigEntry,
    mock_establish_connection: MagicMock,
) -> None:
    """A battery read before the restart is not repeated straight away."""
    import time

    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {"polled": {ADDRESS: time.time() - 3600}, "remote": {}},
    }
    await _setup(hass, sensor_entry)
    await _advertise(hass)
    mock_establish_connection.assert_not_called()


async def test_legacy_storage_format(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    sensor_entry: MockConfigEntry,
    mock_establish_connection: MagicMock,
) -> None:
    """The first storage format (flat address -> time map) still loads."""
    import time

    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {ADDRESS: time.time() - 3600},
    }
    await _setup(hass, sensor_entry)
    assert hass.data[POLL_STORE].last_polled(ADDRESS) is not None
    await _advertise(hass)
    mock_establish_connection.assert_not_called()


async def test_non_connectable_never_polls(
    hass: HomeAssistant,
    sensor_entry: MockConfigEntry,
    mock_establish_connection: MagicMock,
) -> None:
    """Beacons only heard by non-connectable scanners are not connected to."""
    await _setup(hass, sensor_entry)
    scanner, unsub = register_fake_remote_scanner(hass)
    scanner.inject()
    await hass.async_block_till_done(wait_background_tasks=True)
    await _advertise(hass, connectable=False, rssi=-50)
    assert hass.states.get("sensor.tempem_1234_temperature") is not None
    mock_establish_connection.assert_not_called()
    coordinator = sensor_entry.runtime_data
    assert not coordinator._async_needs_poll(make_service_info(connectable=False), None)
    unsub()


async def test_zero_disables(
    hass: HomeAssistant,
    sensor_entry: MockConfigEntry,
    mock_establish_connection: MagicMock,
) -> None:
    """battery_poll_hours = 0 never connects."""
    await _setup(hass, sensor_entry, battery_poll_hours=0)
    await _advertise(hass)
    mock_establish_connection.assert_not_called()
    assert hass.states.get(BATTERY) is None


@pytest.mark.parametrize(
    "error",
    [BleakError("boom"), TimeoutError()],
)
async def test_retry_backoff(
    hass: HomeAssistant,
    sensor_entry: MockConfigEntry,
    mock_establish_connection: MagicMock,
    freezer,
    error: Exception,
) -> None:
    """A failed read is retried after an hour, not on every advertisement."""
    mock_establish_connection.side_effect = error
    await _setup(hass, sensor_entry)

    await _advertise(hass)
    assert mock_establish_connection.call_count == 1
    assert hass.states.get(BATTERY) is None
    assert sensor_entry.runtime_data.last_poll_successful is False
    assert hass.data[POLL_STORE].last_polled(ADDRESS) is None

    freezer.tick(timedelta(seconds=BATTERY_RETRY_SECONDS - 60))
    await _advertise(hass, rssi=-60)
    assert mock_establish_connection.call_count == 1

    mock_establish_connection.side_effect = None
    mock_establish_connection.return_value = _client(b"\x64")
    freezer.tick(timedelta(seconds=120))
    await _advertise(hass, rssi=-61)
    assert mock_establish_connection.call_count == 2
    assert hass.states.get(BATTERY).state == "100"
    assert sensor_entry.runtime_data.last_poll_successful is True


async def test_failed_read_disconnects(
    hass: HomeAssistant,
    sensor_entry: MockConfigEntry,
    mock_establish_connection: MagicMock,
) -> None:
    """The connection is closed when the read fails or returns nothing."""
    client = _client(b"")
    mock_establish_connection.return_value = client
    await _setup(hass, sensor_entry)
    await _advertise(hass)
    client.disconnect.assert_awaited_once()
    assert hass.states.get(BATTERY) is None
    assert hass.data[POLL_STORE].last_polled(ADDRESS) is None


async def test_needs_poll(
    hass: HomeAssistant, sensor_entry: MockConfigEntry, freezer
) -> None:
    """needs_poll respects the interval, the backoff and reachability."""
    await _setup(hass, sensor_entry, battery_poll_hours=2)
    coordinator = sensor_entry.runtime_data
    store = hass.data[POLL_STORE]
    info = make_service_info()
    target = "custom_components.tempem_ble.coordinator.async_ble_device_from_address"

    with patch(target, return_value=None):
        assert not coordinator._async_needs_poll(info, None)
    with patch(target, return_value=info.device):
        assert coordinator._async_needs_poll(info, None)
        store.async_mark_polled(ADDRESS)
        assert not coordinator._async_needs_poll(info, None)
        freezer.tick(timedelta(hours=1, minutes=59))
        assert not coordinator._async_needs_poll(info, None)
        freezer.tick(timedelta(minutes=2))
        assert coordinator._async_needs_poll(info, None)

        # A battery level from a remote gateway also counts.
        store.async_set_remote_battery(ADDRESS, 80)
        assert not coordinator._async_needs_poll(info, None)

        hass.config_entries.async_update_entry(
            sensor_entry, options={"battery_poll_hours": 0}
        )
        freezer.tick(timedelta(days=30))
        assert not coordinator._async_needs_poll(info, None)
