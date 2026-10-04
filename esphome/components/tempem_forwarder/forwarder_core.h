#pragma once
// Platform-independent core of the Tempem webhook forwarder.
//
// Keeps the latest advertisement per beacon and the pending battery readings,
// and renders the webhook JSON payload (contract version 1). It has no
// ESPHome / ESP-IDF dependencies so it can be unit-tested on the host with a
// plain C++17 compiler (see esphome/tests/host/).
//
// All timestamps are uint32_t milliseconds from a monotonic clock (millis());
// every comparison is wraparound-safe and stale entries are pruned long
// before a 2^32 ms wrap could make them look fresh again.

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

namespace tempem_forwarder_core {

static constexpr uint16_t DUSUN_COMPANY_ID = 0x0059;
static constexpr size_t MAX_MFR_LEN = 27;   // 31-byte legacy PDU - AD hdr - company id
static constexpr size_t MAX_NAME_LEN = 29;  // ESPHome's MAX_ADV_NAME_LEN
static constexpr size_t MAX_GATEWAY_STR = 64;
// Entries are dropped from the tables after this long without an update
// (independent of max_advert_age, which only limits what gets *sent*).
static constexpr uint32_t ADVERT_FORGET_MS = 60UL * 60UL * 1000UL;           // 1 h
static constexpr uint32_t BATTERY_FORGET_MS = 30UL * 24UL * 3600UL * 1000UL;  // 30 d

/// Dusun temp/hum frame (payload after the 2 company-id bytes):
/// [type:1][temp:2 BE if bit0][hum:2 BE if bit1]. Valid when bit0 and/or bit1
/// is set, no bits above 0x3F are set, and the announced fields are present.
inline bool is_valid_frame(const uint8_t *p, size_t len) {
  if (p == nullptr || len < 1)
    return false;
  const uint8_t type = p[0];
  if ((type & 0x03) == 0 || (type & 0xC0) != 0)
    return false;
  size_t need = 1;
  if (type & 0x01)
    need += 2;
  if (type & 0x02)
    need += 2;
  return len >= need;
}

inline void append_mac(std::string &out, uint64_t mac) {
  char buf[18];
  snprintf(buf, sizeof(buf), "%02X:%02X:%02X:%02X:%02X:%02X", (unsigned) ((mac >> 40) & 0xFF),
           (unsigned) ((mac >> 32) & 0xFF), (unsigned) ((mac >> 24) & 0xFF), (unsigned) ((mac >> 16) & 0xFF),
           (unsigned) ((mac >> 8) & 0xFF), (unsigned) (mac & 0xFF));
  out.append(buf, 17);
}

/// Parse "AA:BB:CC:DD:EE:FF" (case-insensitive, ':' or '-' separators).
inline bool parse_mac(const std::string &s, uint64_t &out) {
  if (s.size() != 17)
    return false;
  uint64_t v = 0;
  for (size_t i = 0; i < 17; i++) {
    const char c = s[i];
    if (i % 3 == 2) {
      if (c != ':' && c != '-')
        return false;
      continue;
    }
    uint8_t nib;
    if (c >= '0' && c <= '9')
      nib = c - '0';
    else if (c >= 'a' && c <= 'f')
      nib = c - 'a' + 10;
    else if (c >= 'A' && c <= 'F')
      nib = c - 'A' + 10;
    else
      return false;
    v = (v << 4) | nib;
  }
  out = v;
  return true;
}

inline void append_hex(std::string &out, const uint8_t *data, size_t len) {
  static const char HEX_DIGITS[] = "0123456789abcdef";
  for (size_t i = 0; i < len; i++) {
    out.push_back(HEX_DIGITS[data[i] >> 4]);
    out.push_back(HEX_DIGITS[data[i] & 0x0F]);
  }
}

/// Length of a valid UTF-8 sequence starting at s[i], or 0 if invalid.
inline size_t utf8_seq_len(const unsigned char *s, size_t i, size_t len) {
  const unsigned char c = s[i];
  size_t n;
  uint32_t cp;
  if (c < 0x80)
    return 1;
  if ((c & 0xE0) == 0xC0) {
    n = 2;
    cp = c & 0x1F;
  } else if ((c & 0xF0) == 0xE0) {
    n = 3;
    cp = c & 0x0F;
  } else if ((c & 0xF8) == 0xF0) {
    n = 4;
    cp = c & 0x07;
  } else {
    return 0;
  }
  if (i + n > len)
    return 0;
  for (size_t k = 1; k < n; k++) {
    if ((s[i + k] & 0xC0) != 0x80)
      return 0;
    cp = (cp << 6) | (s[i + k] & 0x3F);
  }
  // Reject overlong encodings, surrogates and out-of-range code points.
  if ((n == 2 && cp < 0x80) || (n == 3 && cp < 0x800) || (n == 4 && cp < 0x10000) || cp > 0x10FFFF ||
      (cp >= 0xD800 && cp <= 0xDFFF))
    return 0;
  return n;
}

/// Append `s` as a JSON string literal (with quotes). Escapes '"', '\\' and
/// control characters; invalid UTF-8 bytes become U+FFFD so the output is
/// always valid JSON text.
inline void append_json_string(std::string &out, const char *str, size_t len) {
  const auto *s = reinterpret_cast<const unsigned char *>(str);
  out.push_back('"');
  for (size_t i = 0; i < len;) {
    const unsigned char c = s[i];
    if (c == '"') {
      out.append("\\\"");
      i++;
    } else if (c == '\\') {
      out.append("\\\\");
      i++;
    } else if (c < 0x20 || c == 0x7F) {
      char buf[8];
      snprintf(buf, sizeof(buf), "\\u%04x", (unsigned) c);
      out.append(buf);
      i++;
    } else if (c < 0x80) {
      out.push_back((char) c);
      i++;
    } else {
      const size_t n = utf8_seq_len(s, i, len);
      if (n == 0) {
        out.append("\\ufffd");
        i++;
      } else {
        out.append(str + i, n);
        i += n;
      }
    }
  }
  out.push_back('"');
}

inline void append_json_string(std::string &out, const std::string &s) { append_json_string(out, s.data(), s.size()); }

struct AdvertRecord {
  uint64_t mac{0};
  uint32_t seen_ms{0};  // when the latest valid frame was received
  uint32_t gen{0};      // update generation (for "new since last success")
  int8_t rssi{0};
  uint8_t mfr_len{0};
  uint8_t name_len{0};
  bool has_name{false};
  uint8_t mfr[MAX_MFR_LEN]{};
  char name[MAX_NAME_LEN + 1]{};
};

struct BatteryRecord {
  uint64_t mac{0};
  uint32_t received_ms{0};
  uint8_t level{0};
  bool inflight{false};  // included in the post currently awaiting a result
};

struct GatewayInfo {
  std::string mac;
  std::string name;
  std::string version;
  uint64_t uptime_s{0};
  bool has_wifi_rssi{false};
  int wifi_rssi{0};
  uint32_t free_heap{0};
};

/// Result of interpreting an HTTP response from the HA webhook.
enum class PostOutcome : uint8_t {
  OK,               // 2xx and body contains "ok": true
  NOT_ACKNOWLEDGED, // 2xx but no ok:true (unknown webhook id / handler error)
  HTTP_ERROR,       // non-2xx status
  TRANSPORT_ERROR,  // no response (DNS/TCP/TLS failure, timeout, no network)
};

/// True if the JSON body contains `"ok"` followed by `:` and `true`
/// (arbitrary JSON whitespace allowed around the colon).
inline bool body_has_ok_true(const std::string &body) {
  size_t pos = 0;
  while ((pos = body.find("\"ok\"", pos)) != std::string::npos) {
    size_t i = pos + 4;
    auto skip_ws = [&]() {
      while (i < body.size() && (body[i] == ' ' || body[i] == '\t' || body[i] == '\r' || body[i] == '\n'))
        i++;
    };
    skip_ws();
    if (i < body.size() && body[i] == ':') {
      i++;
      skip_ws();
      if (body.compare(i, 4, "true") == 0)
        return true;
    }
    pos += 4;
  }
  return false;
}

inline PostOutcome classify_response(int status, const std::string &body) {
  // <= 0: no status line was parsed (e.g. the server never answered after
  // the body was sent); that is a transport problem, not "HTTP 0".
  if (status <= 0)
    return PostOutcome::TRANSPORT_ERROR;
  if (status < 200 || status >= 300)
    return PostOutcome::HTTP_ERROR;
  return body_has_ok_true(body) ? PostOutcome::OK : PostOutcome::NOT_ACKNOWLEDGED;
}

class ForwarderState {
 public:
  void configure(size_t max_devices, uint32_t max_advert_age_ms, size_t max_batteries) {
    this->max_devices_ = max_devices == 0 ? 1 : max_devices;
    this->max_advert_age_ms_ = max_advert_age_ms;
    this->max_batteries_ = max_batteries == 0 ? 1 : max_batteries;
    this->adverts_.reserve(this->max_devices_);
    this->batteries_.reserve(this->max_batteries_);
  }

