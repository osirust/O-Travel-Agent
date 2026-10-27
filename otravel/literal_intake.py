"""Recover omitted, explicit facts in a bounded initial lodging declaration.

This is a coverage fallback for proposed operations, not a replacement model or
geocoder. It never chooses a destination, deletes people, or replaces saved facts.
"""
from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

from .country_intake import _literal_adults, _literal_budget
from .date_slots import _DOT, _DURATION, _DURATION_COUNTS, _ISO, _MONTH, _MONTH_TOKEN, _RELATIVE, _calendar
from .domain import MAX_REQUESTED_NIGHTS, MVP_LIMITS, DomainError, TripState, apply_operations
from .intent_grounding import _facts, _key
from .llm_contract import _candidates
from .money import _FOREIGN, _UNIT_PRICE, _duration_price_scopes

_UNCERTAIN = re.compile(r"\b(?:если|бы|допустим|предположим|возможно|может|наверное|например|"
    r"раньше|ранее|прежде|вчера|обсуждал\w*|говорил\w*|писал\w*|цитирую|или|либо|не|нет|"
    r"if|suppose|previously|either|or|not|cancel|отмена|отмени|стоп)\b")
_WISH = re.compile(r"\b(?:хочу|хотим|еду|едем|поеду|поедем|планирую|планируем|нужен|нужна|нужно)\b")
_TRAVEL = re.compile(r"\b(?:отел\w*|гостиниц\w*|проживан\w*|поездк\w*|путешеств\w*|поехать|полететь|еду|едем|поеду|поедем)\b")
_LODGING = re.compile(r"\b(?:отел\w*|гостиниц\w*|проживан\w*|заезд\w*)\b")
_INFO = re.compile(r"^\s*(?:расскажи|объясни|напомни|знаешь|знаете|можно|можешь|сможешь|почему|как|когда|сколько|где|what|why|can)\b")
_START = re.compile(r"(?<!\w)(?:с|заезд(?:\s+с)?)\s+(?P<token>" + "|".join((_ISO.pattern, _DOT.pattern, _MONTH_TOKEN, *_RELATIVE)) + r")(?!\w)")
_NIGHTS = re.compile(r"(?<!\w)(?:" + _DURATION.pattern + r")(?!\w)")


def _pending(value):
    if not isinstance(value, dict):
        return {}
    result = deepcopy(value.get("context")) if isinstance(value.get("context"), dict) else {}
    result.update({key: deepcopy(item) for key, item in value.items() if key != "context"})
    return result


def _target(data, operations, pending, meta):
    stays = data["stays"]
    ids = {stay["id"] for stay in stays}
    aliases = _candidates("stay", ids, max(0, MVP_LIMITS["stays"]-len(ids)))
    for key in ("state_version", "based_state_version"):
        if key in pending and (type(pending[key]) is not int or pending[key] != data["state_version"]):
            meta["notes"].append({"code": "literal_intake_stale_date_target"})
            return None
    if pending.get("trip_id", data["trip_id"]) != data["trip_id"]:
        meta["notes"].append({"code": "literal_intake_foreign_date_target"})
        return None
    explicit = [pending[key] for key in ("target_id", "stay_id") if pending.get(key) is not None]
    if any(not isinstance(item, str) or item not in ids | set(aliases) for item in explicit) or len(set(explicit)) > 1:
        meta["notes"].append({"code": "literal_intake_foreign_date_target"})
        return None
    raw_targets = [op.get("target_id") for op in operations if op.get("type") == "upsert_stay"]
    if any(not isinstance(item, str) or item not in ids | set(aliases) for item in raw_targets):
        meta["notes"].append({"code": "literal_intake_foreign_date_target"})
        return None
    proposed = set(raw_targets)
    targets = proposed | set(explicit)
    if len(targets) > 1:
        meta["unknowns"].append("К какой одной остановке относятся дата и длительность проживания?")
        return None
    if targets:
        target = next(iter(targets))
        if ids and target not in ids:
            meta["unknowns"].append("К существующей или новой остановке относятся дата и длительность?")
            return None
        return target
    return stays[0]["id"] if len(stays) == 1 else aliases[0] if not stays and aliases else None


