"""Narrow protection for deletion of the only stay after an alias question.

No general stay-removal policy. Does not choose duplicate targets, modify
rosters/rooms, create geographic identities, or authorize typed operations.
"""
from __future__ import annotations

import re
from copy import deepcopy

from .domain import DomainError, canonical_city
from .geography import parse_destination
from .operation_aliases import _without_quoted_spans
from .place_refs import CityRefSnapshot
from .route_intake import _FORMS

FINDING_ID = "HARNESS-004-ALIAS-QUESTION-STAY-REMOVAL-001"
_NAMES = "|".join(re.escape(x) for x in sorted(_FORMS, key=len, reverse=True))
_PAIR = re.compile(rf"(?<![\w-])(?P<a>{_NAMES})\s+и\s+(?P<b>{_NAMES})(?![\w-])(?P<tail>[^?;\n]{{0,100}})\?")
_SAME = re.compile(r"\b(?:один(?:\s+и\s+тот\s+же)?|тот\s+же)\s+город\b")
_TWO_UNWANTED = re.compile(r"\b(?:две|2)\s+остановк[иа]\s+не\s+нужн[ыа]\b|"
    r"\bне\s+нужн[ыа]\s+(?:две|2)\s+остановк[иа]\b")
_REMOVE = re.compile(r"\b(?:удали(?:те)?|убери(?:те)?|исключи(?:те)?|вычеркни(?:те)?|"
    r"отмени(?:те)?|delete|remove|drop)\b")
_STAY_OBJECT = re.compile(r"\s+(?:(?:эту|ту|мою|единственную|одну|всю|все)\s+){0,2}"
    r"(?:остановк\w*|проживан\w*|маршрут\w*|поездк\w*|stay\b)")
_NONCURRENT = re.compile(r"\b(?:если|бы|возможно|например|допустим|раньше|прежде|цитат\w*|"
    r"истори\w*|совет\w*|говорят|сказали)\b")
_REPLACE_OBJECT = re.compile(r"\b(?:замени|замените|поменяй|измени|перестрой)\s+"
    r"(?:(?:весь|всю|мой|наш|эту|этот|единственную|единственный)\s+){0,2}"
    r"(?:маршрут\w*|поездк\w*|остановк\w*|направлен\w*)\b")
_REPLACE_CITY = re.compile(rf"\b(?:замени|замените|поменяй)\s+(?P<old>{_NAMES})\s+"
    rf"на\s+(?P<new>{_NAMES})(?![\w-])")


def _key(text):
    return " ".join(text.casefold().replace("ё", "е").split())


def _city(value):
    try:
        return canonical_city(value) if isinstance(value, str) else None
    except DomainError:
        return None


def _current_seed_identity(data, stay):
    city = _city(stay.get("city"))
    if city is None:
        return None
    place_id = stay.get("place_id")
    if place_id is None:
        return city
    try:
        ref = CityRefSnapshot.model_validate((data.get("location_refs") or {}).get(place_id))
    except (ValueError, TypeError):
        return None
    expected = parse_destination(city)["requested_destination"]
    if (ref.place_id != place_id or ref.canonical_name != stay.get("city")
            or expected.get("country_code") != ref.country_iso2):
        return None
    return city


def _explicit_positive_removal_or_replacement(text, city):
    unquoted = _without_quoted_spans(re.sub(r"\x60[^\x60]*\x60", " ", text))
    if unquoted is None:
        return False
    for piece in re.finditer(r"[^.!?;\n]+(?:[.!?;\n]|$)", unquoted):
        clause = piece.group().strip()
        if not clause or clause.endswith("?") or _NONCURRENT.search(clause):
            continue
        replacements = [*_REPLACE_OBJECT.finditer(clause),
                        *[m for m in _REPLACE_CITY.finditer(clause) if _FORMS[m["old"]] == city]]
        if any(not re.search(r"\bне\s+(?:(?:надо|нужно)\s+)?$", clause[:m.start()])
                for m in replacements):
            return True
        for action in _REMOVE.finditer(clause):
            if re.search(r"\bне\s+(?:(?:надо|нужно)\s+)?$", clause[:action.start()]):
                continue
            tail = clause[action.end():]
            if _STAY_OBJECT.match(tail):
                return True
            match = re.match(rf"\s+(?P<city>{_NAMES})(?![\w-])", tail)
            if match and _FORMS[match["city"]] == city:
                return True
    return False


def guard_alias_stay_removal_operations(before_state, operations, text):
    data = before_state.model_dump(mode="json") if hasattr(before_state, "model_dump") else before_state
    copied = [op.model_dump(mode="json") if hasattr(op, "model_dump") else deepcopy(op) for op in operations]
    meta = {"notes": []}
    if not isinstance(data, dict) or not isinstance(text, str) or not 0 < len(text) <= 20_000:
        return copied, meta
    stays = data.get("stays")
    if not isinstance(stays, list) or len(stays) != 1 or not isinstance(stays[0], dict):
        return copied, meta
    stay = stays[0]
    target = stay.get("id")
    if not isinstance(target, str) or not target:
        return copied, meta
    city = _current_seed_identity(data, stay)
    if city is None:
        return copied, meta
    normalized = _key(text)
    pair = any(_FORMS[m["a"]] == _FORMS[m["b"]] == city and m["a"] != m["b"]
        and _SAME.search(m["tail"]) for m in _PAIR.finditer(normalized))
    if (not pair or not _TWO_UNWANTED.search(normalized)
            or _explicit_positive_removal_or_replacement(normalized, city)):
        return copied, meta
    result = []
    removed = 0
    for op in copied:
        if isinstance(op, dict) and op.get("type") == "remove_stay" and op.get("target_id") == target:
            removed += 1
        else:
            result.append(op)
    if removed:
        meta["notes"].append({"code": "alias_question_single_stay_removal_suppressed",
            "finding_id": FINDING_ID, "target_id": target, "city": stay["city"],
            "preserved_stay_count": 1, "suppressed_operations": removed})
    return result, meta
