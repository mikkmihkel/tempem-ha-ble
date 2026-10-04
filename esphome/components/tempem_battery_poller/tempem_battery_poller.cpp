#include "tempem_battery_poller.h"

#ifdef USE_ESP32

#include <cctype>
#include <cstring>

namespace esphome::tempem_battery_poller {

static const char *const TAG = "tempem_battery";

// Timeout constants (in milliseconds)
// Backstop only: Bluedroid reports a failed connect itself (OPEN_EVT) after
// CONFIG_BT_BLE_ESTAB_LINK_CONN_TOUT (20 s in ESPHome builds). Giving up
// earlier would leave that connect pending in the controller, and the next
// target's connect would queue behind it and time out as well.
static constexpr uint32_t CONNECT_TIMEOUT_MS = 25000;
static constexpr uint32_t DISCOVERY_TIMEOUT_MS =
    5000;                                         // 5s for service discovery
static constexpr uint32_t READ_TIMEOUT_MS = 5000; // 5s for characteristic read
static constexpr uint32_t CLOSE_EVENT_TIMEOUT_MS = 5000; // 5s for close event
static constexpr uint32_t SCAN_WAIT_TIMEOUT_MS =
    10000; // 10s waiting for scan to stop
static constexpr uint32_t ADV_WAIT_TIMEOUT_MS =
    20000; // 20s waiting for advertisement
static constexpr uint8_t MAX_CONSECUTIVE_FAILURES =
    5; // Reset GATTC after this many failures
static constexpr uint32_t DISCOVERY_CATCHUP_DELAY_MS =
    60000; // poll newly discovered targets ~1 min after first sighting
static constexpr uint16_t DUSUN_COMPANY_ID = 0x0059;
static constexpr uint32_t REGISTER_RETRY_MS =
    60000; // retry a failed GATTC app registration after this long
static constexpr uint32_t STALE_TARGET_MS =
    24UL * 3600UL * 1000UL; // auto target not heard for 24 h may be replaced
static constexpr uint32_t SCAN_IDLE_WATCHDOG_MS =
    30000; // restart a scanner left idle this long outside a poll cycle

// Deadline `delay_ms` from now; never 0, which means "no deadline".
static uint32_t deadline_in(uint32_t delay_ms) {
  return (millis() + delay_ms) | 1;
}

bool TempemBatteryPoller::is_tempem_frame(const std::vector<uint8_t> &p) {
  if (p.empty())
    return false;
  const uint8_t type = p[0];
  if ((type & 0x03) == 0 || (type & 0xC0) != 0)
    return false;
  size_t need = 1;
  if (type & 0x01)
    need += 2;
  if (type & 0x02)
    need += 2;
  return p.size() >= need;
}

// Same rule Home Assistant uses before offering a beacon for discovery:
// company id 0x0059 belongs to Nordic and is used by many other products, so
// only connect to devices whose frame is exactly temp/hum, or that call
// themselves Tempem/Dusun.
static bool is_likely_tempem(const std::vector<uint8_t> &p,
                             const std::string &name) {
  if (!TempemBatteryPoller::is_tempem_frame(p))
    return false;
  const uint8_t type = p[0];
  const size_t exact = 1 + ((type & 0x01) ? 2 : 0) + ((type & 0x02) ? 2 : 0);
  if ((type & ~0x03) == 0 && p.size() == exact)
    return true;
  std::string lower = name.substr(0, 6);
  for (auto &c : lower)
    c = (char)tolower((unsigned char)c);
  return lower.rfind("tempem", 0) == 0 || lower.rfind("dusun", 0) == 0;
}

void TempemBatteryPoller::add_target(uint64_t mac_u64,
                                     sensor::Sensor *battery_sensor) {
  for (const auto &existing : this->targets_) {
    if (existing.mac_u64 == mac_u64)
      return;
  }
  Target t;
  t.mac_u64 = mac_u64;
  t.battery = battery_sensor;
  t.due = true;
  this->targets_.push_back(t);
}

void TempemBatteryPoller::setup() {
  this->state_ = PollState::IDLE;
  // Auto-discovered targets are appended from parse_device() while a cycle
  // may be iterating; reserve up front so the vector never reallocates.
  this->targets_.reserve(this->max_targets_);
  this->next_cycle_ms_ = millis() + this->first_poll_delay_ms_;
}

void TempemBatteryPoller::trigger_now() {
  ESP_LOGI(TAG, "Manual trigger: poll batteries now");
  // loop() starts the full cycle as soon as the poller is idle and the GATTC
  // interface is registered (wraparound-safe signed comparison).
  this->next_cycle_ms_ = millis();
}

void TempemBatteryPoller::dump_config() {
  ESP_LOGCONFIG(TAG, "Tempem Battery Poller:");
  ESP_LOGCONFIG(TAG, "  Static/known targets: %u",
                (unsigned)this->targets_.size());
  ESP_LOGCONFIG(TAG, "  Auto-discover: %s (max %u targets)",
                YESNO(this->auto_discover_), (unsigned)this->max_targets_);
  ESP_LOGCONFIG(TAG, "  Interval: %u s",
                (unsigned)(this->poll_interval_ms_ / 1000));
  ESP_LOGCONFIG(TAG, "  First poll delay: %u s",
                (unsigned)(this->first_poll_delay_ms_ / 1000));
  ESP_LOGCONFIG(TAG, "  Retry interval: %u s (max %u retries)",
                (unsigned)(this->retry_interval_ms_ / 1000),
                (unsigned)this->max_retries_);
}

bool TempemBatteryPoller::has_due_targets_() const {
  for (const auto &t : this->targets_) {
    if (t.due)
      return true;
  }
  return false;
}

void TempemBatteryPoller::schedule_partial_cycle_(uint32_t delay_ms) {
  uint32_t at = millis() + delay_ms;
  if (at == 0)
    at = 1; // 0 means "not scheduled"
  // Keep the earliest pending schedule (wraparound-safe).
  if (this->partial_cycle_at_ms_ == 0 ||
      (int32_t)(at - this->partial_cycle_at_ms_) < 0) {
    this->partial_cycle_at_ms_ = at;
  }
}

void TempemBatteryPoller::loop() {
  this->ensure_ble_ready_();

  const uint32_t now = millis();

  // Check for delayed scan resume (wraparound-safe comparison)
  if (this->resume_scan_at_ != 0 &&
      (int32_t)(now - this->resume_scan_at_) >= 0) {
    this->resume_scan_at_ = 0;
    this->resume_scan_();
  }

  // Safety net: outside a poll cycle the scanner must be running (the
  // forwarder depends on it). Restart it if it has been left idle.
  auto *scan_tracker = esp32_ble_tracker::global_esp32_ble_tracker;
  if (!this->cycle_active_ && this->resume_scan_at_ == 0 &&
      scan_tracker != nullptr && esp32_ble::global_ble != nullptr &&
      esp32_ble::global_ble->is_active() &&
      scan_tracker->get_scanner_state() ==
          esp32_ble_tracker::ScannerState::IDLE) {
    if (this->scan_idle_since_ms_ == 0) {
      this->scan_idle_since_ms_ = now | 1;
    } else if ((uint32_t)(now - this->scan_idle_since_ms_) >
               SCAN_IDLE_WATCHDOG_MS) {
      ESP_LOGW(TAG, "BLE scan idle outside a poll cycle; restarting it");
      this->scan_idle_since_ms_ = 0;
      this->resume_scan_();
    }
  } else {
    this->scan_idle_since_ms_ = 0;
  }

  // Nothing to poll yet: keep the first full cycle about a minute ahead so
  // it neither goes stale (signed millis() comparison) nor starts the moment
  // the very first beacon is heard.
  if (this->targets_.empty() &&
      (int32_t)(now - this->next_cycle_ms_) >= 0) {
    this->next_cycle_ms_ = now + DISCOVERY_CATCHUP_DELAY_MS;
  }

  // Start new poll cycle when due (wraparound-safe comparison).
  // A full cycle polls every target; a partial (catch-up/retry) cycle polls
  // only targets that are still `due` (newly discovered or failed).
  if (this->state_ == PollState::IDLE && !this->cycle_active_ &&
      !this->targets_.empty()) {
    const bool full_due = (int32_t)(now - this->next_cycle_ms_) >= 0;
    const bool partial_due = !full_due && this->partial_cycle_at_ms_ != 0 &&
                             (int32_t)(now - this->partial_cycle_at_ms_) >= 0;
    if (full_due || partial_due) {
      // Don't start a cycle until the GATTC interface is registered.
      if (this->gattc_if_ == 0) {
        ESP_LOGW(
            TAG,
            "Waiting for GATTC registration before starting battery cycle...");
        if (full_due) {
          this->next_cycle_ms_ = now + 5000;
        } else {
          this->partial_cycle_at_ms_ = 0;
          this->schedule_partial_cycle_(5000);
        }
        return;
      }
      if (full_due) {
        this->start_cycle_(true);
      } else if (this->has_due_targets_()) {
        this->start_cycle_(false);
      } else {
        this->partial_cycle_at_ms_ = 0;
      }
    }
  }

  if (this->state_ == PollState::WAIT_SCAN_IDLE && this->current_index_ >= 0 &&
      this->current_index_ < (int)this->targets_.size()) {
    auto *tracker = esp32_ble_tracker::global_esp32_ble_tracker;
    if (tracker == nullptr) {
      this->fail_target_("no_tracker");
    } else if (tracker->get_scanner_state() ==
                   esp32_ble_tracker::ScannerState::IDLE &&
               this->gattc_if_ != 0) {
      // (gattc_if_ is 0 while the GATTC app re-registers after a reset;
      // the WAIT_SCAN_IDLE deadline covers a registration that never ends.)
      auto &t = this->targets_[this->current_index_];
      this->scan_stop_requested_ = false;
      this->begin_connect_(t);
      this->state_ = PollState::CONNECTING;
      this->op_deadline_ms_ = deadline_in(CONNECT_TIMEOUT_MS);
    } else {
      this->request_scan_stop_();
    }
  }

  // Operation timeout check (wraparound-safe)
  if (this->op_deadline_ms_ != 0 &&
      (int32_t)(now - this->op_deadline_ms_) >= 0) {
    this->fail_target_("timeout");
  }

  // Robust recovery: if we're waiting for CLOSE event and it doesn't arrive,
  // force recovery after timeout to prevent getting stuck (wraparound-safe).
  if (this->pending_close_ && this->close_timeout_ms_ != 0 &&
      (int32_t)(now - this->close_timeout_ms_) >= 0) {
    ESP_LOGW(TAG, "Close event timeout - forcing recovery");
    this->pending_close_ = false;
    this->close_timeout_ms_ = 0;
    this->conn_id_ = 0xFFFF;
    this->finish_disconnect_();
  }
}

void TempemBatteryPoller::ensure_ble_ready_() {
  if (!esp32_ble::global_ble || !esp32_ble::global_ble->is_active())
    return;

  // Register a single GATTC app once BLE is active.
  if (!this->gattc_registered_) {
    const uint32_t now = millis();
    if (this->register_retry_at_ms_ != 0 &&
        (int32_t)(now - this->register_retry_at_ms_) < 0)
      return;
    this->register_retry_at_ms_ = 0;
    esp_err_t err = esp_ble_gattc_app_register(this->app_id);
    if (err != ESP_OK) {
      // Never mark_failed(): loop() would stop running, and a cycle that
      // had stopped the BLE scan would never resume it.
      ESP_LOGE(TAG, "gattc app register failed app_id=%u err=%d; retrying",
               (unsigned)this->app_id, err);
      this->register_retry_at_ms_ = deadline_in(REGISTER_RETRY_MS);
      return;
    }
    // We will receive ESP_GATTC_REG_EVT which sets gattc_if_.
    this->gattc_registered_ = true;
  }
}

void TempemBatteryPoller::start_cycle_(bool full) {
  if (full) {
    for (auto &t : this->targets_) {
      t.due = true;
      t.retries = 0;
    }
  }
  unsigned due = 0;
  for (const auto &t : this->targets_) {
    if (t.due)
      due++;
  }
  ESP_LOGI(TAG, "Starting %s battery poll cycle (%u of %u targets)",
           full ? "full" : "catch-up/retry", due,
           (unsigned)this->targets_.size());
  this->cycle_is_full_ = full;
  this->partial_cycle_at_ms_ = 0;
  this->cycle_active_ = true;
  this->cycle_start_ms_ = millis();
  this->scan_suspended_for_cycle_ = true;
  this->scan_stop_requested_ = false;
  // A resume still pending from the previous cycle would restart the scan
  // in the middle of this one.
  this->resume_scan_at_ = 0;
  this->request_scan_stop_();
  this->start_target_(0);
}

void TempemBatteryPoller::finish_cycle_() {
  this->state_ = PollState::IDLE;
  this->current_index_ = -1;
  this->op_deadline_ms_ = 0;
  this->cycle_active_ = false;
  this->scan_suspended_for_cycle_ = false;
  if (this->cycle_is_full_) {
    this->next_cycle_ms_ = millis() + this->poll_interval_ms_;
    this->full_cycle_done_ = true;
  }

  // Failed targets stay `due` until they run out of retries (see
  // fail_target_); retry them after retry_interval.
  unsigned pending = 0;
  for (const auto &t : this->targets_) {
    if (t.due)
      pending++;
  }
  const unsigned next_full_s =
      (unsigned)((int32_t)(this->next_cycle_ms_ - millis()) > 0
                     ? (this->next_cycle_ms_ - millis()) / 1000
                     : 0);
  if (pending > 0) {
    this->schedule_partial_cycle_(this->retry_interval_ms_);
    ESP_LOGI(TAG,
             "Battery poll cycle finished; %u target(s) failed, retry in %u s; "
             "next full cycle in %u s",
             pending, (unsigned)(this->retry_interval_ms_ / 1000), next_full_s);
  } else {
    ESP_LOGI(TAG, "Battery poll cycle finished; next full cycle in %u s",
             next_full_s);
  }

  if (this->last_cycle_duration_sensor_ != nullptr) {
    float dur_s = (millis() - this->cycle_start_ms_) / 1000.0f;
    this->last_cycle_duration_sensor_->publish_state(dur_s);
  }

  // Resume scan after cycle end.
  if (esp32_ble_tracker::global_esp32_ble_tracker != nullptr) {
    if (esp32_ble_tracker::global_esp32_ble_tracker->get_scanner_state() ==
        esp32_ble_tracker::ScannerState::IDLE) {
      // Schedule resume in 500ms to allow controller to settle
      this->resume_scan_at_ = deadline_in(500);
    } else {
      // If somehow already scanning, just ensure continuous is true
      esp32_ble_tracker::global_esp32_ble_tracker->set_scan_continuous(true);
    }
  }
}

void TempemBatteryPoller::start_target_(size_t idx) {
  // Skip targets that are not due in this cycle.
  while (idx < this->targets_.size() && !this->targets_[idx].due) {
    idx++;
  }
  if (idx >= this->targets_.size()) {
    this->finish_cycle_();
    return;
  }

  this->current_index_ = (int)idx;
  auto &t = this->targets_[idx];
  this->svc_found_ = false;
  this->svc_start_ = 0;
  this->svc_end_ = 0;
  this->batt_char_handle_ = 0;
  this->conn_id_ = 0xFFFF;
  this->scan_stop_requested_ = false;

  // Prefer connecting only after we've seen an advertisement and learned
  // address type.
  if (!t.addr_type_known) {
    if (this->scan_suspended_for_cycle_) {
      // During a cycle we keep scanning stopped; if we still haven't learned
      // the address type, assume RANDOM (most TempemSens devices advertise as
      // random).
      t.addr_type = BLE_ADDR_TYPE_RANDOM;
      t.addr_type_known = true;
      ESP_LOGW(TAG, "Addr type unknown for %s; assuming RANDOM for this cycle",
               this->mac_to_str_(t.mac_u64).c_str());
    } else {
      this->state_ = PollState::WAIT_ADV;
      this->op_deadline_ms_ = deadline_in(ADV_WAIT_TIMEOUT_MS);
      ESP_LOGI(TAG, "Waiting for advertisement to learn addr type for %s",
               this->mac_to_str_(t.mac_u64).c_str());
      return;
    }
  }

  // Stop scanning first; connect only when scanner reports IDLE to avoid
  // scanner state races.
  if (!this->scan_suspended_for_cycle_) {
    this->request_scan_stop_();
  }
  this->state_ = PollState::WAIT_SCAN_IDLE;
  this->op_deadline_ms_ = deadline_in(SCAN_WAIT_TIMEOUT_MS);
}

void TempemBatteryPoller::request_scan_stop_() {
  auto *tracker = esp32_ble_tracker::global_esp32_ble_tracker;
  if (tracker == nullptr)
    return;
  auto st = tracker->get_scanner_state();
  if (st == esp32_ble_tracker::ScannerState::RUNNING ||
      st == esp32_ble_tracker::ScannerState::FAILED) {
    // Called again from loop() while waiting for IDLE: a scan that was still
    // STARTING on the first call is stopped once it is RUNNING.
    tracker->stop_scan();
    this->scan_stop_requested_ = true;
  } else if (st != esp32_ble_tracker::ScannerState::IDLE) {
    // STARTING / STOPPING: just make sure it is not restarted.
    tracker->set_scan_continuous(false);
  }
}

void TempemBatteryPoller::resume_scan_() {
  auto *tracker = esp32_ble_tracker::global_esp32_ble_tracker;
  if (tracker == nullptr)
    return;
  // set_scan_continuous(true) alone does not restart an IDLE scanner: the
  // tracker's loop() only re-evaluates after one of its own state changes,
  // and nothing changes while it sits IDLE. Start it explicitly (same as the
  // esp32_ble_tracker.start_scan action).
  tracker->set_scan_continuous(true);
  if (tracker->get_scanner_state() == esp32_ble_tracker::ScannerState::IDLE) {
    ESP_LOGI(TAG, "Resuming BLE scan");
    tracker->start_scan();
  }
}

void TempemBatteryPoller::begin_connect_(const Target &t) {
  // Fill remote_bda_ from mac_u64 (AA:BB:CC:DD:EE:FF)
  this->remote_bda_[0] = (t.mac_u64 >> 40) & 0xFF;
  this->remote_bda_[1] = (t.mac_u64 >> 32) & 0xFF;
  this->remote_bda_[2] = (t.mac_u64 >> 24) & 0xFF;
  this->remote_bda_[3] = (t.mac_u64 >> 16) & 0xFF;
  this->remote_bda_[4] = (t.mac_u64 >> 8) & 0xFF;
  this->remote_bda_[5] = (t.mac_u64 >> 0) & 0xFF;
  this->remote_addr_type_ = t.addr_type;

  ESP_LOGI(TAG, "Connecting to %s (addr_type=%u)",
           this->mac_to_str_(t.mac_u64).c_str(),
           (unsigned)this->remote_addr_type_);

  // Connect
  esp_err_t err = esp_ble_gattc_open(this->gattc_if_, this->remote_bda_,
                                     this->remote_addr_type_, true);
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "esp_ble_gattc_open failed err=%d", err);
    this->fail_target_("gattc_open_failed");
  }
}

