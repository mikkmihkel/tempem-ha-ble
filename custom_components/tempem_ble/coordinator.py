"""Coordinator for a single Tempem beacon."""

from __future__ import annotations

import logging
import time

from bleak import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components.bluetooth import (
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
    async_ble_device_from_address,
)
from homeassistant.components.bluetooth.active_update_processor import (
    ActiveBluetoothProcessorCoordinator,
)
from homeassistant.components.bluetooth.passive_update_processor import (
    PassiveBluetoothDataUpdate,
    PassiveBluetoothEntityKey,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import (
    BATTERY_LEVEL_CHAR_UUID,
    BATTERY_RETRY_SECONDS,
    CONF_BATTERY_POLL_HOURS,
    CONF_GATEWAY_ID,
    DEFAULT_BATTERY_POLL_HOURS,
    DOMAIN,
    GATEWAY_ID_PREFIX,
    MANUFACTURER,
    MODEL,
    MODEL_ID,
    REMOTE_BATTERY_MAX_AGE_SECONDS,
    SIGNAL_REMOTE_BATTERY,
)
from .parser import parse_manufacturer_data, short_address
from .storage import BatteryPollStore

_LOGGER = logging.getLogger(__name__)

KEY_TEMPERATURE = PassiveBluetoothEntityKey("temperature", None)
KEY_HUMIDITY = PassiveBluetoothEntityKey("humidity", None)
KEY_BATTERY = PassiveBluetoothEntityKey("battery", None)
KEY_RSSI = PassiveBluetoothEntityKey("signal_strength", None)

DESCRIPTIONS: dict[PassiveBluetoothEntityKey, SensorEntityDescription] = {
    KEY_TEMPERATURE: SensorEntityDescription(
        key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    KEY_HUMIDITY: SensorEntityDescription(
        key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    KEY_BATTERY: SensorEntityDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    KEY_RSSI: SensorEntityDescription(
        key="signal_strength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
}


class TempemCoordinator(
    ActiveBluetoothProcessorCoordinator[PassiveBluetoothDataUpdate]
):
    """Turns advertisements (and occasional battery reads) into sensor updates.

    Temperature and humidity come from passive advertisements, from any
    Bluetooth source Home Assistant has: the local adapter, ESPHome Bluetooth
    proxies on the LAN, or a remote Tempem gateway posting to the webhook.

    The battery level is not advertised. It is read over GATT when a
    connectable adapter or proxy can reach the beacon, or reported by a remote
    gateway that polled it itself.
    """

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, poll_store: BatteryPollStore
    ) -> None:
        """Initialize the coordinator."""
        address = entry.unique_id
        assert address is not None
        self._entry = entry
        self._poll_store = poll_store
        self._last_attempt: float | None = None
        # Remote gateway the device is listed under, see _async_link_gateway.
        self._linked_gateway: str | None = None
        self._device_info = DeviceInfo(
            name=entry.title,
            manufacturer=MANUFACTURER,
            model=MODEL,
            model_id=MODEL_ID,
        )
        super().__init__(
            hass=hass,
            logger=_LOGGER,
            address=address,
            mode=BluetoothScanningMode.ACTIVE,
            update_method=self._async_on_advertisement,
            needs_poll_method=self._async_needs_poll,
            poll_method=self._async_poll_battery,
            # Advertisements from non-connectable sources (remote gateways,
            # passive proxies) must reach us too.
            connectable=False,
        )

    @callback
    def async_start_remote_battery_listener(self) -> None:
        """Accept battery levels reported by remote gateways."""
        self._entry.async_on_unload(
            async_dispatcher_connect(
                self.hass, SIGNAL_REMOTE_BATTERY, self._async_remote_battery
            )
        )

    @property
    def _poll_interval_seconds(self) -> int:
        hours = self._entry.options.get(
            CONF_BATTERY_POLL_HOURS, DEFAULT_BATTERY_POLL_HOURS
        )
        return int(hours * 3600)

    def _update(
        self, values: dict[PassiveBluetoothEntityKey, float | int]
    ) -> PassiveBluetoothDataUpdate:
        return PassiveBluetoothDataUpdate(
            devices={None: self._device_info},
            entity_descriptions={key: DESCRIPTIONS[key] for key in values},
            entity_names=dict.fromkeys(values),
            entity_data=values,
        )

    @callback
    def _async_on_advertisement(
        self, service_info: BluetoothServiceInfoBleak
    ) -> PassiveBluetoothDataUpdate:
        self._async_link_gateway(service_info.source)
        values: dict[PassiveBluetoothEntityKey, float | int] = {
            KEY_RSSI: service_info.rssi
        }
        if reading := parse_manufacturer_data(service_info.manufacturer_data):
            if reading.temperature is not None:
                values[KEY_TEMPERATURE] = reading.temperature
            if reading.humidity is not None:
                values[KEY_HUMIDITY] = reading.humidity
        return self._update(values)

    @callback
    def _async_link_gateway(self, source: str) -> None:
        """List the sensor under the remote gateway it is heard through.

        Sensors at a remote site then show up as connected devices of their
        gateway. Hearing the sensor through a local adapter or proxy leaves
        the link alone, so it does not flap when both hear it.
        """
        if source == self._linked_gateway or not source.startswith(GATEWAY_ID_PREFIX):
            return
        gateway_entry = next(
            (
                entry
                for entry in self.hass.config_entries.async_entries(DOMAIN)
                if entry.data.get(CONF_GATEWAY_ID) == source
            ),
            None,
        )
        if gateway_entry is None:
            return
        registry = dr.async_get(self.hass)
        gateway_device = next(
            (
                device
                for device in dr.async_entries_for_config_entry(
                    registry, gateway_entry.entry_id
                )
                if (DOMAIN, source) in device.identifiers
            ),
            None,
        )
        sensor_device = next(
            iter(dr.async_entries_for_config_entry(registry, self._entry.entry_id)),
            None,
        )
        if gateway_device is None or sensor_device is None:
            # The sensor's device is created with its first entities; try
            # again with the next advertisement.
            return
        self._linked_gateway = source
        if sensor_device.via_device_id != gateway_device.id:
            registry.async_update_device(
                sensor_device.id, via_device_id=gateway_device.id
            )

    @callback
    def _async_push(self, update: PassiveBluetoothDataUpdate) -> None:
        """Hand data that did not come from an advertisement to the entities.

        Unlike async_set_updated_data this does not mark the beacon as
        available: a battery report says nothing about whether it is in range.
        """
        for processor in self._processors:
            processor.async_handle_update(update)

    @callback
    def _async_remote_battery(self, address: str, level: int) -> None:
        if address.upper() != self.address.upper():
            return
        # The store already counts the gateway's reading as a poll.
        self._async_push(self._update({KEY_BATTERY: level}))

    @callback
    def async_restore_remote_battery(self) -> None:
        """Show a level a remote gateway reported while this entry was not loaded."""
        if (remote := self._poll_store.remote_battery(self.address)) is None:
            return
        level, timestamp = remote
        if time.time() - timestamp > REMOTE_BATTERY_MAX_AGE_SECONDS:
            return
        # A restored level is either this one, an older remote one or a local
        # read. Only a newer local read beats it.
        if (local := self._poll_store.last_local_poll(self.address)) and (
            local > timestamp
        ):
            return
        self._async_push(self._update({KEY_BATTERY: level}))

    @callback
    def _async_needs_poll(
        self, service_info: BluetoothServiceInfoBleak, last_poll: float | None
    ) -> bool:
        if self.hass.is_stopping or self._poll_interval_seconds <= 0:
            return False
        now = time.time()
        if (
            self._last_attempt is not None
            and now - self._last_attempt < BATTERY_RETRY_SECONDS
        ):
            return False
        last_success = self._poll_store.last_polled(self.address)
        if (
            last_success is not None
            and now - last_success < self._poll_interval_seconds
        ):
            return False
        # Only try when something that can actually connect hears the beacon.
        return (
            async_ble_device_from_address(self.hass, self.address, connectable=True)
            is not None
        )

    async def _async_poll_battery(
        self, service_info: BluetoothServiceInfoBleak
    ) -> PassiveBluetoothDataUpdate:
        self._last_attempt = time.time()
        device = async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if device is None:
            raise BleakError(
                f"{self.address} is not reachable by a connectable adapter"
            )
        name = f"Tempem {short_address(self.address)}"
        client = await establish_connection(BleakClientWithServiceCache, device, name)
        try:
            data = await client.read_gatt_char(BATTERY_LEVEL_CHAR_UUID)
        finally:
            await client.disconnect()
        if not data:
            raise BleakError(f"{name}: empty battery level read")
        level = max(0, min(100, data[0]))
        _LOGGER.debug("%s battery level %s%%", name, level)
        self._poll_store.async_mark_polled(self.address)
        return self._update({KEY_BATTERY: level})
