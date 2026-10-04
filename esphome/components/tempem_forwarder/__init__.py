"""Tempem webhook forwarder.

Collects Tempem/Dusun beacon advertisements (manufacturer data under company
id 0x0059 carrying a valid temp/hum frame) plus battery readings, and renders
them as the JSON body the Home Assistant ``tempem_ble`` integration's webhook
expects. The HTTPS POST is done from YAML with ``http_request.post``:

    body: !lambda return id(fwd).build_payload();
    on_response: id(fwd).on_post_result(response->status_code, body);
    on_error:    id(fwd).on_post_error();
"""

import esphome.codegen as cg
from esphome.components import esp32_ble_tracker, sensor, text_sensor
import esphome.config_validation as cv
from esphome.const import (
    CONF_ID,
    ENTITY_CATEGORY_DIAGNOSTIC,
    STATE_CLASS_MEASUREMENT,
    STATE_CLASS_TOTAL_INCREASING,
)

CODEOWNERS = ["@mikkmihkel"]
DEPENDENCIES = ["esp32_ble_tracker", "network"]
AUTO_LOAD = ["sensor", "text_sensor"]

tempem_forwarder_ns = cg.esphome_ns.namespace("tempem_forwarder")
TempemForwarder = tempem_forwarder_ns.class_(
    "TempemForwarder", cg.Component, esp32_ble_tracker.ESPBTDeviceListener
)

CONF_MAX_DEVICES = "max_devices"
CONF_MAX_ADVERT_AGE = "max_advert_age"
CONF_MAX_PENDING_BATTERIES = "max_pending_batteries"
CONF_INFLIGHT_TIMEOUT = "inflight_timeout"
CONF_POSTS_OK = "posts_ok"
CONF_POSTS_FAILED = "posts_failed"
CONF_LAST_HTTP_STATUS = "last_http_status"
CONF_DEVICES = "devices"
CONF_PENDING_BATTERIES = "pending_batteries"
CONF_LAST_RESULT = "last_result"


def _counter(icon):
    return sensor.sensor_schema(
        icon=icon,
        accuracy_decimals=0,
        state_class=STATE_CLASS_TOTAL_INCREASING,
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
    )


def _gauge(icon):
    return sensor.sensor_schema(
        icon=icon,
        accuracy_decimals=0,
        state_class=STATE_CLASS_MEASUREMENT,
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
    )


CONFIG_SCHEMA = (
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(TempemForwarder),
            cv.Optional(CONF_MAX_DEVICES, default=40): cv.int_range(min=1, max=100),
            cv.Optional(
                CONF_MAX_ADVERT_AGE, default="600s"
            ): cv.positive_time_period_milliseconds,
            cv.Optional(CONF_MAX_PENDING_BATTERIES, default=64): cv.int_range(
                min=1, max=128
            ),
            cv.Optional(
                CONF_INFLIGHT_TIMEOUT, default="120s"
            ): cv.positive_time_period_milliseconds,
            cv.Optional(CONF_POSTS_OK): _counter("mdi:cloud-check"),
            cv.Optional(CONF_POSTS_FAILED): _counter("mdi:cloud-alert"),
            cv.Optional(CONF_LAST_HTTP_STATUS): _gauge("mdi:web"),
            cv.Optional(CONF_DEVICES): _gauge("mdi:bluetooth"),
            cv.Optional(CONF_PENDING_BATTERIES): _gauge("mdi:battery-sync"),
            cv.Optional(CONF_LAST_RESULT): text_sensor.text_sensor_schema(
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
                icon="mdi:cloud-upload",
            ),
        }
    )
    .extend(cv.COMPONENT_SCHEMA)
    .extend(esp32_ble_tracker.ESP_BLE_DEVICE_SCHEMA)
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await esp32_ble_tracker.register_ble_device(var, config)

    cg.add(var.set_max_devices(config[CONF_MAX_DEVICES]))
    cg.add(var.set_max_advert_age_ms(config[CONF_MAX_ADVERT_AGE].total_milliseconds))
    cg.add(var.set_max_pending_batteries(config[CONF_MAX_PENDING_BATTERIES]))
    cg.add(
        var.set_inflight_timeout_ms(config[CONF_INFLIGHT_TIMEOUT].total_milliseconds)
    )

    for key, setter in (
        (CONF_POSTS_OK, var.set_posts_ok_sensor),
        (CONF_POSTS_FAILED, var.set_posts_failed_sensor),
        (CONF_LAST_HTTP_STATUS, var.set_last_http_status_sensor),
        (CONF_DEVICES, var.set_devices_sensor),
        (CONF_PENDING_BATTERIES, var.set_pending_batteries_sensor),
    ):
        if key in config:
            sens = await sensor.new_sensor(config[key])
            cg.add(setter(sens))
    if CONF_LAST_RESULT in config:
        sens = await text_sensor.new_text_sensor(config[CONF_LAST_RESULT])
        cg.add(var.set_last_result_text_sensor(sens))
