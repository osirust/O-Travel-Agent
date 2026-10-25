"""Bound stay date answers; no implicit clock, tools, or state mutation.

Exact date tokens answer a persisted, explicitly targeted stay slot. A bounded
explicit заезд/выезд clause may select that field for a single existing stay.
This is not arbitrary date NLU or a flight-date editor through a hotel stay.
"""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_ISO = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_DOT = re.compile(r"([0-9]{2})\.([0-9]{2})\.([0-9]{4})")
_RELATIVE = {"сегодня": 0, "завтра": 1, "послезавтра": 2}
_EXPLICIT = re.compile(
    r"(?P<label>заезд|выезд)(?:(?:\s+|\s*:\s*)(?P<token>"
    + "|".join((_ISO.pattern, _DOT.pattern, *_RELATIVE))
    + r")|(?P<attached>" + _ISO.pattern + "|" + _DOT.pattern + r"))"
)


def _stored_date(value: Any) -> date | None:
    if type(value) is date:
        return value
    if isinstance(value, str) and _ISO.fullmatch(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    return None


def resolve_date_answer(state: Any, pending: Any, text: Any, now: datetime,
                        timezone: str = "Europe/Moscow") -> dict[str, Any]:
    """Return proposed upsert_stay or a human date question on a fixed clock.

    ``now`` must be timezone-aware; unknown timezone/clock/target or non-exact
    language is unhandled, never replaced by the host clock or a guessed date.
    A successful edit has no pending_context: the answered field must not remain
    bound when the caller advances to a different missing field.
    """
    unhandled = {"handled": False, "operations": []}
    if not isinstance(text, str) or not text.strip():
        return unhandled
    if len(text) > 50 or any(symbol in text for symbol in ("?", ";", "\n", "\r")):
        return unhandled
    key = text.strip().casefold().rstrip(".!").strip()
    explicit = _EXPLICIT.fullmatch(key)
    pending = pending if isinstance(pending, dict) else {}
    context = deepcopy(pending.get("context")) if isinstance(pending.get("context"), dict) else {}
    active_field = pending.get("field") or context.get("field")
    field = active_field
    if explicit:
        field = "stay.check_in" if explicit.group("label") == "заезд" else "stay.check_out"
        key = explicit.group("token") or explicit.group("attached")
    if field not in {"stay.check_in", "stay.check_out"}:
        return unhandled
    iso, dotted = _ISO.fullmatch(key), _DOT.fullmatch(key)
    if key not in _RELATIVE and not iso and not dotted:
        return unhandled
    if hasattr(state, "model_dump"):
        data = state.model_dump(mode="json")
    elif isinstance(state, dict):
        data = state
    else:
        return unhandled
    stays = data.get("stays")
    if not isinstance(stays, list) or not stays:
        return unhandled
    if not isinstance(now, datetime) or not isinstance(timezone, str) or not timezone:
        return unhandled
    try:
        if now.utcoffset() is None:
            return unhandled
        today = now.astimezone(ZoneInfo(timezone)).date()
    except (ValueError, TypeError, OverflowError, ZoneInfoNotFoundError):
        return unhandled
    label = "заезда" if field == "stay.check_in" else "выезда"
    target = None
    if active_field in {"stay.check_in", "stay.check_out"}:
        for candidate in (pending.get("target_id"), pending.get("stay_id"), context.get("target_id"), context.get("stay_id")):
            if candidate is not None and candidate != "":
                target = candidate
                break
    if target is not None:
        if not isinstance(target, str):
            return unhandled
        matches = [stay for stay in stays if isinstance(stay, dict) and stay.get("id") == target]
        if len(matches) != 1:
            return unhandled  # A stale explicit binding is not redirected to another stay.
        stay = matches[0]
    elif explicit:
        if len(stays) != 1:
            bound = {**context, "field": field, "target_id": None, "stay_id": None,
                     "target_selection_required": True,
                     "reference_date": today.isoformat(), "timezone": timezone}
            bound.pop("resolved_date", None)  # A date from an older question is not this proposal.
            return {"handled": True, "operations": [],
                    "question": f"Для какой остановки указать дату {label}? Уточните город или нужный отель.",
                    "pending_context": bound}
        stay = stays[0]
        if not isinstance(stay, dict) or not isinstance(stay.get("id"), str) or not stay["id"]:
            return unhandled
        target = stay["id"]
    else:
        return unhandled

    def question(message: str, resolved: date | None = None) -> dict[str, Any]:
        bound = {**context, "field": field, "target_id": target,
                 "reference_date": today.isoformat(), "timezone": timezone}
        bound.pop("target_selection_required", None)
        if resolved is not None:
            bound["resolved_date"] = resolved.isoformat()
        return {"handled": True, "operations": [], "question": message, "pending_context": bound}

    try:
        if key in _RELATIVE:
            resolved = today + timedelta(days=_RELATIVE[key])
        elif iso:
            resolved = date.fromisoformat(key)
        else:
            day, month, year = map(int, dotted.groups())
            resolved = date(year, month, day)
    except (ValueError, OverflowError):
        return question(f"Дата «{text.strip()}» не существует в календаре. Какую корректную дату {label} вы выбираете?")
    start, end = _stored_date(stay.get("check_in")), _stored_date(stay.get("check_out"))
    if (stay.get("check_in") is not None and start is None) or (stay.get("check_out") is not None and end is None):
        return unhandled  # Malformed stored state is not repaired by guessing another slot.
    actual = resolved.strftime("%d.%m.%Y")
    prefix = f"«{text.strip()}» — это {actual}. "
    if field == "stay.check_in":
        if resolved < today:
            return question(prefix + "Дата заезда оказалась в прошлом. На какую будущую дату планируете заезд?", resolved)
        if end is not None and resolved >= end:
            return question(prefix + f"Дата заезда должна быть раньше даты выезда {end.strftime('%d.%m.%Y')}. Какую более раннюю дату заезда выбираете?", resolved)
    else:
        if start is not None and resolved <= start:
            return question(prefix + f"Дата выезда должна быть позже даты заезда {start.strftime('%d.%m.%Y')}. На какую более позднюю дату планируете выезд?", resolved)
        if resolved <= today:
            return question(prefix + f"Дата выезда должна быть позже сегодняшней даты {today.strftime('%d.%m.%Y')}. На какую более позднюю дату планируете выезд?", resolved)
    return {"handled": True, "operations": [{"type": "upsert_stay", "target_id": target,
        "value": {field.split(".", 1)[1]: resolved.isoformat()}}]}
