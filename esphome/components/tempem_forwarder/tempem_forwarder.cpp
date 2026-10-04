#include "tempem_forwarder.h"

#ifdef USE_ESP32

#include "esphome/components/network/util.h"
#include "esphome/core/application.h"
#include "esphome/core/hal.h"
#include "esphome/core/log.h"
#include "esphome/core/version.h"

#ifdef USE_WIFI
#include "esphome/components/wifi/wifi_component.h"
#endif

#include <esp_heap_caps.h>

namespace esphome::tempem_forwarder {

using tempem_forwarder_core::PostOutcome;

static const char *const TAG = "tempem_forwarder";
static constexpr uint32_t MAINTENANCE_INTERVAL_MS = 10000;

void TempemForwarder::setup() {
  this->state_.configure(this->max_devices_, this->max_advert_age_ms_, this->max_pending_batteries_);
  if (this->posts_ok_sensor_ != nullptr)
    this->posts_ok_sensor_->publish_state(0);
  if (this->posts_failed_sensor_ != nullptr)
    this->posts_failed_sensor_->publish_state(0);
  this->publish_result_("waiting for first post");
}

void TempemForwarder::dump_config() {
  ESP_LOGCONFIG(TAG,
                "Tempem webhook forwarder:\n"
                "  Max devices: %u\n"
                "  Max advert age: %u s\n"
                "  Max pending batteries: %u",
                (unsigned) this->max_devices_, (unsigned) (this->max_advert_age_ms_ / 1000),
                (unsigned) this->max_pending_batteries_);
}

void TempemForwarder::loop() {
  const uint32_t now = millis();
  if ((uint32_t) (now - this->last_maintenance_ms_) < MAINTENANCE_INTERVAL_MS)
    return;
  this->last_maintenance_ms_ = now;
  this->state_.prune(now);
  this->publish_table_sensors_();
}

bool TempemForwarder::parse_device(const esp32_ble_tracker::ESPBTDevice &device) {
  const uint8_t *mfr = nullptr;
  size_t mfr_len = 0;
  for (const auto &md : device.get_manufacturer_datas()) {
    if (md.uuid.type() == ble_device_base::ESPBTUUID::Type::UUID16 &&
        md.uuid.uuid16() == tempem_forwarder_core::DUSUN_COMPANY_ID) {
      mfr = md.data.data();
      mfr_len = md.data.size();
      if (tempem_forwarder_core::is_valid_frame(mfr, mfr_len))
        break;
    }
  }
  const auto name = device.get_name();
  if (mfr == nullptr && name.size() == 0)
    return false;  // neither a frame nor a name we could merge

  const size_t before = this->state_.device_count();
  const bool valid = this->state_.on_advert(device.address_uint64(), device.get_rssi(), name.c_str(), name.size(),
                                            mfr, mfr_len, millis());
  if (valid && this->state_.device_count() > before) {
    char mac_buf[esp32_ble_tracker::ESPBTDevice::MAC_ADDRESS_PRETTY_BUFFER_SIZE];
    ESP_LOGI(TAG, "New Tempem beacon %s rssi=%d name='%s' (%u tracked)", device.address_str_to(mac_buf),
             device.get_rssi(), name.c_str(), (unsigned) this->state_.device_count());
  }
  return valid;
}

bool TempemForwarder::ready_to_post() {
  if (this->state_.inflight()) {
    // build_payload() was called but no result arrived; give up on it so a
    // lost callback can never block forwarding forever.
    if ((uint32_t) (millis() - this->post_started_ms_) < this->inflight_timeout_ms_)
      return false;
    ESP_LOGW(TAG, "Previous post never reported a result; treating it as failed");
    this->finish_post_(PostOutcome::TRANSPORT_ERROR, -1, "");
  }
  if (!network::is_connected()) {
    ESP_LOGD(TAG, "Network not connected; skipping post");
    return false;
  }
  return true;
}

std::string TempemForwarder::build_payload() {
  tempem_forwarder_core::GatewayInfo gw;
  char mac_buf[MAC_ADDRESS_PRETTY_BUFFER_SIZE];
  gw.mac = get_mac_address_pretty_into_buffer(mac_buf);
  const auto &name = App.get_name();
  gw.name.assign(name.c_str(), name.size());
  gw.version = ESPHOME_VERSION;
  gw.uptime_s = millis_64() / 1000ULL;
#ifdef USE_WIFI
  if (wifi::global_wifi_component != nullptr && wifi::global_wifi_component->is_connected()) {
    const int8_t rssi = wifi::global_wifi_component->wifi_rssi();
    if (rssi != wifi::WIFI_RSSI_DISCONNECTED && rssi < 0) {
      gw.has_wifi_rssi = true;
      gw.wifi_rssi = rssi;
    }
  }
#endif
  gw.free_heap = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);

  this->post_started_ms_ = millis();
  std::string body = this->state_.build_payload(gw, this->post_started_ms_);
  ESP_LOGD(TAG, "Posting %u advert(s), %u battery reading(s), %u bytes",
           (unsigned) this->state_.last_advert_count(), (unsigned) this->state_.last_battery_count(),
           (unsigned) body.size());
  return body;
}

