"""Bound one explicit initial city route; no general NLU, tools, or mutation.

Only a missing first stay may be proposed. The caller gates speech act and model
conflicts before calling, then validates and commits the combined operation batch.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from .domain import DomainError, canonical_city
from .geography import known_cities


def _key(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").replace("—", "-")
                    .replace("–", "-").replace("‑", "-").split())


# Closed literal forms only. Validate their canonical destination through the
# domain registry; never infer a city from a country or approximate spelling.
_FORMS = {_key(name): canonical_city(name) for name in known_cities()}
for _city, _aliases in {
    "Москва": ("Москвы", "Москву", "Москве", "Мск", "Moscow"),
    "Санкт-Петербург": ("Санкт Петербург", "Санкт-Петербурга", "Санкт Петербурга", "Питер", "Питера", "СПб",
                        "Saint Petersburg", "St Petersburg", "St. Petersburg"),
    "Сочи": ("Sochi",), "Казань": ("Казани", "Kazan"),
    "Стамбул": ("Стамбула", "Стамбуле", "Istanbul"),
    "Анталья": ("Антальи", "Анталью", "Анталье", "Анталия", "Анталии", "Анталию", "Antalya"),
    "Дубай": ("Дубая", "Дубаи", "Дубае", "Dubai"),
    "Белград": ("Белграда", "Белграде", "Belgrade", "Beograd"),
    "Нови-Сад": ("Нови Сад", "Нови-Сада", "Нови Сада", "Novi Sad"),
    "Белгород": ("Белгорода", "Белгороде", "Belgorod"),
}.items():
    for _alias in _aliases:
        _FORMS[_key(_alias)] = canonical_city(_city)
_NAMES = "|".join(re.escape(name) for name in sorted(_FORMS, key=len, reverse=True))
_CITY = re.compile(rf"(?<![\w-])(?:{_NAMES})(?![\w-])")
_ROUTE = re.compile(rf"(?<![\w-])из\s+(?P<origin>{_NAMES})\s+в\s+(?P<destination>{_NAMES})(?![\w-])")
_NON_CURRENT = re.compile(
    r"\b(?:если|if|бы|например|допустим|предположим|представь|представим|возможно|наверное|может|раньше|прежде|вчера|ранее|"
    r"изначально|обсуждали|был|была|были|ездил\w*|ехал\w*|летал\w*|путешествовал\w*|прошл\w*|стар\w*)\b|\b(?:хотел\w*|мог\w*)\s+бы\b"
)
_ALTERNATIVE_ROUTE = re.compile(r"\b(?:или|либо|вместо|через|потом|затем|далее|дальше|обратно|возвращ\w*)\b|→|->|⇒")
_QUESTION = re.compile(r"\b(?:сколько|как|почему|зачем|куда|откуда|какой|какие|можно|можешь)\b")
_ROUTE_NEGATION = re.compile(
    r"\bне\s+(?:хоч\w*|хотим|планир\w*|собира\w*|буду|будем|надо|нуж\w*|делай|строй|"
    r"добавляй|добавлять|выбирай|выбирать|запоминай|сохраняй|ехать|еду|едем|поеду|поедем|лететь|лечу|летим|полетим|полететь|"
    r"отправляй|отправляться|маршрут|поездка)\b|\bбез\s+(?:поездки|маршрута|перелета)\b|\bотмен\w*\b"
)
_NO_HOTEL = re.compile(r"\bбез\s+(?:отел\w*|гостиниц\w*|проживан\w*)\b|\bтолько\s+(?:перелет\w*|авиабилет\w*|билет\w*)\b")
_OTHER_ROUTE_OPERATIONS = {"upsert_stay", "remove_stay", "upsert_leg", "remove_leg", "replace_hotel", "shift_return", "undo"}
_TARGET = "new-stay-1"


def _get(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _city(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return canonical_city(value)
    except DomainError:
        return None


def ground_initial_route(state: Any, operations: list, text: Any) -> tuple[list, list[dict]]:
    """Fill only an omitted city on an agreed, explicit initial single route.

    More than one directional clause, quoted/history/hypothetical text, an
    existing route operation, or disagreement with either origin is left alone.
    Missing dates/rooms/people stay missing. This never fixes other operations.
    """
    result = deepcopy(operations)
    stays = _get(state, "stays")
    if (not isinstance(result, list) or len(result) >= 30 or not isinstance(stays, list) or stays
            or _get(state, "legs", []) or not isinstance(text, str) or not 0 < len(text) <= 20_000):
        return result, []
    if any(_get(op, "type") in _OTHER_ROUTE_OPERATIONS for op in result):
        return result, []
    normalized = _key(text)
    if ("?" in normalized or any(mark in normalized for mark in ('"', "«", "»", "“", "”"))
            or _NON_CURRENT.search(normalized) or _ALTERNATIVE_ROUTE.search(normalized)
            or _QUESTION.search(normalized) or _NO_HOTEL.search(normalized)):
        return result, []
    matches = list(_ROUTE.finditer(normalized))
    if (len(matches) != 1 or len(re.findall(r"\bиз\b", normalized)) != 1
            or len(re.findall(r"\bв\b", normalized)) != 1):
        return result, []
    route = matches[0]
    if any(match.start() < route.start() or match.end() > route.end() for match in _CITY.finditer(normalized)):
        return result, []
    # Scope negation to the route clause, not the later 'детей пока не будет'.
    left = max(normalized.rfind(mark, 0, route.start()) for mark in ",;.!\n") + 1
    ends = [index for mark in ",;.!\n" if (index := normalized.find(mark, route.end())) >= 0]
    clause = normalized[left:min(ends) if ends else len(normalized)]
    prefix = normalized[left:route.start()]
    suffix = clause[route.end() - left:]
    if (_ROUTE_NEGATION.search(clause) or re.search(r"\bне\s*$", prefix)
            or re.fullmatch(r"\s*[-:]*\s*нет\s*", suffix)):
        return result, []
    origin, destination = _FORMS[route["origin"]], _FORMS[route["destination"]]
    existing_origin = _get(state, "origin")
    if existing_origin is not None and _city(existing_origin) != origin:
        return result, []
    proposed_origins = [op for op in result if _get(op, "type") == "set_origin"]
    if len(proposed_origins) > 1:
        return result, []
    if proposed_origins:
        value = _get(proposed_origins[0], "value")
        if not isinstance(value, dict) or set(value) != {"city"} or _city(value["city"]) != origin:
            return result, []
    elif not existing_origin:
        return result, []
    # Stable IDs must not alias an existing person/group/leg or model target.
    if (any(_get(item, "id") == _TARGET for name in ("travelers", "groups", "legs") for item in (_get(state, name, []) or []))
            or any(_get(op, "target_id") == _TARGET or _get(_get(op, "value", {}), "id") == _TARGET for op in result)):
        return result, []
    result.append({"type": "upsert_stay", "target_id": _TARGET, "value": {"city": destination}})
    return result, [{"field": "destination.city", "code": "initial_route_destination_grounded",
        "source": "explicit_initial_route", "before": None, "after": destination, "target_id": _TARGET}]