void TempemBatteryPoller::begin_discovery_() {
  // Discover Battery Service (0x180F)
  esp_bt_uuid_t svc_uuid = make_uuid16_(0x180F);
  esp_err_t err =
      esp_ble_gattc_search_service(this->gattc_if_, this->conn_id_, &svc_uuid);
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "esp_ble_gattc_search_service failed err=%d", err);
    this->fail_target_("search_service_failed");
  } else {
    this->state_ = PollState::DISCOVERING;
    this->op_deadline_ms_ = deadline_in(DISCOVERY_TIMEOUT_MS);
  }
}

void TempemBatteryPoller::begin_read_() {
  // Find Battery Level characteristic 0x2A19 within the discovered service
  // range.
  esp_bt_uuid_t chr_uuid = make_uuid16_(0x2A19);
  esp_gattc_char_elem_t char_elem_result;
  uint16_t count = 1;
  esp_err_t err = esp_ble_gattc_get_char_by_uuid(
      this->gattc_if_, this->conn_id_, this->svc_start_, this->svc_end_,
      chr_uuid, &char_elem_result, &count);
  if (err != ESP_OK || count == 0) {
    ESP_LOGW(TAG, "Battery characteristic not found err=%d count=%u", err,
             (unsigned)count);
    this->fail_target_("char_not_found");
    return;
  }
  this->batt_char_handle_ = char_elem_result.char_handle;

  err =
      esp_ble_gattc_read_char(this->gattc_if_, this->conn_id_,
                              this->batt_char_handle_, ESP_GATT_AUTH_REQ_NONE);
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "esp_ble_gattc_read_char failed err=%d", err);
    this->fail_target_("read_char_failed");
    return;
  }
  this->state_ = PollState::READING;
  this->op_deadline_ms_ = deadline_in(READ_TIMEOUT_MS);
}

