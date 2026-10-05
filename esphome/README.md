# Tempem remote gateway (ESPHome firmware)

Firmware for an ESP32-C6 board (tested target: DFRobot FireBeetle 2 ESP32-C6,
`esp32-c6-devkitc-1`, ESP-IDF) that sits at a **remote site** next to
Tempem / Dusun BLE temperature-humidity beacons and forwards their readings to
Home Assistant over plain **HTTPS**.

Use it when the site has internet access, but Home Assistant is somewhere
else and only reachable through its public URL (for example a Cloudflare
Tunnel). Nothing has to connect to the gateway, so you don't need a VPN,
port forwarding or MQTT.

```
 Tempem beacons ))) ESP32-C6 gateway --HTTPS POST every 60 s--> https://<your-domain>/api/webhook/<id>
   (BLE adverts,     (site Wi-Fi)                                  (Cloudflare Tunnel -> Home Assistant
    GATT battery)                                                   `tempem_ble` integration)
```

* **No per-sensor configuration.** Every beacon that sends a valid Tempem
  frame (manufacturer data, company id `0x0059`) is forwarded. Home Assistant
  injects the adverts into its Bluetooth stack as a remote scanner, so the
  sensors are discovered automatically.
* **Battery** is not in the adverts. The gateway reads it over GATT (Battery
  Service `0x180F`/`0x2A19`) once a week for every Tempem beacon it has heard
  (one whose `0x0059` frame is exactly temperature/humidity, or whose name
  starts with "Tempem"/"Dusun": `0x0059` is Nordic's id and other products
  use it too, so it doesn't connect to anything else; up to
  `max_targets`, 32; a beacon not heard for 24 h gives its slot to a new
  one). The first read happens about 2 minutes after boot. Beacons heard
  later are read about 1 minute after they are first heard. Failed reads are
  retried every hour, up to 3 times. BLE scanning pauses while a poll cycle
  runs (a few seconds per beacon, about 20 s for one that does not answer).
* **Batching**: every `post_interval` (default 60 s) the gateway sends one
  JSON document with the latest advert of each beacon heard since the last
  post that Home Assistant acknowledged (never older than 10 min), plus any
  battery readings not yet acknowledged. The post is also sent when nothing
  changed, as a heartbeat.

## Files

| Path | What |
| --- | --- |
| `tempem-remote-gateway.yaml` | The firmware configuration (one generic build: nothing site-specific is compiled in) |
| `webhook_url.h` | Checks and masks the Webhook URL setting |
| `install/index.html` | Browser installer page (published to GitHub Pages by the release workflow) |
| `scripts/make_manifests.py` | Packages a build for a release (update manifest) and for the installer |
| `components/tempem_forwarder/` | Collects adverts/batteries, renders the webhook JSON (`forwarder_core.h` is plain C++, unit-tested on the host) |
| `components/tempem_battery_poller/` | GATT battery poller (auto-discovery, retry, `on_battery` trigger); still accepts the old static `targets:` config |
| `tests/` | Config-validation YAMLs and host-side tests (`tests/host/run_host_tests.sh`) |

## 1. Get the webhook URL from Home Assistant

1. Install the **Tempem BLE** integration (this repository, via HACS) and
   restart Home Assistant.
2. In Home Assistant, go to **Settings → Devices & services → Add integration → Tempem BLE**
   and choose **Add a remote gateway**.
3. Copy the webhook URL it shows, e.g.
   `https://ha.example.com/api/webhook/3f9c…`. You can see it again later
   under the gateway's **Configure**. Treat it like a password: anyone who has
   it can send data to this integration.

Home Assistant must know its public address for the URL to be right. Set it under
**Settings → System → Network → Home Assistant URL → Internet**, e.g.
`https://ha.example.com`. Check that the host part of the URL is the hostname
your tunnel serves.

## 2. Install the firmware from the browser

You need a computer with **Chrome or Edge** and a USB-C **data** cable. Nothing
else to install.

1. Open the installer: **<https://mikkmihkel.github.io/tempem-ha-ble/>**.
2. Plug the board in, click **Install**, pick its port (`USB JTAG/serial debug
   unit`) and confirm. On a board that ran other firmware before, tick
   **Erase device**. Flashing takes about a minute.
3. The installer then asks for **Wi-Fi**. Enter the network of the site where
   the gateway will run (or skip, see step 3 below).
4. Click **Visit device**. On the gateway's page, paste the webhook URL from
   step 1 into **Webhook URL** and press Enter. The field then shows
   `https://ha.example.com/api/webhook/…1a2b` (the id itself is never shown
   again), and the gateway posts right away.

