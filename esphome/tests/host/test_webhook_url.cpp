// Host-side checks for ../../webhook_url.h (the "Webhook URL" setting).
#include <cstdio>
#include <cstdlib>
#include <string>

#include "../../webhook_url.h"

static int failures = 0;

static void check(bool ok, const char *what) {
  if (!ok) {
    std::printf("FAIL: %s\n", what);
    failures++;
  }
}

int main() {
  const std::string id(64, 'a');
  const std::string good = "https://ha.example.com/api/webhook/" + id.substr(0, 60) + "1a2b";
  check(is_valid_webhook_url(good), "https URL accepted");
  check(is_valid_webhook_url("http://192.168.1.5:8123/api/webhook/abc"), "http URL accepted");
  check(!is_valid_webhook_url(""), "empty rejected");
  check(!is_valid_webhook_url("ha.example.com/api/webhook/abc"), "missing scheme rejected");
  check(!is_valid_webhook_url("https://ha.example.com/api/webhook/"), "missing id rejected");
  check(!is_valid_webhook_url("https:///api/webhook/abc"), "missing host rejected");
  check(!is_valid_webhook_url("https://ha.example.com/api/webhook/a b"), "space rejected");
  check(!is_valid_webhook_url("https://ha.example.com/api/webhook/abc\n"), "newline rejected");
  check(!is_valid_webhook_url("ftp://ha.example.com/api/webhook/abc"), "other scheme rejected");
  check(!is_valid_webhook_url("https://ha.example.com/" + std::string(240, 'x') + "/api/webhook/abc"),
        "too long rejected");

  check(!is_valid_webhook_url("https://ha.example.com/api/webhook/\xc3\xa4" "bc"), "non-ASCII rejected");

  check(mask_webhook_url("") == "not set", "empty masked");
  const std::string masked = mask_webhook_url(good);
  check(!is_valid_webhook_url(masked), "masked value submitted back is rejected");
  check(masked == "https://ha.example.com/api/webhook/…1a2b", "masked keeps host and last 4");
  check(masked.find(id.substr(0, 8)) == std::string::npos, "masked hides the id");
  check(mask_webhook_url("https://ha.example.com/api/webhook/ab") == "https://ha.example.com/api/webhook/…",
        "short id fully hidden");

  if (failures == 0)
    std::printf("all webhook_url checks passed\n");
  return failures == 0 ? EXIT_SUCCESS : EXIT_FAILURE;
}
