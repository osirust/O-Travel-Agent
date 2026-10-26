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

from .geography import parse_destination

_ISO = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_DOT = re.compile(r"([0-9]{2})\.([0-9]{2})\.([0-9]{4})")
_MONTHS = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4,
           "мая": 5, "июня": 6, "июля": 7, "августа": 8,
           "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12}
_MONTH_TOKEN = r"[0-9]{1,2}\s+(?:" + "|".join(_MONTHS) + r")(?:\s+[0-9]{4}(?:\s+года)?)?"
_MONTH = re.compile(r"([0-9]{1,2})\s+(" + "|".join(_MONTHS) + r")(?:\s+([0-9]{4})(?:\s+года)?)?")
_RELATIVE = {"сегодня": 0, "завтра": 1, "послезавтра": 2}
_EXPLICIT = re.compile(
    r"(?P<label>заезд|выезд)(?:(?:\s+|\s*:\s*)(?P<token>"
    + "|".join((_ISO.pattern, _DOT.pattern, _MONTH_TOKEN, *_RELATIVE))
    + r")|(?P<attached>" + _ISO.pattern + "|" + _DOT.pattern + "|" + _MONTH_TOKEN + r"))"
)
_PROPOSAL_OWNER = "date_slots.v1"
_ORDINALS = {"первая": 1, "первую": 1, "первой": 1, "вторая": 2, "вторую": 2,
             "второй": 2, "третья": 3, "третью": 3, "третьей": 3}
_CONTROLS = {"пауза", "стоп", "отмена", "отмени", "продолжай", "продолжить", "resume", "pause", "cancel"}


def _calendar(token: str, today: date) -> date:
    if token in _RELATIVE:
        return today + timedelta(days=_RELATIVE[token])
    if _ISO.fullmatch(token):
        return date.fromisoformat(token)
    dotted = _DOT.fullmatch(token)
    if dotted:
        day, month, year = map(int, dotted.groups())
        return date(year, month, day)
    month = _MONTH.fullmatch(token)
    if month and month[3] is not None:
        return date(int(month[3]), _MONTHS[month[2]], int(month[1]))
    raise ValueError("Not an exact date token")


def _candidate_ids(stays: list) -> list[str] | None:
    ids = [stay.get("id") if isinstance(stay, dict) else None for stay in stays]
    if any(not isinstance(item, str) or not item for item in ids) or len(set(ids)) != len(ids):
        return None
    return ids


def _selection_question(stays: list, field: str) -> str:
    choices = "; ".join(f"{i}: {stay.get('city') or 'город не указан'}"
                        for i, stay in enumerate(stays, 1) if isinstance(stay, dict))
    label = "заезда" if field == "stay.check_in" else "выезда"
    return f"Для какой остановки указать дату {label}? Уточните город или номер остановки: {choices}."


