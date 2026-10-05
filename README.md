<img src="custom_components/tempem_ble/brand/icon.png" alt="" width="64" align="right">

# Tempem BLE for Home Assistant

Support for **Tempem ST1** Bluetooth temperature and humidity sensors (Dusun beacon format) in Home Assistant, in two
setups:

| | Setup | Hardware you need | How sensors are added |
|---|---|---|---|
| **A** | **Local**: HACS integration that uses Home Assistant's own Bluetooth | The Bluetooth adapter in your HA machine (Raspberry Pi, NUC + USB dongle) and/or any ESPHome Bluetooth proxy on the LAN | Discovered automatically; click **Add** |
| **B** | **Remote site**: ESP32-C6 on another network that reports to HA over the internet (works through a Cloudflare Tunnel) | One ESP32-C6 (e.g. FireBeetle 2) at the remote site | Same as A: they show up as discovered in HA, nothing is configured on the ESP32 |

Both setups use the same integration, so every sensor looks the same in HA: one device per beacon with **Temperature**, **Humidity**, **Battery** and (disabled by default) **Signal strength**.

---

## How it works

```
                    ┌──────────────────────── Home Assistant ─────────────────────────┐
 Tempem beacon ))) ─┤ local BT adapter ─┐                                              │
 Tempem beacon ))) ─┤ ESPHome BT proxy ─┼─► HA Bluetooth ─► tempem_ble ─► sensors     │
                    │                   │    (discovery)                               │
 remote site:       │  webhook (HTTPS) ─┘                                              │
 Tempem ))) ESP32-C6 ── internet ── Cloudflare Tunnel ──► /api/webhook/<secret id>     │
                    └──────────────────────────────────────────────────────────────────┘
```

* Beacons broadcast manufacturer data under company id `0x0059`: `[type][temperature BE16][humidity BE16]`
  (`T = X·175.72/65536 − 46.85`, `RH = X·125/65536 − 6`, see the Dusun *Bluetooth beacon sensor data format V1.2*).
  Readings are passive, so they cost the beacon nothing extra.
* The **battery level is not broadcast**. It is read by connecting to the beacon (GATT Battery Service `0x180F`/`0x2A19`).
  Connecting costs the beacon battery, so it is done rarely: weekly by default.
  * Setup A: Home Assistant connects when a *connectable* adapter or proxy can hear the beacon.
  * Setup B: the ESP32 reads it itself and includes it in its report.
* The remote gateway registers in HA as a **Bluetooth scanner** (like an ESPHome proxy or a Ruuvi Gateway). Advertisements it
  forwards go through HA's normal Bluetooth stack. That's why beacons at the remote site are discovered exactly like local ones.

---

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=mikkmihkel&repository=tempem-ha-ble&category=integration)

The button above opens the repository in HACS on your own Home Assistant. Otherwise, add it by hand:

