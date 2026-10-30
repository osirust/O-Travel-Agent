"""Read-only, bounded projections of plans actually shown in one session.

The caller must authenticate/authorize session_id before calling this helper.
History is untrusted reference data, never a current quote or reusable consent.
No Store writes, source refreshes, model calls, or new history tables.
"""

from __future__ import annotations

from typing import Any


def _text(value: Any) -> str | None:
    # Never truncate identifiers into different, nonexistent identifiers.
    return value if isinstance(value, str) and 0 < len(value) <= 200 else None


def _project(plan: dict, run_id: str, position: int) -> dict:
    hotels, transport = [], []
    seen_hotels = set()
    for offer in plan.get("offers", [])[:40]:
        if not isinstance(offer, dict):
            continue
        details = offer.get("details") if isinstance(offer.get("details"), dict) else {}
        binding = details.get("binding") if isinstance(details.get("binding"), dict) else {}
        kind = offer.get("kind", details.get("kind"))
        source = _text(offer.get("source_type"))
        if kind == "hotel":
            hotel = {
                "stay_id": _text(binding.get("stay_id", offer.get("component_id"))),
                "hotel_id": _text(binding.get("hotel_id", offer.get("hotel_id", details.get("hotel_id")))),
                "city": _text(binding.get("city", offer.get("city"))),
                "check_in": _text(binding.get("checkin", offer.get("check_in", details.get("checkin")))),
                "check_out": _text(binding.get("checkout", offer.get("check_out", details.get("checkout")))),
                "source_type": source,
            }
            identity = tuple(hotel.values())
            if identity not in seen_hotels and hotel["stay_id"] and hotel["hotel_id"]:
                hotels.append(hotel)
                seen_hotels.add(identity)
        elif kind in {"flight", "train"}:
            leg_ids = binding.get("leg_ids", [binding["leg_id"]] if "leg_id" in binding else [])
            transport.append({
                "component_id": _text(offer.get("component_id", offer.get("offer_id"))),
                "leg_ids": [_text(value) for value in leg_ids[:10] if isinstance(value, str)] if isinstance(leg_ids, list) else [],
                "mode": kind, "origin": _text(binding.get("origin", offer.get("origin"))),
                "destination": _text(binding.get("destination", offer.get("destination"))),
                "departure_date": _text(binding.get("departure_date", offer.get("departure_date"))),
                "source_type": source,
            })
    amount = plan.get("total_minor")
    return {
        "plan_id": _text(plan["plan_id"]), "state_version": plan["state_version"],
        "run_id": run_id, "shown_position": position,
        "source_type": _text(plan.get("source_type")), "hotels": hotels, "transport": transport,
        "historical_total_minor": amount if type(amount) is int and amount >= 0 else None,
        "currency": _text(plan.get("currency")), "price_status": "historical_unverified",
        "historical_only": True, "not_bookable": True, "consent_reusable": False,
        "requires_new_search_and_preview": True,
    }


async def plan_history(store, session_id: str, current_version: int, limit: int = 2) -> dict:
    """Return first displayed plan + recent projections after caller authorization.

    The first plan is the first item of the earliest successful assistant run
    containing valid own-trip plans. Recent plans are newest-run-first, retaining
    their positions within that run. Future-version/cross-session plans are
    excluded. Prices are reference-only even when their version equals current.
    """
    if type(current_version) is not int or current_version < 0:
        raise ValueError("current_version must be a nonnegative integer")
    if type(limit) is not int or not 0 <= limit <= 10:
        raise ValueError("history limit must be an integer from 0 to 10")
    session = await store.get_session(session_id)
    visible_version = min(current_version, session["state_version"])
    messages = await store.list_messages(session_id)
    cache: dict[str, list[dict]] = {}

    async def shown(message):
        if message.get("role") != "assistant" or message.get("session_id") != session_id:
            return []
        run_id = message.get("run_id")
        if not isinstance(run_id, str):
            return []
        if run_id in cache:
            return cache[run_id]
        run = await store.get_run(run_id)
        if run.get("session_id") != session_id or run.get("status") != "completed":
            cache[run_id] = []
            return []
        result = run.get("result")
        if not isinstance(result, dict):
            cache[run_id] = []
            return []
        payload = result.get("result") if isinstance(result.get("result"), dict) else result
        plans = payload.get("plans")
        projected = []
        if isinstance(plans, list):
            for position, plan in enumerate(plans[:10], 1):
                if not isinstance(plan, dict) or not isinstance(plan.get("offers"), list):
                    continue
                version = plan.get("state_version")
                if (not _text(plan.get("plan_id"))
                        or plan.get("trip_id") != session["trip_id"]
                        or type(version) is not int or not 0 <= version <= visible_version):
                    continue
                projected.append(_project(plan, run_id, position))
        cache[run_id] = projected
        return projected

    first = None
    for message in messages:
        candidates = await shown(message)
        if candidates:
            first = candidates[0]
            break
    recent, seen = [], set()
    if limit:
        for message in reversed(messages):
            for plan in await shown(message):
                identity = (plan["plan_id"], plan["state_version"])
                if identity in seen:
                    continue
                seen.add(identity)
                recent.append(plan)
                if len(recent) >= limit:
                    break
            if len(recent) >= limit:
                break
    return {
        "current_version": current_version, "observed_version": session["state_version"],
        "visible_through_version": visible_version,
        "first_saved_plan": first, "recent_plans": recent,
        "historical_only": True, "untrusted_reference_data": True,
        "notice": "История показанных вариантов. Старые цены/наличие и согласия не действуют; нужны новый поиск, preview и подтверждение.",
    }