  /// Feed one received advertisement. `mfr`/`mfr_len` is the 0x0059 payload
  /// (after the company id) or nullptr if the packet carried none; `name` may
  /// be nullptr/empty. Returns true if the packet was a valid Tempem frame.
  bool on_advert(uint64_t mac, int rssi, const char *name, size_t name_len, const uint8_t *mfr, size_t mfr_len,
                 uint32_t now) {
    const bool valid = mfr != nullptr && is_valid_frame(mfr, mfr_len);
    AdvertRecord *rec = this->find_advert_(mac);
    if (!valid) {
      // A scan response without the frame can still teach us the name.
      if (rec != nullptr && name != nullptr && name_len > 0 && this->set_name_(*rec, name, name_len))
        rec->gen = ++this->gen_;
      return false;
    }
    if (rec == nullptr)
      rec = this->alloc_advert_(mac, now);
    rec->seen_ms = now;
    rec->rssi = (int8_t) (rssi < -127 ? -127 : (rssi > 127 ? 127 : rssi));
    rec->mfr_len = (uint8_t) (mfr_len > MAX_MFR_LEN ? MAX_MFR_LEN : mfr_len);
    memcpy(rec->mfr, mfr, rec->mfr_len);
    if (name != nullptr && name_len > 0)
      this->set_name_(*rec, name, name_len);
    rec->gen = ++this->gen_;
    this->total_adverts_++;
    return true;
  }

