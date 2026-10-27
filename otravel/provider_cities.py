"""Bounded search identities, separate from DEMO inventory and availability.

Belgrade's LiteAPI label/hotel rates and Duffel RS/BEG place identity were read
on 2026-10-27. This permits requests, never promises current rooms or flights.
No credentials, HTTP, TripState mutation or synthetic eighth-city catalogue.
"""
from __future__ import annotations

import math
import re
import unicodedata

from .domain import CITIES, DomainError, canonical_city
from .place_refs import CityRefSnapshot

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
        base = (*CITIES, "Белград")
    else:
        base = tuple(CITIES)
    if _get(policy, "mode") != "HYBRID":
        return base
    refs = _get(state, "location_refs", {})
    if not isinstance(refs, dict):
        return base
    names = []
    for key, value in refs.items():
        try:
            ref = CityRefSnapshot.model_validate(value)
        except ValueError:
            continue
        if key == ref.place_id:
            names.append(ref.canonical_name)
    return tuple(dict.fromkeys([*base, *names]))


def liteapi_city_query(city: str) -> tuple[str, str] | None:
    """Exact provider country/name pair for an already canonical city."""
    return _LITEAPI.get(city) if isinstance(city, str) else None


def flight_iata(city: str) -> str | None:
    """Verified location identity only; availability still requires a search."""
    return _IATA.get(city) if isinstance(city, str) else None


# This is a consistency radius for comparing provider metadata to an already
# selected city point. It is not a city boundary or an availability promise.
DYNAMIC_CITY_CONSISTENCY_KM = 50.0


def place_city_key(value: str) -> str:
    if not isinstance(value, str):
        return ""
    text = "".join(char for char in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(char))
    return " ".join(re.findall(r"[^\W_]+", text))


def private_place_ref(request: dict) -> CityRefSnapshot:
    if not isinstance(request, dict) or "place_ref" not in request:
        raise ValueError("A server-owned place_ref is required.")
    ref = CityRefSnapshot.model_validate(request["place_ref"])
    if "city" in request and request["city"] != ref.canonical_name:
        raise ValueError("The display city does not match its selected place_ref.")
    return ref


def place_city_aliases(ref: CityRefSnapshot) -> set[str]:
    result = {place_city_key(ref.canonical_name)}
    try:
        legacy = canonical_city(ref.canonical_name)
    except DomainError:
        legacy = None
    pair = _LITEAPI.get(legacy)
    if pair is not None and pair[0] == ref.country_iso2:
        result.update((place_city_key(legacy), place_city_key(pair[1])))
        if legacy == "Санкт-Петербург":
            result.add(place_city_key("Saint Petersburg"))
    return result


def place_coordinates_match(ref: CityRefSnapshot, latitude, longitude) -> bool:
    if (type(latitude) not in {int, float} or type(longitude) not in {int, float}
            or not math.isfinite(latitude) or not math.isfinite(longitude)
            or not -90 <= latitude <= 90 or not -180 <= longitude <= 180):
        return False
    lat1, lat2 = math.radians(ref.latitude), math.radians(latitude)
    delta_lat = lat2 - lat1
    delta_lon = math.radians(longitude - ref.longitude)
    term = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    km = 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(max(0.0, term))))
    return km <= DYNAMIC_CITY_CONSISTENCY_KM


def place_metadata_matches(ref: CityRefSnapshot, *, country, city, latitude, longitude) -> bool:
    return (country == ref.country_iso2 and place_city_key(city) in place_city_aliases(ref)
        and place_coordinates_match(ref, latitude, longitude))
