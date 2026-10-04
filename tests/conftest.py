"""Fixtures for Tempem BLE tests."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import loader
from homeassistant.config_entries import SOURCE_BLUETOOTH
from homeassistant.const import CONF_WEBHOOK_ID
from homeassistant.core import HomeAssistant

from . import ADDRESS
from custom_components.tempem_ble.const import (
    CONF_ENTRY_TYPE,
    CONF_GATEWAY_ID,
    DOMAIN,
    ENTRY_TYPE_GATEWAY,
    ENTRY_TYPE_SENSOR,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading custom_components/."""


@pytest.fixture
def only_tempem_bluetooth_matchers() -> Generator[None]:
    """Only match our own manifest's Bluetooth matchers.

    Core integrations also match company id 0x0059 (e.g. switchbot) and
    their discovery flows would fail on missing libraries.
    """
    real = loader.async_get_bluetooth

    async def _async_get_bluetooth(hass: HomeAssistant) -> list[dict[str, Any]]:
        return [m for m in await real(hass) if m["domain"] == DOMAIN]

    with patch(
        "homeassistant.components.bluetooth.async_get_bluetooth", _async_get_bluetooth
    ):
        yield


@pytest.fixture(autouse=True)
def auto_enable_bluetooth(
    auto_enable_custom_integrations: None,
    only_tempem_bluetooth_matchers: None,
    enable_bluetooth: None,
) -> None:
    """Every test runs with HA's Bluetooth stack (scanner start mocked)."""


@pytest.fixture
def sensor_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A sensor config entry, not yet set up."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Tempem 1234",
        unique_id=ADDRESS,
        data={CONF_ENTRY_TYPE: ENTRY_TYPE_SENSOR},
        source=SOURCE_BLUETOOTH,
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def gateway_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A remote gateway config entry, not yet set up."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Summer house",
        unique_id="tempem-gw-1a2b3c4d",
        data={
            CONF_ENTRY_TYPE: ENTRY_TYPE_GATEWAY,
            CONF_GATEWAY_ID: "tempem-gw-1a2b3c4d",
            CONF_WEBHOOK_ID: "test-webhook-id",
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def mock_establish_connection() -> Generator:
    """Patch the GATT connection used for battery reads."""
    with patch(
        "custom_components.tempem_ble.coordinator.establish_connection"
    ) as mock_connect:
        yield mock_connect
