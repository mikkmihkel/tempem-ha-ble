"""Tempem/Dusun battery poller.

Connects to each Tempem beacon in turn over BLE GATT and reads the standard
Battery Level characteristic (service 0x180F, characteristic 0x2A19).

Targets can be configured statically (``targets:``, optionally with a battery
sensor each, as in the original single-site gateway) and/or learned at runtime
(``auto_discover: true``): every MAC that advertises a valid Dusun temp/hum
frame under company id 0x0059 becomes a poll target.

Each successful reading fires ``on_battery`` with ``mac`` (std::string,
"AA:BB:CC:DD:EE:FF") and ``level`` (uint8_t, percent).
"""

from esphome import automation
import esphome.codegen as cg
from esphome.components import esp32_ble_tracker, sensor
import esphome.config_validation as cv
from esphome.const import CONF_ID, CONF_INTERVAL

CODEOWNERS = ["@mikkmihkel"]
DEPENDENCIES = ["esp32_ble_tracker"]
AUTO_LOAD = ["sensor"]

tempem_ns = cg.esphome_ns.namespace("tempem_battery_poller")
TempemBatteryPoller = tempem_ns.class_(
    "TempemBatteryPoller", cg.Component, esp32_ble_tracker.ESPBTClient
)

CONF_TARGETS = "targets"
CONF_MAC_ADDRESS = "mac_address"
CONF_BATTERY_SENSOR_ID = "battery_sensor_id"
CONF_SUCCESS_TOTAL_SENSOR_ID = "success_total_sensor_id"
CONF_FAILURE_TOTAL_SENSOR_ID = "failure_total_sensor_id"
CONF_LAST_CYCLE_DURATION_SENSOR_ID = "last_cycle_duration_sensor_id"
CONF_AUTO_DISCOVER = "auto_discover"
CONF_MAX_TARGETS = "max_targets"
CONF_FIRST_POLL_DELAY = "first_poll_delay"
CONF_RETRY_INTERVAL = "retry_interval"
CONF_MAX_RETRIES = "max_retries"
CONF_ON_BATTERY = "on_battery"

# Static targets only: the first cycle can start quickly.
DEFAULT_FIRST_POLL_DELAY_STATIC = "20s"
# Auto-discovery: give the scanner time to learn the beacons first.
DEFAULT_FIRST_POLL_DELAY_AUTO = "120s"
# Deadlines are compared as signed 32-bit millis() differences, so every
# period must stay well below 2^31 ms (~24.8 days).
MAX_PERIOD = cv.TimePeriod(days=24)


def _validate(config):
    if not config[CONF_TARGETS] and not config[CONF_AUTO_DISCOVER]:
        raise cv.Invalid(
            f"Configure at least one entry in '{CONF_TARGETS}' or set "
            f"'{CONF_AUTO_DISCOVER}: true'"
        )
    if len(config[CONF_TARGETS]) > config[CONF_MAX_TARGETS]:
        raise cv.Invalid(
            f"{len(config[CONF_TARGETS])} static targets exceed "
            f"{CONF_MAX_TARGETS} ({config[CONF_MAX_TARGETS]})"
        )
    macs = [str(t[CONF_MAC_ADDRESS]) for t in config[CONF_TARGETS]]
    if len(macs) != len(set(macs)):
        raise cv.Invalid("Duplicate mac_address in targets")
    if CONF_FIRST_POLL_DELAY not in config:
        config[CONF_FIRST_POLL_DELAY] = cv.positive_time_period_milliseconds(
            DEFAULT_FIRST_POLL_DELAY_AUTO
            if config[CONF_AUTO_DISCOVER]
            else DEFAULT_FIRST_POLL_DELAY_STATIC
        )
    return config


