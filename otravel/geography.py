"""Small curated geography registry, independent of inventory/search coverage.

This is deliberately not a general natural-language parser or a world gazetteer.
An unknown or ambiguous name requires clarification; no city is guessed from a
country. ``available`` means membership in the caller's supported city list,
never hotel/flight availability or permission to book.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Any


_COUNTRIES = {
    "RS": ("Сербия", "Serbia", "Сербию", "Сербии", "Сербией", "Republic of Serbia"),
    "RU": ("Россия", "Russia", "России", "Россию", "Россией", "РФ", "Russian Federation", "Российская Федерация", "Российскую Федерацию"),
    "TR": ("Турция", "Turkey", "Türkiye", "Turkiye", "Турцию", "Турции", "Турцией", "Republic of Türkiye"),
    "AE": ("ОАЭ", "UAE", "U.A.E.", "United Arab Emirates", "Эмираты", "Эмиратах", "Объединенные Арабские Эмираты", "Объединенных Арабских Эмиратах"),
    "BY": ("Беларусь", "Belarus", "Белоруссия", "Беларуси", "Белоруссию", "Белоруссии"),
    "KZ": ("Казахстан", "Kazakhstan", "Казахстане"),
    "GE": ("Грузия", "Грузию", "Грузии", "Georgia"),
    "AM": ("Армения", "Armenia", "Армению", "Армении"),
    "AZ": ("Азербайджан", "Azerbaijan", "Азербайджане"),
    "UZ": ("Узбекистан", "Uzbekistan", "Узбекистане"),
    "ME": ("Черногория", "Montenegro", "Черногорию", "Черногории"),
    "CY": ("Кипр", "Cyprus", "Кипре"),
    "TH": ("Таиланд", "Thailand", "Тайланд", "Таиланде", "Тайланде"),
    "EG": ("Египет", "Egypt", "Египте"),
    "CN": ("Китай", "China", "Китае"),
    "JP": ("Япония", "Japan", "Японию", "Японии"),
    "IN": ("Индия", "India", "Индию", "Индии"),
    "ID": ("Индонезия", "Indonesia", "Индонезию", "Индонезии"),
    "VN": ("Вьетнам", "Vietnam", "Вьетнаме"),
    "MV": ("Мальдивы", "Maldives", "Мальдивах"),
    "LK": ("Шри-Ланка", "Sri Lanka", "Шри Ланка", "Шри-Ланку", "Шри-Ланке"),
    "IT": ("Италия", "Italy", "Италию", "Италии"),
    "FR": ("Франция", "France", "Францию", "Франции"),
    "ES": ("Испания", "Spain", "Испанию", "Испании"),
    "DE": ("Германия", "Germany", "Германию", "Германии"),
    "GR": ("Греция", "Greece", "Грецию", "Греции"),
    "GB": ("Великобритания", "United Kingdom", "UK", "Великобританию", "Великобритании", "Great Britain"),
    "US": ("США", "USA", "U.S.A.", "United States", "United States of America", "Соединенные Штаты", "Соединенные Штаты Америки"),
}
_CITIES = {
    "Москва": ("RU", ("Moscow", "Москву", "Москве", "Мск")),
    "Санкт-Петербург": ("RU", ("Saint Petersburg", "St Petersburg", "St. Petersburg", "Санкт Петербург", "Санкт-Петербурге", "Питер", "Питере", "СПб")),
    "Сочи": ("RU", ("Sochi",)),
    "Казань": ("RU", ("Kazan", "Казани")),
    "Стамбул": ("TR", ("Istanbul", "Стамбуле")),
    "Анталья": ("TR", ("Antalya", "Antalia", "Анталия", "Анталии", "Анталию", "Анталью", "Анталье")),
    "Дубай": ("AE", ("Dubai", "Дубаи", "Дубае")),
    "Белград": ("RS", ("Belgrade", "Beograd", "Белграде")),
    "Нови-Сад": ("RS", ("Novi Sad", "Нови Сад", "Нови-Саде", "Новом Саде")),
    "Белгород": ("RU", ("Belgorod", "Белгороде")),
}
_AMBIGUOUS = {
    "georgia": "Вы имеете в виду страну Грузию или штат Джорджия в США? Уточните город.",
    "джорджия": "Вы имеете в виду страну Грузию или штат Джорджия в США? Уточните город.",
    "congo": "Уточните страну: Республика Конго или Демократическая Республика Конго? Какой город?",
    "конго": "Уточните страну: Республика Конго или Демократическая Республика Конго? Какой город?",
    "korea": "Уточните страну и город: Южная или Северная Корея?",
    "корея": "Уточните страну и город: Южная или Северная Корея?",
    "america": "Уточните страну и город: что вы имеете в виду под Америкой?",
    "америка": "Уточните страну и город: что вы имеете в виду под Америкой?",
}


def _key(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    value = value.replace("–", "-").replace("—", "-").replace("‑", "-")
    return " ".join(value.split()).strip(" \t\n\r.!?,;:\"'«»“”")


_COUNTRY_ALIASES = {_key(alias): code for code, aliases in _COUNTRIES.items() for alias in (*aliases, code)}
_CITY_ALIASES = {_key(alias): name for name, (_, aliases) in _CITIES.items() for alias in (name, *aliases)}


def known_cities() -> tuple[str, ...]:
    """Curated geographic names, independent of supported search inventory."""
    return tuple(_CITIES)


_PREFIX = re.compile(
    r"^(?:(?:я |мы )?(?:хочу|хотим)(?: поехать| полететь| отправиться)? (?:в|во) |"
    r"(?:поехать|полететь|еду|едем|летим) (?:в|во) |(?:в|во) |"
    r"(?:i want to |we want to |i would like to )?(?:travel to|go to|visit) |(?:to|in) )"
)
_SUFFIX = re.compile(r" (?:может быть )?(?:хочу|хотим|поехать|полететь|пожалуйста|please)$")


def _candidate(value: str) -> str:
    """Remove only bounded request wrappers, never arbitrary context/negation."""
    key = _key(value)
    key = _PREFIX.sub("", key, count=1)
    return _key(_SUFFIX.sub("", key, count=1))


def _supported(available_cities: Iterable[str] | None) -> set[str]:
    if available_cities is None:
        # Lazy to permit domain to import the pure parser without an import cycle.
        from .domain import CITIES

        available_cities = CITIES
    return {_CITY_ALIASES.get(_key(city), city) for city in available_cities}


def parse_destination(value: str, *, available_cities: Iterable[str] | None = None) -> dict[str, Any]:
    """Return ``requested_destination`` and a clarification, without search calls.

    Country suggestions are known cities, not invented search coverage. The small
    registry intentionally returns ``unknown`` for unregistered names; this is not
    a claim that such a place does not exist. Inputs can be bare names or simple
    travel phrases such as ``в сербию хочу``. Compound requests remain unknown.
    """
    if not isinstance(value, str):
        raise TypeError("Название направления должно быть строкой.")
    name = value.strip()
    key = _candidate(name)
    if key in _AMBIGUOUS:
        return {"requested_destination": {"kind": "ambiguous", "name": name, "country_code": None},
                "clarification": _AMBIGUOUS[key]}
    if key in _COUNTRY_ALIASES:
        code = _COUNTRY_ALIASES[key]
        supported = _supported(available_cities)
        suggestions = [{"name": city, "country_code": code, "available": city in supported}
                       for city, (city_code, _) in _CITIES.items() if city_code == code]
        destination = {"kind": "country", "name": _COUNTRIES[code][0], "country_code": code}
        if suggestions:
            destination["suggested_cities"] = suggestions
        choices = " или ".join(item["name"] for item in suggestions[:2])
        question = f"Понял: {_COUNTRIES[code][0]}. Какой город выбираем" + (f" — {choices}?" if choices else "?")
        return {"requested_destination": destination, "clarification": question}
    if key in _CITY_ALIASES:
        city = _CITY_ALIASES[key]
        return {"requested_destination": {"kind": "city", "name": city, "country_code": _CITIES[city][0]},
                "clarification": None}
    return {"requested_destination": {"kind": "unknown", "name": name, "country_code": None},
            "clarification": (f"Уточните страну и город назначения: «{name}» не удалось однозначно распознать."
                              if name else "В какую страну и какой город вы хотите поехать?")}


normalize_destination = parse_destination
