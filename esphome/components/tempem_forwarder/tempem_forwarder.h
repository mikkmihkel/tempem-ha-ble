#pragma once

#include "esphome/core/component.h"
#include "esphome/core/defines.h"
#include "esphome/core/helpers.h"

#include "esphome/components/esp32_ble_tracker/esp32_ble_tracker.h"
#include "esphome/components/sensor/sensor.h"
#include "esphome/components/text_sensor/text_sensor.h"

#include "forwarder_core.h"

#ifdef USE_ESP32

#include <string>

namespace esphome::tempem_forwarder {

/// Collects Tempem/Dusun beacon advertisements (company id 0x0059) and
/// battery readings, and renders them as the JSON body for the Home
/// Assistant `tempem_ble` webhook. The HTTP POST itself is done from YAML
/// with `http_request.post` (see tempem-remote-gateway.yaml):
///
///   ready_to_post() -> build_payload() -> on_post_result(status, body)
class TempemForwarder : public Component, public esp32_ble_tracker::ESPBTDeviceListener {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::DATA; }

  // esp32_ble_tracker::ESPBTDeviceListener
  bool parse_device(const esp32_ble_tracker::ESPBTDevice &device) override;

  /// True when a post may be started now (network up, no post in flight).
  bool ready_to_post();
  /// Render the payload; its adverts/batteries are in flight until
  /// on_post_result()/on_post_error() is called.
  std::string build_payload();
  /// Result of the HTTP request: status code and (captured) response body.
  /// Only 2xx with `"ok": true` in the body counts as delivered.
  void on_post_result(int status, const std::string &body);
  /// Convenience overload: `true` = delivered, `false` = failed.
  void on_post_result(bool ok);
  /// The request failed without an HTTP response (DNS/TCP/TLS/timeout).
  void on_post_error();
  /// Queue a battery reading (mac "AA:BB:CC:DD:EE:FF", level in %).
  void add_battery(const std::string &mac, uint8_t level);

  void set_max_devices(uint16_t n) { this->max_devices_ = n; }
  void set_max_advert_age_ms(uint32_t ms) { this->max_advert_age_ms_ = ms; }
  void set_max_pending_batteries(uint16_t n) { this->max_pending_batteries_ = n; }
  void set_inflight_timeout_ms(uint32_t ms) { this->inflight_timeout_ms_ = ms; }

  void set_posts_ok_sensor(sensor::Sensor *s) { this->posts_ok_sensor_ = s; }
  void set_posts_failed_sensor(sensor::Sensor *s) { this->posts_failed_sensor_ = s; }
  void set_last_http_status_sensor(sensor::Sensor *s) { this->last_http_status_sensor_ = s; }
  void set_devices_sensor(sensor::Sensor *s) { this->devices_sensor_ = s; }
  void set_pending_batteries_sensor(sensor::Sensor *s) { this->pending_batteries_sensor_ = s; }
  void set_last_result_text_sensor(text_sensor::TextSensor *s) { this->last_result_text_sensor_ = s; }

 protected:
  void finish_post_(tempem_forwarder_core::PostOutcome outcome, int status, const std::string &body);
  void publish_table_sensors_();
  void publish_result_(const char *text);

  tempem_forwarder_core::ForwarderState state_;
  uint16_t max_devices_{40};
  uint32_t max_advert_age_ms_{600000};
  uint16_t max_pending_batteries_{64};
  uint32_t inflight_timeout_ms_{120000};

  uint32_t post_started_ms_{0};
  uint32_t last_maintenance_ms_{0};
  uint32_t posts_ok_{0};
  uint32_t posts_failed_{0};
  uint8_t consecutive_failures_{0};
  int last_published_devices_{-1};
  int last_published_batteries_{-1};

  sensor::Sensor *posts_ok_sensor_{nullptr};
  sensor::Sensor *posts_failed_sensor_{nullptr};
  sensor::Sensor *last_http_status_sensor_{nullptr};
  sensor::Sensor *devices_sensor_{nullptr};
  sensor::Sensor *pending_batteries_sensor_{nullptr};
  text_sensor::TextSensor *last_result_text_sensor_{nullptr};
};

}  // namespace esphome::tempem_forwarder

#endif  // USE_ESP32
