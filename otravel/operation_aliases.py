"""Normalize one proven duplicate return-date representation, never execute it.

Both original raw operations and normalization notes remain available to the
caller. Non-equivalent or unsupported intents retain normal conflict handling.
"""
from __future__ import annotations

import re
from copy import deepcopy
from datetime import date, timedelta
from typing import Any

from .domain import Operation, TripState

_NUMBERS = {"один": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
            "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10}
_NUMBER = r"(?:[+-]?[0-9]+|" + "|".join(_NUMBERS) + r")"
_DAYS = re.compile(r"\bна\s+(?P<number>" + _NUMBER + r")\s+(?:день|дня|дней)\b")
_RETURN = re.compile(r"\b(?:возвращени[ея]|возврат|обратн\w*\s+(?:рейс\w*|вылет\w*|перелет\w*|дорог\w*))\b")
_ACTION = re.compile(r"\b(?:перенеси|перенести|сдвинь|сдвинуть|передвинь|передвинуть|отложи|отложить|переносим|сдвигаем)\b")
_HOTEL_RELATIVE = re.compile(r"\b(?P<action>продли|продлить|сократи|сократить|увеличь|уменьши)\s+последн\w*\s+(?:проживан\w*|остановк\w*)\s+на\s+(?P<number>" + _NUMBER + r")\s+(?:день|дня|дней)\b")
_UNCERTAIN = re.compile(r"\b(?:если|допустим|предположим|возможно|наверное|например|ранее|прежде|обсуждали|говорил\w*|просил\w*|прошлый|старый)\b|\bраньше\s+(?:я|мы|было|просил\w*)\b|\b(?:хотел\w*|мог\w*)\s+бы\b")
_NEGATED_ACTION = re.compile(r"\bне\s+(?:переноси\w*|сдвиг\w*|передвиг\w*|отклад\w*|меняй|менять)\b")


def _get(value: Any, key: str, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _integer(value: str) -> int:
    return _NUMBERS[value] if value in _NUMBERS else int(value)


def _without_quoted_spans(text: str) -> str | None:
    """Quotes cannot authorize a date edit or veto an independent sentence.

    A displayed hotel name may be quoted in another clause. Discard complete
    quoted spans before looking for the current return instruction; unmatched
    delimiters remain ambiguous and retain the original conservative refusal.
    """
    unquoted = re.sub(r'«[^»]*»|“[^”]*”|"[^"]*"|\'[^\']*\'', ' ', text)
    return None if any(mark in unquoted for mark in ('"', "'", '«', '»', '“', '”')) else unquoted


def _literal_shift(text: str) -> int | None:
    normalized = " ".join(text.casefold().replace("ё", "е").split())
    normalized = _without_quoted_spans(normalized)
    if normalized is None or _UNCERTAIN.search(normalized):
        return None
    clauses = re.split(r"(?<=[.!?;])\s+", normalized)
    shifts = set()
    hotel_shifts = set()
    return_link = False
    for clause in clauses:
        if "?" in clause:
            continue
        hotel = _HOTEL_RELATIVE.search(clause)
        if hotel and not _NEGATED_ACTION.search(clause[:hotel.start() + len(hotel[0])]):
            amount = _integer(hotel["number"])
            hotel_shifts.add(-amount if hotel["action"] in {"сократи", "сократить", "уменьши"} else amount)
        if not _RETURN.search(clause) or _NEGATED_ACTION.search(clause):
            continue
        if (re.search(r"\b(?:вслед|следом|вместе|соответственно)\b", clause)
                and re.search(r"\b(?:выезд\w*|checkout)\b", clause)
                and re.search(r"\b(?:сдвиг\w*|перенос\w*|передвиг\w*)\b", clause)):
            return_link = True
        if _ACTION.search(clause):
            for match in _DAYS.finditer(clause):
                value = _integer(match["number"])
                shifts.add(-abs(value) if re.search(r"\bраньше\b", clause) else value)
    if return_link:
        shifts.update(hotel_shifts)
    return next(iter(shifts)) if len(shifts) == 1 else None


def normalize_equivalent_return_ops(state: TripState | dict, operations: list, text: str) -> tuple[list, list[dict]]:
    """Remove only a redundant last-stay check_out proven by before+days.

    A relative hotel edit alone is not proof of a return instruction. An explicit
    link saying the return follows the new last checkout is accepted. Unknown
    before dates, changed topology, hypotheses and non-equivalent dates stay
    untouched; the caller's conflict gate can then ask instead of ordering them.
    """
    result = deepcopy(operations)
    if not isinstance(text, str) or not isinstance(result, list):
        return result, []
    state = TripState.model_validate(state)
    if (not state.stays or state.stays[-1].check_out is None
            or state.route_policy.kind in {"one_way", "undecided"}
            or state.route_policy.kind == "open_jaw" and state.route_policy.end_city is None):
        return result, []
    if any(_get(op, "type") in {"set_route_policy", "remove_stay", "undo"} for op in result):
        return result, []
    shifts = [op for op in result if _get(op, "type") == "shift_return"]
    last = state.stays[-1]
    absolute = [op for op in result if _get(op, "type") == "upsert_stay" and _get(op, "target_id") == last.id
                and isinstance(_get(op, "value"), dict) and "check_out" in _get(op, "value")]
    if len(shifts) != 1 or len(absolute) != 1:
        return result, []
    value = _get(shifts[0], "value")
    if not isinstance(value, dict) or set(value) != {"days"} or type(value["days"]) is not int or _get(shifts[0], "target_id") is not None:
        return result, []
    days = value["days"]
    literal = _literal_shift(text)
    if literal is None or not days or literal != days:
        return result, []
    try:
        expected = last.check_out + timedelta(days=days)
        proposed = _get(absolute[0], "value")["check_out"]
        proposed = proposed if type(proposed) is date else date.fromisoformat(proposed)
    except (ValueError, TypeError, OverflowError):
        return result, []
    if proposed != expected:
        return result, [{"code": "return_alias_dates_not_equivalent", "status": "clarification_required",
            "field": "stay.check_out", "target_id": last.id,
            "question": f"Сдвиг возвращения даёт выезд {expected:%d.%m.%Y}, а другое изменение — {proposed:%d.%m.%Y}. Какая дата верна?"}]
    rebuilt = []
    for operation in result:
        if operation is absolute[0]:
            patch = deepcopy(_get(operation, "value"))
            patch.pop("check_out")
            if not patch:
                continue
            if isinstance(operation, Operation):
                operation.value = patch
            else:
                operation["value"] = patch
        rebuilt.append(operation)
    return rebuilt, [{"code": "equivalent_return_checkout_alias_removed", "field": "stay.check_out", "target_id": last.id,
        "source": "explicit_current_relative_return", "before_check_out": last.check_out.isoformat(),
        "removed_absolute_check_out": proposed.isoformat(), "kept_shift_days": days}]
