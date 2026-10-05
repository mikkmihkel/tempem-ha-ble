# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
(see [CONTRIBUTING.md](CONTRIBUTING.md#versioning) for what counts as breaking).

## [Unreleased]

## [0.2.0] - 2026-10-05

### Added
- **Browser installer for the remote gateway firmware.** Each release now includes a prebuilt firmware, and the
  installer page (GitHub Pages, ESP Web Tools) flashes it from Chrome/Edge over USB. The same dialog then sets up
  Wi-Fi (Improv). The release `.factory.bin` also works with web.esphome.io.
- **Webhook URL set on the gateway's web page** (stored in flash, shown masked), instead of compiled in.
- **Automatic firmware updates:**
  - The gateway checks the latest GitHub release every 12 hours and after connecting to Wi-Fi, and installs it.
  - The image is MD5-checked, and ESP-IDF rolls back a release that doesn't start.
  - A release is kept only after it has run for 5 minutes. A rolled-back release isn't auto-installed again, and a
    failed download is retried every 6 hours.
  - An *Auto-update* switch turns this off. *Check for firmware update* and *Factory reset* buttons were added, as
    was firmware upload from the web page.
  - Builds from forks update from the fork's own releases.
- Release workflow builds the firmware, attaches `tempem-remote-gateway.factory.bin`/`.ota.bin`/`.manifest.json` to
  the release and deploys the installer page. CI compiles the firmware on every push.
- Dependabot for GitHub Actions and Python dependencies.

### Changed
- The gateway firmware is now a single generic build with **no Wi-Fi network compiled in**, so Wi-Fi entered on site
  is kept across updates. `secrets.yaml` is no longer used. Self-builders can still bake in a webhook URL with
  `esphome -s webhook_url …`.
- Gateway hostnames get a MAC suffix (`tempem-remote-xxxxxx`), so several gateways can share a network.
- The webhook report's `gateway.version` is now the firmware release version (shown as the device's firmware in
  Home Assistant).
- The gateway follows HTTP redirects (needed for GitHub downloads). A redirect of the webhook to a Cloudflare Access
  login page is still reported, as "not acknowledged".
- The local web page no longer has a password, because a prebuilt firmware has no per-device secrets. The webhook URL
  is never displayed or logged (`http_request` component logs are off), and the README explains how to add a password
  in your own build.
- ESPHome is pinned (`esphome/requirements.txt`, 2026.9.1) for reproducible firmware builds.

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
- Release workflow: a version bump on `main` (or a pushed `vX.Y.Z` tag) creates the tag and a GitHub release with
  `tempem_ble.zip` and the changelog section as notes, after checking that `manifest.json` and this changelog agree.
- MIT license.

[Unreleased]: https://github.com/mikkmihkel/tempem-ha-ble/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/mikkmihkel/tempem-ha-ble/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/mikkmihkel/tempem-ha-ble/releases/tag/v0.1.0
