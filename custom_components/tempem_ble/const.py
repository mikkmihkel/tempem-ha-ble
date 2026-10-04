"""Constants for the Tempem BLE integration."""

from __future__ import annotations

DOMAIN = "tempem_ble"
MANUFACTURER = "Dusun"
MODEL = "Tempem temperature/humidity beacon"

# Config entry data
CONF_ENTRY_TYPE = "entry_type"
ENTRY_TYPE_SENSOR = "sensor"
ENTRY_TYPE_GATEWAY = "gateway"
CONF_GATEWAY_ID = "gateway_id"

# Options
CONF_BATTERY_POLL_HOURS = "battery_poll_hours"
DEFAULT_BATTERY_POLL_HOURS = 168  # one week, same as the ESPHome gateway

# Battery service / characteristic (standard GATT)
BATTERY_LEVEL_CHAR_UUID = "00002a19-0000-1000-8000-00805f9b34fb"

# How long to wait before retrying a failed battery read.
BATTERY_RETRY_SECONDS = 60 * 60

# A battery level cached from a remote gateway is shown on a newly added
# sensor only if it is not older than this.
REMOTE_BATTERY_MAX_AGE_SECONDS = 30 * 24 * 3600

# Dispatcher signals
SIGNAL_REMOTE_BATTERY = f"{DOMAIN}_remote_battery"
SIGNAL_GATEWAY_STATUS = f"{DOMAIN}_gateway_status_{{}}"

STORAGE_KEY = f"{DOMAIN}.battery_polls"
STORAGE_VERSION = 1