def _dates(text, now, meta):
    value = {}
    starts = list(_START.finditer(text))
    if len(starts) > 1:
        meta["unknowns"].append("Укажите одну дату начала проживания для этой остановки.")
    elif starts:
        try:
            if not isinstance(now, datetime) or now.utcoffset() is None:
                raise ValueError
            today = now.astimezone(ZoneInfo("Europe/Moscow")).date()
            token = starts[0]["token"]
            month = _MONTH.fullmatch(token)
            inferred_year = bool(month and month[3] is None)
            resolved = _calendar(f"{token} {today.year}" if inferred_year else token, today)
            if resolved < today:
                meta["unknowns"].append("Дата начала проживания в прошлом. Укажите будущую дату полностью, включая год.")
            else:
                value["check_in"] = resolved.isoformat()
                if inferred_year:
                    meta["notes"].append({"code": "literal_initial_current_year", "reference_date": today.isoformat(),
                        "timezone": "Europe/Moscow", "check_in": resolved.isoformat()})
        except (ValueError, OverflowError, TypeError):
            meta["unknowns"].append("Укажите корректную дату начала проживания полностью, включая год.")
    durations = list(_NIGHTS.finditer(text))
    counts = set()
    for match in durations:
        if not match["unit"].startswith(("ноч", "night")):
            meta["unknowns"].append("Сколько именно ночей проживания нужно? Дни не приравниваются к ночам.")
            continue
        raw = match["count"]
        count = int(raw) if raw.isdecimal() else _DURATION_COUNTS[raw]
        if 1 <= count <= MAX_REQUESTED_NIGHTS:
            counts.add(count)
        else:
            meta["unknowns"].append("Уточните допустимое число ночей проживания.")
    if re.search(r"\bна\s+(?:одну\s+)?неделю\b", text):
        counts.add(7)
    if len(counts) == 1:
        value["requested_nights"] = next(iter(counts))
    elif len(counts) > 1:
        meta["unknowns"].append("Вы указали разные длительности проживания. Сколько ночей сохранить?")
    return value


