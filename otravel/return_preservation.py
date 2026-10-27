"""Model-only suppression of replayed shifts under current date preservation.

Only a valid shift_return is omitted. Dates, room/group setters, typed commands
and genuine or ambiguous current date-edit requests remain untouched.
"""
from __future__ import annotations

import re
from copy import deepcopy

from .domain import TripState
from .operation_aliases import _literal_shift, _without_quoted_spans

FINDING_ID = "HARNESS-004-UNASKED-RETURN-REPLAY-001"
_TYPED = "\x1eOTRAVEL_TYPED_V1:"
_SUBJECT = re.compile(r"\b(?:дат(?:а|у|ы|е|ой|ам|ах|ами)?|заезд\w*|выезд\w*|возвращен\w*|обратн\w*\s+(?:рейс\w*|вылет\w*|перелет\w*)|"
    r"dates?|return(?:\s+flight|\s+date)?|check[ -]?(?:in|out))\b")
_KEEP = re.compile(r"\b(?:сохрани(?:те)?|сохранить|сохраня(?:ем|й|йте)|сохраним|"
    r"остав(?:ь(?:те)?|ить|ляем|ляй(?:те)?|им)|keep|preserve|retain)\b|"
    r"\b(?:не|do\s+not)\s+(?:меня\w*|изменя\w*|трог\w*|сдвига\w*|перенос\w*|change|move|shift)\b")
_EDIT = re.compile(r"\b(?:перенес\w*|перенос\w*|сдвин\w*|сдвига\w*|измен\w*|меня\w*|поменя\w*|"
    r"передвин\w*|исправ\w*|продли\w*|сократ\w*|увелич\w*|уменьш\w*|добав\w*|move|shift|change|extend|shorten)\b")
_DURATION = re.compile(r"\b(?:дн\w*|день|дня|дней|ноч\w*|недел\w*|days?|nights?|weeks?)\b")
_NONCURRENT = re.compile(r"\b(?:если|бы|допустим|предположим|возможно|может|наверное|гипотетически|"
    r"ранее|прежде|вчера|говорил\w*|просил\w*|писал\w*|цитата|if|would|previously|suppose)\b")
_NEGATED_KEEP = re.compile(r"\b(?:не|not)\s*$")
_NEGATED_EDIT = re.compile(r"\b(?:не|not|не\s+(?:надо|нужно|следует))\s*$")
_RELATIVE_RETURN = re.compile(r"\b(?:возвращен\w*|обратн\w*\s+рейс\w*|return)\b[^.!?;]{0,80}"
    r"(?:\b(?:позже|раньше|later|earlier)\b|[+−-]\s*\d)")
_DATE_VALUE = re.compile(r"\b(?:\d{1,4}[./-]\d{1,2}(?:[./-]\d{1,4})?|\d{1,2}\s+(?:январ\w*|феврал\w*|март\w*|"
    r"апрел\w*|ма[йяе]|июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*|"
    r"january|february|march|april|may|june|july|august|september|october|november|december)|"
    r"завтра|послезавтра|сегодня|tomorrow|today)\b")
_PRESERVED_LIST_FIELD = re.compile(r"^\s*(?:дат\w*|маршрут\w*|возвращен\w*|(?:общ\w*\s+)?бюджет\w*|гражданств\w*)\b")


def _sentences(text):
    if not isinstance(text, str) or not 0 < len(text) <= 20_000:
        return []
    normalized = re.sub(r"\bdon['’]t\b", "do not", text.casefold().replace("ё", "е"))
    normalized = re.sub(r"`[^`]*`", " ", normalized)
    normalized = _without_quoted_spans(normalized)
    if normalized is None or "`" in normalized:
        return []
    pieces = re.split(r"([!?;\n]|(?<!\d)\.|\.(?!\d))", normalized)
    result = []
    for index in range(0, len(pieces), 2):
        sentence = " ".join(pieces[index].split())
        end = pieces[index + 1] if index + 1 < len(pieces) else ""
        if sentence and end != "?" and not sentence.startswith(">") and not _NONCURRENT.search(sentence):
            result.append(sentence)
    return result


def _preserves_dates(sentences):
    return any(_SUBJECT.search(sentence) and any(
        not _NEGATED_KEEP.search(sentence[:match.start()]) for match in _KEEP.finditer(sentence))
        for sentence in sentences)


def _positive_date_edit(sentences):
    for sentence in sentences:
        if _literal_shift(sentence) is not None or _RELATIVE_RETURN.search(sentence):
            return True
        # A hotel/group/budget edit in another comma-clause is not date authority.
        clauses = sentence.split(",")
        for index, clause in enumerate(clauses):
            if not (_SUBJECT.search(clause) or _DURATION.search(clause)):
                continue
            trailing_list_keep = (bool(clauses[index + 1:])
                and all(_PRESERVED_LIST_FIELD.search(part) for part in clauses[index + 1:])
                and _KEEP.search(clauses[-1]) is not None)
            if (_SUBJECT.search(clause) and _DATE_VALUE.search(clause)
                    and not _KEEP.search(clause) and not trailing_list_keep):
                return True  # An elliptical absolute date is still possible current edit authority.
            if any(not _NEGATED_EDIT.search(clause[:match.start()]) for match in _EDIT.finditer(clause)):
                return True
    return False


def guard_preserved_return_operations(before_state, operations, text, *, model_generated=True):
    copied = [operation.model_dump(mode="json") if hasattr(operation, "model_dump") else deepcopy(operation)
        for operation in operations]
    meta = {"notes": [], "unknowns": [], "conflicts": []}
    if model_generated is not True or not isinstance(text, str) or text.startswith(_TYPED):
        return copied, meta
    sentences = _sentences(text)
    if not _preserves_dates(sentences) or _positive_date_edit(sentences):
        return copied, meta
    try:
        state = TripState.model_validate(before_state)
    except (TypeError, ValueError):
        return copied, meta
    if (state.route_policy.kind != "round_trip" or not state.stays or not state.legs
            or state.stays[-1].check_out is None or state.legs[-1].departure_date != state.stays[-1].check_out
            or state.legs[-1].destination != state.origin):
        return copied, meta
    result = []
    for operation in copied:
        value = operation.get("value") if isinstance(operation, dict) else None
        eligible = (isinstance(operation, dict) and operation.get("type") == "shift_return"
            and operation.get("target_id") is None and isinstance(value, dict)
            and set(value) == {"days"} and type(value["days"]) is int)
        if not eligible:
            result.append(operation)
            continue
        meta["notes"].append({"finding_id": FINDING_ID, "code": "unrequested_return_shift_suppressed",
            "operation": "shift_return", "status": "suppressed", "source": "asserted_current_date_preservation",
            "preserved_return_date": state.legs[-1].departure_date.isoformat(), "other_fields_changed": False})
    return result, meta
