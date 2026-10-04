// Host-side tests for components/tempem_forwarder/forwarder_core.h.
//
// Prints one "<label>\t<payload json>" line per rendered payload; the
// companion check_payloads.py validates every line with Python's json module
// and checks it against the webhook contract. Logic assertions live here.
//
// Build & run: ./run_host_tests.sh

#include "../../components/tempem_forwarder/forwarder_core.h"

#include <cstdio>
#include <cstdlib>
#include <string>

using namespace tempem_forwarder_core;

static int g_failures = 0;
#define CHECK(cond)                                                     \
  do {                                                                  \
    if (!(cond)) {                                                      \
      fprintf(stderr, "CHECK failed %s:%d: %s\n", __FILE__, __LINE__, #cond); \
      g_failures++;                                                     \
    }                                                                   \
  } while (0)

static void emit(const char *label, const std::string &json) { printf("%s\t%s\n", label, json.c_str()); }

static GatewayInfo gw(bool rssi = true) {
  GatewayInfo g;
  g.mac = "AA:BB:CC:DD:EE:FF";
  g.name = "tempem-remote";
  g.version = "2026.9.1";
  g.uptime_s = 12345;
  g.has_wifi_rssi = rssi;
  g.wifi_rssi = -61;
  g.free_heap = 123456;
  return g;
}

static const uint8_t FRAME[] = {0x03, 0x6e, 0x08, 0x7b, 0x34};  // temp+hum
static const uint64_t MAC1 = 0xC0FFEE001234ULL;
static const uint64_t MAC2 = 0xC0FFEE005678ULL;

static size_t count(const std::string &hay, const std::string &needle) {
  size_t n = 0, pos = 0;
  while ((pos = hay.find(needle, pos)) != std::string::npos) {
    n++;
    pos += needle.size();
  }
  return n;
}

int main() {
  // --- frame validation -----------------------------------------------------
  {
    const uint8_t temp_only[] = {0x01, 0x6e, 0x08};
    const uint8_t hum_only[] = {0x02, 0x7b, 0x34};
    const uint8_t extra_bits_ok[] = {0x23, 1, 2, 3, 4};  // 0x20 allowed (<= 0x3F)
    const uint8_t no_bits[] = {0x04, 1, 2};
    const uint8_t high_bit[] = {0x43, 1, 2, 3, 4};
    const uint8_t short_frame[] = {0x03, 1, 2, 3};
    CHECK(is_valid_frame(FRAME, sizeof(FRAME)));
    CHECK(is_valid_frame(temp_only, sizeof(temp_only)));
    CHECK(is_valid_frame(hum_only, sizeof(hum_only)));
    CHECK(is_valid_frame(extra_bits_ok, sizeof(extra_bits_ok)));
    CHECK(!is_valid_frame(no_bits, sizeof(no_bits)));
    CHECK(!is_valid_frame(high_bit, sizeof(high_bit)));
    CHECK(!is_valid_frame(short_frame, sizeof(short_frame)));
    CHECK(!is_valid_frame(FRAME, 0));
    CHECK(!is_valid_frame(nullptr, 5));
  }

  // --- response classification ------------------------------------------------
  {
    CHECK(classify_response(200, "{\"ok\":true,\"accepted\":3}") == PostOutcome::OK);
    CHECK(classify_response(200, "{\"ok\": true, \"accepted\": 0}") == PostOutcome::OK);
    CHECK(classify_response(204, "{ \"accepted\":1,\n \"ok\" :\ttrue }") == PostOutcome::OK);
    CHECK(classify_response(200, "") == PostOutcome::NOT_ACKNOWLEDGED);
    CHECK(classify_response(200, "{\"ok\":false}") == PostOutcome::NOT_ACKNOWLEDGED);
    CHECK(classify_response(200, "{\"okay\":true}") == PostOutcome::NOT_ACKNOWLEDGED);
    CHECK(classify_response(302, "{\"ok\":true}") == PostOutcome::HTTP_ERROR);
    CHECK(classify_response(403, "") == PostOutcome::HTTP_ERROR);
    CHECK(classify_response(-1, "") == PostOutcome::TRANSPORT_ERROR);
    CHECK(classify_response(0, "") == PostOutcome::TRANSPORT_ERROR);
  }

  // --- MAC helpers ---------------------------------------------------------------
  {
    uint64_t m = 0;
    CHECK(parse_mac("C0:FF:EE:00:12:34", m) && m == MAC1);
    CHECK(parse_mac("c0-ff-ee-00-12-34", m) && m == MAC1);
    CHECK(!parse_mac("C0:FF:EE:00:12", m));
    CHECK(!parse_mac("G6:53:47:6E:9D:62", m));
    std::string s;
    append_mac(s, MAC1);
    CHECK(s == "C0:FF:EE:00:12:34");
  }

  // --- heartbeat (empty tables, unknown wifi rssi) -------------------------------
  {
    ForwarderState st;
    st.configure(40, 600000, 64);
    std::string p = st.build_payload(gw(false), 1000);
    emit("heartbeat_no_rssi", p);
    CHECK(p.find("wifi_rssi") == std::string::npos);
    st.on_post_result(true);
  }

  // --- adverts, name escaping, merge, ack semantics --------------------------------
  {
    ForwarderState st;
    st.configure(40, 600000, 64);
    uint32_t t = 100000;
    // advert without name, then scan response with a nasty name
    CHECK(st.on_advert(MAC1, -70, nullptr, 0, FRAME, sizeof(FRAME), t));
    const char nasty[] = "Te\"mp\\em\x01\n\xff\xc3\xa9";  // quote, backslash, ctrl, invalid, valid é
    CHECK(!st.on_advert(MAC1, -71, nasty, sizeof(nasty) - 1, nullptr, 0, t + 100));
    // unknown MAC with only a name is ignored (not tracked)
    CHECK(!st.on_advert(MAC2, -50, "TempemSens", 10, nullptr, 0, t + 100));
    CHECK(st.device_count() == 1);
    // invalid frame is not accepted
    const uint8_t bad[] = {0x80, 1, 2, 3, 4};
    CHECK(!st.on_advert(MAC2, -50, "TempemSens", 10, bad, sizeof(bad), t + 100));
    CHECK(st.device_count() == 1);
    // second device with name in the same packet
    CHECK(st.on_advert(MAC2, -80, "TempemSens", 10, FRAME, sizeof(FRAME), t + 200));
    st.add_battery(MAC1, 87, t + 300);

    std::string p1 = st.build_payload(gw(), t + 3500);
    emit("two_adverts_one_battery", p1);
    CHECK(count(p1, "\"address\":") == 3);
    CHECK(p1.find("\"manufacturer_data\":{\"89\":\"036e087b34\"}") != std::string::npos);
    CHECK(st.last_advert_count() == 2 && st.last_battery_count() == 1);

    // failed post: everything is sent again
    st.on_post_result(false);
    std::string p2 = st.build_payload(gw(), t + 4000);
    emit("after_failure", p2);
    CHECK(st.last_advert_count() == 2 && st.last_battery_count() == 1);

    // acknowledged: nothing new -> empty arrays (heartbeat)
    st.on_post_result(true);
    std::string p3 = st.build_payload(gw(), t + 5000);
    emit("after_ack_heartbeat", p3);
    CHECK(st.last_advert_count() == 0 && st.last_battery_count() == 0);
    CHECK(p3.find("\"advertisements\":[]") != std::string::npos);
    CHECK(p3.find("\"batteries\":[]") != std::string::npos);
    st.on_post_result(true);

    // new advert for MAC1 only -> only MAC1 sent, name kept from scan response
    CHECK(st.on_advert(MAC1, -65, nullptr, 0, FRAME, sizeof(FRAME), t + 6000));
    std::string p4 = st.build_payload(gw(), t + 6000);
    emit("one_update", p4);
    CHECK(st.last_advert_count() == 1);
    CHECK(p4.find("C0:FF:EE:00:12:34") != std::string::npos);
    CHECK(p4.find("C0:FF:EE:00:56:78") == std::string::npos);
    CHECK(p4.find("\"age\":0.0") != std::string::npos);

    // a battery reading arriving while a post is in flight is not dropped by
    // that post's ack
    st.add_battery(MAC2, 55, t + 6100);
    st.on_post_result(true);
    CHECK(st.pending_battery_count() == 1);
    std::string p5 = st.build_payload(gw(), t + 7000);
    emit("late_battery", p5);
    CHECK(st.last_battery_count() == 1);
    st.on_post_result(true);
    CHECK(st.pending_battery_count() == 0);

    // updated reading for a battery that is in flight replaces it and stays
    st.add_battery(MAC1, 80, t + 8000);
    st.build_payload(gw(), t + 8000);
    st.add_battery(MAC1, 79, t + 8100);
    st.on_post_result(true);
    CHECK(st.pending_battery_count() == 1);
    std::string p6 = st.build_payload(gw(), t + 9000);
    emit("replaced_battery", p6);
    CHECK(p6.find("\"level\":79") != std::string::npos);
    st.on_post_result(true);
  }

  // --- max advert age: never send adverts older than the limit ------------------
  {
    ForwarderState st;
    st.configure(40, 600000, 64);
    CHECK(st.on_advert(MAC1, -70, "TempemSens", 10, FRAME, sizeof(FRAME), 1000));
    std::string p = st.build_payload(gw(), 1000 + 601000);
    emit("too_old", p);
    CHECK(st.last_advert_count() == 0);
    st.on_post_result(false);

    ForwarderState st2;
    st2.configure(40, 600000, 64);
    CHECK(st2.on_advert(MAC1, -70, "TempemSens", 10, FRAME, sizeof(FRAME), 1000));
    st2.build_payload(gw(), 1000 + 599000);  // just inside the limit
    CHECK(st2.last_advert_count() == 1);
    st2.on_post_result(false);
  }

  // --- millis() wraparound -----------------------------------------------------------
  {
    ForwarderState st;
    st.configure(40, 600000, 64);
    const uint32_t before_wrap = 0xFFFFFF00u;
    CHECK(st.on_advert(MAC1, -70, "TempemSens", 10, FRAME, sizeof(FRAME), before_wrap));
    st.add_battery(MAC1, 90, before_wrap);
    std::string p = st.build_payload(gw(), before_wrap + 5000);  // wrapped
    emit("wraparound", p);
    CHECK(st.last_advert_count() == 1);
    CHECK(p.find("\"age\":5.0") != std::string::npos);
    CHECK(p.find("\"age\":5}") != std::string::npos);
    st.on_post_result(true);
  }

  // --- table cap: evict the least recently heard device ------------------------------
  {
    ForwarderState st;
    st.configure(3, 600000, 2);
    for (uint64_t i = 0; i < 5; i++)
      st.on_advert(0xC00000000000ULL + i, -60, nullptr, 0, FRAME, sizeof(FRAME), 1000 + (uint32_t) i * 10);
    CHECK(st.device_count() == 3);
    for (uint64_t i = 0; i < 4; i++)
      st.add_battery(0xC00000000000ULL + i, (uint8_t) (50 + i), 1000 + (uint32_t) i);
    CHECK(st.pending_battery_count() == 2);
    std::string p = st.build_payload(gw(), 2000);
    emit("capped", p);
    CHECK(p.find("C0:00:00:00:00:00") == std::string::npos);
    CHECK(p.find("C0:00:00:00:00:04") != std::string::npos);
    st.on_post_result(true);
  }

  // --- long and non-ASCII names are bounded / valid ---------------------------------
  {
    ForwarderState st;
    st.configure(40, 600000, 64);
    std::string long_name(60, 'x');
    long_name += "\xe2\x82";  // truncated UTF-8 at the very end
    CHECK(st.on_advert(MAC1, -70, long_name.data(), long_name.size(), FRAME, sizeof(FRAME), 10));
    const char cut[] = "Tempem\xe2\x82\xac\xe2\x82\xac\xe2\x82\xac\xe2\x82\xac\xe2\x82\xac\xe2\x82\xac\xe2\x82\xac\xe2";
    CHECK(st.on_advert(MAC2, -70, cut, sizeof(cut) - 1, FRAME, sizeof(FRAME), 10));
    std::string p = st.build_payload(gw(), 20);
    emit("bounded_names", p);
    st.on_post_result(true);
  }

  if (g_failures) {
    fprintf(stderr, "%d check(s) failed\n", g_failures);
    return 1;
  }
  fprintf(stderr, "all C++ checks passed\n");
  return 0;
}