def ground_explicit_initial_facts(state, text, operations, *, pending=None, now=None):
    """Append missing proven initial facts; preserve all original operations."""
    copied = [op.model_dump(mode="json") if hasattr(op, "model_dump") else deepcopy(op) for op in operations]
    meta = {"recovered": False, "notes": [], "conflicts": [], "unknowns": [], "speech_act": None}
    try:
        data = TripState.model_validate(state.model_dump(mode="json") if hasattr(state, "model_dump") else state).model_dump(mode="json")
    except (ValueError, TypeError):
        return copied, meta
    if (not isinstance(text, str) or not 0 < len(text) <= 4000 or "?" in text
            or any(mark in text for mark in ('"', "'", '«', '»', '“', '”', '`'))
            or text.lstrip().startswith(">") or len(data["stays"]) > 1
            or any(not isinstance(op, dict) or not isinstance(op.get("value", {}), dict) for op in copied)):
        return copied, meta
    key = _key(text)
    if _UNCERTAIN.search(key) or _INFO.search(key) or not _WISH.search(key) or not _TRAVEL.search(key):
        return copied, meta
    if data.get("origin") and data["travelers"] and data["stays"] and all(
            stay.get("city") and stay.get("check_in") and stay.get("check_out") and stay.get("rooms") for stay in data["stays"]):
        return copied, meta
    additions = []
    def recover(operation, field):
        additions.append(operation)
        meta["notes"].append({"code": "literal_initial_fact_recovered", "field": field})
    def conflict(field):
        meta["conflicts"].append(f"Предложенные моделью операции противоречат явно указанному полю «{field}». Уточните значение; исходные операции сохранены для проверки.")
    facts = _facts(data, key, _pending(pending))
    if len(facts["origin"]) == 1:
        origin = next(iter(facts["origin"]))
        raw = [op for op in copied if op.get("type") == "set_origin"]
        if raw and any(op["value"].get("city", op["value"].get("origin")) != origin for op in raw):
            conflict("origin.city")
        elif not raw and not data.get("origin"):
            recover({"type": "set_origin", "value": {"city": origin}}, "origin.city")
    adults = _literal_adults(key)
    if adults is not None and 1 <= adults <= 10:
        existing = {person["id"] for person in data["travelers"]}
        raw_adds = [op["value"] for op in copied if op.get("type") == "add_traveler"]
        ids = [person.get("id") for person in raw_adds]
        count = sum(person["type"] == "adult" for person in data["travelers"])
        valid_ids = all(isinstance(identifier, str) and identifier not in existing for identifier in ids) and len(set(ids)) == len(ids)
        proposed = sum(person.get("type") == "adult" for person in raw_adds)
        if any(op.get("type") in {"remove_traveler", "update_traveler"} for op in copied) or raw_adds and (not valid_ids or count + proposed > adults):
            conflict("travelers.adults")
        elif count < adults and valid_ids:
            aliases = _candidates("traveler", existing | set(ids), min(10, max(0, MVP_LIMITS["travelers"]-len(existing)-len(ids))))
            missing = adults-count-proposed
            if missing <= len(aliases):
                for identifier in aliases[:missing]:
                    recover({"type": "add_traveler", "value": {"id": identifier, "type": "adult"}}, "travelers.adults")
    budget = None
    unit_scope = _UNIT_PRICE.search(key) or _duration_price_scopes(key) or re.search(
        r"\b(?:за|в|per)\s+(?:(?:кажд\w*|одн\w*)\s+)?(?:ноч\w*|сутк\w*|день|номер\w*|человек\w*|гост\w*)\b", key)
    if not _FOREIGN.search(key) and not unit_scope:
        budget = _literal_budget(key)
    if budget is not None:
        raw = [op for op in copied if op.get("type") == "set_budget"]
        expected = {"amount_minor": budget, "currency": "RUB", "scope": "whole_trip"}
        if raw and any(any(op["value"].get(k, "RUB" if k == "currency" else "whole_trip" if k == "scope" else None) != v for k, v in expected.items()) for op in raw):
            conflict("budget")
        elif not raw and data.get("budget") is None:
            recover({"type": "set_budget", "value": expected}, "budget")
    if _LODGING.search(key):
        target = _target(data, copied, _pending(pending), meta)
        if target is not None:
            date_meta = {"notes": [], "unknowns": []}
            dates = _dates(key, now, date_meta)
            meta["unknowns"].extend(date_meta["unknowns"])
            prior = next((stay for stay in data["stays"] if stay["id"] == target), {})
            values = {}
            raw = [op for op in copied if op.get("type") == "upsert_stay" and op.get("target_id") == target]
            for field, value in dates.items():
                proposed = [op["value"][field] for op in raw if field in op["value"]]
                if proposed and any(item != value for item in proposed):
                    conflict("stay." + field)
                elif not proposed and prior.get(field) is None:
                    values[field] = value
            if values:
                proposed = {"type": "upsert_stay", "target_id": target, "value": values}
                try:
                    apply_operations(TripState.model_validate(data), [*additions, proposed])
                except (DomainError, ValueError, OverflowError):
                    meta["unknowns"].append("Дата и длительность не согласуются с сохранённым проживанием. Уточните даты этой остановки.")
                else:
                    if "check_in" in values:
                        meta["notes"].extend(date_meta["notes"])
                    recover(proposed, "stay.dates")
    if additions:
        copied.extend(additions)
        meta.update(recovered=True, speech_act="CHANGE")
    meta["unknowns"] = list(dict.fromkeys(meta["unknowns"]))
    meta["conflicts"] = list(dict.fromkeys(meta["conflicts"]))
    return copied, meta
