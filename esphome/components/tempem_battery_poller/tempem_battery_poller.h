#pragma once

#include "esphome/components/sensor/sensor.h"
#include "esphome/core/automation.h"
#include "esphome/core/component.h"
#include "esphome/core/helpers.h"
#include "esphome/core/log.h"

#include "esphome/components/esp32_ble/ble.h"
#include "esphome/components/esp32_ble_tracker/esp32_ble_tracker.h"

#ifdef USE_ESP32

#include <esp_gattc_api.h>

#include <string>
#include <vector>

namespace esphome::tempem_battery_poller {

class TempemBatteryPoller : public Component,
                            public esp32_ble_tracker::ESPBTClient {
public:
  void set_poll_interval_ms(uint32_t poll_ms) {
    this->poll_interval_ms_ = poll_ms;
  }
  void set_first_poll_delay_ms(uint32_t ms) {
    this->first_poll_delay_ms_ = ms;
  }
  void set_retry_interval_ms(uint32_t ms) { this->retry_interval_ms_ = ms; }
  void set_max_retries(uint8_t n) { this->max_retries_ = n; }
  void set_auto_discover(bool v) { this->auto_discover_ = v; }
  void set_max_targets(uint8_t n) { this->max_targets_ = n; }
  void add_target(uint64_t mac_u64, sensor::Sensor *battery_sensor);
  void set_success_total_sensor(sensor::Sensor *s) {
    this->success_total_sensor_ = s;
  }
  void set_failure_total_sensor(sensor::Sensor *s) {
    this->failure_total_sensor_ = s;
  }
  void set_last_cycle_duration_sensor(sensor::Sensor *s) {
    this->last_cycle_duration_sensor_ = s;
  }
  /// Fired after each successful battery read: (mac "AA:BB:..", level %).
  Trigger<std::string, uint8_t> *get_battery_trigger() {
    return &this->battery_trigger_;
  }
  /// Start a full poll cycle as soon as possible.
  void trigger_now();
  size_t target_count() const { return this->targets_.size(); }

  void setup() override;
  void loop() override;
  void dump_config() override;
  // Loop after esp32_ble, so GATTC events that queued up while the main loop
  // was blocked (e.g. by an HTTPS post) are handled before our timeouts run.
  float get_setup_priority() const override {
    return setup_priority::AFTER_BLUETOOTH;
  }

  // esp32_ble_tracker::ESPBTClient
  bool parse_device(const esp32_ble_tracker::ESPBTDevice &device) override;
  bool gattc_event_handler(esp_gattc_cb_event_t event, esp_gatt_if_t gattc_if,
                           esp_ble_gattc_cb_param_t *param) override;
  void gap_event_handler(esp_gap_ble_cb_event_t event,
                         esp_ble_gap_cb_param_t *param) override;
  void connect() override;
  void disconnect() override;
  void ble_before_disabled_event_handler() override;

  /// True if `p` is a Dusun temp/hum frame (payload after the 0x0059 company
  /// id): type byte with bit0 (temp) and/or bit1 (hum) set, no bits above
  /// 0x3F, and long enough for the announced fields.
  static bool is_tempem_frame(const std::vector<uint8_t> &p);

protected:
  struct Target {
    uint64_t mac_u64{0};
    sensor::Sensor *battery{nullptr};
    bool addr_type_known{false};
    esp_ble_addr_type_t addr_type{BLE_ADDR_TYPE_PUBLIC};
    uint32_t last_ok_ms{0};
    // Scheduling: `due` targets are polled by the next (full or partial)
    // cycle. A failed target stays due and is retried after retry_interval,
    // at most max_retries times until the next full cycle.
    bool due{false};
    uint8_t retries{0};
    // Auto-discovered targets that have not been heard for a long time are
    // replaced by new beacons once max_targets is reached.
    bool auto_discovered{false};
    uint32_t last_seen_ms{0};
  };

  enum class PollState : uint8_t {
    INIT = 0,
    IDLE,
    WAIT_ADV,
    WAIT_SCAN_IDLE,
    CONNECTING,
    DISCOVERING,
    READING,
    DISCONNECTING,
  };

  void start_cycle_(bool full);
  void start_target_(size_t idx);
  void finish_cycle_();
  void fail_target_(const char *reason);
  void next_target_();
  void ensure_ble_ready_();
  void request_scan_stop_();
  void resume_scan_();
  Target *find_stale_target_(uint32_t now);
  void begin_connect_(const Target &t);
  void begin_discovery_();
  void begin_read_();
  void finish_disconnect_();
  void schedule_partial_cycle_(uint32_t delay_ms);
  bool has_due_targets_() const;
  std::string mac_to_str_(uint64_t mac_u64) const;

  static esp_bt_uuid_t make_uuid16_(uint16_t uuid16);

  std::vector<Target> targets_;

  uint32_t poll_interval_ms_{60UL * 60UL * 1000UL};
  uint32_t first_poll_delay_ms_{20000};
  uint32_t retry_interval_ms_{60UL * 60UL * 1000UL};
  uint8_t max_retries_{3};
  bool auto_discover_{false};
  uint8_t max_targets_{32};
  bool full_cycle_done_{false};
  bool cycle_is_full_{false};
  bool max_targets_warned_{false};

  uint32_t next_cycle_ms_{0};
  // 0 = no partial (catch-up / retry) cycle scheduled.
  uint32_t partial_cycle_at_ms_{0};
  uint32_t op_deadline_ms_{0};

  PollState state_{PollState::INIT};

  // current target
  int current_index_{-1};
  esp_gatt_if_t gattc_if_{0};
  bool gattc_registered_{false};
  // 0 = register as soon as BLE is active; else retry registration then.
  uint32_t register_retry_at_ms_{0};
  uint16_t conn_id_{0xFFFF};
  esp_bd_addr_t remote_bda_{};
  esp_ble_addr_type_t remote_addr_type_{BLE_ADDR_TYPE_PUBLIC};

  // handles discovered
  bool svc_found_{false};
  uint16_t svc_start_{0};
  uint16_t svc_end_{0};
  uint16_t batt_char_handle_{0};

  bool scan_stop_requested_{false};
  uint32_t resume_scan_at_{0};
  // When the scanner was first seen idle outside a cycle (0 = not idle).
  uint32_t scan_idle_since_ms_{0};

  bool cycle_active_{false};
  bool scan_suspended_for_cycle_{false};

  // Diagnostics
  sensor::Sensor *success_total_sensor_{nullptr};
  sensor::Sensor *failure_total_sensor_{nullptr};
  sensor::Sensor *last_cycle_duration_sensor_{nullptr};
  uint32_t success_total_{0};
  uint32_t failure_total_{0};
  uint32_t cycle_start_ms_{0};

  // Robust recovery: timeout if CLOSE event doesn't arrive
  uint32_t close_timeout_ms_{0};
  bool pending_close_{false};
  uint8_t consecutive_target_failures_{0};

  Trigger<std::string, uint8_t> battery_trigger_;
};

} // namespace esphome::tempem_battery_poller

#endif // USE_ESP32
