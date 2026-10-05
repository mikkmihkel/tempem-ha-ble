#pragma once
// Helpers for the "Webhook URL" setting in tempem-remote-gateway.yaml.

#include <string>

// Accepts http(s)://<host>/api/webhook/<id> in printable ASCII. Rejecting
// everything else also rejects the masked value ("…" is not ASCII) when it is
// submitted back unchanged.
inline bool is_valid_webhook_url(const std::string &url) {
  if (url.size() > 254)
    return false;
  for (char c : url) {
    // unsigned: plain char is unsigned on the ESP32-C6 (RISC-V), signed on x86.
    const auto u = static_cast<unsigned char>(c);
    if (u <= ' ' || u >= 0x7f)
      return false;
  }
  size_t scheme;
  if (url.rfind("https://", 0) == 0)
    scheme = 8;
  else if (url.rfind("http://", 0) == 0)
    scheme = 7;
  else
    return false;
  const size_t path = url.find("/api/webhook/", scheme);
  return path != std::string::npos && path > scheme && url.size() > path + 13;
}

// "https://ha.example.com/api/webhook/…1a2b": enough to recognise the URL,
// without showing the secret webhook id.
inline std::string mask_webhook_url(const std::string &url) {
  if (url.empty())
    return "not set";
  const size_t path = url.find("/api/webhook/");
  if (path == std::string::npos)
    return "set";
  const std::string id = url.substr(path + 13);
  const std::string tail = id.size() > 4 ? id.substr(id.size() - 4) : "";
  return url.substr(0, path) + "/api/webhook/…" + tail;
}