def _deferred_answer(data: dict, pending: dict, context: dict, text: str,
                     now: datetime, timezone: str) -> dict:
    """Consume only a server-owned proposal tied to this trip/version/order.

    The original clock resolves relative language once. Current clock and the
    full domain validator still check the saved absolute date before proposing
    an edit. Persistence and proposal removal belong to the caller's same CAS.
    """
    proposal = context["date_proposal"]
    stays = data.get("stays") or []
    field = proposal.get("field") if isinstance(proposal, dict) else None
    field = field if isinstance(field, str) and field in {"stay.check_in", "stay.check_out"} else "stay.check_out"
    bound = {**context, "field": field, "target_id": None, "stay_id": None,
             "target_selection_required": True}

    def ask(message: str, *, discard: bool = False) -> dict:
        result = {"handled": True, "operations": [], "question": message, "pending_context": deepcopy(bound)}
        if discard:
            result["pending_context"].pop("date_proposal", None)
            result["pending_context_clear"] = ["date_proposal"]
        return result

    try:
        if not isinstance(proposal, dict) or proposal.get("owner") != _PROPOSAL_OWNER:
            raise ValueError("Unknown owner")
        version = proposal["based_state_version"]
        if (type(version) is not int or type(data.get("state_version")) is not int
                or version != data["state_version"] or proposal["trip_id"] != data.get("trip_id")):
            raise ValueError("Stale trip")
        ids = _candidate_ids(stays)
        pending_version = pending.get("state_version", version)
        if (not ids or proposal["candidate_stay_ids"] != ids or type(pending_version) is not int
                or pending_version != version or proposal["timezone"] != timezone):
            raise ValueError("Stale candidates")
        if proposal["field"] != field or (pending.get("field") or context.get("field")) != field:
            raise ValueError("Changed slot")
        clock = datetime.fromisoformat(proposal["reference_clock"])
        if clock.utcoffset() is None or not isinstance(proposal["source_text"], str) or len(proposal["source_text"]) > 50:
            raise ValueError("Invalid reference")
        reference = clock.astimezone(ZoneInfo(proposal["timezone"])).date()
        source_key = proposal["source_text"].strip().casefold().rstrip(".!").strip()
        if proposal.get("source_kind", "explicit_clause") == "date_answer":
            resolved = _calendar(source_key, reference)
        else:
            explicit = _EXPLICIT.fullmatch(source_key)
            if not explicit or ("stay.check_in" if explicit["label"] == "заезд" else "stay.check_out") != field:
                raise ValueError("Invalid source")
            resolved = _calendar(explicit["token"] or explicit["attached"], reference)
        if reference.isoformat() != proposal["reference_date"] or resolved.isoformat() != proposal["resolved_date"]:
            raise ValueError("Invalid resolution")
    except (KeyError, ValueError, TypeError, OverflowError, ZoneInfoNotFoundError):
        return ask("Поездка или адрес даты изменились. Укажите заново дату заезда или выезда и нужную остановку.", discard=True)
    key = text.strip().casefold().rstrip(".!").strip()
    if key in _RELATIVE or _ISO.fullmatch(key) or _DOT.fullmatch(key) or _MONTH.fullmatch(key):
        # A new exact date replaces the saved date; it never silently chooses a
        # target. Keep the original bare input distinguishable in provenance.
        fresh_context = {k: v for k, v in context.items() if k != "date_proposal"}
        fresh_context.update(target_id=None, stay_id=None)
        fresh_pending = {"field": field, "context": fresh_context}
        replacement = resolve_date_answer(data, fresh_pending,
            ("Заезд " if field == "stay.check_in" else "Выезд ") + text.strip(), now, timezone)
        new_proposal = replacement.get("pending_context", {}).get("date_proposal")
        if new_proposal:
            new_proposal.update(source_kind="date_answer", source_text=text.strip())
        elif replacement.get("handled"):
            replacement["pending_context_clear"] = ["date_proposal"]
        return replacement
    if re.match(r"^(?:добавь|добавить|удали|убери|измени|замени|поменяй|установи|бюджет|хочу)\b", key):
        return {"handled": False, "operations": []}
    if any(ch in text for ch in ("?", ";", ",", "\n", "\r")) or re.search(r"\b(?:не|нет|ли|если|может|или)\b", key):
        return ask(_selection_question(stays, field))
    target = text.strip() if text.strip() in ids else None
    ordinal = _ORDINALS.get(key)
    numbered = re.fullmatch(r"(?:(?:остановка|остановку|номер)\s+)?([1-9][0-9]*)", key)
    if numbered:
        ordinal = int(numbered[1])
    if ordinal is not None and 1 <= ordinal <= len(ids):
        if target is not None and target != ids[ordinal - 1]:
            return ask("Номер совпал с идентификатором другой остановки. Уточните уникальный город или другой точный идентификатор.")
        target = ids[ordinal - 1]
    if target is None and len(text) <= 80 and not any(ch in text for ch in ("?", ";", ",", "\n", "\r")) and not re.search(
            r"\b(?:не|нет|ли|если|может|или|хочу|замени|поменяй|добавь)\b", key):
        place = parse_destination(text)["requested_destination"]
        if place.get("kind") == "city":
            matches = [stay["id"] for stay in stays if stay.get("city") == place["name"]]
            if len(matches) == 1:
                target = matches[0]
    if target is None:
        return ask(_selection_question(stays, field))
    selected_pending = {"field": field, "target_id": target}
    answer = resolve_date_answer(data, selected_pending, proposal["resolved_date"], now, timezone)
    if not answer["handled"] or not answer["operations"]:
        return ask((answer.get("question") or "Эта дата не подходит выбранной остановке.")
                   + " Укажите другую остановку или новую дату заезда/выезда.")
    # Local start/end validation is insufficient: a valid individual stay may
    # overlap the next stop. This is a copy-only preflight, never a DB write.
    from .domain import DomainError, TripState, apply_operations

    try:
        apply_operations(TripState.model_validate(data), answer["operations"])
    except (DomainError, ValueError):
        return ask("Дата не подходит маршруту или пересекается с другой остановкой. Укажите другую остановку или новую дату заезда/выезда.")
    answer["pending_context_clear"] = ["date_proposal"]
    return answer


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
    key = text.strip().casefold().rstrip(".!").strip()
    explicit = _EXPLICIT.fullmatch(key)
    pending = pending if isinstance(pending, dict) else {}
    context = deepcopy(pending.get("context")) if isinstance(pending.get("context"), dict) else {}
    data = state.model_dump(mode="json") if hasattr(state, "model_dump") else state if isinstance(state, dict) else {}
    active_field = pending.get("field") or context.get("field")
    proposal_field = context.get("date_proposal", {}).get("field") if isinstance(context.get("date_proposal"), dict) else None
    if (not explicit and "date_proposal" in context and isinstance(proposal_field, str)
            and isinstance(active_field, str) and (active_field != proposal_field
                or pending.get("target_id") is not None or pending.get("stay_id") is not None)):
        # A newer exact question owns this answer, even if both slots are hotel
        # dates. Drop the old packet only with the caller's successful CAS.
        clean_pending = deepcopy(pending)
        clean_pending["context"] = {k: v for k, v in context.items() if k not in {"date_proposal", "target_selection_required"}}
        answer = resolve_date_answer(data, clean_pending, text, now, timezone)
        if answer["handled"]:
            answer["pending_context_clear"] = ["date_proposal"]
        return answer
    if ("date_proposal" in context and not explicit and isinstance(active_field, str)
            and active_field in {"stay.check_in", "stay.check_out"}):
        if key in _CONTROLS:
            return unhandled
        return _deferred_answer(data, pending, context, text, now, timezone)
    if len(text) > 50 or any(symbol in text for symbol in ("?", ";", "\n", "\r")):
        return unhandled
    field = active_field
    if explicit:
        field = "stay.check_in" if explicit.group("label") == "заезд" else "stay.check_out"
        key = explicit.group("token") or explicit.group("attached")
    if field not in {"stay.check_in", "stay.check_out"}:
        return unhandled
    iso, dotted, month = _ISO.fullmatch(key), _DOT.fullmatch(key), _MONTH.fullmatch(key)
    if key not in _RELATIVE and not iso and not dotted and not month:
        return unhandled
    if month and not explicit:
        # Month words without a label are answers only to the current addressed
        # hotel-date slot, never evidence for an unrelated/stale question.
        for version in (pending.get("state_version"), context.get("based_state_version")):
            if version is not None and (type(version) is not int or version != data.get("state_version")):
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
    stay = None
    if active_field in {"stay.check_in", "stay.check_out"} and "date_proposal" not in context:
        for candidate in (pending.get("target_id"), pending.get("stay_id"), context.get("target_id"), context.get("stay_id")):
            if candidate is not None and candidate != "":
                target = candidate
                break
    if target is not None:
        if not isinstance(target, str):
            return unhandled
        matches = [item for item in stays if isinstance(item, dict) and item.get("id") == target]
        if len(matches) != 1:
            return unhandled  # A stale explicit binding is not redirected.
        stay = matches[0]
    elif explicit and len(stays) == 1:
        stay = stays[0]
        if not isinstance(stay, dict) or not isinstance(stay.get("id"), str) or not stay["id"]:
            return unhandled
        target = stay["id"]
    elif not explicit:
        return unhandled
    if month and month[3] is None:
        # Even if another date is known, do not infer a year or roll a past
        # month into next year. The follow-up must supply an absolute date.
        bound = {**context, "field": field, "target_id": target, "stay_id": target,
                 "target_selection_required": target is None and len(stays) > 1}
        bound.pop("date_proposal", None)
        answer = {"handled": True, "operations": [],
                  "question": (f"Укажите дату {label} полностью: день, месяц и год."
                               if target else _selection_question(stays, field) + " Укажите также год поездки."),
                  "pending_context": bound}
        if "date_proposal" in context:
            answer["pending_context_clear"] = ["date_proposal"]
        return answer
    try:
        resolved = _calendar(key, today)
    except (ValueError, OverflowError):
        bound = {**context, "field": field, "target_id": target, "stay_id": target,
                 "target_selection_required": target is None and len(stays) > 1}
        bound.pop("date_proposal", None)
        answer = {"handled": True, "operations": [],
                  "question": f"Дата «{text.strip()}» не существует в календаре. Какую корректную дату {label} вы выбираете?",
                  "pending_context": bound}
        if "date_proposal" in context:
            answer["pending_context_clear"] = ["date_proposal"]
        return answer
    if stay is None:
        bound = {**context, "field": field, "target_id": None, "stay_id": None,
                 "target_selection_required": True,
                 "reference_date": today.isoformat(), "timezone": timezone}
        bound.pop("resolved_date", None)  # A date from an older question is not this proposal.
        ids = _candidate_ids(stays)
        if ids and type(data.get("state_version")) is int and isinstance(data.get("trip_id"), str):
            bound["date_proposal"] = {"owner": _PROPOSAL_OWNER, "trip_id": data["trip_id"],
                "based_state_version": data["state_version"], "field": field,
                "resolved_date": resolved.isoformat(), "source_text": text.strip(),
                "reference_clock": now.isoformat(), "reference_date": today.isoformat(),
                "timezone": timezone, "candidate_stay_ids": ids}
        else:
            bound.pop("date_proposal", None)
        return {"handled": True, "operations": [],
                "question": _selection_question(stays, field),
                "pending_context": bound}

    def question(message: str, resolved: date | None = None) -> dict[str, Any]:
        bound = {**context, "field": field, "target_id": target,
                 "reference_date": today.isoformat(), "timezone": timezone}
        bound.pop("target_selection_required", None)
        if resolved is not None:
            bound["resolved_date"] = resolved.isoformat()
        return {"handled": True, "operations": [], "question": message, "pending_context": bound}

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
    answer = {"handled": True, "operations": [{"type": "upsert_stay", "target_id": target,
        "value": {field.split(".", 1)[1]: resolved.isoformat()}}]}
    if "date_proposal" in context:
        answer["pending_context_clear"] = ["date_proposal"]
    return answer
