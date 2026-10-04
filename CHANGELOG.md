# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
(see [CONTRIBUTING.md](CONTRIBUTING.md#versioning) for what counts as breaking).

## [Unreleased]

## [0.1.0] - 2026-10-04

First release.

### Added

#### Home Assistant integration (`tempem_ble`)
- Automatic Bluetooth discovery of Dusun/Tempem temperature & humidity beacons (manufacturer id `0x0059`) from any
  Home Assistant Bluetooth source: local adapter, ESPHome Bluetooth proxies, or a remote Tempem gateway.
  Only valid Dusun temperature/humidity frames are offered, because many other Nordic devices use `0x0059` too.
- Per-beacon device with temperature, humidity, battery and (disabled by default) signal strength sensors.
  Values are restored after a restart.
- Battery level read over GATT (Battery Service `0x180F`/`0x2A19`) when a connectable adapter or proxy hears the beacon.
  The interval is configurable (default weekly; `0` disables). The last read time persists across restarts, and a failed
  read is retried after an hour.
- Manual setup menu: pick a sensor from the ones currently heard, or add a remote gateway.
- **Remote gateways:** a webhook (`/api/webhook/<id>`, POST only, 64 KiB limit) that accepts batched advertisements and
  battery levels over HTTPS (works through a Cloudflare Tunnel). Advertisements are injected into Home Assistant's
  Bluetooth stack as a non-connectable remote scanner, so remote beacons are discovered like local ones.
  Remote battery levels are cached, so sensors added later get the level immediately.
- Gateway device with Connectivity, Last report, Advertisements in last report, Wi-Fi signal, Uptime and Free memory
  entities. The webhook URL is shown again under **Configure**.
- Webhook hardening for an internet-facing endpoint:
  - Only Dusun (`0x0059`) manufacturer data is injected into Home Assistant Bluetooth, so other integrations
    can't be spoofed.
  - At most 1024 distinct addresses per gateway per 15 minutes, and at most 8 manufacturer data entries per advertisement.
  - The remote battery cache is capped.
  - Deeply nested JSON and non-finite numbers are rejected.
  - Rejected reports log a warning once, not on every request.
- HACS support (`hacs.json`, release zip).

#### ESPHome remote gateway firmware (`esphome/`)
- `tempem-remote-gateway.yaml` for ESP32-C6 (ESP-IDF): forwards every valid Tempem beacon to the webhook every 60 s
  (heartbeat even when idle). It needs no per-sensor configuration. A delivery is confirmed only by a `"ok": true`
  response, and readings are kept and resent until then.
- On-site setup without reflashing: fallback hotspot + captive portal, or Improv over USB. Local web UI with
  diagnostics and a "Post now" button.
- `tempem_forwarder` component (bounded advert/battery tables, JSON building with host-side unit tests).
- `tempem_battery_poller` component extended from the original gateway: `auto_discover`, `max_targets`,
  `on_battery` trigger, `first_poll_delay`, catch-up polls for late-discovered beacons, hourly retries. The old
  static `targets:` configuration still works.
  - Auto-discovery only connects to beacons with an exact temperature/humidity frame or a Tempem/Dusun name,
    the same rule Home Assistant uses.
  - The connect timeout is longer than Bluedroid's own (one unreachable beacon no longer makes the next ones fail).
  - The GATT client re-registers cleanly after repeated failures.
  - A watchdog restarts BLE scanning if it is ever left stopped outside a poll cycle.

#### Project
- Webhook protocol v1 documentation (`docs/webhook-protocol.md`).
- CI: hassfest, HACS validation, ruff, pytest, ESPHome config validation, forwarder host tests.
  The test suite was run against Home Assistant 2025.3.0 through 2026.2.3.
- Tag-driven release workflow that checks the tag, `manifest.json` and this changelog agree.
- MIT license.

[Unreleased]: https://github.com/mikkmihkel/tempem-ha-ble/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/mikkmihkel/tempem-ha-ble/releases/tag/v0.1.0