**Without the installer page** (e.g. GitHub Pages not enabled): download
`tempem-remote-gateway.factory.bin` from the
[latest release](https://github.com/mikkmihkel/tempem-ha-ble/releases/latest),
open <https://web.esphome.io>, click **Connect**, pick the port, **Install**,
choose the file. Afterwards use **⋮ → Configure Wi-Fi** there, then open the
device's IP address in the browser for step 4.

If the port doesn't show up: hold **BOOT**, tap **RST**, release **BOOT**, and try
again. On Linux your user may need to be in the `dialout` group.

## 3. On site

Power the gateway from any USB supply. Within a minute the gateway's
**Connectivity** turns on in Home Assistant and the beacons near it appear
under **Discovered**.

If it can't join a Wi-Fi network (none entered yet, wrong password, or a
different site), it opens the hotspot **`Tempem-Remote-Setup`** after 1
minute. Join it with a phone. The captive portal opens (if not, browse to
`http://192.168.4.1`). Pick the network, enter its password and save. While the
hotspot is up the gateway doesn't reboot itself, and if the site Wi-Fi drops
later, the hotspot comes back after a minute without a connection.

Wi-Fi and the webhook URL are stored in flash and **kept across firmware
updates**. (ESPHome only keeps runtime-entered Wi-Fi when the firmware has no
network compiled in, which is why this firmware has none.) **Factory reset**
on the web page forgets both, e.g. before moving the gateway to another site.

## 4. Updating

Every release (see the [changelog](../CHANGELOG.md)) ships the integration
and the gateway firmware together, with the same version number.

* **Automatic (default).** The gateway checks
  `releases/latest/download/tempem-remote-gateway.manifest.json` every 12 hours,
  and 30 s after it connects to Wi-Fi. When a new release is out, it downloads and installs
  it, then restarts. The download is checked against the MD5 in the manifest.
  If the new firmware crashes or restarts within its first 5 minutes, the
  bootloader rolls back to the previous one, and the gateway doesn't install that release by itself
  again (install it from the web page if you want to retry). A download that
  fails is retried every 6 hours. Turn **Auto-update** off on the web page to update only
  by hand. A gateway on a site you can't visit then can't be updated, so
  leave it on there.
* **From the web page.** Press **Check for firmware update**. The **Firmware**
  entity shows the installed and the latest version, with an **Install**
  button. Or upload `tempem-remote-gateway.ota.bin` from a release in the
  **OTA Update** box.
* **Over USB.** Use the installer page again and **leave Erase device
  unticked**. The settings are kept.

The installed version is also shown in Home Assistant, on the gateway
device's **Firmware** line.

The repository must be **public** for gateways and the installer to download
releases.

## 5. Building it yourself (optional)

Only needed to change the configuration. Requires Python 3.12+:

```bash
pip install -r esphome/requirements.txt
cd esphome
esphome run tempem-remote-gateway.yaml                 # USB, or OTA on the same network
# optional: bake in a webhook URL / version
esphome -s webhook_url https://ha.example.com/api/webhook/<id> -s fw_version 0.2.0-local run tempem-remote-gateway.yaml
```

A self-built firmware with the default `fw_version` (`dev`) never installs
releases by itself. With any other `fw_version` that differs from the latest
release, it installs that release as soon as it finds it while **Auto-update** is on.
Turn it off, or point `update_manifest_url` at your own releases. A
`webhook_url` baked in at build time is not stored in flash, so a release
installed over it starts without one: enter the URL on the web page as well.

To lock down the web page and OTA on an untrusted site network, add
`auth:` to `web_server:` and a `password:` to the `esphome` OTA platform. Note
that the browser installer can't do that for you: a prebuilt firmware has no
per-device secrets.

## Cloudflare / reverse-proxy notes

The gateway does one thing: `POST https://<domain>/api/webhook/<id>` with
`Content-Type: application/json`. Requests that hit an interactive challenge
or a login page fail:

* **Cloudflare Access / Zero Trust** protecting the hostname: add an
  application policy with action **Bypass** (Everyone) for the path
  `/api/webhook/*`. Otherwise the gateway is redirected to the Access login
  page. That page has no `"ok": true` in it, so the log says
  `… no "ok": true … or a Cloudflare Access login page`.
* **Bot Fight Mode / Super Bot Fight Mode / WAF managed rules / rate
  limiting**: these may block or challenge a non-browser client
  (`HTTP 403`). Add a WAF custom rule with action **Skip** for
  `URI Path starts with /api/webhook/`. Free-plan Bot Fight Mode can't be
  skipped per path; if it blocks the gateway, turn it off for the zone.