CONFIG_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(TempemBatteryPoller),
            cv.Optional(CONF_INTERVAL, default="1h"): cv.All(
                cv.positive_time_period, cv.Range(max=MAX_PERIOD)
            ),
            cv.Optional(CONF_TARGETS, default=[]): cv.ensure_list(
                cv.Schema(
                    {
                        cv.Required(CONF_MAC_ADDRESS): cv.mac_address,
                        cv.Optional(CONF_BATTERY_SENSOR_ID): cv.use_id(sensor.Sensor),
                    }
                )
            ),
            cv.Optional(CONF_AUTO_DISCOVER, default=False): cv.boolean,
            cv.Optional(CONF_MAX_TARGETS, default=32): cv.int_range(min=1, max=64),
            cv.Optional(CONF_FIRST_POLL_DELAY): cv.All(
                cv.positive_time_period_milliseconds, cv.Range(max=MAX_PERIOD)
            ),
            cv.Optional(CONF_RETRY_INTERVAL, default="1h"): cv.All(
                cv.positive_time_period_milliseconds, cv.Range(max=MAX_PERIOD)
            ),
            cv.Optional(CONF_MAX_RETRIES, default=3): cv.int_range(min=0, max=20),
            cv.Optional(CONF_SUCCESS_TOTAL_SENSOR_ID): cv.use_id(sensor.Sensor),
            cv.Optional(CONF_FAILURE_TOTAL_SENSOR_ID): cv.use_id(sensor.Sensor),
            cv.Optional(CONF_LAST_CYCLE_DURATION_SENSOR_ID): cv.use_id(sensor.Sensor),
            cv.Optional(CONF_ON_BATTERY): automation.validate_automation(single=True),
        }
    )
    .extend(cv.COMPONENT_SCHEMA)
    .extend(esp32_ble_tracker.ESP_BLE_DEVICE_SCHEMA),
    _validate,
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await esp32_ble_tracker.register_client(var, config)

    cg.add(var.set_poll_interval_ms(config[CONF_INTERVAL].total_milliseconds))
    cg.add(var.set_first_poll_delay_ms(config[CONF_FIRST_POLL_DELAY].total_milliseconds))
    cg.add(var.set_retry_interval_ms(config[CONF_RETRY_INTERVAL].total_milliseconds))
    cg.add(var.set_max_retries(config[CONF_MAX_RETRIES]))
    cg.add(var.set_auto_discover(config[CONF_AUTO_DISCOVER]))
    cg.add(var.set_max_targets(config[CONF_MAX_TARGETS]))

    for t in config[CONF_TARGETS]:
        mac_u64_int = int(str(t[CONF_MAC_ADDRESS]).replace(":", ""), 16)
        mac_expr = esp32_ble_tracker.as_hex(f"{mac_u64_int:012x}")
        if CONF_BATTERY_SENSOR_ID in t:
            sens = await cg.get_variable(t[CONF_BATTERY_SENSOR_ID])
            cg.add(var.add_target(mac_expr, sens))
        else:
            cg.add(var.add_target(mac_expr, cg.nullptr))

    if CONF_SUCCESS_TOTAL_SENSOR_ID in config:
        sens = await cg.get_variable(config[CONF_SUCCESS_TOTAL_SENSOR_ID])
        cg.add(var.set_success_total_sensor(sens))
    if CONF_FAILURE_TOTAL_SENSOR_ID in config:
        sens = await cg.get_variable(config[CONF_FAILURE_TOTAL_SENSOR_ID])
        cg.add(var.set_failure_total_sensor(sens))
    if CONF_LAST_CYCLE_DURATION_SENSOR_ID in config:
        sens = await cg.get_variable(config[CONF_LAST_CYCLE_DURATION_SENSOR_ID])
        cg.add(var.set_last_cycle_duration_sensor(sens))

    if CONF_ON_BATTERY in config:
        await automation.build_automation(
            var.get_battery_trigger(),
            [(cg.std_string, "mac"), (cg.uint8, "level")],
            config[CONF_ON_BATTERY],
        )
