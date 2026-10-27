"""Identity-only projection of the latest current UI preview selection."""
from __future__ import annotations

from datetime import datetime

_SOURCES = frozenset({"DEMO", "SANDBOX", "LIVE_INDICATIVE", "LIVE_BOOKABLE"})


def _identifier(value):
    return value if isinstance(value, str) and 0 < len(value) <= 200 and value == value.strip() else None


def _coherent(*values):
    present = [value for value in values if value is not None]
    if not present or any(not _identifier(value) for value in present):
        return None
    return present[0] if all(value == present[0] for value in present) else None


def project_current_selection(session: dict, quote: dict | None, expected_version: int, now: datetime) -> dict | None:
    """Current fresh selection only; prices and checkout credentials never leave.

    Expired, revoked, ordered or stale rows yield no selection. The Store must
    supply the latest row of any status, not the latest qualifying old row.
    """
    if (type(expected_version) is not int or expected_version < 0 or not isinstance(session, dict)
            or not isinstance(quote, dict) or type(session.get("state_version")) is not int
            or session["state_version"] != expected_version or quote.get("status") != "active"
            or type(quote.get("state_version")) is not int or quote["state_version"] != expected_version
            or quote.get("session_id") != session.get("session_id")
            or not isinstance(now, datetime) or now.utcoffset() is None):
        return None
    expiry = quote.get("expires_at")
    if not isinstance(expiry, datetime) or expiry.utcoffset() is None or expiry <= now:
        return None
    state, plan = session.get("state"), quote.get("plan")
    if (not isinstance(state, dict) or not isinstance(plan, dict)
            or not _identifier(session.get("trip_id")) or state.get("trip_id") != session["trip_id"]
            or type(state.get("state_version")) is not int or state["state_version"] != expected_version
            or plan.get("trip_id") != session["trip_id"] or not _identifier(plan.get("plan_id"))
            or type(plan.get("state_version")) is not int or plan["state_version"] != expected_version
            or not isinstance(plan.get("source_type"), str) or plan["source_type"] not in _SOURCES | {"MIXED"}):
        return None
    stays, offers = state.get("stays"), plan.get("offers")
    if (not isinstance(stays, list) or not 1 <= len(stays) <= 10
            or not isinstance(offers, list) or not 1 <= len(offers) <= 40
            or any(not isinstance(stay, dict) or not _identifier(stay.get("id")) for stay in stays)):
        return None
    owned = {stay["id"]: stay for stay in stays}
    if len(owned) != len(stays):
        return None
    hotels = {}
    for offer in offers:
        if not isinstance(offer, dict):
            return None
        details = offer.get("details", {})
        if not isinstance(details, dict):
            return None
        if offer.get("kind", details.get("kind")) != "hotel":
            continue
        if "kind" in offer and "kind" in details and offer["kind"] != details["kind"]:
            return None
        binding = details.get("binding", {})
        if not isinstance(binding, dict):
            return None
        if ("trip_id" in binding and binding["trip_id"] != session["trip_id"]
                or "state_version" in binding and (type(binding["state_version"]) is not int
                    or binding["state_version"] != expected_version)):
            return None
        stay_id = _coherent(binding.get("stay_id"), offer.get("component_id"))
        if stay_id not in owned:
            return None
        stay = owned[stay_id]
        hotel_id = _coherent(binding.get("hotel_id"), details.get("hotel_id"), offer.get("hotel_id"))
        city = _coherent(binding.get("city"), details.get("city"), offer.get("city"))
        checkin = _coherent(binding.get("checkin"), details.get("checkin"), offer.get("check_in"))
        checkout = _coherent(binding.get("checkout"), details.get("checkout"), offer.get("check_out"))
        source = offer.get("source_type")
        if (not hotel_id or city != stay.get("city") or checkin != stay.get("check_in")
                or checkout != stay.get("check_out") or not isinstance(source, str) or source not in _SOURCES
                or stay.get("hotel_id") is not None and hotel_id != stay["hotel_id"]):
            return None
        previous = hotels.get(stay_id)
        if previous and (previous["hotel_id"], previous["city"], previous["source_type"]) != (hotel_id, city, source):
            return None
        if not previous:
            name = offer.get("name", offer.get("description"))
            hotels[stay_id] = {"stay_id": stay_id, "hotel_id": hotel_id,
                "name": name[:200] if isinstance(name, str) and name.strip() else None,
                "city": city, "source_type": source}
    if set(hotels) != set(owned):
        return None
    return {"source": "ui_preview_selection", "selection_status": "current_reference_only",
        "trip_id": session["trip_id"], "plan_id": plan["plan_id"], "state_version": expected_version, "source_type": plan["source_type"],
        "hotels": [hotels[stay["id"]] for stay in stays], "reference_only": True, "prices_included": False}
