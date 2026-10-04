"""Tests for the advertisement parser (no Home Assistant needed)."""

from __future__ import annotations

import pytest

from . import PDF_PAYLOAD
from custom_components.tempem_ble.parser import (
    DUSUN_MANUFACTURER_ID,
    TempemReading,
    is_canonical_payload,
    is_supported_advertisement,
    is_tempem_name,
    parse_manufacturer_data,
    parse_payload,
    short_address,
)


def test_pdf_example() -> None:
    """The example from the Dusun spec decodes to 28.68 C / 54.2 %."""
    assert parse_payload(PDF_PAYLOAD) == TempemReading(temperature=28.68, humidity=54.2)
    assert parse_manufacturer_data({DUSUN_MANUFACTURER_ID: PDF_PAYLOAD}) == (
        TempemReading(temperature=28.68, humidity=54.2)
    )


def test_manufacturer_id() -> None:
    """Only company id 0x0059 is decoded."""
    assert DUSUN_MANUFACTURER_ID == 89
    assert parse_manufacturer_data({0x004C: PDF_PAYLOAD}) is None
    assert parse_manufacturer_data({}) is None


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (bytes.fromhex("016e08"), TempemReading(temperature=28.68, humidity=None)),
        (bytes.fromhex("027b34"), TempemReading(temperature=None, humidity=54.2)),
        # Extra fields after temperature/humidity (e.g. accelerometer) are fine.
        (
            bytes.fromhex("076e087b34000100020003"),
            TempemReading(temperature=28.68, humidity=54.2),
        ),
        # Humidity is clamped to 0..100.
        (bytes.fromhex("02ffff"), TempemReading(temperature=None, humidity=100.0)),
        (bytes.fromhex("020000"), TempemReading(temperature=None, humidity=0.0)),
    ],
)
def test_valid_payloads(payload: bytes, expected: TempemReading) -> None:
    """Type bits select which fields follow."""
    assert parse_payload(payload) == expected


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        # Unknown type bits (bit 6/7): some other Nordic based device.
        bytes.fromhex("436e087b34"),
        bytes.fromhex("806e087b34"),
        # No temperature or humidity bit.
        bytes.fromhex("04000100020003"),
        bytes.fromhex("00"),
        # Too short for the announced fields.
        bytes.fromhex("03"),
        bytes.fromhex("036e08"),
        bytes.fromhex("036e087b"),
        bytes.fromhex("016e"),
        # Temperature out of the sensor's range (128.87 C / -46.85 C).
        bytes.fromhex("01ffff"),
        bytes.fromhex("010000"),
    ],
)
def test_rejected_payloads(payload: bytes) -> None:
    """Payloads that are not Dusun temperature/humidity frames are rejected."""
    assert parse_payload(payload) is None


def test_canonical_payload() -> None:
    """Exact temperature/humidity frames are canonical."""
    assert is_canonical_payload(PDF_PAYLOAD)
    assert is_canonical_payload(bytes.fromhex("016e08"))
    assert not is_canonical_payload(PDF_PAYLOAD + b"\x00")
    assert not is_canonical_payload(bytes.fromhex("076e087b34000100020003"))
    assert not is_canonical_payload(b"")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("TempemSens", True),
        ("tempem", True),
        ("DUSUN-T1", True),
        ("Nordic_UART", False),
        ("", False),
        (None, False),
    ],
)
def test_is_tempem_name(name: str | None, expected: bool) -> None:
    """Local names of Tempem/Dusun beacons are recognised."""
    assert is_tempem_name(name) is expected


def test_short_address() -> None:
    """The last 4 hex digits identify a beacon."""
    assert short_address("C0:FF:EE:00:12:34") == "1234"
    assert short_address("c0-ff-ee-00-12-34") == "1234"


def test_is_supported_advertisement() -> None:
    """Discovery needs a valid payload and either the name or an exact frame."""
    data = {DUSUN_MANUFACTURER_ID: PDF_PAYLOAD}
    assert is_supported_advertisement(data, "TempemSens")
    assert is_supported_advertisement(data, None)
    assert is_supported_advertisement(data, "C0:FF:EE:00:12:34")
    longer = {DUSUN_MANUFACTURER_ID: PDF_PAYLOAD + b"\x00\x01"}
    assert is_supported_advertisement(longer, "TempemSens")
    assert not is_supported_advertisement(longer, "Some nRF52 thing")
    assert not is_supported_advertisement(
        {DUSUN_MANUFACTURER_ID: bytes.fromhex("436e087b34")}, "TempemSens"
    )
    assert not is_supported_advertisement({0x004C: PDF_PAYLOAD}, "TempemSens")
    # Nordic's nRF beacons use 0x0059 with an iBeacon layout (0x02 0x15 ...).
    # The first bytes happen to decode as a humidity frame, but the length
    # gives it away, so it is not offered for discovery.
    ibeacon = {
        DUSUN_MANUFACTURER_ID: bytes.fromhex(
            "0215e2c56db5dffb48d2b060d0f5a71096e000010002c5"
        )
    }
    assert not is_supported_advertisement(ibeacon, None)
    assert not is_supported_advertisement(ibeacon, "nRF Beacon")
