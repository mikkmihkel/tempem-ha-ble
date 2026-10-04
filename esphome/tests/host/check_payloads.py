#!/usr/bin/env python3
"""Validate payload lines printed by test_forwarder_core against the contract.

Each stdin line: "<label>\\t<json>". Every payload must parse with json.loads
and match the webhook contract (v1) the Home Assistant integration expects.
"""

import json
import re
import sys

MAC_RE = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")
HEX_RE = re.compile(r"^([0-9a-f]{2})+$")


def check(label: str, p: dict) -> None:
    assert set(p) == {"v", "gateway", "advertisements", "batteries"}, p.keys()
    assert p["v"] == 1
    gw = p["gateway"]
    assert set(gw) <= {"mac", "name", "version", "uptime", "wifi_rssi", "free_heap"}, gw
    assert {"mac", "name", "version", "uptime", "free_heap"} <= set(gw), gw
    assert MAC_RE.match(gw["mac"]), gw["mac"]
    assert isinstance(gw["name"], str) and isinstance(gw["version"], str)
    assert isinstance(gw["uptime"], int) and gw["uptime"] >= 0
    assert isinstance(gw["free_heap"], int)
    if "wifi_rssi" in gw:
        assert isinstance(gw["wifi_rssi"], int) and gw["wifi_rssi"] < 0
    assert isinstance(p["advertisements"], list)
    seen = set()
    for a in p["advertisements"]:
        assert set(a) <= {"address", "rssi", "name", "manufacturer_data", "age"}, a
        assert MAC_RE.match(a["address"]), a
        assert a["address"] not in seen, "duplicate address"
        seen.add(a["address"])
        assert isinstance(a["rssi"], int)
        if "name" in a:
            assert a["name"] is None or isinstance(a["name"], str)
        md = a["manufacturer_data"]
        assert list(md) == ["89"], md
        assert HEX_RE.match(md["89"]), md
        assert isinstance(a["age"], (int, float)) and 0 <= a["age"] <= 600
    for b in p["batteries"]:
        assert set(b) == {"address", "level", "age"}, b
        assert MAC_RE.match(b["address"]), b
        assert isinstance(b["level"], int) and 0 <= b["level"] <= 255
        assert isinstance(b["age"], (int, float)) and b["age"] >= 0


def main() -> int:
    n = 0
    payloads = {}
    for line in sys.stdin:
        line = line.rstrip("\n")
        if not line:
            continue
        label, _, raw = line.partition("\t")
        try:
            p = json.loads(raw)
        except json.JSONDecodeError as err:
            print(f"FAIL {label}: invalid JSON ({err}): {raw!r}")
            return 1
        try:
            check(label, p)
        except AssertionError as err:
            print(f"FAIL {label}: contract violation {err!r}: {raw}")
            return 1
        payloads[label] = p
        n += 1

    # Spot checks on specific payloads.
    adv = {a["address"]: a for a in payloads["two_adverts_one_battery"]["advertisements"]}
    name = adv["C0:FF:EE:00:12:34"]["name"]
    assert name == 'Te"mp\\em\x01\n�é', repr(name)
    assert adv["C0:FF:EE:00:56:78"]["name"] == "TempemSens"
    assert adv["C0:FF:EE:00:12:34"]["manufacturer_data"] == {"89": "036e087b34"}
    assert adv["C0:FF:EE:00:12:34"]["rssi"] == -70
    assert payloads["two_adverts_one_battery"]["batteries"] == [
        {"address": "C0:FF:EE:00:12:34", "level": 87, "age": 3}
    ]
    assert "wifi_rssi" not in payloads["heartbeat_no_rssi"]["gateway"]
    assert payloads["two_adverts_one_battery"]["gateway"]["wifi_rssi"] == -61
    assert payloads["after_ack_heartbeat"]["advertisements"] == []
    assert payloads["after_ack_heartbeat"]["batteries"] == []
    names = [a.get("name") for a in payloads["bounded_names"]["advertisements"]]
    assert all(n is not None and len(n.encode()) <= 29 * 3 for n in names), names
    print("payload sample:", json.dumps(payloads["two_adverts_one_battery"], ensure_ascii=False))
    print(f"OK: {n} payloads are valid JSON and match the v1 contract")
    return 0


if __name__ == "__main__":
    sys.exit(main())
