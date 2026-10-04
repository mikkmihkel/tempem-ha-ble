"""Parser for Dusun/Tempem beacon advertisements.

Dusun beacons put their readings in manufacturer specific data under company
identifier 0x0059. After the company id the payload is:

    [sensor_type:1][fields...]

``sensor_type`` is a bitmask (bit0 temperature, bit1 humidity, bit2
accelerometer, bit3 geomagnetism, bit4 pressure, bit5 liquid level). Fields
follow in that order and are big-endian. See "Bluetooth beaconsensor data
format V1.2" from Dusun for the full specification.

This module has no Home Assistant dependencies so it can be unit tested on
its own.
"""

from __future__ import annotations

from dataclasses import dataclass

DUSUN_MANUFACTURER_ID = 0x0059

TYPE_TEMPERATURE = 0x01
TYPE_HUMIDITY = 0x02
_KNOWN_TYPE_BITS = 0x3F

_NAME_PREFIXES = ("tempem", "dusun")


@dataclass(frozen=True, slots=True)
class TempemReading:
    """A decoded advertisement."""

    temperature: float | None
    humidity: float | None


def _expected_length(sensor_type: int) -> int:
    """Return the minimum payload length (incl. type byte) for the type bits."""
    length = 1
    if sensor_type & TYPE_TEMPERATURE:
        length += 2
    if sensor_type & TYPE_HUMIDITY:
        length += 2
    return length


def parse_payload(payload: bytes) -> TempemReading | None:
    """Decode the manufacturer data payload (without the company id).

    Returns None if the payload does not look like a Dusun temperature /
    humidity beacon. Company id 0x0059 is shared by every Nordic based
    device, so the checks here are deliberately strict.
    """
    if not payload:
        return None
    sensor_type = payload[0]
    if sensor_type & ~_KNOWN_TYPE_BITS:
        return None
    if not sensor_type & (TYPE_TEMPERATURE | TYPE_HUMIDITY):
        return None
    needed = _expected_length(sensor_type)
    if len(payload) < needed:
        return None

    cursor = 1
    temperature: float | None = None
    humidity: float | None = None
    if sensor_type & TYPE_TEMPERATURE:
        raw = int.from_bytes(payload[cursor : cursor + 2], "big")
        temperature = round(raw * 175.72 / 65536 - 46.85, 2)
        cursor += 2
    if sensor_type & TYPE_HUMIDITY:
        raw = int.from_bytes(payload[cursor : cursor + 2], "big")
        humidity = round(raw * 125.0 / 65536 - 6.0, 1)
        cursor += 2

    # Out of range values mean we decoded some other vendor's data.
    if temperature is not None and not -40.0 <= temperature <= 125.0:
        return None
    if humidity is not None:
        humidity = min(max(humidity, 0.0), 100.0)
    return TempemReading(temperature=temperature, humidity=humidity)


def parse_manufacturer_data(
    manufacturer_data: dict[int, bytes],
) -> TempemReading | None:
    """Decode a Home Assistant style manufacturer data dict."""
    if (payload := manufacturer_data.get(DUSUN_MANUFACTURER_ID)) is None:
        return None
    return parse_payload(payload)


def is_canonical_payload(payload: bytes) -> bool:
    """Return True if the payload is exactly a temperature/humidity frame.

    Used together with the local name to decide whether a 0x0059 advertiser
    is worth offering for discovery.
    """
    if not payload or payload[0] & ~(TYPE_TEMPERATURE | TYPE_HUMIDITY):
        return False
    return len(payload) == _expected_length(payload[0])


def is_tempem_name(name: str | None) -> bool:
    """Return True if the advertised local name belongs to a Tempem/Dusun beacon."""
    return bool(name) and name.lower().startswith(_NAME_PREFIXES)


def short_address(address: str) -> str:
    """Return the last 4 hex chars of a MAC, e.g. 'C0:FF:EE:00:12:34' -> '1234'."""
    return address.replace(":", "").replace("-", "")[-4:].upper()


def is_supported_advertisement(
    manufacturer_data: dict[int, bytes], local_name: str | None
) -> bool:
    """Return True if an advertisement should be offered as a Tempem sensor."""
    if (payload := manufacturer_data.get(DUSUN_MANUFACTURER_ID)) is None:
        return False
    if parse_payload(payload) is None:
        return False
    return is_tempem_name(local_name) or is_canonical_payload(payload)
