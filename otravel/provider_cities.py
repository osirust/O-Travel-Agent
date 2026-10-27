"""Bounded search identities, separate from DEMO inventory and availability.

Belgrade's LiteAPI label/hotel rates and Duffel RS/BEG place identity were read
on 2026-10-27. This permits requests, never promises current rooms or flights.
No credentials, HTTP, TripState mutation or synthetic eighth-city catalogue.
"""
from __future__ import annotations

from .domain import CITIES

_LITEAPI = {
    "Москва": ("RU", "Moscow"), "Санкт-Петербург": ("RU", "St. Petersburg"),
    "Сочи": ("RU", "Sochi"), "Казань": ("RU", "Kazan"),
    "Стамбул": ("TR", "Istanbul"), "Анталья": ("TR", "Antalya"), "Дубай": ("AE", "Dubai"),
    "Белград": ("RS", "Belgrade"),
}
_IATA = {"Москва": "MOW", "Санкт-Петербург": "LED", "Сочи": "AER", "Казань": "KZN",
    "Стамбул": "IST", "Анталья": "AYT", "Дубай": "DXB", "Белград": "BEG"}


def _get(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def available_search_cities(state) -> tuple[str, ...]:
    """Return configured request capabilities, not provider availability.

    Canonical modes are DEMO/HYBRID. The eighth identity is qualified only for
    the specific LiteAPI + Duffel HYBRID path. Other policies keep the original
    seven-city scope; unknown/malformed policy values do not broaden it.
    """
    policy = _get(state, "search_policy")
    if (_get(policy, "mode") == "HYBRID" and _get(policy, "hotel_provider") == "liteapi"
            and _get(policy, "flight_provider") == "duffel"):
        return (*CITIES, "Белград")
    return tuple(CITIES)


def liteapi_city_query(city: str) -> tuple[str, str] | None:
    """Exact provider country/name pair for an already canonical city."""
    return _LITEAPI.get(city) if isinstance(city, str) else None


def flight_iata(city: str) -> str | None:
    """Verified location identity only; availability still requires a search."""
    return _IATA.get(city) if isinstance(city, str) else None