void TempemBatteryPoller::finish_disconnect_() {
  this->op_deadline_ms_ = 0;
  this->pending_close_ = false;
  this->close_timeout_ms_ = 0;
  // During an active cycle we keep scanning stopped the whole time to avoid
  // scan/start races.
  if (!this->scan_suspended_for_cycle_) {
    if (esp32_ble_tracker::global_esp32_ble_tracker != nullptr) {
      if (esp32_ble_tracker::global_esp32_ble_tracker->get_scanner_state() ==
          esp32_ble_tracker::ScannerState::IDLE) {
        this->resume_scan_at_ = deadline_in(500);
      } else {
        esp32_ble_tracker::global_esp32_ble_tracker->set_scan_continuous(true);
      }
    }
  }
  this->next_target_();
}

void TempemBatteryPoller::fail_target_(const char *reason) {
  if (this->current_index_ < 0 ||
      this->current_index_ >= (int)this->targets_.size()) {
    this->op_deadline_ms_ = 0;
    this->pending_close_ = false;
    this->close_timeout_ms_ = 0;
    if (this->cycle_active_) {
      // Never leave a cycle (and the stopped scan) behind.
      this->finish_cycle_();
    } else {
      this->state_ = PollState::IDLE;
    }
    return;
  }
  auto &t = this->targets_[this->current_index_];
  // Retry bookkeeping: the target stays due (and is retried by a later
  // partial cycle) until it has used up max_retries_.
  if (t.retries < 255)
    t.retries++;
  if (t.retries > this->max_retries_)
    t.due = false;
  ESP_LOGW(TAG, "Battery poll failed for %s: %s (attempt %u)",
           this->mac_to_str_(t.mac_u64).c_str(), reason, (unsigned)t.retries);
  this->failure_total_++;
  this->consecutive_target_failures_++;
  if (this->failure_total_sensor_ != nullptr) {
    this->failure_total_sensor_->publish_state((float)this->failure_total_);
  }

  // Clear operation deadline since we're handling the failure
  this->op_deadline_ms_ = 0;

  // Check for too many consecutive failures - reset GATTC if stuck
  if (this->consecutive_target_failures_ >= MAX_CONSECUTIVE_FAILURES) {
    ESP_LOGW(TAG, "Too many consecutive failures (%u) - resetting GATTC",
             (unsigned)this->consecutive_target_failures_);
    // (With gattc_if_ == 0 a registration is already pending or being
    // retried; registering the same app id twice would be rejected.)
    if (this->gattc_if_ != 0) {
      esp_ble_gattc_app_unregister(this->gattc_if_);
      this->gattc_registered_ = false;
      // Let the deregistration (and the close of an open link) finish
      // first: the same app id cannot register while it is pending.
      this->register_retry_at_ms_ = deadline_in(2000);
    }
    this->gattc_if_ = 0;
    this->consecutive_target_failures_ = 0;
    this->conn_id_ = 0xFFFF;
    // Don't try to close - just move on
    this->pending_close_ = false;
    this->close_timeout_ms_ = 0;
    this->finish_disconnect_();
    return;
  }

  // Attempt to close connection if we have one
  if (this->conn_id_ != 0xFFFF) {
    esp_ble_gattc_close(this->gattc_if_, this->conn_id_);
    // Set pending_close with timeout - if CLOSE event doesn't arrive,
    // loop() will force recovery to prevent getting stuck
    this->pending_close_ = true;
    this->close_timeout_ms_ = deadline_in(CLOSE_EVENT_TIMEOUT_MS);
    this->state_ = PollState::DISCONNECTING;
  } else {
    // No active connection - directly recover
    this->pending_close_ = false;
    this->close_timeout_ms_ = 0;
    this->finish_disconnect_();
  }
}

