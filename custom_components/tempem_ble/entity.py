"""Shared entity helpers for remote gateway entities."""

from __future__ import annotations

import logging

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .gateway import GATEWAY_MANUFACTURER, GATEWAY_MODEL, TempemGateway

_LOGGER = logging.getLogger(__name__)


class TempemGatewayEntity(Entity):
    """Base class for entities describing a remote gateway."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, gateway: TempemGateway, key: str) -> None:
        """Initialize the entity."""
        self.gateway = gateway
        self._attr_unique_id = f"{gateway.gateway_id}-{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, gateway.gateway_id)},
            name=gateway.entry.title,
            manufacturer=GATEWAY_MANUFACTURER,
            model=GATEWAY_MODEL,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to gateway reports."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self.gateway.status_signal, self._async_gateway_updated
            )
        )

    @callback
    def _async_gateway_updated(self) -> None:
        status = self.gateway.status
        if (device := self.device_entry) is not None:
            registry = dr.async_get(self.hass)
            connections = (
                {(CONNECTION_NETWORK_MAC, status.mac.lower())} if status.mac else set()
            )
            if connections - device.connections:
                try:
                    registry.async_update_device(
                        device.id, merge_connections=connections
                    )
                except dr.DeviceConnectionCollisionError:
                    # Another device (e.g. the same board added through the
                    # ESPHome integration, or an older gateway entry) has
                    # this MAC already.
                    _LOGGER.debug(
                        "%s: MAC %s already belongs to another device",
                        self.gateway.entry.title,
                        status.mac,
                    )
            if status.version and device.sw_version != status.version:
                registry.async_update_device(device.id, sw_version=status.version)
        self.async_write_ha_state()
