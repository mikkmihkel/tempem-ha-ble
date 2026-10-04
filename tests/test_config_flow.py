"""Tests for the config and options flows."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER
from homeassistant.const import CONF_ADDRESS, CONF_NAME, CONF_WEBHOOK_ID
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from . import ADDRESS, OTHER_ADDRESS, make_service_info, register_fake_remote_scanner
from custom_components.tempem_ble.const import (
    CONF_BATTERY_POLL_HOURS,
    CONF_ENTRY_TYPE,
    CONF_GATEWAY_ID,
    DOMAIN,
    ENTRY_TYPE_GATEWAY,
    ENTRY_TYPE_SENSOR,
)


async def test_bluetooth_discovery(hass: HomeAssistant) -> None:
    """A discovered beacon is confirmed and becomes a sensor entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=make_service_info()
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"
    assert result["description_placeholders"] == {
        "name": "Tempem 1234",
        "address": ADDRESS,
        "source": "local",
    }

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Tempem 1234"
    assert result["data"] == {CONF_ENTRY_TYPE: ENTRY_TYPE_SENSOR}
    assert result["result"].unique_id == ADDRESS
    await hass.async_block_till_done()


async def test_bluetooth_discovery_not_supported(hass: HomeAssistant) -> None:
    """Other Nordic (0x0059) devices are not offered."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=make_service_info(
            name="Nordic thing", manufacturer_data={0x0059: bytes.fromhex("436e087b34")}
        ),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


async def test_bluetooth_discovery_already_configured(
    hass: HomeAssistant, sensor_entry: MockConfigEntry
) -> None:
    """A beacon that already has an entry is not offered again."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=make_service_info()
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_bluetooth_discovery_already_in_progress(hass: HomeAssistant) -> None:
    """A second discovery of the same beacon aborts."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=make_service_info()
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=make_service_info()
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_in_progress"


async def test_user_menu(hass: HomeAssistant) -> None:
    """The user step is a menu."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["pick_sensor", "gateway"]


async def test_pick_sensor_no_devices(hass: HomeAssistant) -> None:
    """Nothing heard -> abort."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "pick_sensor"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_pick_sensor(hass: HomeAssistant) -> None:
    """Beacons heard by any scanner (also non-connectable) can be picked."""
    scanner, unsub = register_fake_remote_scanner(hass)
    scanner.inject(ADDRESS)
    # Not a Tempem beacon: not offered.
    scanner.inject(
        OTHER_ADDRESS,
        name="Nordic thing",
        manufacturer_data={0x0059: bytes.fromhex("436e087b34")},
    )
    # Already configured: not offered.
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="D1:00:00:00:00:01",
        data={CONF_ENTRY_TYPE: ENTRY_TYPE_SENSOR},
    ).add_to_hass(hass)
    scanner.inject("D1:00:00:00:00:01")
    await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "pick_sensor"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pick_sensor"
    options = result["data_schema"].schema[CONF_ADDRESS].container
    assert list(options) == [ADDRESS]
    assert options[ADDRESS] == f"Tempem 1234 ({ADDRESS}, -70 dBm)"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    # The label in the list has address and RSSI, the title (= device name)
    # does not.
    assert result["title"] == "Tempem 1234"
    assert result["data"] == {CONF_ENTRY_TYPE: ENTRY_TYPE_SENSOR}
    assert result["result"].unique_id == ADDRESS
    await hass.async_block_till_done()
    # The bluetooth discovery flow that was started for it is gone.
    assert not [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"].get("unique_id") == ADDRESS
    ]
    unsub()


async def test_gateway_flow(hass: HomeAssistant) -> None:
    """A remote gateway gets a webhook and an id."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "gateway"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "gateway"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Summer house"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "gateway_url"
    url = result["description_placeholders"]["webhook_url"]
    assert "/api/webhook/" in url

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Summer house"
    data = result["data"]
    assert data[CONF_ENTRY_TYPE] == ENTRY_TYPE_GATEWAY
    assert data[CONF_GATEWAY_ID].startswith("tempem-gw-")
    assert data[CONF_WEBHOOK_ID]
    assert url.endswith(f"/api/webhook/{data[CONF_WEBHOOK_ID]}")
    assert result["result"].unique_id == data[CONF_GATEWAY_ID]
    await hass.async_block_till_done()


async def test_gateway_flow_external_url(hass: HomeAssistant) -> None:
    """The webhook URL uses the external URL when one is configured."""
    hass.config.external_url = "https://ha.example.com"
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "gateway"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Summer house"}
    )
    assert result["description_placeholders"]["webhook_url"].startswith(
        "https://ha.example.com/api/webhook/"
    )


async def test_sensor_options(
    hass: HomeAssistant, sensor_entry: MockConfigEntry
) -> None:
    """Sensors have a battery poll interval option."""
    assert await hass.config_entries.async_setup(sensor_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(sensor_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_BATTERY_POLL_HOURS: 24}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert sensor_entry.options == {CONF_BATTERY_POLL_HOURS: 24}
    assert sensor_entry.runtime_data._poll_interval_seconds == 24 * 3600


async def test_gateway_options(
    hass: HomeAssistant, gateway_entry: MockConfigEntry
) -> None:
    """Gateways show their webhook URL again."""
    assert await hass.config_entries.async_setup(gateway_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(gateway_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "gateway"
    assert result["description_placeholders"]["webhook_url"].endswith(
        "/api/webhook/test-webhook-id"
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert gateway_entry.options == {}