  /// Queue a battery reading; it is included in every post until a post that
  /// contained it is acknowledged. A newer reading for the same MAC replaces
  /// the older one.
  void add_battery(uint64_t mac, uint8_t level, uint32_t now) {
    for (auto &b : this->batteries_) {
      if (b.mac == mac) {
        b.level = level;
        b.received_ms = now;
        b.inflight = false;  // must be re-sent: the in-flight post had the old value
        return;
      }
    }
    if (this->batteries_.size() >= this->max_batteries_) {
      size_t oldest = 0;
      for (size_t i = 1; i < this->batteries_.size(); i++) {
        if ((int32_t) (this->batteries_[i].received_ms - this->batteries_[oldest].received_ms) < 0)
          oldest = i;
      }
      this->batteries_.erase(this->batteries_.begin() + oldest);
    }
    BatteryRecord b;
    b.mac = mac;
    b.level = level;
    b.received_ms = now;
    this->batteries_.push_back(b);
  }

  /// Drop table entries that have not been updated for a long time.
  void prune(uint32_t now) {
    for (size_t i = 0; i < this->adverts_.size();) {
      if ((uint32_t) (now - this->adverts_[i].seen_ms) > ADVERT_FORGET_MS) {
        this->adverts_.erase(this->adverts_.begin() + i);
      } else {
        i++;
      }
    }
    for (size_t i = 0; i < this->batteries_.size();) {
      if (!this->batteries_[i].inflight && (uint32_t) (now - this->batteries_[i].received_ms) > BATTERY_FORGET_MS) {
        this->batteries_.erase(this->batteries_.begin() + i);
      } else {
        i++;
      }
    }
  }