void TempemBatteryPoller::next_target_() {
  int next = this->current_index_ + 1;
  this->start_target_((size_t)next);
}

bool TempemBatteryPoller::parse_device(
    const esp32_ble_tracker::ESPBTDevice &device) {
  // Update addr type mapping for any known MAC
  uint64_t u64 = device.address_uint64();
  const uint32_t now = millis();
  bool known = false;
  for (auto &t : this->targets_) {
    if (t.mac_u64 == u64) {
      t.addr_type = device.get_address_type();
      t.addr_type_known = true;
      t.last_seen_ms = now;
      known = true;
      break;
    }
  }

  // Auto-discovery: any MAC advertising a Dusun temp/hum frame under company
  // id 0x0059 becomes a poll target (bounded by max_targets_), see
  // is_likely_tempem().
  if (!known && this->auto_discover_) {
    bool is_tempem = false;
    for (const auto &md : device.get_manufacturer_datas()) {
      if (md.uuid.type() == ble_device_base::ESPBTUUID::Type::UUID16 &&
          md.uuid.uuid16() == DUSUN_COMPANY_ID &&
          is_likely_tempem(md.data, device.get_name())) {
        is_tempem = true;
        break;
      }
    }
    if (is_tempem) {
      // Never erase from targets_ (a cycle may be iterating it by index):
      // append while there is room, else reuse the slot of an
      // auto-discovered target that has not been heard for a long time.
      Target *slot = nullptr;
      if (this->targets_.size() < this->max_targets_) {
        this->targets_.push_back(Target{});
        slot = &this->targets_.back();
      } else {
        slot = this->find_stale_target_(now);
        if (slot != nullptr) {
          ESP_LOGI(TAG, "Replacing battery target %s (not heard for %u h)",
                   this->mac_to_str_(slot->mac_u64).c_str(),
                   (unsigned)((uint32_t)(now - slot->last_seen_ms) / 3600000UL));
        }
      }
      if (slot != nullptr) {
        *slot = Target{};
        slot->mac_u64 = u64;
        slot->addr_type = device.get_address_type();
        slot->addr_type_known = true;
        slot->auto_discovered = true;
        slot->last_seen_ms = now;
        slot->due = true;
        ESP_LOGI(TAG, "Auto-discovered battery target %s (%u/%u)",
                 this->mac_to_str_(u64).c_str(),
                 (unsigned)this->targets_.size(),
                 (unsigned)this->max_targets_);
        // Before the first full cycle, that cycle picks it up; afterwards
        // poll it in a catch-up cycle instead of waiting a whole interval.
        if (this->full_cycle_done_) {
          this->schedule_partial_cycle_(DISCOVERY_CATCHUP_DELAY_MS);
        }
      } else if (!this->max_targets_warned_) {
        this->max_targets_warned_ = true;
        ESP_LOGW(TAG,
                 "max_targets (%u) reached; not polling battery of %s and "
                 "further new sensors",
                 (unsigned)this->max_targets_, this->mac_to_str_(u64).c_str());
      }
    }
  }

  // If we're waiting for adv to learn addr type for current target, advance to
  // connect
  if (this->state_ == PollState::WAIT_ADV && this->current_index_ >= 0) {
    auto &t = this->targets_[this->current_index_];
    if (t.mac_u64 == u64) {
      this->request_scan_stop_();
      this->state_ = PollState::WAIT_SCAN_IDLE;
      this->op_deadline_ms_ = deadline_in(20000);
      return true;
    }
  }

  return false;
}

