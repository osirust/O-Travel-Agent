"""Pure LiteAPI rate evidence. No HTTP, pricing conversion, or availability claim.

Disclosure contract supplied for this integration:
https://docs.liteapi.travel/docs/hotel-rates-api-json-data-structure
An explicitly present taxesAndFees:null means taxes/fees are included. Missing
fields are unknown. Cancellation tags/time contract:
https://docs.liteapi.travel/docs/canceling-a-booking
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any


_MISSING = object()


def _number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() and result >= 0 else None


def _zone(value: Any):
    if not isinstance(value, str):
        return None
    label = value.strip().upper()
    if label in {"UTC", "GMT", "Z"}:
        return timezone.utc
    match = re.fullmatch(r"(?:(?:UTC|GMT))?([+-])(\d{1,2})(?::?(\d{2}))?", label)
    if not match:
        return None
    hours, minutes = int(match[2]), int(match[3] or 0)
    if hours >= 24 or minutes >= 60:
        return None
    offset = timedelta(hours=hours, minutes=minutes)
    return timezone(offset if match[1] == "+" else -offset)


def _timestamp(value: Any, zone_label: Any = None) -> datetime | None:
    if not isinstance(value, str) or not re.search(r"[T ]\d{2}:\d{2}", value):
        return None
    raw = value.strip()
    trailing = re.search(r"\s+((?:UTC|GMT)(?:[+-]\d{1,2}(?::?\d{2})?)?)$", raw, flags=re.IGNORECASE)
    if trailing:
        if zone_label is not None:
            declared, suffix = _zone(zone_label), _zone(trailing[1])
            if declared is None or suffix is None or declared.utcoffset(None) != suffix.utcoffset(None):
                return None
        zone_label = trailing[1]
        raw = raw[:trailing.start()].strip()
    if re.search(r"\d{2}:\d{2}:\d{2}[.,]\d{7,}", raw):
        return None  # Do not silently truncate a supposedly exact deadline.
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
    except ValueError:
        return None
    explicit = _zone(zone_label) if zone_label is not None else None
    if zone_label is not None and explicit is None:
        return None  # CST/IST and unsupported named zones are not guessed.
    if parsed.tzinfo is None:
        if explicit is None:
            return None
        parsed = parsed.replace(tzinfo=explicit)
    elif explicit is not None and parsed.utcoffset() != explicit.utcoffset(None):
        return None  # Contradictory offset and timezone evidence.
    return parsed.astimezone(timezone.utc)


def _uniform(values: list[Any]) -> bool | None:
    if values and all(value is True for value in values):
        return True
    if values and all(value is False for value in values):
        return False
    return None


def lite_rate_facts(rates: Any, occupancies: Any, now: datetime) -> dict[str, Any]:
    """Normalize only facts explicitly supplied by the selected room-rate bundle.

    One rate must identify each requested room with exact one-based numbering.
    Ages are an exact multiset, not an inference from the request. The only empty
    age normalization is explicit childCount=0 plus request children=[]. Optional
    maxOccupancy, when supplied, must be a sufficient positive integer; absence
    leaves capacity_verified=None, without manufacturing a capacity.

    constraint_evidence contains confirmed booleans only. RFN without an exact
    penalty deadline is not promoted to a hard free-cancellation guarantee.
    free_cancellation_now, tags and per-room deadlines remain separate facts.
    """
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be an explicit timezone-aware datetime")
    now = now.astimezone(timezone.utc)
    issues: list[dict[str, Any]] = []

    def issue(code: str, path: str, message: str) -> None:
        item = {"code": code, "path": path, "message": message}
        if item not in issues:
            issues.append(item)

    valid_rates = isinstance(rates, list) and bool(rates) and all(isinstance(rate, dict) for rate in rates)
    request_valid = isinstance(occupancies, list) and bool(occupancies)
    if request_valid:
        for index, room in enumerate(occupancies):
            if (not isinstance(room, dict) or type(room.get("adults")) is not int or room["adults"] < 1
                    or not isinstance(room.get("children"), list)
                    or any(type(age) is not int or not 0 <= age < 18 for age in room["children"])):
                request_valid = False
                issue("invalid_requested_occupancy", f"occupancies[{index}]", "Состав комнаты в запросе неполон или некорректен.")
    if not valid_rates:
        issue("invalid_rates", "rates", "Не получен непустой список комнатных тарифов.")
    if not request_valid:
        issue("invalid_occupancies", "occupancies", "Не получен полный состав запрошенных комнат.")
    rows = rates if valid_rates else []
    allocation = valid_rates and request_valid and len(rows) == len(occupancies)
    if valid_rates and request_valid and len(rows) != len(occupancies):
        issue("room_count_mismatch", "rates", "Количество тарифов не совпадает с количеством комнат.")
    numbers = [rate.get("occupancyNumber") for rate in rows]
    if request_valid and (any(type(number) is not int for number in numbers)
                          or sorted(numbers) != list(range(1, len(occupancies) + 1))):
        allocation = False
        issue("occupancy_number_mismatch", "rates", "Нужна ровно одна запись для каждого occupancyNumber от 1 до числа комнат.")
    capacities: list[bool | None] = []
    room_facts: list[dict[str, Any]] = []
    fee_states: list[bool | None] = []
    breakfasts: list[bool | None] = []
    cancel_facts: list[dict[str, Any]] = []

    for index, rate in enumerate(rows):
        path = f"rates[{index}]"
        adults, child_count = rate.get("adultCount"), rate.get("childCount")
        ages = rate.get("childrenAges", _MISSING)
        number = rate.get("occupancyNumber")
        request = occupancies[number - 1] if request_valid and type(number) is int and 1 <= number <= len(occupancies) else None
        # Explicit zero children proves there are no ages; no other absent guest
        # parameter is filled from a request.
        if type(child_count) is int and child_count == 0 and request and request["children"] == [] and (ages is _MISSING or ages is None):
            ages = []
        guest_valid = (type(adults) is int and adults >= 1 and type(child_count) is int and child_count >= 0
                       and isinstance(ages, list) and all(type(age) is int and 0 <= age < 18 for age in ages)
                       and child_count == len(ages))
        matches = bool(guest_valid and request and adults == request["adults"] and Counter(ages) == Counter(request["children"]))
        if not matches:
            allocation = False
            issue("guest_mismatch", path, "Ответ поставщика не подтверждает точный состав гостей этой комнаты.")
        capacity = rate.get("maxOccupancy", _MISSING)
        if capacity is _MISSING:
            capacity_ok = None
        else:
            capacity_ok = bool(type(capacity) is int and capacity > 0 and guest_valid and capacity >= adults + child_count)
            if not capacity_ok:
                allocation = False
                issue("invalid_capacity", path + ".maxOccupancy", "Вместимость не подтверждена либо меньше числа гостей.")
        capacities.append(capacity_ok)
        room_facts.append({"occupancy_number": number if type(number) is int else None,
                           "guest_counts_match": matches, "capacity_verified": capacity_ok,
                           "reported_capacity": capacity if type(capacity) is int else None})
        board = rate.get("boardType")
        board = board.strip().upper() if isinstance(board, str) else None
        breakfasts.append(True if board == "BI" else False if board == "RO" else None)

        retail = rate.get("retailRate")
        taxes = retail.get("taxesAndFees", _MISSING) if isinstance(retail, dict) else _MISSING
        if taxes is _MISSING:
            fee_states.append(None)
            issue("taxes_missing", path + ".retailRate.taxesAndFees", "Неизвестно, включены ли все обязательные сборы.")
        elif taxes is None:
            fee_states.append(True)  # Explicit supplier disclosure contract.
        elif not isinstance(taxes, list):
            fee_states.append(None)
            issue("taxes_malformed", path + ".retailRate.taxesAndFees", "Некорректная структура налогов и сборов.")
        else:
            statuses = []
            for tax_index, tax in enumerate(taxes):
                tax_path = path + f".retailRate.taxesAndFees[{tax_index}]"
                valid_tax = (isinstance(tax, dict) and type(tax.get("included")) is bool
                             and _number(tax.get("amount")) is not None
                             and isinstance(tax.get("currency"), str) and re.fullmatch(r"[A-Za-z]{3}", tax["currency"]))
                if not valid_tax:
                    statuses.append(None)
                    issue("tax_malformed", tax_path, "Налог или сбор не содержит проверяемого included/amount/currency.")
                else:
                    statuses.append(tax["included"])
            fee_state = False if any(value is False for value in statuses) else None if any(value is None for value in statuses) else True
            fee_states.append(fee_state)
            if fee_state is False:
                issue("excluded_fees", path + ".retailRate.taxesAndFees", "Есть невключённые сборы; полная стоимость компонента не подтверждена.")
        cancel_facts.append(_cancellation(rate.get("cancellationPolicies"), now, path, issue))

    breakfast = _uniform(breakfasts)
    if rows and breakfast is None:
        issue("board_mixed_or_unknown", "rates.boardType", "Питание комнат различается или не подтверждено кодами BI/RO.")
    fees_included = False if any(value is False for value in fee_states) else _uniform(fee_states)
    capacity_verified = False if any(value is False for value in capacities) else _uniform(capacities)
    tags_known = bool(cancel_facts) and all(item["refundable_tag"] in {"RFN", "NRFN"} for item in cancel_facts)
    from_tags = _uniform([True if item["refundable_tag"] == "RFN" else False if item["refundable_tag"] == "NRFN" else None for item in cancel_facts])
    free_values = [item["free_cancellation_now"] for item in cancel_facts]
    free_now = False if any(value is False for value in free_values) else _uniform(free_values)
    # RFN alone, or a currently zero fee without any exact deadline, does not
    # promise free cancellation for an unspecified future interval.
    refundable = _uniform([False if item["refundable_tag"] == "NRFN" or item["cancellation_charge_applies_now"] is True
                           else item["free_cancellation_now"] if item["cancellation_deadline"] else None for item in cancel_facts])
    evidence = {}
    if breakfast is not None:
        evidence["breakfast"] = breakfast
    if refundable is not None:
        evidence["refundable"] = refundable
    deadlines = [item["cancellation_deadline"] for item in cancel_facts]
    deadline = min(deadlines) if deadlines and all(item is not None for item in deadlines) else None
    return {"allocation_verified": bool(allocation), "capacity_verified": capacity_verified,
            "mandatory_fees_known": fees_included is True, "fees_included": fees_included,
            "constraint_evidence": evidence, "refundable_tag_verified": tags_known,
            "refundable_from_tags": from_tags, "free_cancellation_now": free_now,
            "cancellation_deadline": deadline, "room_facts": room_facts,
            "cancellation_by_room": [{"occupancy_number": row.get("occupancyNumber") if type(row.get("occupancyNumber")) is int else None, **facts} for row, facts in zip(rows, cancel_facts)],
            "issues": issues, "warnings": list(dict.fromkeys(item["message"] for item in issues)),
            "source_type": "SANDBOX", "availability_verified": False,
            "supplier_verified": False, "ready_for_real_booking": False}


def _cancellation(policy: Any, now: datetime, path: str, issue) -> dict[str, Any]:
    result = {"refundable_tag": None, "free_cancellation_now": None,
              "cancellation_deadline": None, "cancellation_charge_applies_now": None}
    if not isinstance(policy, dict):
        issue("cancellation_missing", path + ".cancellationPolicies", "Условия отмены не получены.")
        return result
    tag = policy.get("refundableTag")
    tag = tag.strip().upper() if isinstance(tag, str) else None
    result["refundable_tag"] = tag if tag in {"RFN", "NRFN"} else None
    if tag == "NRFN":
        result["free_cancellation_now"] = False
    if policy.get("hotelRemarks"):
        issue("hotel_remarks_present", path + ".cancellationPolicies.hotelRemarks", "У тарифа есть дополнительные условия отеля; их нужно показать отдельно.")
    infos = policy.get("cancelPolicyInfos")
    if not isinstance(infos, list) or not infos:
        if tag != "NRFN":
            issue("cancellation_schedule_missing", path + ".cancellationPolicies.cancelPolicyInfos", "Нет точного графика штрафов отмены.")
        return result
    records: dict[datetime, Decimal] = {}
    malformed = False
    for index, item in enumerate(infos):
        item_path = path + f".cancellationPolicies.cancelPolicyInfos[{index}]"
        if not isinstance(item, dict):
            malformed = True
        else:
            zone = item.get("timezone") or item.get("timeZone") or policy.get("timezone") or policy.get("timeZone")
            instant = _timestamp(item.get("cancelTime"), zone)
            amount = _number(item.get("amount"))
            if instant is None or amount is None or instant in records and records[instant] != amount:
                malformed = True
            else:
                records[instant] = amount
                continue
        issue("cancellation_schedule_invalid", item_path, "Штраф или срок отмены не подтверждён однозначным временем с timezone.")
    if malformed or not records or tag not in {"RFN", "NRFN"}:
        return result
    ordered = sorted(records.items())
    active = [(instant, amount) for instant, amount in ordered if instant <= now]
    if active:
        result["cancellation_charge_applies_now"] = active[-1][1] > 0
    if tag == "NRFN":
        return result
    if active and active[-1][1] > 0:
        result["free_cancellation_now"] = False
        zero_dates = [instant for instant, amount in active if amount == 0]
        last_zero = max(zero_dates) if zero_dates else None
        deadline = min(instant for instant, amount in active if amount > 0 and (last_zero is None or instant > last_zero))
        result["cancellation_deadline"] = deadline.isoformat()
    else:
        future_penalties = [instant for instant, amount in ordered if instant > now and amount > 0]
        if future_penalties:
            result["free_cancellation_now"] = True
            result["cancellation_deadline"] = min(future_penalties).isoformat()
        elif active and active[-1][1] == 0:
            result["free_cancellation_now"] = True  # Snapshot only; no future guarantee.
    return result
