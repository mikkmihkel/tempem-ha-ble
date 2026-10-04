"""Sensors for Tempem beacons and remote gateways."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.bluetooth.passive_update_processor import (
    PassiveBluetoothDataProcessor,
    PassiveBluetoothDataUpdate,
    PassiveBluetoothProcessorEntity,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfInformation,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TempemConfigEntry
from .entity import TempemGatewayEntity
from .gateway import GatewayStatus, TempemGateway


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TempemConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors for a beacon or a gateway."""
    runtime = entry.runtime_data
    if isinstance(runtime, TempemGateway):
        async_add_entities(
            TempemGatewaySensor(runtime, description) for description in GATEWAY_SENSORS
        )
        return

    processor: PassiveBluetoothDataProcessor[Any, PassiveBluetoothDataUpdate] = (
        PassiveBluetoothDataProcessor(lambda update: update)
    )
    entry.async_on_unload(
        processor.async_add_entities_listener(TempemBeaconSensor, async_add_entities)
    )
    entry.async_on_unload(
        runtime.async_register_processor(processor, SensorEntityDescription)
    )


class TempemBeaconSensor(
    PassiveBluetoothProcessorEntity[
        PassiveBluetoothDataProcessor[Any, PassiveBluetoothDataUpdate]
    ],
    SensorEntity,
):
    """Temperature, humidity, battery or signal strength of a beacon."""

    @property
    def native_value(self) -> int | float | None:
        """Return the value."""
        return self.processor.entity_data.get(self.entity_key)

    @property
    def available(self) -> bool:
        """Battery is read rarely; keep showing the last known value."""
        if self.entity_description.key == "battery":
            return self.processor.entity_data.get(self.entity_key) is not None
        return super().available


@dataclass(frozen=True, kw_only=True)
class TempemGatewaySensorDescription(SensorEntityDescription):
    """Describes a gateway sensor."""

    value_fn: Callable[[GatewayStatus], Any]


GATEWAY_SENSORS: tuple[TempemGatewaySensorDescription, ...] = (
    TempemGatewaySensorDescription(
        key="last_report",
        translation_key="last_report",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.last_seen,
    ),
    TempemGatewaySensorDescription(
        key="advertisements",
        translation_key="advertisements",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.advertisements if s.last_seen else None,
    ),
    TempemGatewaySensorDescription(
        key="wifi_rssi",
        translation_key="wifi_rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.wifi_rssi,
    ),
    TempemGatewaySensorDescription(
        key="uptime",
        translation_key="uptime",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.uptime,
    ),
    TempemGatewaySensorDescription(
        key="free_heap",
        translation_key="free_heap",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.KILOBYTES,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.free_heap,
    ),
)


class TempemGatewaySensor(TempemGatewayEntity, SensorEntity):
    """A value reported by a remote gateway."""

    entity_description: TempemGatewaySensorDescription

    def __init__(
        self, gateway: TempemGateway, description: TempemGatewaySensorDescription
    ) -> None:
        """Initialize the sensor."""
        super().__init__(gateway, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the value."""
        return self.entity_description.value_fn(self.gateway.status)