TempemBatteryPoller::Target *
TempemBatteryPoller::find_stale_target_(uint32_t now) {
  Target *best = nullptr;
  uint32_t best_age = STALE_TARGET_MS;
  for (size_t i = 0; i < this->targets_.size(); i++) {
    auto &t = this->targets_[i];
    if (!t.auto_discovered)
      continue;
    if (this->cycle_active_ && (int)i == this->current_index_)
      continue;
    const uint32_t age = now - t.last_seen_ms;
    if (age > best_age) {
      best_age = age;
      best = &t;
    }
  }
  return best;
}

bool TempemBatteryPoller::gattc_event_handler(esp_gattc_cb_event_t event,
                                              esp_gatt_if_t gattc_if,
                                              esp_ble_gattc_cb_param_t *param) {
  // Capture our registered gattc interface on REG event
  if (event == ESP_GATTC_REG_EVT) {
    // Registration events are broadcast; only handle our own app id.
    if (param->reg.app_id != this->app_id) {
      return false;
    }
    if (param->reg.status != ESP_GATT_OK) {
      // Retried from ensure_ble_ready_(); see there for why not mark_failed().
      ESP_LOGE(TAG, "GATTC registration failed status=%d; retrying",
               param->reg.status);
      this->gattc_registered_ = false;
      this->register_retry_at_ms_ = deadline_in(REGISTER_RETRY_MS);
      return true;
    }
    this->gattc_if_ = gattc_if;
    ESP_LOGI(TAG, "GATTC registered (gattc_if=%d)", (int)gattc_if);
    return true;
  }

  // Ignore events not for our interface (and everything while it is not
  // registered: then nothing of ours can be open).
  if (this->gattc_if_ == 0 || gattc_if != this->gattc_if_) {
    return false;
  }

  if (event == ESP_GATTC_OPEN_EVT) {
    const bool expected =
        this->cycle_active_ && this->state_ == PollState::CONNECTING &&
        memcmp(param->open.remote_bda, this->remote_bda_,
               sizeof(esp_bd_addr_t)) == 0;
    if (!expected) {
      // A connect that already timed out (e.g. while the main loop was busy
      // with an HTTPS post) completed late. Never leave it open: a connected
      // beacon stops advertising, and its readings would stop being
      // forwarded. An unexpected failed open must not fail the current
      // target either.
      if (param->open.status == ESP_GATT_OK &&
          param->open.conn_id != this->conn_id_) {
        ESP_LOGW(TAG, "Closing unexpected late connection (conn_id=%u)",
                 (unsigned)param->open.conn_id);
        esp_ble_gattc_close(gattc_if, param->open.conn_id);
      }
      return true;
    }
  }

  // Ignore connection-related events if we're not actively polling.
  if (!this->cycle_active_) {
    return false;
  }

  switch (event) {
  case ESP_GATTC_OPEN_EVT: {
    if (param->open.status != ESP_GATT_OK) {
      this->fail_target_("open_status_not_ok");
      return true;
    }
    ESP_LOGI(TAG, "Connected (conn_id=%u)", (unsigned)param->open.conn_id);
    this->conn_id_ = param->open.conn_id;
    this->begin_discovery_();
    return true;
  }
  case ESP_GATTC_SEARCH_RES_EVT: {
    // service discovery result
    if (this->conn_id_ == 0xFFFF ||
        param->search_res.conn_id != this->conn_id_) {
      return false;
    }
    if (param->search_res.srvc_id.uuid.len == ESP_UUID_LEN_16 &&
        param->search_res.srvc_id.uuid.uuid.uuid16 == 0x180F) {
      this->svc_found_ = true;
      this->svc_start_ = param->search_res.start_handle;
      this->svc_end_ = param->search_res.end_handle;
    }
    return true;
  }
  case ESP_GATTC_SEARCH_CMPL_EVT: {
    if (this->conn_id_ == 0xFFFF ||
        param->search_cmpl.conn_id != this->conn_id_) {
      return false;
    }
    if (!this->svc_found_) {
      this->fail_target_("battery_service_not_found");
      return true;
    }
    this->begin_read_();
    return true;
  }
  case ESP_GATTC_READ_CHAR_EVT: {
    if (this->conn_id_ == 0xFFFF || param->read.conn_id != this->conn_id_) {
      return false;
    }
    if (param->read.status != ESP_GATT_OK) {
      this->fail_target_("read_status_not_ok");
      return true;
    }
    if (param->read.value_len < 1) {
      this->fail_target_("read_empty");
      return true;
    }
    uint8_t batt = param->read.value[0];

    if (this->current_index_ >= 0 &&
        this->current_index_ < (int)this->targets_.size()) {
      auto &t = this->targets_[this->current_index_];
      if (t.battery != nullptr) {
        t.battery->publish_state(batt);
      }
      t.last_ok_ms = millis();
      t.due = false;
      t.retries = 0;
      const std::string mac_str = this->mac_to_str_(t.mac_u64);
      ESP_LOGI(TAG, "Battery ok %s = %u%%", mac_str.c_str(), (unsigned)batt);
      this->battery_trigger_.trigger(mac_str, batt);
      this->success_total_++;
      this->consecutive_target_failures_ = 0; // Reset on success
      if (this->success_total_sensor_ != nullptr) {
        this->success_total_sensor_->publish_state((float)this->success_total_);
      }
    }

    // Close the connection; we'll resume scanning on close/disconnect.
    esp_ble_gattc_close(this->gattc_if_, this->conn_id_);
    this->state_ = PollState::DISCONNECTING;
    this->pending_close_ = true;
    this->close_timeout_ms_ = deadline_in(CLOSE_EVENT_TIMEOUT_MS);
    this->op_deadline_ms_ =
        deadline_in(CLOSE_EVENT_TIMEOUT_MS + 1000); // Slightly longer for fallback
    return true;
  }
  case ESP_GATTC_DISCONNECT_EVT: {
    if (this->conn_id_ == 0xFFFF ||
        param->disconnect.conn_id != this->conn_id_) {
      return false;
    }
    // Link lost before the battery was read: count it as a failure and wait
    // for CLOSE_EVT (or the close timeout) before the next connect, so the
    // controller has released the link.
    if (this->state_ != PollState::DISCONNECTING) {
      this->fail_target_("disconnected");
    }
    return true;
  }
  case ESP_GATTC_CLOSE_EVT: {
    if (this->conn_id_ == 0xFFFF || param->close.conn_id != this->conn_id_) {
      return false;
    }
    // Connection closed; clear pending_close and move on.
    const bool expected = this->state_ == PollState::DISCONNECTING;
    this->pending_close_ = false;
    this->close_timeout_ms_ = 0;
    this->conn_id_ = 0xFFFF;
    if (expected) {
      this->finish_disconnect_();
    } else {
      this->fail_target_("closed");
    }
    return true;
  }
  default:
    return false;
  }
}

