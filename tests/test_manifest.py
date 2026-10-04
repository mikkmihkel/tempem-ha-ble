"""Static checks similar to hassfest."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.tempem_ble.binary_sensor import TempemGatewayConnectivity
from custom_components.tempem_ble.sensor import GATEWAY_SENSORS

COMPONENT = Path(__file__).parent.parent / "custom_components" / "tempem_ble"


def _load(name: str) -> dict:
    return json.loads((COMPONENT / name).read_text(encoding="utf-8"))


def test_manifest() -> None:
    """Keys are sorted like hassfest wants and required keys exist."""
    manifest = _load("manifest.json")
    keys = list(manifest)
    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])
    for key in (
        "codeowners",
        "config_flow",
        "documentation",
        "integration_type",
        "iot_class",
        "issue_tracker",
        "requirements",
        "version",
    ):
        assert key in manifest, key
    assert manifest["domain"] == "tempem_ble"
    assert manifest["config_flow"] is True
    # Integrations with Bluetooth matchers must depend on bluetooth_adapters.
    assert "bluetooth_adapters" in manifest["dependencies"]
    assert "webhook" in manifest["dependencies"]
    hacs = json.loads((COMPONENT.parent.parent / "hacs.json").read_text())
    assert hacs["name"] == manifest["name"]


def test_translations_match_strings() -> None:
    """translations/en.json is a copy of strings.json."""
    assert _load("strings.json") == _load("translations/en.json")


def test_translation_keys_exist() -> None:
    """Every step, abort reason and entity translation key is translated."""
    strings = _load("strings.json")
    config = strings["config"]
    for step in ("user", "bluetooth_confirm", "pick_sensor", "gateway", "gateway_url"):
        assert step in config["step"], step
    assert set(config["step"]["user"]["menu_options"]) == {"pick_sensor", "gateway"}
    for reason in (
        "already_configured",
        "already_in_progress",
        "no_devices_found",
        "not_supported",
    ):
        assert reason in config["abort"], reason
    assert "{name}" in config["flow_title"]
    assert "{webhook_url}" in config["step"]["gateway_url"]["description"]
    options = strings["options"]["step"]
    assert "battery_poll_hours" in options["init"]["data"]
    assert "{webhook_url}" in options["gateway"]["description"]

    sensors = strings["entity"]["sensor"]
    for description in GATEWAY_SENSORS:
        assert description.translation_key in sensors, description.key
    assert "connectivity" in strings["entity"]["binary_sensor"]
    assert TempemGatewayConnectivity  # translation key "connectivity"