void TempemForwarder::on_post_result(int status, const std::string &body) {
  this->finish_post_(tempem_forwarder_core::classify_response(status, body), status, body);
}

void TempemForwarder::on_post_result(bool ok) {
  this->finish_post_(ok ? PostOutcome::OK : PostOutcome::TRANSPORT_ERROR, ok ? 200 : -1, "");
}

void TempemForwarder::on_post_error() { this->finish_post_(PostOutcome::TRANSPORT_ERROR, -1, ""); }

void TempemForwarder::finish_post_(PostOutcome outcome, int status, const std::string &body) {
  if (!this->state_.inflight()) {
    ESP_LOGD(TAG, "Post result (%d) without a post in flight; ignored", status);
    return;
  }
  const unsigned adverts = this->state_.last_advert_count();
  const unsigned batteries = this->state_.last_battery_count();
  const uint32_t duration = millis() - this->post_started_ms_;
  const bool ok = outcome == PostOutcome::OK;
  this->state_.on_post_result(ok);

  if (this->last_http_status_sensor_ != nullptr)
    this->last_http_status_sensor_->publish_state(status);

  if (ok) {
    this->posts_ok_++;
    if (this->consecutive_failures_ > 0) {
      ESP_LOGI(TAG, "Webhook reachable again after %u failed post(s)", (unsigned) this->consecutive_failures_);
    }
    this->consecutive_failures_ = 0;
    ESP_LOGI(TAG, "Posted %u advert(s), %u battery reading(s): HTTP %d ok (%u ms)", adverts, batteries, status,
             (unsigned) duration);
    if (this->posts_ok_sensor_ != nullptr)
      this->posts_ok_sensor_->publish_state(this->posts_ok_);
    this->publish_result_("ok");
  } else {
    this->posts_failed_++;
    if (this->consecutive_failures_ < 255)
      this->consecutive_failures_++;
    if (this->posts_failed_sensor_ != nullptr)
      this->posts_failed_sensor_->publish_state(this->posts_failed_);
    switch (outcome) {
      case PostOutcome::NOT_ACKNOWLEDGED: {
        // HA answers 200 with an empty body for webhook ids it does not know.
        const std::string snippet = body.substr(0, 64);
        ESP_LOGW(TAG,
                 "HTTP %d but no \"ok\": true in response (body: '%s') - webhook id unknown to Home Assistant "
                 "- check tempem_webhook_url",
                 status, snippet.c_str());
        this->publish_result_("not acknowledged: webhook id unknown to Home Assistant?");
        break;
      }
      case PostOutcome::HTTP_ERROR: {
        const char *hint = "";
        if (status >= 300 && status < 400) {
          hint = " - redirect: Cloudflare Access login? add a Bypass policy for /api/webhook/*";
        } else if (status == 401 || status == 403) {
          hint = " - blocked: Cloudflare Access/WAF/Bot Fight Mode? add a skip/bypass rule for /api/webhook/*";
        } else if (status == 404 || status == 405) {
          hint = " - wrong URL? expected https://<domain>/api/webhook/<id>";
        } else if (status >= 500) {
          hint = " - Home Assistant or tunnel error (cloudflared down / HA restarting?)";
        }
        ESP_LOGW(TAG, "Post failed: HTTP %d%s", status, hint);
        char buf[48];
        snprintf(buf, sizeof(buf), "HTTP %d", status);
        this->publish_result_(buf);
        break;
      }
      default:
        ESP_LOGW(TAG, "Post failed: no HTTP response (DNS/TLS/connection/timeout)");
        this->publish_result_("connection error");
        break;
    }
  }
  this->publish_table_sensors_();
}

void TempemForwarder::add_battery(const std::string &mac, uint8_t level) {
  uint64_t mac_u64;
  if (!tempem_forwarder_core::parse_mac(mac, mac_u64)) {
    ESP_LOGW(TAG, "add_battery: invalid MAC '%s'", mac.c_str());
    return;
  }
  this->state_.add_battery(mac_u64, level, millis());
  ESP_LOGI(TAG, "Battery %s = %u%% queued for upload (%u pending)", mac.c_str(), (unsigned) level,
           (unsigned) this->state_.pending_battery_count());
  this->publish_table_sensors_();
}

void TempemForwarder::publish_table_sensors_() {
  const int devices = (int) this->state_.device_count();
  if (this->devices_sensor_ != nullptr && devices != this->last_published_devices_) {
    this->last_published_devices_ = devices;
    this->devices_sensor_->publish_state(devices);
  }
  const int batteries = (int) this->state_.pending_battery_count();
  if (this->pending_batteries_sensor_ != nullptr && batteries != this->last_published_batteries_) {
    this->last_published_batteries_ = batteries;
    this->pending_batteries_sensor_->publish_state(batteries);
  }
}

void TempemForwarder::publish_result_(const char *text) {
  if (this->last_result_text_sensor_ != nullptr)
    this->last_result_text_sensor_->publish_state(text);
}

}  // namespace esphome::tempem_forwarder

#endif  // USE_ESP32