1. Make sure [HACS](https://hacs.xyz/docs/use/) is installed (Home Assistant 2025.3 or newer).
2. In Home Assistant open **HACS**. Click **⋮** (top right) → **Custom repositories**.
3. **Repository:** `https://github.com/mikkmihkel/tempem-ha-ble`. **Type:** **Integration**. Click **Add**.
4. Search HACS for **Tempem BLE**, open it, click **Download** and pick the latest version.
5. **Restart Home Assistant** (Settings → System → ⋮ → Restart).
6. Add it: Settings → Devices & services → **Add integration** → **Tempem BLE**, or just wait for beacons to show up under
   **Discovered**.

   [![Open your Home Assistant instance and start setting up Tempem BLE.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=tempem_ble)

**Updates:** HACS shows an update for **Tempem BLE** when a new version is released. Install it, then restart Home
Assistant. What changed is in [`CHANGELOG.md`](CHANGELOG.md) and on the
[releases page](https://github.com/mikkmihkel/tempem-ha-ble/releases). To install a specific (older or pre-release)
version, open Tempem BLE in HACS → **⋮ → Redownload** and choose the version.

**Uninstall:** delete the Tempem BLE entries under Settings → Devices & services. Then in HACS → Tempem BLE → ⋮ →
**Remove**, and restart.

### Manual

Download `tempem_ble.zip` from the [latest release](https://github.com/mikkmihkel/tempem-ha-ble/releases/latest),
extract it into `<config>/custom_components/tempem_ble/` (so that `manifest.json` ends up at
`<config>/custom_components/tempem_ble/manifest.json`), and restart Home Assistant.

---

## Setup A: local (HACS)

1. **Bluetooth in HA.** Settings → Devices & services → **Bluetooth** must be set up.
   * A Raspberry Pi's built-in Bluetooth works. A USB dongle on a short extension cable is usually better.
   * Or (recommended for range) reflash your existing ESP32-C6 gateway as an **ESPHome Bluetooth proxy**. See
     [Turning the old gateway into a proxy](#turning-the-old-esphome-gateway-into-a-bluetooth-proxy).
2. **Install the integration** with HACS. See [Installation](#installation) above. Restart Home Assistant afterwards.
3. **Add sensors.** Beacons that HA hears show up under Settings → Devices & services → **Discovered**. Click **Add** on each.
   Or use **Add integration → Tempem BLE → Add a Tempem sensor that Home Assistant can hear** to pick from a list
   (it shows MAC and RSSI, so you can tell which is which).
4. **Battery polling (optional).** Each sensor's **Configure** sets how often to read the battery (hours; `0` = never).
   The default is 168 h (weekly). The last read time is remembered across restarts, so restarting HA does not trigger
   a round of connections. If a read fails it is retried after an hour.

Each sensor shows up as an **ST1 by Tempem** device with the Tempem icon. The icon needs Home Assistant 2026.3 or newer,
which loads it from the integration itself; older versions show a placeholder.

Entities are named after the last 4 hex digits of the MAC, e.g. `C0:FF:EE:00:12:34` → **Tempem 1234**
(`sensor.tempem_1234_temperature`, `sensor.tempem_1234_humidity`, `sensor.tempem_1234_battery`).

### Turning the old ESPHome gateway into a Bluetooth proxy

On the LAN you don't need the custom gateway firmware any more. A stock ESPHome Bluetooth proxy on the same ESP32-C6 lets HA
both receive advertisements and read batteries, and you never have to edit YAML to add a sensor again:

```yaml
esp32:
  board: esp32-c6-devkitc-1
  variant: esp32c6
  framework:
    type: esp-idf

wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password

api:
ota:
  platform: esphome

esp32_ble_tracker:
  scan_parameters:
    active: true      # Tempem names arrive in scan responses

bluetooth_proxy:
  active: true        # lets HA connect to read the battery
```

New entity IDs differ from the old template sensors. If you want to keep history, delete the old gateway's entities first,
then rename the new ones to the old entity IDs.

---

## Setup B: remote site (ESP32-C6 over the internet)

### Why a webhook (and not the ESPHome API or MQTT)?

| Option | Works through a Cloudflare Tunnel? | Notes |
|---|---|---|
| ESPHome native API / Bluetooth proxy | ❌ | HA must open the connection *to* the ESP, which is behind the remote site's NAT |
| MQTT to HA's Mosquitto through the tunnel | ❌ | Public tunnel hostnames carry HTTP(S)/WebSocket only, and ESPHome's MQTT client cannot use WebSockets (TCP/TLS only) |
| MQTT with a port forward on 8883 | ✅ | Needs an open port at home, which is what the tunnel is there to avoid |
| Cloud MQTT broker + Mosquitto bridge | ✅ | Works, but adds a third-party service and account |
| VPN (WireGuard/Tailscale) | ⚠️ | ESPHome's WireGuard needs an inbound UDP port at home; Tailscale doesn't run on an ESP32 |
| **HTTPS POST to an HA webhook** | ✅ | Outbound HTTPS only. Uses the domain you already have. No extra services. **This is what this repo does.** |

### Steps

1. **Home Assistant (once)**
   * Settings → System → Network → **Home Assistant URL → Internet** = your public URL, e.g. `https://ha.example.com`.
   * [Install the integration](#installation) (a local Bluetooth adapter is *not* required for Setup B).
   * **Add integration → Tempem BLE → Add a remote gateway**, give it a name (e.g. *Summer house*).
     HA shows the **webhook URL** (`https://ha.example.com/api/webhook/<long random id>`). Copy it.
     You can see it again later under the gateway's **Configure**. Treat it like a password.
2. **Cloudflare.** If the hostname is protected by **Cloudflare Access / Zero Trust**, add an application or policy for path
   `/api/webhook/*` with action **Bypass**. The ESP32 can't do the Access login. If you enabled **Bot Fight Mode** or custom WAF
   rules, add a skip rule for that path too. Without Access, nothing needs changing.
3. **Install the firmware from the browser** (Chrome or Edge, USB data cable, nothing else to install):
   open the **[installer page](https://mikkmihkel.github.io/tempem-ha-ble/)**, plug in the ESP32-C6, click **Install**.
   When it's done, enter the site's **Wi-Fi** in the same dialog (or later, see step 4). Then click **Visit device**
   and paste the webhook URL into **Webhook URL**. Details and the no-installer route (web.esphome.io + the release
   `.bin`) are in [`esphome/README.md`](esphome/README.md).
4. **At the remote site:** power it on. If it can't join a Wi-Fi network, it opens the hotspot **Tempem-Remote-Setup**
   after a minute. Join it with your phone, and the captive portal lets you pick the site's Wi-Fi.
   Wi-Fi and webhook URL are kept across firmware updates.
5. **In HA:** within a minute the gateway's **Connectivity** turns on and the beacons near it appear under **Discovered**.
   Add them like local ones. Battery levels arrive after the gateway's first battery round, a few minutes after boot.

The gateway's device in HA shows *Connectivity*, *Last report*, *Advertisements in last report* and *Wi-Fi signal*
(plus disabled-by-default *Uptime* and *Free memory*). It also appears in the Bluetooth integration as a remote scanner.

**Updates** install themselves: the gateway checks GitHub for a new release every 12 hours and after connecting to
Wi-Fi. It installs the release and rolls back if the new version doesn't start. You can turn this off with the
*Auto-update* switch on its web page; it can also be updated from that page or over USB with the installer
([details](esphome/README.md#4-updating)). Integration and firmware are released together under one version.

The webhook protocol is documented in [`docs/webhook-protocol.md`](docs/webhook-protocol.md), in case you want to
write your own gateway.

---

## Troubleshooting

* **Beacon not discovered (A):** check Settings → Bluetooth → *Advertisement monitor* for the MAC. Tempem beacons use
  company id 89 (0x0059). Because that id belongs to Nordic Semiconductor and many devices use it, the integration only
  offers devices whose payload is a valid Dusun temperature/humidity frame.
* **Battery stays unknown (A):** the adapter or proxy that hears the beacon must be *connectable* (ESPHome
  `bluetooth_proxy: active: true`, or a local adapter). Passive-only sources can't read it. Look for
  `Bluetooth error whilst polling` in the log.
* **Gateway shows disconnected (B):** open `https://<domain>/api/webhook/<id>` in a browser. A *405 Method Not Allowed*
  means the tunnel and webhook are fine (it only accepts POST). A Cloudflare login page means you still need the Access
  bypass. The ESP's local web page and serial log show the HTTP status of each report.
* **Debug logging:**
  ```yaml
  logger:
    logs:
      custom_components.tempem_ble: debug
  ```

## Development

```bash
python3.14 -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
pytest
ruff check custom_components tests
```
