"""Connectivity of a remote Tempem gateway."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from . import TempemConfigEntry
from .entity import TempemGatewayEntity
from .gateway import TempemGateway

# The gateway reports every minute by default; allow a few missed reports.
OFFLINE_AFTER = timedelta(minutes=5)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TempemConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the gateway connectivity sensor."""
    gateway = entry.runtime_data
    assert isinstance(gateway, TempemGateway)
    async_add_entities([TempemGatewayConnectivity(gateway)])


class TempemGatewayConnectivity(TempemGatewayEntity, BinarySensorEntity):
    """On while the gateway keeps reporting."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, gateway: TempemGateway) -> None:
        """Initialize the sensor."""
        super().__init__(gateway, "connectivity")

    async def async_added_to_hass(self) -> None:
        """Re-evaluate periodically so the sensor goes off when reports stop."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._async_tick, timedelta(seconds=30)
            )
        )

    @callback
    def _async_tick(self, _now) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        """Return True if a report arrived recently."""
        last_seen = self.gateway.status.last_seen
        return last_seen is not None and dt_util.utcnow() - last_seen < OFFLINE_AFTER