  /// Render the webhook payload and mark its content as in flight.
  std::string build_payload(const GatewayInfo &gw, uint32_t now) {
    this->prune(now);
    std::string out;
    out.reserve(160 + this->adverts_.size() * 150 + this->batteries_.size() * 64);
    out.append("{\"v\":1,\"gateway\":{\"mac\":");
    append_json_string(out, gw.mac.data(), gw.mac.size() > MAX_GATEWAY_STR ? MAX_GATEWAY_STR : gw.mac.size());
    out.append(",\"name\":");
    append_json_string(out, gw.name.data(), gw.name.size() > MAX_GATEWAY_STR ? MAX_GATEWAY_STR : gw.name.size());
    out.append(",\"version\":");
    append_json_string(out, gw.version.data(),
                       gw.version.size() > MAX_GATEWAY_STR ? MAX_GATEWAY_STR : gw.version.size());
    char buf[64];
    snprintf(buf, sizeof(buf), ",\"uptime\":%llu", (unsigned long long) gw.uptime_s);
    out.append(buf);
    if (gw.has_wifi_rssi) {
      snprintf(buf, sizeof(buf), ",\"wifi_rssi\":%d", gw.wifi_rssi);
      out.append(buf);
    }
    snprintf(buf, sizeof(buf), ",\"free_heap\":%lu}", (unsigned long) gw.free_heap);
    out.append(buf);

    out.append(",\"advertisements\":[");
    bool first = true;
    this->last_advert_count_ = 0;
    for (const auto &a : this->adverts_) {
      const uint32_t age_ms = now - a.seen_ms;
      if ((int32_t) (a.gen - this->acked_gen_) <= 0)
        continue;  // already delivered by an acknowledged post
      if (age_ms > this->max_advert_age_ms_)
        continue;  // too old to be useful
      if (!first)
        out.push_back(',');
      first = false;
      out.append("{\"address\":\"");
      append_mac(out, a.mac);
      snprintf(buf, sizeof(buf), "\",\"rssi\":%d", (int) a.rssi);
      out.append(buf);
      if (a.has_name) {
        out.append(",\"name\":");
        append_json_string(out, a.name, a.name_len);
      }
      out.append(",\"manufacturer_data\":{\"89\":\"");
      append_hex(out, a.mfr, a.mfr_len);
      snprintf(buf, sizeof(buf), "\"},\"age\":%lu.%lu}", (unsigned long) (age_ms / 1000),
               (unsigned long) ((age_ms % 1000) / 100));
      out.append(buf);
      this->last_advert_count_++;
    }
    out.append("],\"batteries\":[");
    first = true;
    this->last_battery_count_ = 0;
    for (auto &b : this->batteries_) {
      if (!first)
        out.push_back(',');
      first = false;
      out.append("{\"address\":\"");
      append_mac(out, b.mac);
      snprintf(buf, sizeof(buf), "\",\"level\":%u,\"age\":%lu}", (unsigned) b.level,
               (unsigned long) ((uint32_t) (now - b.received_ms) / 1000));
      out.append(buf);
      b.inflight = true;
      this->last_battery_count_++;
    }
    out.append("]}");

    this->inflight_gen_ = this->gen_;
    this->inflight_ = true;
    return out;
  }

  /// Apply the outcome of the post built by the last build_payload().
  /// Only an acknowledged post (PostOutcome::OK) advances the "sent" marker
  /// and drops the batteries it carried.
  void on_post_result(bool acknowledged) {
    if (!this->inflight_)
      return;
    this->inflight_ = false;
    if (acknowledged) {
      this->acked_gen_ = this->inflight_gen_;
      for (size_t i = 0; i < this->batteries_.size();) {
        if (this->batteries_[i].inflight) {
          this->batteries_.erase(this->batteries_.begin() + i);
        } else {
          i++;
        }
      }
    } else {
      for (auto &b : this->batteries_)
        b.inflight = false;
    }
  }

  bool inflight() const { return this->inflight_; }
  size_t device_count() const { return this->adverts_.size(); }
  size_t pending_battery_count() const { return this->batteries_.size(); }
  size_t last_advert_count() const { return this->last_advert_count_; }
  size_t last_battery_count() const { return this->last_battery_count_; }
  uint32_t total_adverts() const { return this->total_adverts_; }

 protected:
  AdvertRecord *find_advert_(uint64_t mac) {
    for (auto &a : this->adverts_) {
      if (a.mac == mac)
        return &a;
    }
    return nullptr;
  }

  AdvertRecord *alloc_advert_(uint64_t mac, uint32_t now) {
    if (this->adverts_.size() >= this->max_devices_) {
      // Evict the entry heard least recently.
      size_t oldest = 0;
      uint32_t oldest_age = 0;
      for (size_t i = 0; i < this->adverts_.size(); i++) {
        const uint32_t age = now - this->adverts_[i].seen_ms;
        if (age >= oldest_age) {
          oldest_age = age;
          oldest = i;
        }
      }
      this->adverts_.erase(this->adverts_.begin() + oldest);
    }
    AdvertRecord rec;
    rec.mac = mac;
    this->adverts_.push_back(rec);
    return &this->adverts_.back();
  }

  /// Returns true if the stored name changed.
  bool set_name_(AdvertRecord &rec, const char *name, size_t len) {
    if (len > MAX_NAME_LEN)
      len = MAX_NAME_LEN;
    if (rec.has_name && rec.name_len == len && memcmp(rec.name, name, len) == 0)
      return false;
    memcpy(rec.name, name, len);
    rec.name[len] = '\0';
    rec.name_len = (uint8_t) len;
    rec.has_name = true;
    return true;
  }

  std::vector<AdvertRecord> adverts_;
  std::vector<BatteryRecord> batteries_;
  size_t max_devices_{40};
  uint32_t max_advert_age_ms_{600000};
  size_t max_batteries_{64};
  uint32_t gen_{0};
  uint32_t acked_gen_{0};
  uint32_t inflight_gen_{0};
  bool inflight_{false};
  size_t last_advert_count_{0};
  size_t last_battery_count_{0};
  uint32_t total_adverts_{0};
};

}  // namespace tempem_forwarder_core
