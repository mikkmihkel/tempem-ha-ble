# CLAUDE.md

Guidance for Claude Code (and humans) working in this repository.

## What this is

Tempem/Dusun BLE temperature & humidity beacons for Home Assistant. One repo, one version, two deliverables that
share the webhook protocol in `docs/webhook-protocol.md`:

* `custom_components/tempem_ble/`: HACS integration.
  * `parser.py`: pure decoding, no HA imports.
  * `coordinator.py`: an `ActiveBluetoothProcessorCoordinator` per beacon (adverts plus a weekly GATT battery read).
  * `gateway.py`: webhook → `BaseHaRemoteScanner` for remote ESP32 gateways.
  * `storage.py`: persisted battery poll times and remote levels.
* `esphome/`: ESP32-C6 (ESP-IDF) remote gateway firmware. One generic build (no Wi-Fi or webhook URL compiled in), released
  with the integration, installed from the browser (`esphome/install/`) and self-updating from GitHub releases.
  * `tempem_forwarder`: batches adverts and batteries and builds the JSON. Its logic is in the host-testable `forwarder_core.h`.
  * `tempem_battery_poller`: GATT battery state machine.

## Commands

```bash
pip install -r requirements_test.txt        # python 3.13
pytest -q
ruff check custom_components tests && ruff format --check custom_components tests
bash esphome/tests/host/run_host_tests.sh   # forwarder JSON/ack logic (g++)
pip install -r esphome/requirements.txt && esphome config esphome/tempem-remote-gateway.yaml
```

## Rules

* Conventional Commits, SemVer, Keep a Changelog. Release steps are in `CONTRIBUTING.md`. Bumping `manifest.json` `version` on
  `main` (with a matching `CHANGELOG.md` section) makes the release workflow tag and publish it.
* Integration and firmware must keep speaking the same webhook protocol. A change to one side needs the doc and the
  other side updated in the same PR.
* The webhook is internet-facing: validate everything, keep the limits, and never log the webhook id.
* Company id `0x0059` is Nordic's. Never treat every `0x0059` device as a Tempem beacon: use
  `parser.is_supported_advertisement` (HA) or `is_likely_tempem` (firmware).
* Supported Home Assistant: 2025.3.0+ (`hacs.json`). Don't use newer APIs without raising that.
* Never compile site-specific settings (Wi-Fi networks, webhook URL) into the released firmware: Wi-Fi compiled in
  makes ESPHome forget runtime-entered credentials on every update.
* `secrets.yaml` is never committed.
