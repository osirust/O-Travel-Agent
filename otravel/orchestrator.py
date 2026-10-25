"""Bounded component search. Calls only the injected gateway, never HTTP itself.

DEMO preserves the existing inventory contract. HYBRID performs one search per
stay/leg and accepts only explicit provider evidence; missing parts may use a
marked deterministic DEMO alternative. There is no Cartesian basket search.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import inspect
import json
from typing import Any, Callable

from .domain import TripState, validate_trip
from .inventory import DemoInventory, _allocation
from .planning import assemble_plan, compare_plans
from .providers import ProviderError


_IATA = {"Москва": "MOW", "Санкт-Петербург": "LED", "Сочи": "AER", "Казань": "KZN",
         "Стамбул": "IST", "Анталья": "AYT", "Дубай": "DXB"}
_TOTALS = {"hotel": {"PROVIDER_ROOMS_STAY_TOTAL", "INDICATIVE_STAY_TOTAL", "PROVIDER_DISPLAY_RATE_TOTAL", "COMPONENT_TOTAL"},
           "flight": {"GROUP_ITINERARY_TOTAL", "INDICATIVE_ITINERARY_TOTAL", "COMPONENT_TOTAL"}}


def _issue(code: str, message: str, **details: Any) -> dict:
    return {"code": code, "message": message, "details": details}


def _timestamp(value: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return moment.astimezone(timezone.utc) if moment.tzinfo is not None else None
    except (TypeError, AttributeError, ValueError):
        return None


def _comparison_input(plan: dict) -> dict:
    """Project a saved legacy DEMO basket for display; never revalidate it as current."""
    if isinstance(plan.get("components"), list):
        return plan
    result = deepcopy(plan)
    result["components"] = []
    for offer in result.get("offers", []):
        component_id, kind = offer.get("component_id"), offer.get("kind")
        if kind == "hotel":
            covers = [f"hotel:{component_id}:{room['room_id']}" for room in offer.get("room_assignments", [])]
        elif kind == "transfer":
            covers = [f"transfer:{str(component_id).removeprefix('transfer-')}"]
        else:
            covers = [f"leg:{component_id}"]
        digest = sha256(json.dumps(offer, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        result["components"].append({"component_id": offer["offer_id"], "covers": covers,
            "amount_minor": offer["total_minor"], "currency": offer["currency"],
            "source_type": offer["source_type"], "snapshot_hash": digest})
    result["snapshot_hash"] = sha256(json.dumps({"trip_id": plan.get("trip_id"), "state_version": plan.get("state_version"),
        "offers": plan.get("offers", [])}, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return result


class TripSearch:
    def __init__(self, travel: Any, *, max_tool_calls: int = 16, clock: Callable | None = None):
        self.travel = travel
        self.inventory = DemoInventory()
        self.max_tool_calls = min(30, max(0, int(max_tool_calls)))
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    async def search(self, state: TripState | dict, previous_plan: dict | None = None,
                     event_sink: Callable | None = None) -> dict:
        state = TripState.model_validate(state).model_copy(deep=True)
        policy = state.search_policy
        if policy.mode == "DEMO":
            result = self.inventory.search_trip(state)
            if previous_plan is not None:
                for plan in result["plans"]:
                    plan["comparison"] = compare_plans(_comparison_input(previous_plan), _comparison_input(plan))
            return {**result, "tool_calls_used": 0, "max_tool_calls": self.max_tool_calls}
        def current_time() -> datetime:
            moment = self.clock()
            if not isinstance(moment, datetime) or moment.tzinfo is None:
                raise ValueError("TripSearch clock must return timezone-aware datetime")
            return moment.astimezone(timezone.utc)
        current_time()
        errors = [issue for issue in validate_trip(state) if not (issue["code"] == "unsupported_budget" and state.budget and state.budget.scope == "whole_trip")]
        questions = list(dict.fromkeys(i["message"] for i in errors if i["code"].startswith("missing_")))
        if errors:
            return {"plans": [], "questions": questions, "warnings": [], "violations": errors,
                    "source_type": "INCOMPLETE", "tool_calls_used": 0, "max_tool_calls": self.max_tool_calls}
        warnings: list[str] = []
        component_errors: list[dict] = []
        selected: list[dict] = []
        rejected: dict[str, set[str]] = {}
        used = 0
        currency = state.budget.currency.upper() if state.budget else "RUB"

        async def emit(event_type: str, payload: dict) -> None:
            if event_sink is not None:
                result = event_sink(event_type, deepcopy(payload))
                if inspect.isawaitable(result):
                    await result

        async def call(method: str, component_id: str, target_provider: str, *args: Any, **kwargs: Any) -> Any:
            nonlocal used
            if used >= self.max_tool_calls:
                raise ProviderError("TOOL_BUDGET_EXHAUSTED", "Лимит вызовов поиска исчерпан.", provider=target_provider)
            target = getattr(self.travel, method, None)
            if target is None:
                raise ProviderError("UNSUPPORTED_PROVIDER", "Gateway не предоставляет нужный read-only метод.", provider=target_provider)
            used += 1
            event = {"component_id": component_id, "provider": target_provider, "method": method, "attempt": used}
            # Persistence failure here propagates before invoking the gateway.
            await emit("TOOL_CALL_STARTED", event)
            try:
                value = target(*args, **kwargs)
                value = await value if inspect.isawaitable(value) else value
            except ProviderError as exc:
                await emit("TOOL_CALL_FAILED", {**event, "code": exc.code})
                raise
            except Exception:
                await emit("TOOL_CALL_FAILED", {**event, "code": "PROVIDER_FAILURE"})
                raise ProviderError("PROVIDER_FAILURE", "Поисковый gateway завершился ошибкой.", provider=target_provider) from None
            await emit("TOOL_CALL_COMPLETED", {**event, "count": len(value) if isinstance(value, list) else None,
                                               "checked_at": current_time().isoformat()})
            return value

        def unavailable(component_id: str, code: str, message: str) -> None:
            issue = _issue(code, message, component_id=component_id)
            component_errors.append(issue)
            suffix = " Будет рассмотрен явно обозначенный DEMO-компонент." if policy.demo_fallback else " DEMO fallback запрещён политикой."
            warnings.append(f"{component_id}: {message}{suffix}")

        def choose(rows: Any, kind: str, component_id: str, provider: str, binding: dict, report: bool = True) -> dict | None:
            if not isinstance(rows, list):
                unavailable(component_id, "INVALID_RESPONSE", "Gateway не вернул список предложений.")
                return None
            eligible = []
            reasons: set[str] = set()
            for raw in rows[:30]:
                if not isinstance(raw, dict) or not isinstance(raw.get("details"), dict):
                    reasons.add("invalid_shape")
                    continue
                offer, details = deepcopy(raw), deepcopy(raw["details"])
                if offer.get("provider") != provider or not isinstance(offer.get("offer_id"), str):
                    reasons.add("source_mismatch")
                    continue
                if offer.get("source_type") not in {"SANDBOX", "LIVE_INDICATIVE", "LIVE_BOOKABLE"}:
                    reasons.add("unknown_source_type")
                    continue
                if offer.get("currency") != currency or type(offer.get("amount_minor")) is not int or offer["amount_minor"] < 0:
                    reasons.add("unpriced_or_fx_required")
                    continue
                if offer.get("price_kind") not in _TOTALS[kind]:
                    reasons.add("not_component_total")
                    continue
                if details.get("mandatory_fees_known") is not True and details.get("fees_included") is not True:
                    reasons.add("mandatory_fees_unknown")
                    continue
                if kind == "hotel" and details.get("allocation_verified") is not True:
                    reasons.add("allocation_unverified")
                    continue
                if kind == "flight" and (details.get("group_quote") is False or details.get("passengers_count", len(state.travelers)) != len(state.travelers)):
                    reasons.add("group_quote_unverified")
                    continue
                retrieved, expiry = _timestamp(offer.get("retrieved_at")), _timestamp(offer.get("expires_at"))
                checked_at = current_time()
                if retrieved is None or expiry is None or not retrieved <= checked_at < expiry:
                    reasons.add("stale_source_snapshot")
                    continue
                if "binding" in details and details["binding"] != binding:
                    reasons.add("source_binding_mismatch")
                    continue
                evidence = details.get("constraint_evidence", {})
                supported = True
                for condition in state.constraints:
                    relevant = condition.key == "pets" or kind == "hotel" and condition.key in {"breakfast", "refundable", "pets_allowed"}
                    if (not condition.hard or not relevant or condition.stay_id is not None and condition.stay_id != binding.get("stay_id")):
                        continue
                    fact = evidence.get(condition.key) if isinstance(evidence, dict) else None
                    if fact != condition.value or type(fact) is not type(condition.value):
                        supported = False
                if not supported:
                    reasons.add("hard_constraint_unverified")
                    continue
                # These are request-scoping facts, not invented supplier flags.
                details["binding"], details["kind"] = deepcopy(binding), kind
                offer["details"] = details
                eligible.append(offer)
            if not eligible:
                rejected.setdefault(component_id, set()).update(reasons or {"no_matches"})
                if report:
                    unavailable(component_id, "NO_VERIFIED_COMPONENT", "Нет пригодного тарифа: " + ", ".join(sorted(reasons) or ["no_matches"]))
                return None
            return min(eligible, key=lambda offer: (offer["amount_minor"], offer["offer_id"]))

        common = {"trip_id": state.trip_id, "state_version": state.state_version}
        for stay in state.stays:
            provider = policy.hotel_provider
            if provider == "liteapi" and not policy.guest_nationality:
                message = "Для живого LiteAPI-поиска нужно явно указать guest_nationality ISO-2."
                unavailable(stay.id, "MISSING_NATIONALITY", message)
                if not policy.demo_fallback:
                    questions.append(message)
                continue
            if provider == "serpapi" and len(stay.rooms) != 1:
                unavailable(stay.id, "UNSUPPORTED_MULTIROOM", "SerpApi не подтверждает multiroom allocation этой остановки.")
                continue
            # Reuse group-aware placement, without asserting a real room's
            # capacity. The provider must independently verify allocation.
            assignments = _allocation(state, stay, max(1, len(state.travelers)))
            if assignments is None:
                unavailable(stay.id, "ALLOCATION_UNRESOLVED", "Нельзя распределить группу по заданным комнатам без смешения семей.")
                continue
            assignments = [{"room_id": room["room_id"], "traveler_ids": list(room["traveler_ids"])} for room in assignments]
            people = {person.id: person for person in state.travelers}
            occupancies = [{"adults": sum(people[p].type == "adult" for p in room["traveler_ids"]),
                            "children": [people[p].age_on(stay.check_in) for p in room["traveler_ids"] if people[p].type == "child"]}
                           for room in assignments]
            request = {"checkin": stay.check_in.isoformat(), "checkout": stay.check_out.isoformat(),
                       "currency": currency, "occupancies": occupancies}
            try:
                if provider == "liteapi":
                    if stay.hotel_id:
                        ids = [stay.hotel_id]
                    else:
                        resolved = await call("resolve_hotels", stay.id, provider, stay.city, limit=3)
                        if not isinstance(resolved, list):
                            raise ProviderError("INVALID_RESPONSE", "Resolver не вернул список гостиниц.", provider=provider)
                        ids = [row["id"] for row in resolved[:3] if isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"]]
                    if not ids:
                        raise ProviderError("NO_MATCHES", "Нет проверенных provider hotel IDs для города.", provider=provider)
                    request.update(hotelIds=ids, guestNationality=policy.guest_nationality)
                    for constraint in state.constraints:
                        if constraint.hard and constraint.value is True and constraint.stay_id in (None, stay.id):
                            if constraint.key == "breakfast":
                                request["boardType"] = "BI"
                            elif constraint.key == "refundable":
                                request["refundableRatesOnly"] = True
                else:
                    request["city"] = stay.city
                rows = await call("search_hotels", stay.id, provider, request, provider=provider)
                # hotel_id comes from each actual response, never a name match.
                bound_rows = []
                for row in rows[:30] if isinstance(rows, list) else []:
                    if not isinstance(row, dict) or not isinstance(row.get("details"), dict):
                        continue
                    hotel_id = row["details"].get("hotel_id")
                    if not hotel_id:
                        continue
                    if provider == "liteapi" and hotel_id not in request["hotelIds"]:
                        rejected.setdefault(stay.id, set()).add("unrequested_hotel_id")
                        continue
                    binding = {**common, "stay_id": stay.id, "room_ids": [room["room_id"] for room in assignments],
                               "room_assignments": assignments, "city": stay.city, "checkin": request["checkin"],
                               "checkout": request["checkout"], "hotel_id": hotel_id}
                    candidate = choose([row], "hotel", stay.id, provider, binding, False)
                    if candidate:
                        bound_rows.append(candidate)
                if bound_rows:
                    selected.append(min(bound_rows, key=lambda item: (item["amount_minor"], item["offer_id"])))
                elif not rows:
                    unavailable(stay.id, "NO_MATCHES", "Поставщик не вернул гостиничных предложений.")
                elif not any(issue["details"]["component_id"] == stay.id for issue in component_errors):
                    reasons = ", ".join(sorted(rejected.get(stay.id, {"provider_hotel_id_unknown"})))
                    unavailable(stay.id, "NO_VERIFIED_COMPONENT", "Нет пригодного гостиничного тарифа: " + reasons)
            except ProviderError as exc:
                unavailable(stay.id, exc.code, exc.message)

        legs = list(state.legs)
        if not legs:
            # Domain permits fully specified stays with derived route in DEMO.
            unavailable("route", "MISSING_EXPLICIT_LEGS", "Для HYBRID нужны явные leg IDs, даты и маршрут.")
        for leg in legs:
            if leg.mode != "flight":
                unavailable(leg.id, "UNSUPPORTED_LIVE_TRANSPORT", "Flight gateway не подменяет запрошенный поезд самолётом.")
                continue
            if leg.departure_date is None:
                unavailable(leg.id, "MISSING_DEPARTURE_DATE", "Для provider request нужна явная дата отправления.")
                continue
            provider = policy.flight_provider
            request = {"slices": [{"origin": _IATA[leg.origin], "destination": _IATA[leg.destination],
                                    "departure_date": leg.departure_date.isoformat()}], "currency": currency,
                       "passengers": [{"type": "adult"} if person.type == "adult" else {"age": person.age_on(leg.departure_date)} for person in state.travelers],
                       "adults": sum(person.type == "adult" for person in state.travelers),
                       "children": sum(person.type == "child" for person in state.travelers)}
            binding = {**common, "leg_ids": [leg.id], "origin": leg.origin, "destination": leg.destination,
                       "departure_date": leg.departure_date.isoformat(), "traveler_ids": [p.id for p in state.travelers]}
            try:
                rows = await call("search_flights", leg.id, provider, request, provider=provider)
                chosen = choose(rows, "flight", leg.id, provider, binding)
                if chosen:
                    selected.append(chosen)
            except ProviderError as exc:
                unavailable(leg.id, exc.code, exc.message)
        if state.stays:
            warnings.append("Live transfer gateway не подключён; трансферы могут быть только явно обозначенными DEMO fallback-компонентами.")
        plan = assemble_plan(state, selected, demo_fallback=policy.demo_fallback,
                             previous_plan=_comparison_input(previous_plan) if previous_plan is not None else None, now=current_time())
        warnings.extend(plan["warnings"])
        ready = plan["ready_for_simulated_checkout"]
        result = {"plans": [plan] if ready else [], "questions": list(dict.fromkeys(questions)),
                  "warnings": list(dict.fromkeys(warnings)), "violations": plan["blockers"],
                  "component_errors": component_errors, "partial_plan": None if ready else plan,
                  "source_type": plan["source_type"], "tool_calls_used": used, "max_tool_calls": self.max_tool_calls,
                  "comparison_scope": "first_30_offers_per_component_not_global_market_minimum",
                  "price_kind": "simulated_estimate", "ready_for_real_booking": False,
                  "search_policy": policy.model_dump(mode="json")}
        await emit("TRIP_SEARCH_COMPLETED", {"source_type": plan["source_type"], "ready_for_simulated_checkout": ready,
                    "tool_calls_used": used, "blocker_count": len(plan["blockers"])})
        return result
