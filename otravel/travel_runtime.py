"""Private, transient travel pacing and static hotel lookup memory.

No prices, rates, availability, credentials, model budgets, or persistence. This
does not coordinate other processes/accounts. The 30-second missing-header delay
is an engineering bound, not a supplier quota/reset promise.
"""
from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

TRAVEL_PROVIDERS = frozenset({"liteapi", "duffel", "serpapi", "open_meteo", "hotelbeds", "ratehawk", "travelpayouts"})


class TravelClockError(ValueError):
    pass


@dataclass(frozen=True)
class Cooldown:
    deadline: float
    source: str
    until_utc: str | None


@dataclass(frozen=True)
class HotelLookup:
    deadline: float
    requested_limit: int
    rows: list[dict]


class TravelRuntime:
    def __init__(self, clock: Callable[[], datetime] | None = None,
                 monotonic: Callable[[], float] | None = None,
                 lookup_ttl_seconds: int = 3600, default_cooldown_seconds: int = 30):
        self._clock = clock or (lambda: datetime.now(UTC))
        # Injected aware clocks also drive expiry unless a separate elapsed clock
        # is supplied. Real production TTLs use monotonic time, not wall jumps.
        self._elapsed = monotonic or (time.monotonic if clock is None else lambda: self.now().timestamp())
        if type(lookup_ttl_seconds) is not int or lookup_ttl_seconds <= 0:
            raise ValueError("Hotel lookup TTL must be a positive integer")
        if type(default_cooldown_seconds) is not int or default_cooldown_seconds <= 0:
            raise ValueError("Cooldown must be a positive integer")
        self.lookup_ttl_seconds = lookup_ttl_seconds
        self.default_cooldown_seconds = default_cooldown_seconds
        self.cooldowns: dict[str, Cooldown] = {}
        self.hotel_lookups: dict[tuple[str, str, str], HotelLookup] = {}
        self._lookup_locks: dict[tuple[str, str, str], asyncio.Lock] = {}

    def now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise TravelClockError("Travel clock must return an aware datetime")
        return value.astimezone(UTC)

    def elapsed(self) -> float:
        self.now()  # Do not silently accept a naive source clock.
        value = self._elapsed()
        if type(value) not in {int, float} or not math.isfinite(value):
            raise TravelClockError("Elapsed clock must return a finite value")
        return float(value)

    def retry_after(self, value) -> int | None:
        if type(value) in {int, float}:
            numeric = float(value)
        elif isinstance(value, str) and 0 < len(value) <= 128:
            try:
                numeric = float(value.strip())
            except ValueError:
                try:
                    instant = parsedate_to_datetime(value)
                    if instant.utcoffset() is None:
                        return None
                    numeric = (instant.astimezone(UTC) - self.now()).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    return None
        else:
            return None
        return math.ceil(numeric) if math.isfinite(numeric) and numeric > 0 else None

    def block(self, provider: str, delay=None, source: str = "provider_error") -> dict:
        if provider not in TRAVEL_PROVIDERS:
            raise ValueError("Unknown travel provider")
        seconds = self.retry_after(delay)
        if seconds is None:
            seconds, source = self.default_cooldown_seconds, "engineering_default"
        now, elapsed = self.now(), self.elapsed()
        try:
            until = (now + timedelta(seconds=seconds)).isoformat()
        except OverflowError:
            until = None
        prior = self.cooldowns.get(provider)
        deadline = elapsed + seconds
        if prior and prior.deadline > deadline:
            deadline, source, until = prior.deadline, prior.source, prior.until_utc
        self.cooldowns[provider] = Cooldown(deadline, source, until)
        return self.remaining(provider) or {}

    def remaining(self, provider: str) -> dict | None:
        elapsed = self.elapsed()
        saved = self.cooldowns.get(provider)
        if saved is None:
            return None
        seconds = math.ceil(saved.deadline - elapsed)
        if seconds <= 0:
            self.cooldowns.pop(provider, None)
            return None
        result = {"retry_after_seconds": seconds, "cooldown_source": saved.source,
                  "cooldown_scope": "private_process_provider", "vendor_reset_guaranteed": False}
        if saved.until_utc is not None:
            result["cooldown_until_utc"] = saved.until_utc
        return result

    @staticmethod
    def hotel_key(city: str) -> tuple[str, str, str]:
        # No credential, URL, guest, dates, price or other session data in a key.
        return "liteapi", "sandbox", city

    def lookup_lock(self, city: str) -> asyncio.Lock:
        return self._lookup_locks.setdefault(self.hotel_key(city), asyncio.Lock())

    def get_hotels(self, city: str, limit: int) -> list[dict] | None:
        elapsed = self.elapsed()
        key = self.hotel_key(city)
        saved = self.hotel_lookups.get(key)
        if saved is None:
            return None
        if saved.deadline <= elapsed:
            self.hotel_lookups.pop(key, None)
            return None
        if limit > saved.requested_limit:
            return None
        return deepcopy(saved.rows[:limit])

    def put_hotels(self, city: str, limit: int, rows: list[dict]) -> None:
        self.hotel_lookups[self.hotel_key(city)] = HotelLookup(
            self.elapsed() + self.lookup_ttl_seconds, limit, deepcopy(rows))
