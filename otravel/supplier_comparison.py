"""Disclosed Google Hotels rate facts, never supplier-bookable equivalence."""
from __future__ import annotations

import hashlib
import json
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


def _text(value, limit=200):
    if not isinstance(value, str) or not value.strip():
        return None
    value = re.sub(r"https?://\S+|[\w.+-]+@[\w.-]+", "[REDACTED]", value)
    return re.sub(r"[\x00-\x1f\x7f]", " ", value).strip()[:limit]


def _labels(value):
    return [label for item in value[:12] if (label := _text(item))] if isinstance(value, list) else []


def _minor(value, currency):
    if type(value) not in {int, float}:
        return None
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount > Decimal(100000000000):
            return None
        exponent = 0 if currency in {"JPY", "KRW", "VND", "CLP", "ISK"} else 3 if currency in {"BHD", "JOD", "KWD", "OMR", "TND"} else 2
        return int((amount * (10 ** exponent)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    except (InvalidOperation, OverflowError):
        return None


def normalize_serp_hotel_details(body: dict, request: dict) -> dict:
    """Read-only response facts grouped by the exact validated request context.

    The caller validates the echoed property/query and the supported occupancy.
    No parent seller summary or room capacity becomes the priced guest count.
    Only room-level rates explicitly reporting that count enter the group.
    """
    requested_guests = request["occupancies"][0]["adults"]
    currency = request["currency"].upper()
    names, sellers, matching = set(), [], []
    excluded = {"other_guest_count": 0, "unknown_guest_count": 0, "missing_stay_total_after_taxes": 0,
        "seller_summary_not_a_room_quote": 0, "truncated_rows": 0, "matching_rate_limit_reached": False}
    for section in ("featured_prices", "prices"):
        rows = body.get(section, [])
        if not isinstance(rows, list):
            continue
        excluded["truncated_rows"] += max(0, len(rows) - 20)
        for seller in rows[:20]:
            if not isinstance(seller, dict) or not (name := _text(seller.get("source"), 120)):
                continue
            names.add(name)
            summary = {"supplier_name": name, "section": section, "matching_guest_rate_count": 0,
                "reported_summary_guests": seller.get("num_guests") if type(seller.get("num_guests")) is int else None}
            # Aggregate rows can describe a different room/guest count. Their
            # price is not exported as a quote for the user's requested party.
            excluded["seller_summary_not_a_room_quote"] += 1
            rooms = seller.get("rooms", [])
            rooms = rooms if isinstance(rooms, list) else []
            excluded["truncated_rows"] += max(0, len(rooms) - 20)
            for room in rooms[:20]:
                if not isinstance(room, dict):
                    continue
                rates = room.get("rates", [])
                rates = rates if isinstance(rates, list) else []
                excluded["truncated_rows"] += max(0, len(rates) - 20)
                for rate in rates[:20]:
                    if not isinstance(rate, dict):
                        continue
                    guests = rate.get("num_guests")
                    if type(guests) is not int:
                        excluded["unknown_guest_count"] += 1
                        continue
                    if guests != requested_guests:
                        excluded["other_guest_count"] += 1
                        continue
                    if len(matching) >= 64:
                        excluded["matching_rate_limit_reached"] = True
                        continue
                    total = rate.get("total_rate")
                    amount = _minor(total.get("extracted_after_taxes_fees"), currency) if isinstance(total, dict) else None
                    if amount is None:
                        excluded["missing_stay_total_after_taxes"] += 1
                    beds = []
                    if isinstance(rate.get("beds"), list):
                        for bed in rate["beds"][:8]:
                            if (isinstance(bed, dict) and (bed_type := _text(bed.get("type"), 60))
                                    and type(bed.get("count")) is int and 0 < bed["count"] <= 20):
                                beds.append({"type": bed_type, "count": bed["count"]})
                    cancel_date = _text(rate.get("free_cancellation_until_date"), 80)
                    cancel_time = _text(rate.get("free_cancellation_until_time"), 80)
                    deadline = " ".join(part for part in (cancel_date, cancel_time) if part)
                    year_known = bool(re.search(r"\b20\d{2}\b", deadline))
                    timezone_known = bool(re.search(r"\b(?:UTC|GMT)\b|[+-]\d{2}:?\d{2}\b|\b\d{2}:\d{2}(?::\d{2})?Z\b", deadline))
                    breakfast = rate.get("breakfast_included") if type(rate.get("breakfast_included")) is bool else None
                    cancellation = rate.get("free_cancellation") if type(rate.get("free_cancellation")) is bool else None
                    missing = ["room_product_identity", "supplier_eligibility", "final_mandatory_fees"]
                    if not _text(room.get("name")):
                        missing.append("room_name")
                    if len(beds) != 1:
                        missing.append("bed_configuration")
                    if breakfast is None:
                        missing.append("board")
                    if cancellation is None:
                        missing.append("cancellation")
                    if cancellation is True and (not year_known or not timezone_known):
                        missing.append("cancellation_deadline_year_timezone")
                    if amount is None:
                        missing.append("stay_total_after_taxes")
                    item = {"supplier_name": name, "room_name": _text(room.get("name")), "num_guests": guests,
                        "amount_minor": amount, "currency": currency, "price_scope": "source_reported_stay_total_after_taxes_fees",
                        "beds": beds, "breakfast_included": breakfast, "free_cancellation": cancellation,
                        "cancellation_until_date_text": cancel_date, "cancellation_until_time_text": cancel_time,
                        "cancellation_deadline_verified": False, "inclusions": _labels(rate.get("inclusions")),
                        "discount_remarks": _labels(rate.get("discount_remarks")) + _labels(seller.get("discount_remarks")),
                        "benefits": _labels(seller.get("benefits")), "missing_evidence": missing,
                        "equivalent": False, "supplier_bookable": False}
                    identity = {k: v for k, v in item.items() if k != "amount_minor"}
                    item["reference_rate_id"] = "serp-rate:" + hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
                    matching.append(item)
                    summary["matching_guest_rate_count"] += 1
            sellers.append(summary)
    selected = next((item for item in matching if item["amount_minor"] is not None), None)
    context = {"property_token": request["property_token"], "checkin": request.get("checkin", request.get("check_in_date")),
        "checkout": request.get("checkout", request.get("check_out_date")), "currency": currency, "num_guests": requested_guests}
    return {"status": "NOT_EQUIVALENT_OR_UNPROVEN", "source_type": "LIVE_INDICATIVE", "requested_guests": requested_guests,
        "seller_count": len(names), "seller_names": sorted(names), "seller_summaries": sellers,
        "comparison_groups": [{**context, "rates": matching, "equivalent": False}] if matching else [],
        "selected_reference_rate": selected, "reference_selection_policy": "first_disclosed_matching_guest_stay_total_not_lowest_equivalent",
        "excluded": excluded, "equivalent": False, "supplier_bookable": False,
        "warning": "Сравниваются раскрытые поисковые данные продавцов. Одинаковость номеров, условий, итоговых сборов и возможность покупки не подтверждены."}