void TempemBatteryPoller::gap_event_handler(esp_gap_ble_cb_event_t,
                                            esp_ble_gap_cb_param_t *) {
  // Not used.
}

void TempemBatteryPoller::ble_before_disabled_event_handler() {
  // The BLE stack is being torn down: nothing in flight will complete and
  // the GATTC app must be registered again once the stack is back.
  ESP_LOGW(TAG, "BLE stack disabled; aborting battery poll state");
  this->gattc_registered_ = false;
  this->gattc_if_ = 0;
  this->conn_id_ = 0xFFFF;
  this->pending_close_ = false;
  this->close_timeout_ms_ = 0;
  this->op_deadline_ms_ = 0;
  this->scan_stop_requested_ = false;
  if (this->cycle_active_) {
    this->cycle_active_ = false;
    this->scan_suspended_for_cycle_ = false;
    this->current_index_ = -1;
    // Re-enable continuous scanning once the stack is back.
    this->resume_scan_at_ = deadline_in(2000);
    if (this->has_due_targets_()) {
      this->schedule_partial_cycle_(DISCOVERY_CATCHUP_DELAY_MS);
    }
  }
  this->state_ = PollState::IDLE;
}

void TempemBatteryPoller::connect() {
  // Not used; we call esp_ble_gattc_open directly.
}

