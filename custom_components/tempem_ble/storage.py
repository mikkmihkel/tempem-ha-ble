"""Remembers when each beacon's battery was last read, and what it read.

Kept outside the config entry so that a Home Assistant restart does not cause
every beacon to be connected to again straight away, and so that a battery
level reported by a remote gateway (only once per weekly poll) is not lost
when the sensor entry is added after that report.
"""

from __future__ import annotations

import time
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN, REMOTE_BATTERY_MAX_AGE_SECONDS, STORAGE_KEY, STORAGE_VERSION

_SAVE_DELAY = 10
# Remote battery levels kept at most. The addresses come from the webhook, so
# the cache must not grow without bound.
MAX_REMOTE_BATTERIES = 512

POLL_STORE: HassKey[BatteryPollStore] = HassKey(DOMAIN)


class BatteryPollStore:
    """Persisted battery bookkeeping per beacon address.

    * ``polled``: address -> unix time of the last local battery read.
    * ``remote``: address -> [level, unix time] last reported by a gateway.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the store."""
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._polled: dict[str, float] = {}
        self._remote: dict[str, list[float]] = {}

    async def async_load(self) -> None:
        """Load data from disk."""
        data = await self._store.async_load() or {}
        if "polled" in data or "remote" in data:
            self._polled = dict(data.get("polled") or {})
            self._remote = dict(data.get("remote") or {})
        else:
            # First format: a flat address -> timestamp map.
            self._polled = {
                key: value
                for key, value in data.items()
                if isinstance(value, (int, float))
            }

    @callback
    def _data_to_save(self) -> dict[str, Any]:
        return {"polled": self._polled, "remote": self._remote}

    @callback
    def _async_schedule_save(self) -> None:
        self._store.async_delay_save(self._data_to_save, _SAVE_DELAY)

    def last_polled(self, address: str) -> float | None:
        """Return when the battery was last read, locally or by a gateway."""
        address = address.upper()
        times = [self._polled.get(address)]
        if (remote := self._remote.get(address)) is not None:
            times.append(remote[1])
        return max((ts for ts in times if ts is not None), default=None)

    def last_local_poll(self, address: str) -> float | None:
        """Return when the battery was last read over GATT by Home Assistant."""
        return self._polled.get(address.upper())

    def remote_battery(self, address: str) -> tuple[int, float] | None:
        """Return the last (level, unix time) reported by a remote gateway."""
        if (remote := self._remote.get(address.upper())) is None:
            return None
        return int(remote[0]), float(remote[1])

    @callback
    def async_mark_polled(self, address: str) -> None:
        """Record a successful battery read."""
        self._polled[address.upper()] = time.time()
        self._async_schedule_save()

    @callback
    def async_set_remote_battery(
        self, address: str, level: int, timestamp: float | None = None
    ) -> bool:
        """Remember a battery level reported by a remote gateway.

        Returns False if a newer level is already known.
        """
        if timestamp is None:
            timestamp = time.time()
        address = address.upper()
        if (old := self._remote.get(address)) is not None and old[1] > timestamp:
            return False
        remote = self._remote
        remote[address] = [level, timestamp]
        if len(remote) > MAX_REMOTE_BATTERIES:
            # Drop levels too old to be shown, then the oldest ones.
            cutoff = time.time() - REMOTE_BATTERY_MAX_AGE_SECONDS
            for addr in [a for a, (_, ts) in remote.items() if ts < cutoff]:
                del remote[addr]
            by_age = sorted(remote, key=lambda addr: remote[addr][1])
            for addr in by_age[: len(remote) - MAX_REMOTE_BATTERIES]:
                del remote[addr]
        self._async_schedule_save()
        return True

    @callback
    def async_forget(self, address: str) -> None:
        """Drop the poll bookkeeping of an address (entry removed).

        A level reported by a gateway is kept: it is still valid if the
        sensor is added again.
        """
        if self._polled.pop(address.upper(), None) is not None:
            self._async_schedule_save()
