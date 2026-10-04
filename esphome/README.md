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
| `tempem-remote-gateway.yaml` | The firmware configuration |
| `secrets.yaml.example` | Copy to `secrets.yaml` and fill in |
| `components/tempem_forwarder/` | Collects adverts/batteries, renders the webhook JSON (`forwarder_core.h` is plain C++, unit-tested on the host) |
| `components/tempem_battery_poller/` | GATT battery poller (auto-discovery, retry, `on_battery` trigger); still accepts the old static `targets:` config |
| `tests/` | Config-validation YAMLs and the host-side payload test (`tests/host/run_host_tests.sh`) |

## 1. Get the webhook URL from Home Assistant

1. Install the **Tempem BLE** integration (this repository, via HACS or
   `custom_components/`) and restart Home Assistant.
2. In Home Assistant, go to **Settings → Devices & services → Add integration → Tempem BLE**
   and choose **Remote gateway**.
3. Copy the webhook URL it shows, e.g.
   `https://ha.example.com/api/webhook/3f9c…`.
   Treat it like a password: anyone who has it can send data to this
   integration.

Home Assistant must know its public address for the URL to be right. Set it under
**Settings → System → Network → Home Assistant URL → Internet**, e.g.
`https://ha.example.com`. Check that the host part of the URL is the hostname
your tunnel serves.

## 2. Flash at home (USB)

```bash
cd esphome
cp secrets.yaml.example secrets.yaml   # fill in tempem_webhook_url, passwords
esphome run tempem-remote-gateway.yaml # board on USB; pick the serial port
```

* You can leave `wifi_ssid` / `wifi_password` as placeholders if you don't know
  the site Wi-Fi yet.
* To test at home, put your home Wi-Fi in `secrets.yaml`. The logs should show
  `Posted N advert(s) … HTTP 200 ok` once a minute.
* To use the components without a checkout, see the commented
  `github://mikkmihkel/tempem-ha-ble@main` source in the YAML.
* Later updates: `esphome run` over the network (OTA, `ota_password`), or
  upload `firmware.ota.bin` in the web UI.

## 3. On site: connect it to the Wi-Fi

**Captive portal (phone):**

1. Power the gateway (any USB supply).
2. Wait about 1 minute. If the configured Wi-Fi isn't reachable, it opens a
   hotspot called **`Tempem-Remote-Setup`** (password = `ap_password`).
3. Join the hotspot with a phone. The captive portal page opens (if it
   doesn't, browse to `http://192.168.4.1`). Pick the site network, enter its
   password and save.
4. The gateway connects and starts posting. The credentials are saved in
   flash and survive reboots.

ESPHome ties these saved credentials to the configuration: a firmware
update built from a changed YAML/`secrets.yaml` or with another ESPHome
version starts without them and falls back to `wifi_ssid` / `wifi_password`.
Once you know the site Wi-Fi, put it in `secrets.yaml` before you build an
update, or be ready to enter it through the hotspot again.

While the hotspot is up, the gateway does not reboot itself. If the site
Wi-Fi goes down later, it keeps retrying, and the hotspot comes back after
1 minute without a connection, so you can always fix the credentials.

**Improv over USB (laptop):** plug the gateway into a laptop running Chrome or
Edge, open <https://web.esphome.io>, click **Connect**, then choose the
serial port → **⋮ → Configure Wi-Fi**.

## Cloudflare / reverse-proxy notes

The gateway does one thing: `POST https://<domain>/api/webhook/<id>` with
`Content-Type: application/json`. Requests that hit an interactive challenge
or a login page fail:

* **Cloudflare Access / Zero Trust** protecting the hostname: add an
  application policy with action **Bypass** (Everyone) for the path
  `/api/webhook/*`. Otherwise the gateway gets a `302` redirect to the
  Access login. It does not follow redirects, so the log says
  `HTTP 302 … Cloudflare Access login?`.
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

You can open the local web UI at `http://tempem-remote.local/` or
`http://<device-ip>/` (user `admin`, password `web_password`). It shows live
logs and these diagnostics:

| Entity | Meaning |
| --- | --- |
| Webhook last result | `ok`, `HTTP 403`, `connection error`, `not acknowledged: webhook id unknown…` |
| Webhook last HTTP status | Last status code (`-1` = no response) |
| Webhook posts ok / failed | Counters since boot |
| Tempem beacons tracked | Beacons currently in the advert table |
| Battery readings pending | Battery values waiting for an acknowledged post |

There are also buttons: **Post to Home Assistant now**, **Poll batteries
now** and **Restart**. Without the web UI, use `esphome logs
tempem-remote-gateway.yaml` over USB (there is no `api:`, so logs are not
available over the network).

When a post gets an HTTP error status, ESPHome's `http_request` component logs
the full URL, including the webhook id. Don't share logs without removing it.

| Symptom | Likely cause |
| --- | --- |
| `HTTP 200 but no "ok": true … webhook id unknown to Home Assistant` | Home Assistant answers 200 with an empty body for webhook ids it doesn't know. The integration entry was deleted or re-created, or the URL has a typo. Copy the URL again from the integration. |
| `HTTP 302` / `HTTP 403` | Cloudflare Access / WAF / bot protection; see above. |
| `HTTP 404` / `405` | Wrong path. The URL must end in `/api/webhook/<id>`. |
| `HTTP 502`/`530` | cloudflared is not running or can't reach Home Assistant. |
| `connection error` | DNS, TLS or timeout. Check that the site Wi-Fi has internet access, that the hostname resolves, and that no captive portal or firewall at the site intercepts HTTPS. |
| No beacons tracked | Beacons out of range, or not Tempem/Dusun firmware with the `0x0059` frame. Each beacon that is picked up is logged once as `New Tempem beacon …`. |
| Batteries never arrive | The beacon must accept GATT connections. Check the `tempem_battery` log lines; failures are retried hourly (3×) and in every weekly cycle. |

## Resource notes

The build for this configuration (ESPHome 2026.9.1, ESP-IDF 5.5.5) uses
about 41 % of static RAM and 90 % of the 1.75 MB app partition. At runtime,
each post also needs some heap for the TLS handshake (roughly 40 KB; this is
an estimate, not a hardware measurement). The **Free heap** value in every
payload lets you watch it from Home Assistant. The payload is bounded: at most 40 beacons × ~150 B plus at most
64 battery entries. The web UI is optional: remove `web_server:` to save
flash and RAM.

## Payload (contract v1)

```json
{
  "v": 1,
  "gateway": {"mac": "AA:BB:CC:DD:EE:FF", "name": "tempem-remote", "version": "2026.9.1",
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
* `name` is left out until the beacon's name has been seen (scan response).
  `wifi_rssi` is left out when unknown.
* A post counts as delivered only on HTTP 2xx **and** a body containing
  `"ok": true`. Until then, its battery readings stay pending and its adverts
  are sent again (if younger than `max_advert_age`).

Host test of the JSON rendering (needs `g++` and `python3`):

```bash
esphome/tests/host/run_host_tests.sh
```