void TempemBatteryPoller::disconnect() {
  if (this->conn_id_ != 0xFFFF) {
    esp_ble_gattc_close(this->gattc_if_, this->conn_id_);
  }
}

esp_bt_uuid_t TempemBatteryPoller::make_uuid16_(uint16_t uuid16) {
  esp_bt_uuid_t uuid{};
  uuid.len = ESP_UUID_LEN_16;
  uuid.uuid.uuid16 = uuid16;
  return uuid;
}

std::string TempemBatteryPoller::mac_to_str_(uint64_t mac_u64) const {
  uint8_t bda[6];
  bda[0] = (mac_u64 >> 40) & 0xFF;
  bda[1] = (mac_u64 >> 32) & 0xFF;
  bda[2] = (mac_u64 >> 24) & 0xFF;
  bda[3] = (mac_u64 >> 16) & 0xFF;
  bda[4] = (mac_u64 >> 8) & 0xFF;
  bda[5] = (mac_u64 >> 0) & 0xFF;
  char out[18];
  snprintf(out, sizeof(out), "%02X:%02X:%02X:%02X:%02X:%02X", bda[0], bda[1],
           bda[2], bda[3], bda[4], bda[5]);
  return std::string(out);
}

} // namespace esphome::tempem_battery_poller

#endif // USE_ESP32
