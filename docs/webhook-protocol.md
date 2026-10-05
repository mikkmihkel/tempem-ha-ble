# Remote gateway webhook protocol (v1)

A remote gateway reports to Home Assistant with an HTTPS `POST` to the webhook URL shown when the gateway entry is
created:

```
POST https://<home-assistant>/api/webhook/<webhook_id>
Content-Type: application/json
```

The webhook id is the only credential, so keep it secret. Only `POST` is accepted. Bodies over 64 KiB are rejected.

## Body

```json
{
  "v": 1,
  "gateway": {
    "mac": "AA:BB:CC:DD:EE:FF",
    "name": "tempem-remote-ddeeff",
    "version": "0.2.0",
    "uptime": 12345,
    "wifi_rssi": -61,
    "free_heap": 123456
  },
  "advertisements": [
    {
      "address": "C0:FF:EE:00:12:34",
      "rssi": -70,
      "name": "TempemSens",
      "manufacturer_data": { "89": "036e087b34" },
      "age": 3.2
    }
  ],
  "batteries": [
    { "address": "C0:FF:EE:00:12:34", "level": 87, "age": 120 }
  ]
}
```

| Field | Type | Meaning |
|---|---|---|
| `v` | int | Protocol version, `1` |
| `gateway.*` | object | Optional, all fields optional. Shown on the gateway's diagnostic entities. `mac` is added to the device's connections, and `version` becomes its firmware version |
| `advertisements[]` | array, ≤ 256 | Latest advertisement per beacon since the previous successful report. A gateway can report at most 1024 distinct addresses per 15 minutes; further new addresses are skipped |
| `address` | string | Beacon MAC, `AA:BB:CC:DD:EE:FF` |
| `rssi` | int | dBm, −127…20 |
| `name` | string \| null | Local name, if the gateway has seen one (often in the scan response). ≤ 64 characters |
| `manufacturer_data` | object, ≤ 8 entries | Key: company id as a **decimal** string. Value: hex payload **after** the 2 company id bytes (≤ 62 bytes) |
| `age` | number | Seconds since the gateway received this advertisement (≥ 0). Entries older than 15 min are ignored |
| `batteries[]` | array, ≤ 256 | Battery levels read over GATT by the gateway |
| `level` | int | 0…100 % |
| `age` | number | Seconds since the reading was taken (≥ 0). Entries older than 7 days are ignored, as is a reading older than the last one received for that beacon |

Each advertisement is handed to Home Assistant's Bluetooth stack as if a (non-connectable) Bluetooth adapter at the
remote site had received it. Only the Dusun manufacturer data (company id `89` / `0x0059`) and the name are passed on.
Other company ids are dropped, and an advertisement without `89` data is skipped. That way someone who learns the webhook URL
cannot fake advertisements for other Bluetooth integrations. Battery entries are routed to the matching Tempem
sensor, if one is configured.

## Responses

| Status | Body | Meaning |
|---|---|---|
| 200 | `{"ok": true, "accepted": <n>}` | Processed. `n` = advertisements accepted. Invalid single entries are skipped, not fatal |
| 400 | `{"ok": false, "error": "invalid payload"}` | Not JSON, or the top-level structure or a `gateway` field is wrong. Numbers must be finite: `NaN`/`Infinity` in `gateway` rejects the report, in an entry skips that entry |
| 405 | — | Wrong method |
| 413 | `{"ok": false, "error": "payload too large"}` | Body over 64 KiB |

An unknown webhook id also returns 200 with an empty body. That's how Home Assistant hides which ids exist, so a gateway
should treat only `"ok": true` in the body as a confirmed delivery if it needs to be sure.

## Example

```bash
curl -sS -X POST "https://ha.example.com/api/webhook/<id>" \
  -H 'Content-Type: application/json' \
  -d '{"v":1,"advertisements":[{"address":"C0:FF:EE:00:12:34","rssi":-70,"manufacturer_data":{"89":"036e087b34"},"age":0}]}'
```