* **TLS**: certificates are verified against the ESP-IDF CA bundle
  (`verify_ssl: true`), which covers the CAs Cloudflare uses (Google Trust
  Services, Let's Encrypt, SSL.com, …). Don't turn verification off.
* Home Assistant itself needs no `trusted_proxies` change for webhooks. It
  still needs a correct external URL (see step 1).

## Troubleshooting

The gateway's web page, `http://<device-ip>/` or
`http://tempem-remote-xxxxxx.local/` (the last 6 characters of its MAC), shows
live logs and these entities:

| Entity | Meaning |
| --- | --- |
| Webhook URL | Host and last 4 characters of the configured URL, or `not set` |
| Webhook last result | `ok`, `HTTP 403`, `connection error`, `not acknowledged: wrong webhook URL or Cloudflare Access?` |
| Webhook last HTTP status | Last status code (`-1` = no response) |
| Webhook posts ok / failed | Counters since boot |
| Tempem beacons tracked | Beacons currently in the advert table |
| Battery readings pending | Battery values waiting for an acknowledged post |
| Firmware / Firmware version | Installed and latest release |

There are also buttons: **Post to Home Assistant now**, **Poll batteries
now**, **Check for firmware update**, **Restart** and **Factory reset**.
Over USB, `esphome logs tempem-remote-gateway.yaml` (or the browser
installer's **Logs**) shows the same log.

The web page has no password: anyone on the site's network can change the
webhook URL, upload firmware or factory-reset the gateway. The webhook URL
itself is never displayed. See [Building it yourself](#5-building-it-yourself-optional)
to add a password.

ESPHome's `http_request` component logs the full URL (with the webhook id)
when a request gets an HTTP error status, so its log messages are turned off
(`logger: logs: http_request: NONE`). The forwarder's own log lines and the
**Webhook last result** entity show the outcome instead.

| Symptom | Likely cause |
| --- | --- |
| `No webhook URL yet` | Paste it into **Webhook URL** on the web page. |
| `HTTP 200 but no "ok": true …` | Home Assistant answers 200 with an empty body for webhook ids it doesn't know: the integration entry was deleted or re-created, or the URL has a typo. Copy the URL again from the integration. Or the request ended on a Cloudflare Access login page; see above. |
| `HTTP 403` | Cloudflare WAF / bot protection; see above. |
| `HTTP 404` / `405` | Wrong path. The URL must end in `/api/webhook/<id>`. |
| `HTTP 502`/`530` | cloudflared is not running or can't reach Home Assistant. |
| `connection error` | DNS, TLS or timeout. Check that the site Wi-Fi has internet access, that the hostname resolves, and that no captive portal or firewall at the site intercepts HTTPS. |
| No beacons tracked | Beacons out of range, or not Tempem/Dusun firmware with the `0x0059` frame. Each beacon that is picked up is logged once as `New Tempem beacon …`. |
| Batteries never arrive | The beacon must accept GATT connections. Check the `tempem_battery` log lines; failures are retried hourly (3×) and in every weekly cycle. |
| Firmware never updates | **Auto-update** off, the repository is private, or the site blocks `github.com` / `*.githubusercontent.com` (where GitHub serves release files). The **Firmware** entity shows the error. A release that was rolled back is not installed automatically again (log: `… did not start (rolled back)`). |

## Resource notes

The build for this configuration (ESPHome 2026.9.1, ESP-IDF 5.5.5) uses
about 41 % of static RAM and 91 % of the 1.75 MB app partition (two such
partitions, for updates with rollback). At runtime,
each post also needs some heap for the TLS handshake (roughly 40 KB; this is
an estimate, not a hardware measurement). The **Free heap** value in every
payload lets you watch it from Home Assistant. The payload is bounded: at most 40 beacons × ~150 B plus at most
64 battery entries. The web UI is optional: remove `web_server:` to save
flash and RAM.

## Payload (contract v1)

```json
{
  "v": 1,
  "gateway": {"mac": "AA:BB:CC:DD:EE:FF", "name": "tempem-remote-ddeeff", "version": "0.2.0",
              "uptime": 12345, "wifi_rssi": -61, "free_heap": 123456},
  "advertisements": [
    {"address": "C0:FF:EE:00:12:34", "rssi": -70, "name": "TempemSens",
     "manufacturer_data": {"89": "036e087b34"}, "age": 3.2}
  ],
  "batteries": [{"address": "C0:FF:EE:00:12:34", "level": 87, "age": 120}]
}
```

* `manufacturer_data` is keyed by the decimal company id (`"89"` = `0x0059`).
  The value is the lowercase hex of the bytes after the company id.
* `age` is the number of seconds since the advert / battery reading was received.
* `gateway.version` is the firmware (release) version.
* `name` is left out until the beacon's name has been seen (scan response).
  `wifi_rssi` is left out when unknown.
* A post counts as delivered only on HTTP 2xx **and** a body containing
  `"ok": true`. Until then, its battery readings stay pending and its adverts
  are sent again (if younger than `max_advert_age`).

Host tests of the JSON rendering and the Webhook URL checks (need `g++` and `python3`):

```bash
esphome/tests/host/run_host_tests.sh
```
