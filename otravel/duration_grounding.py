"""Remove only a newly invented duration accompanying complete model dates.

Existing duration requirements, explicit duration language and typed commands
remain untouched. No date inference, persistence, model call or HTTP occurs.
"""
from __future__ import annotations

import re
from copy import deepcopy
from datetime import date

from .domain import MAX_REQUESTED_NIGHTS, TripState

FINDING_ID = "HARNESS-004-STALE-DERIVED-DURATION-001"
_TYPED_PREFIX = "\x1eOTRAVEL_TYPED_V1:"
_ISO = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
# Conservative veto: even quoted/negated/conditional duration language prevents
# this omission guard. It never adjudicates which person/stay that text meant.
# Calendar month names and a child's age in years are not duration units here.
_DURATION_LANGUAGE = re.compile(
    r"(?<![A-Za-zА-Яа-яЁё_])(?:ночь|ночи|ночей|ночью|ночам|ночами|ночах|день|дня|дней|дни|дням|днями|днях|"
    r"сутки|суток|суткам|сутками|сутках|недел\w*|месяц\w*|дн|нед|ноч|"
    r"nights?|days?|weeks?|months?|overnight|weekend|выходн\w*|длительност\w*|"
    r"duration|requested_nights)\b|\b(?:на|for)\s*(?:\d+(?:[.,]\d+)?\s*)?(?:год|полгода|years?|an?\s+year)\b",
    re.IGNORECASE,
)


def _full_date(value):
    if not isinstance(value, str) or _ISO.fullmatch(value) is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def guard_unasked_duration_operations(before_state, operations, text, *, model_generated=True):
    """Copy proposals; suppress only unasked new requested_nights on full dates.

    Caller must use this only for model extraction, after date grounding and
    before domain application. ``model_generated=False`` and reserved typed
    command text provide explicit no-op boundaries for other callers.
    """
    copied = [operation.model_dump(mode="json") if hasattr(operation, "model_dump") else deepcopy(operation)
        for operation in operations]
    meta = {"notes": [], "unknowns": [], "conflicts": []}
    if (model_generated is not True or not isinstance(text, str) or not 0 < len(text) <= 20_000
            or text.startswith(_TYPED_PREFIX) or _DURATION_LANGUAGE.search(text)):
        return copied, meta
    try:
        state = TripState.model_validate(before_state.model_dump(mode="json")
            if hasattr(before_state, "model_dump") else before_state)
    except (ValueError, TypeError):
        return copied, meta
    for operation in copied:
        if not isinstance(operation, dict) or operation.get("type") != "upsert_stay":
            continue
        value = operation.get("value")
        if not isinstance(value, dict):
            continue
        target = operation.get("target_id") or value.get("id")
        if (not isinstance(target, str) or not target
                or operation.get("target_id") is not None and value.get("id", target) != target):
            continue
        existing = [stay for stay in state.stays if stay.id == target]
        if len(existing) > 1 or existing and existing[0].requested_nights is not None:
            continue
        nights = value.get("requested_nights")
        # Invalid field shapes remain the schema/domain validator's concern.
        if type(nights) is not int or not 1 <= nights <= MAX_REQUESTED_NIGHTS:
            continue
        start, end = _full_date(value.get("check_in")), _full_date(value.get("check_out"))
        if start is None or end is None or end <= start:
            continue
        operation["value"] = {key: item for key, item in value.items() if key != "requested_nights"}
        meta["notes"].append({"finding_id": FINDING_ID, "code": "unasked_requested_nights_removed",
            "field": "stays.requested_nights", "stay_id": target, "status": "suppressed",
            "source": "model_complete_date_pair_without_duration_language", "dates_changed": False})
    return copied, meta
