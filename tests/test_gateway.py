"""End-to-end tests for remote gateways posting to the webhook."""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus
import json
import logging
import time
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from homeassistant.components import webhook
from homeassistant.components.bluetooth import async_discovered_service_info
from homeassistant.config_entries import SOURCE_BLUETOOTH, ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.util.aiohttp import MockRequest

from . import ADDRESS, OTHER_ADDRESS, contract_payload, find_device
from custom_components.tempem_ble.const import DOMAIN
from custom_components.tempem_ble.gateway import MAX_BODY_BYTES
from custom_components.tempem_ble.storage import POLL_STORE

URL = "/api/webhook/test-webhook-id"
GW_ID = "tempem-gw-1a2b3c4d"
TEMP = "sensor.tempem_1234_temperature"
BATTERY = "sensor.tempem_1234_battery"


async def _setup(hass: HomeAssistant, *entries: MockConfigEntry) -> None:
    for entry in entries:
        if entry.state is ConfigEntryState.NOT_LOADED:
            assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _post(
    client: Any, payload: Any, status: HTTPStatus = HTTPStatus.OK
) -> dict[str, Any]:
    resp = await client.post(URL, json=payload)
    assert resp.status == status, await resp.text()
    return await resp.json()


async def test_contract_payload_discovers_beacon(
    hass: HomeAssistant,
    gateway_entry: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """Adverts reach HA Bluetooth and start a discovery flow."""
    await _setup(hass, gateway_entry)
    client = await hass_client_no_auth()

    assert await _post(client, contract_payload()) == {"ok": True, "accepted": 1}
    await hass.async_block_till_done()

    infos = {
        info.address: info
        for info in async_discovered_service_info(hass, connectable=False)
    }
    assert ADDRESS in infos
    info = infos[ADDRESS]
    assert info.source == GW_ID
    assert info.connectable is False
    assert info.manufacturer_data == {89: bytes.fromhex("036e087b34")}
    assert info.name == "TempemSens"
    assert info.rssi == -70
    # Not reachable for GATT through the gateway.
    assert ADDRESS not in {
        i.address for i in async_discovered_service_info(hass, connectable=True)
    }

    flows = [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"]["source"] == SOURCE_BLUETOOTH
    ]
    assert len(flows) == 1
    assert flows[0]["context"]["unique_id"] == ADDRESS
    # The confirm dialog names the gateway, not its internal id.
    flow = hass.config_entries.flow.async_get(flows[0]["flow_id"])
    assert flow["step_id"] == "bluetooth_confirm"
    result = await hass.config_entries.flow.async_configure(flows[0]["flow_id"])
    assert result["description_placeholders"]["source"] == f"Summer house ({GW_ID})"


async def test_contract_payload_updates_sensor(
    hass: HomeAssistant,
    gateway_entry: MockConfigEntry,
    sensor_entry: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """A configured sensor behind a remote gateway gets readings and battery."""
    await _setup(hass, gateway_entry, sensor_entry)
    client = await hass_client_no_auth()

    await _post(client, contract_payload())
    await hass.async_block_till_done()

    assert hass.states.get(TEMP).state == "28.68"
    assert hass.states.get("sensor.tempem_1234_humidity").state == "54.2"
    assert hass.states.get(BATTERY).state == "87"
    store = hass.data[POLL_STORE]
    # A remote battery counts as polled so a local adapter does not reconnect.
    assert store.last_polled(ADDRESS) is not None
    assert store.remote_battery(ADDRESS)[0] == 87

    # Next report with new values.
    payload = contract_payload()
    payload["advertisements"][0]["manufacturer_data"] = {"89": "0360005000"}
    payload["batteries"] = []
    await _post(client, payload)
    await hass.async_block_till_done()
    assert hass.states.get(TEMP).state == "19.04"
    assert hass.states.get(BATTERY).state == "87"


async def test_gateway_sensors(
    hass: HomeAssistant,
    gateway_entry: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Diagnostics of the gateway update with every report."""
    await _setup(hass, gateway_entry)
    assert hass.states.get("binary_sensor.summer_house_connectivity").state == STATE_OFF
    assert hass.states.get("sensor.summer_house_last_report").state == STATE_UNKNOWN
    assert (
        hass.states.get("sensor.summer_house_advertisements_in_last_report").state
        == STATE_UNKNOWN
    )

    client = await hass_client_no_auth()
    await _post(client, contract_payload())
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.summer_house_connectivity").state == STATE_ON
    assert hass.states.get("sensor.summer_house_last_report").state != STATE_UNKNOWN
    assert (
        hass.states.get("sensor.summer_house_advertisements_in_last_report").state
        == "1"
    )
    assert hass.states.get("sensor.summer_house_wi_fi_signal").state == "-61"

    device = find_device(hass, identifier=(DOMAIN, GW_ID))
    assert device is not None
    assert device.sw_version == "2026.9.1"
    assert (dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:ff") in device.connections

    status = gateway_entry.runtime_data.status
    assert status.uptime == 12345
    assert status.free_heap == 123456
    assert status.reports == 1


async def test_gateway_connectivity_goes_off(
    hass: HomeAssistant,
    gateway_entry: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
    freezer,
) -> None:
    """Connectivity turns off when reports stop."""

    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    await _setup(hass, gateway_entry)
    client = await hass_client_no_auth()
    await _post(client, contract_payload(advertisements=[], batteries=[]))
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.summer_house_connectivity").state == STATE_ON

    freezer.tick(timedelta(minutes=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.summer_house_connectivity").state == STATE_OFF


async def test_invalid_json(
    hass: HomeAssistant,
    gateway_entry: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """Broken JSON or wrong shapes are rejected with 400."""
    await _setup(hass, gateway_entry)
    client = await hass_client_no_auth()

    resp = await client.post(URL, data=b"{not json")
    assert resp.status == HTTPStatus.BAD_REQUEST
    assert (await resp.json())["ok"] is False

    for bad in ([], "x", None, {"advertisements": "nope"}, {"gateway": {"mac": "zz"}}):
        resp = await client.post(URL, json=bad)
        assert resp.status == HTTPStatus.BAD_REQUEST, bad

    resp = await client.post(URL, data=b"\xff\xfe")
    assert resp.status == HTTPStatus.BAD_REQUEST
    assert gateway_entry.runtime_data.status.reports == 0
    assert gateway_entry.runtime_data.status.rejected == 7


async def test_too_many_advertisements(
    hass: HomeAssistant,
    gateway_entry: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """More than 256 adverts in one batch is rejected."""
    await _setup(hass, gateway_entry)
    client = await hass_client_no_auth()
    advert = contract_payload()["advertisements"][0]
    resp = await client.post(URL, json=contract_payload(advertisements=[advert] * 257))
    assert resp.status == HTTPStatus.BAD_REQUEST


async def test_oversized(
    hass: HomeAssistant,
    gateway_entry: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """Bodies over the limit get 413, also when streamed without a length."""
    await _setup(hass, gateway_entry)
    client = await hass_client_no_auth()
    big = json.dumps(contract_payload(padding="x" * MAX_BODY_BYTES)).encode()

    resp = await client.post(URL, data=big)
    assert resp.status == HTTPStatus.REQUEST_ENTITY_TOO_LARGE

    async def _chunks():
        for i in range(0, len(big), 1000):
            yield big[i : i + 1000]

    resp = await client.post(URL, data=_chunks())
    assert resp.status == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert gateway_entry.runtime_data.status.reports == 0


async def test_chunked_body(
    hass: HomeAssistant,
    gateway_entry: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """A body that arrives in several chunks is read completely."""
    await _setup(hass, gateway_entry)
    client = await hass_client_no_auth()
    advert = contract_payload()["advertisements"][0]
    adverts = [
        {**advert, "address": f"C0:FE:ED:01:{i // 256:02X}:{i % 256:02X}"}
        for i in range(200)
    ]
    body = json.dumps(contract_payload(advertisements=adverts)).encode()
    assert len(body) > 16 * 1024

    async def _chunks():
        for i in range(0, len(body), 512):
            yield body[i : i + 512]

    resp = await client.post(URL, data=_chunks())
    assert resp.status == HTTPStatus.OK
    assert (await resp.json()) == {"ok": True, "accepted": 200}


async def test_cloud_mock_request(
    hass: HomeAssistant, gateway_entry: MockConfigEntry
) -> None:
    """Home Assistant Cloud delivers webhooks as a MockRequest."""
    await _setup(hass, gateway_entry)
    request = MockRequest(
        content=json.dumps(contract_payload()).encode(),
        mock_source="cloud",
        method="POST",
    )
    resp = await webhook.async_handle_webhook(hass, "test-webhook-id", request)
    assert resp.status == HTTPStatus.OK
    assert json.loads(resp.body) == {"ok": True, "accepted": 1}
    assert gateway_entry.runtime_data.status.reports == 1


async def test_bad_entries_skipped(
    hass: HomeAssistant,
    gateway_entry: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """A single bad advert or battery does not fail the batch."""
    await _setup(hass, gateway_entry)
    client = await hass_client_no_auth()
    good = contract_payload()["advertisements"][0]
    adverts = [
        {**good, "address": "not-a-mac"},
        {**good, "address": OTHER_ADDRESS, "rssi": "loud"},
        {**good, "address": OTHER_ADDRESS, "manufacturer_data": {"89": "zz"}},
        {**good, "address": OTHER_ADDRESS, "manufacturer_data": {"x": "00"}},
        {**good, "address": OTHER_ADDRESS, "manufacturer_data": {"70000": "00"}},
        {**good, "address": OTHER_ADDRESS, "age": -1},
        # Too old to be useful.
        {**good, "address": OTHER_ADDRESS, "age": 3600},
        "garbage",
        good,
        # Lower case MAC with dashes is normalised.
        {**good, "address": "c1-22-33-44-55-77", "name": None},
    ]
    batteries = [
        {"address": ADDRESS, "level": 101},
        {"address": "bad", "level": 50},
        {"address": OTHER_ADDRESS, "level": 50, "age": 30 * 24 * 3600},
        {"address": ADDRESS, "level": 87},
    ]
    result = await _post(
        client, contract_payload(advertisements=adverts, batteries=batteries)
    )
    assert result == {"ok": True, "accepted": 2}
    addresses = {
        info.address for info in async_discovered_service_info(hass, connectable=False)
    }
    assert ADDRESS in addresses
    assert "C1:22:33:44:55:77" in addresses
    assert OTHER_ADDRESS not in addresses
    store = hass.data[POLL_STORE]
    assert store.remote_battery(ADDRESS)[0] == 87
    assert store.remote_battery(OTHER_ADDRESS) is None


async def test_method_not_allowed(
    hass: HomeAssistant,
    gateway_entry: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """Only POST is accepted."""
    await _setup(hass, gateway_entry)
    client = await hass_client_no_auth()
    resp = await client.get(URL)
    assert resp.status == HTTPStatus.METHOD_NOT_ALLOWED


async def test_minimal_payload(
    hass: HomeAssistant,
    gateway_entry: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """All top level keys are optional; unknown keys are ignored."""
    await _setup(hass, gateway_entry)
    client = await hass_client_no_auth()
    assert await _post(client, {}) == {"ok": True, "accepted": 0}
    assert await _post(client, {"v": 2, "future": True}) == {"ok": True, "accepted": 0}


async def test_remote_battery_cached_for_later_sensor(
    hass: HomeAssistant,
    gateway_entry: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """A battery reported before the sensor entry exists is shown once added."""
    await _setup(hass, gateway_entry)
    client = await hass_client_no_auth()
    await _post(client, contract_payload())
    await hass.async_block_till_done()

    sensor_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Tempem 1234",
        unique_id=ADDRESS,
        data={"entry_type": "sensor"},
    )
    sensor_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(sensor_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(BATTERY).state == "87"
    assert hass.states.get(TEMP).state == "28.68"


async def test_old_remote_battery_not_restored(
    hass: HomeAssistant, sensor_entry: MockConfigEntry
) -> None:
    """A cached remote battery older than 30 days is not shown."""
    await _setup(hass, sensor_entry)
    store = hass.data[POLL_STORE]
    assert await hass.config_entries.async_unload(sensor_entry.entry_id)
    store.async_set_remote_battery(ADDRESS, 50, time.time() - 31 * 24 * 3600)
    # Older reports never replace newer ones.
    store.async_set_remote_battery(ADDRESS, 10, time.time() - 40 * 24 * 3600)
    assert store.remote_battery(ADDRESS)[0] == 50
    assert await hass.config_entries.async_setup(sensor_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(BATTERY) is None

    assert await hass.config_entries.async_unload(sensor_entry.entry_id)
    store.async_set_remote_battery(ADDRESS, 60, time.time() - 29 * 24 * 3600)
    assert await hass.config_entries.async_setup(sensor_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(BATTERY).state == "60"


async def test_gateway_name_starting_with_hci(
    hass: HomeAssistant,
    gateway_entry: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """A title like "hci gw" must not be mistaken for a local adapter."""
    hass.config_entries.async_update_entry(gateway_entry, title="hci gw")
    await _setup(hass, gateway_entry)
    assert gateway_entry.state is ConfigEntryState.LOADED
    client = await hass_client_no_auth()
    assert await _post(client, contract_payload()) == {"ok": True, "accepted": 1}


async def test_hostile_payloads(
    hass: HomeAssistant,
    gateway_entry: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """Deep nesting and non-finite numbers are rejected, not raised."""
    await _setup(hass, gateway_entry)
    client = await hass_client_no_auth()
    status = gateway_entry.runtime_data.status

    for raw in (
        b"[" * 60000,
        b'{"v": Infinity}',
        b'{"gateway": {"wifi_rssi": Infinity}}',
        b'{"gateway": {"free_heap": -Infinity}}',
        b'{"gateway": {"uptime": Infinity}}',
    ):
        resp = await client.post(URL, data=raw)
        assert resp.status == HTTPStatus.BAD_REQUEST, raw[:40]
        assert (await resp.json()) == {"ok": False, "error": "invalid payload"}
    assert status.rejected == 5

    # Single bad entries are skipped, the rest of the batch still counts.
    good = json.dumps(contract_payload()["advertisements"][0])
    body = (
        '{"advertisements": ['
        f'{good[:-1]}, "address": "{OTHER_ADDRESS}", "rssi": Infinity}}, '
        f'{good[:-1]}, "address": "{OTHER_ADDRESS}\\n"}}, '
        f'{good[:-1]}, "address": "{OTHER_ADDRESS}", '
        '"manufacturer_data": {"89": "036e087b34\\n"}}, '
        f'{good[:-1]}, "address": "{OTHER_ADDRESS}", "age": NaN}}, '
        f"{good}], "
        '"batteries": [{"address": "' + ADDRESS + '", "level": Infinity}]}'
    )
    resp = await client.post(URL, data=body.encode())
    assert resp.status == HTTPStatus.OK
    assert (await resp.json()) == {"ok": True, "accepted": 1}
    addresses = {
        info.address for info in async_discovered_service_info(hass, connectable=False)
    }
    assert ADDRESS in addresses
    assert not {a for a in addresses if a.startswith(OTHER_ADDRESS)}
    assert hass.data[POLL_STORE].remote_battery(ADDRESS) is None
    assert status.reports == 1


async def test_rejected_report_logged_once(
    hass: HomeAssistant,
    gateway_entry: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Invalid reports warn once until a good report arrives."""
    await _setup(hass, gateway_entry)
    client = await hass_client_no_auth()

    def _warnings() -> int:
        return sum(
            1
            for record in caplog.records
            if record.levelno == logging.WARNING and "rejected report" in record.message
        )

    for _ in range(3):
        await _post(client, [], HTTPStatus.BAD_REQUEST)
    assert _warnings() == 1
    await _post(client, {})
    await _post(client, [], HTTPStatus.BAD_REQUEST)
    assert _warnings() == 2


async def test_distinct_address_cap(
    hass: HomeAssistant,
    gateway_entry: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """A gateway cannot inject an unbounded number of addresses."""
    await _setup(hass, gateway_entry)
    client = await hass_client_no_auth()
    advert = contract_payload()["advertisements"][0]

    def _adverts(start: int, count: int) -> list[dict[str, Any]]:
        return [
            {
                **advert,
                "address": f"C0:FE:ED:{i // 65536:02X}:{i // 256 % 256:02X}:"
                f"{i % 256:02X}",
            }
            for i in range(start, start + count)
        ]

    with patch("custom_components.tempem_ble.gateway.MAX_ADDRESSES", 300):
        result = await _post(client, contract_payload(advertisements=_adverts(0, 256)))
        assert result["accepted"] == 256
        result = await _post(
            client, contract_payload(advertisements=_adverts(200, 256))
        )
        # 56 known addresses + 44 new ones up to the cap.
        assert result["accepted"] == 100
        # Known addresses keep being accepted.
        result = await _post(client, contract_payload(advertisements=_adverts(0, 10)))
        assert result["accepted"] == 10

        # Addresses not reported for 15 minutes free their slot.
        addresses = gateway_entry.runtime_data._addresses
        for address in addresses:
            addresses[address] -= 16 * 60
        result = await _post(
            client, contract_payload(advertisements=_adverts(1000, 256))
        )
        assert result["accepted"] == 256


async def test_older_remote_battery_not_shown(
    hass: HomeAssistant,
    gateway_entry: MockConfigEntry,
    sensor_entry: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """A battery reading older than the one already shown is ignored."""
    await _setup(hass, gateway_entry, sensor_entry)
    client = await hass_client_no_auth()
    await _post(
        client,
        contract_payload(batteries=[{"address": ADDRESS, "level": 80, "age": 10}]),
    )
    await _post(
        client,
        contract_payload(batteries=[{"address": ADDRESS, "level": 90, "age": 3600}]),
    )
    await hass.async_block_till_done()
    assert hass.states.get(BATTERY).state == "80"


async def test_gateway_mac_used_by_other_device(
    hass: HomeAssistant,
    gateway_entry: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A MAC that already belongs to another device does not break the entities.

    Before Home Assistant 2026.9 a connection could belong to one device only
    and adding it raised DeviceConnectionCollisionError; since then it may be
    shared across config entries. Either way the report must go through.
    """
    other_entry = MockConfigEntry(domain="esphome")
    other_entry.add_to_hass(hass)
    other = device_registry.async_get_or_create(
        config_entry_id=other_entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:ff")},
    )
    await _setup(hass, gateway_entry)
    client = await hass_client_no_auth()
    await _post(client, contract_payload())
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.summer_house_connectivity").state == STATE_ON
    assert hass.states.get("sensor.summer_house_wi_fi_signal").state == "-61"
    device = find_device(hass, identifier=(DOMAIN, GW_ID))
    assert device.sw_version == "2026.9.1"
    assert device_registry.async_get(other.id).connections == other.connections


async def test_remote_battery_cache_bounded(
    hass: HomeAssistant, gateway_entry: MockConfigEntry
) -> None:
    """The persisted remote battery cache does not grow without bound."""
    await _setup(hass, gateway_entry)
    store = hass.data[POLL_STORE]
    now = time.time()
    with patch("custom_components.tempem_ble.storage.MAX_REMOTE_BATTERIES", 3):
        store.async_set_remote_battery("C1:00:00:00:00:01", 50, now - 40 * 24 * 3600)
        store.async_set_remote_battery("C1:00:00:00:00:02", 50, now - 300)
        store.async_set_remote_battery("C1:00:00:00:00:03", 50, now - 200)
        store.async_set_remote_battery("C1:00:00:00:00:04", 50, now - 100)
        store.async_set_remote_battery("C1:00:00:00:00:05", 50, now)
    assert [
        address
        for address in (f"C1:00:00:00:00:0{i}" for i in range(1, 6))
        if store.remote_battery(address)
    ] == ["C1:00:00:00:00:03", "C1:00:00:00:00:04", "C1:00:00:00:00:05"]


async def test_newer_remote_battery_after_reload(
    hass: HomeAssistant, sensor_entry: MockConfigEntry
) -> None:
    """A level reported while the entry was not loaded replaces the restored one."""
    await _setup(hass, sensor_entry)
    store = hass.data[POLL_STORE]
    store.async_set_remote_battery(ADDRESS, 77, time.time() - 100)
    sensor_entry.runtime_data._async_remote_battery(ADDRESS, 77)
    await hass.async_block_till_done()
    assert hass.states.get(BATTERY).state == "77"

    assert await hass.config_entries.async_unload(sensor_entry.entry_id)
    store.async_set_remote_battery(ADDRESS, 60, time.time())
    assert await hass.config_entries.async_setup(sensor_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(BATTERY).state == "60"


async def test_only_dusun_data_injected(
    hass: HomeAssistant,
    gateway_entry: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """Other company ids are dropped so other integrations cannot be spoofed."""
    await _setup(hass, gateway_entry)
    client = await hass_client_no_auth()
    payload = contract_payload()
    payload["advertisements"][0]["manufacturer_data"] = {
        "89": "036e087b34",
        "76": "02150000",
    }
    payload["advertisements"].append(
        {
            "address": OTHER_ADDRESS,
            "rssi": -60,
            "manufacturer_data": {"76": "02150000"},
            "age": 0,
        }
    )
    assert await _post(client, payload) == {"ok": True, "accepted": 1}
    await hass.async_block_till_done()

    infos = {
        info.address: info
        for info in async_discovered_service_info(hass, connectable=False)
    }
    assert infos[ADDRESS].manufacturer_data == {89: bytes.fromhex("036e087b34")}
    assert OTHER_ADDRESS not in infos
