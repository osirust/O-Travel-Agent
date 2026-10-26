"""Pure canonical basket assembly. No HTTP, database, clock reads or booking.

Provider offers require details.binding with trip_id/state_version and explicit
stay/room or leg IDs. A binding associates data; it does not invent allocation,
fees or inventory verification. Existing DemoInventory snapshots stay intact.
"""
from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from .domain import TripState, route_edges, validate_trip
from .fx import FxUnavailable, convert_minor, validate_snapshot
from .inventory import DemoInventory

_PINNED_SOURCE = {"liteapi": "SANDBOX", "duffel": "SANDBOX", "hotelbeds": "SANDBOX",
                  "ratehawk": "SANDBOX", "serpapi": "LIVE_INDICATIVE", "travelpayouts": "LIVE_INDICATIVE"}
_TOTAL_KINDS = {"PROVIDER_ROOMS_STAY_TOTAL", "GROUP_ITINERARY_TOTAL", "INDICATIVE_STAY_TOTAL",
                "INDICATIVE_ITINERARY_TOTAL", "PROVIDER_DISPLAY_RATE_TOTAL", "COMPONENT_TOTAL"}
_KNOWN_HARD_CONSTRAINTS = {"breakfast", "refundable", "pets_allowed", "pets", "nonstop"}


def _digest(value: Any) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _issue(code: str, message: str, **details: Any) -> dict:
    return {"code": code, "message": message, "details": details}


def _stamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except ValueError:
        return None


def required_components(state: TripState | dict) -> list[str]:
    state = TripState.model_validate(state)
    rooms = [f"hotel:{stay.id}:{room.id}" for stay in state.stays for room in stay.rooms]
    legs = [f"leg:{leg.id}" for leg in state.legs] if state.legs else [f"leg:leg-{i}" for i in range(len(route_edges(state)))]
    return rooms + legs + [f"transfer:{stay.id}" for stay in state.stays]


def _demo_binding(state: TripState, offer: dict) -> dict:
    component = offer.get("component_id")
    common = {"trip_id": state.trip_id, "state_version": state.state_version}
    if offer.get("kind") == "hotel":
        return {**common, "stay_id": component, "room_ids": [r["room_id"] for r in offer["room_assignments"]],
                "room_assignments": deepcopy(offer["room_assignments"]), "city": offer.get("city"),
                "checkin": offer.get("check_in"), "checkout": offer.get("check_out"), "hotel_id": offer.get("hotel_id")}
    if offer.get("kind") == "transfer":
        return {**common, "stay_id": str(component).removeprefix("transfer-"), "traveler_ids": offer.get("traveler_ids", []),
                "city": offer.get("city"), "date": offer.get("date")}
    return {**common, "leg_ids": [component], "traveler_ids": offer.get("traveler_ids", [])}


def _covers(state: TripState, offer: dict, binding: dict, errors: list[dict]) -> list[str]:
    oid = offer.get("offer_id")
    kind = offer.get("kind", offer.get("details", {}).get("kind"))
    if binding.get("trip_id") != state.trip_id or binding.get("state_version") != state.state_version:
        errors.append(_issue("stale_offer_binding", "Предложение связано с другой поездкой или ревизией.", offer_id=oid))
    people = {person.id: person for person in state.travelers}
    if kind == "hotel":
        stay = next((s for s in state.stays if s.id == binding.get("stay_id")), None)
        if stay is None:
            errors.append(_issue("unknown_stay_binding", "Нужен явный stay_id этой поездки.", offer_id=oid))
            return []
        if (binding.get("city") != stay.city or binding.get("checkin") != (stay.check_in.isoformat() if stay.check_in else None)
                or binding.get("checkout") != (stay.check_out.isoformat() if stay.check_out else None)):
            errors.append(_issue("stay_binding_mismatch", "Город или даты предложения не совпадают с остановкой.", offer_id=oid))
        hotel_id = binding.get("hotel_id")
        if not isinstance(hotel_id, str) or not hotel_id or (stay.hotel_id and stay.hotel_id != hotel_id):
            errors.append(_issue("hotel_identity_mismatch", "Не подтверждён выбранный отель.", offer_id=oid))
        room_ids, assignments = binding.get("room_ids"), binding.get("room_assignments")
        known = {room.id: room for room in stay.rooms}
        if not isinstance(room_ids, list) or not room_ids or any(not isinstance(r, str) or r not in known for r in room_ids) or len(set(room_ids)) != len(room_ids):
            errors.append(_issue("invalid_room_binding", "Нужны точные room_ids этой остановки без повторов.", offer_id=oid))
            return []
        if not isinstance(assignments, list) or any(not isinstance(r, dict) for r in assignments):
            errors.append(_issue("allocation_unknown", "Нет распределения гостей по выбранным комнатам.", offer_id=oid))
        else:
            if Counter(r.get("room_id") for r in assignments) != Counter(room_ids):
                errors.append(_issue("allocation_room_mismatch", "Allocation должна соответствовать покрытым комнатам.", offer_id=oid))
            separate = {g.id for g in state.groups if g.keep_separate}
            for assignment in assignments:
                ids = assignment.get("traveler_ids")
                rid = assignment.get("room_id")
                if not isinstance(ids, list) or not ids or any(not isinstance(p, str) or p not in people for p in ids) or len(set(ids)) != len(ids):
                    errors.append(_issue("invalid_allocation", "Некорректный состав комнаты.", offer_id=oid, room_id=rid))
                    continue
                if rid in known and known[rid].traveler_ids and sorted(ids) != sorted(known[rid].traveler_ids):
                    errors.append(_issue("changed_allocation", "Предложение изменило явное распределение группы.", offer_id=oid, room_id=rid))
                groups = {people[p].group_id for p in ids}
                if len(groups) > 1 and groups.intersection(separate):
                    errors.append(_issue("mixed_families", "Раздельные семьи смешаны в комнате.", offer_id=oid, room_id=rid))
                if not any(people[p].type == "adult" for p in ids):
                    errors.append(_issue("adult_required", "В комнате ребёнка нужен взрослый.", offer_id=oid, room_id=rid))
                capacity = assignment.get("capacity")
                if capacity is not None and (type(capacity) is not int or capacity < len(ids)):
                    errors.append(_issue("room_capacity", "Вместимость комнаты меньше состава или не определена корректно.", offer_id=oid, room_id=rid))
        return [f"hotel:{stay.id}:{rid}" for rid in room_ids]
    if kind == "transfer":
        stay = next((s for s in state.stays if s.id == binding.get("stay_id")), None)
        if stay is None or binding.get("city") != stay.city or binding.get("date") != (stay.check_in.isoformat() if stay.check_in else None):
            errors.append(_issue("invalid_transfer_binding", "Не подтверждены остановка и дата трансфера.", offer_id=oid))
            return []
        covered = [f"transfer:{stay.id}"]
    elif kind in {"flight", "train"}:
        ids = binding.get("leg_ids", [binding["leg_id"]] if "leg_id" in binding else None)
        valid_ids = {leg.id for leg in state.legs} if state.legs else {f"leg-{i}" for i in range(len(route_edges(state)))}
        if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or i not in valid_ids for i in ids) or len(set(ids)) != len(ids):
            errors.append(_issue("invalid_leg_binding", "Нужны точные ID всех оплаченных плеч.", offer_id=oid))
            return []
        for leg in state.legs:
            if leg.id in ids and leg.mode != kind:
                errors.append(_issue("transport_mode_mismatch", "Тип транспорта не совпадает с запросом.", offer_id=oid, leg_id=leg.id))
            if leg.id in ids and len(ids) == 1:
                for field, expected in (("origin", leg.origin), ("destination", leg.destination),
                                        ("departure_date", leg.departure_date.isoformat() if leg.departure_date else None)):
                    if field in binding and binding[field] != expected:
                        errors.append(_issue("leg_binding_mismatch", "Маршрут или дата provider-компонента не совпадают с плечом.", offer_id=oid, field=field))
        covered = [f"leg:{i}" for i in ids]
    else:
        errors.append(_issue("unsupported_component", "Неизвестный тип компонента.", offer_id=oid))
        return []
    ids = binding.get("traveler_ids")
    if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids) or Counter(ids) != Counter(people.keys()):
        errors.append(_issue("passenger_coverage", "Транспорт должен покрывать каждого участника ровно один раз.", offer_id=oid))
    return covered


def assemble_plan(state: TripState | dict, offers: list[dict], *, demo_fallback: bool = False,
                  previous_plan: dict | None = None, now: datetime | None = None,
                  fx_snapshot: dict | None = None) -> dict:
    state = TripState.model_validate(state)
    errors = [item for item in validate_trip(state) if not (item["code"] == "unsupported_budget" and state.budget and state.budget.scope == "whole_trip")]
    for condition in state.constraints:
        if condition.hard and condition.key not in _KNOWN_HARD_CONSTRAINTS:
            errors.append(_issue("unsupported_hard_constraint", "Этот жёсткий критерий пока не проверяется сборщиком; он не снят молча.", key=condition.key))
    warnings: list[str] = []
    required = required_components(state)
    selected: list[dict] = []
    components: list[dict] = []
    demo = DemoInventory().search_trip(state, catalogue_only=True)
    catalog = {offer["offer_id"]: offer for plan in demo["plans"] for offer in plan["offers"]}
    clock = now.astimezone(timezone.utc) if isinstance(now, datetime) and now.tzinfo is not None else None

    def add(raw: Any, fallback: bool = False) -> None:
        if not isinstance(raw, dict):
            errors.append(_issue("invalid_offer", "Предложение должно быть объектом."))
            return
        _digest(raw)  # Reject non-JSON/NaN before any partial output mutation.
        offer = deepcopy(raw)
        details = offer.get("details", {})
        if not isinstance(details, dict) or not isinstance(offer.get("offer_id"), str):
            errors.append(_issue("invalid_offer", "Нет offer_id/details."))
            return
        local_demo = offer.get("source_type") == "DEMO"
        if local_demo:
            original = catalog.get(offer["offer_id"])
            if original is None or offer != original:
                errors.append(_issue("unknown_demo_snapshot", "DEMO-снимок не совпадает с текущим конечным каталогом.", offer_id=offer["offer_id"]))
                return
            binding = _demo_binding(state, offer)
        else:
            binding = details.get("binding")
            if not isinstance(binding, dict):
                errors.append(_issue("missing_source_binding", "Provider offer требует явной details.binding.", offer_id=offer["offer_id"]))
                return
        covers = _covers(state, offer, binding, errors)
        source = _PINNED_SOURCE.get(str(offer.get("provider", "")).strip().casefold(), offer.get("source_type"))
        if source not in {"DEMO", "SANDBOX", "LIVE_INDICATIVE", "LIVE_BOOKABLE"}:
            errors.append(_issue("unknown_source", "Неизвестное происхождение предложения.", offer_id=offer["offer_id"]))
            source = "UNKNOWN"
        if source != offer.get("source_type"):
            warnings.append(f"{offer['offer_id']}: source_type понижен до {source}; название провайдера не подтверждает LIVE_BOOKABLE.")
        if source == "LIVE_BOOKABLE" and (details.get("availability_verified") is not True or details.get("supplier_reprice_verified") is not True):
            source = "LIVE_INDICATIVE"
            warnings.append(f"{offer['offer_id']}: нет доказательства supplier reprice; показана оценка.")
        amount = offer.get("total_minor") if local_demo else offer.get("amount_minor")
        currency = offer.get("currency")
        priced = type(amount) is int and amount >= 0 and isinstance(currency, str) and len(currency) == 3 and currency.isalpha()
        if not priced:
            errors.append(_issue("invalid_price", "Нужна целая неотрицательная сумма в минимальных единицах валюты.", offer_id=offer["offer_id"]))
        if not local_demo:
            if offer.get("price_kind") not in _TOTAL_KINDS:
                errors.append(_issue("unsupported_price_basis", "Цена за ночь/net/reference не является полной ценой компонента.", offer_id=offer["offer_id"]))
                priced = False
            if details.get("mandatory_fees_known") is not True and details.get("fees_included") is not True:
                errors.append(_issue("mandatory_fees_unknown", "Неизвестны обязательные сборы компонента.", offer_id=offer["offer_id"]))
            kind = details.get("kind", offer.get("kind"))
            evidence = details.get("constraint_evidence", {})
            for condition in state.constraints:
                relevant = (condition.key == "pets" or kind == "hotel" and condition.key in {"breakfast", "refundable", "pets_allowed"}
                            or kind == "flight" and condition.key == "nonstop" and condition.value is True)
                if (not condition.hard or not relevant or condition.key not in _KNOWN_HARD_CONSTRAINTS
                        or condition.stay_id is not None and condition.stay_id != binding.get("stay_id")):
                    continue
                if condition.room_id is not None:
                    # A hotel/aggregate true does not prove the chosen rate of
                    # one canonical room. Another component covers other rooms.
                    bound_rooms = binding.get("room_ids")
                    if not isinstance(bound_rooms, list) or condition.room_id not in bound_rooms:
                        continue
                    per_room = details.get("room_constraint_evidence")
                    room_evidence = per_room.get(condition.room_id) if isinstance(per_room, dict) else None
                    actual = room_evidence.get(condition.key) if isinstance(room_evidence, dict) else None
                else:
                    actual = evidence.get(condition.key) if isinstance(evidence, dict) else None
                if (actual != condition.value or type(actual) is not type(condition.value)):
                    errors.append(_issue("hard_constraint_unverified", "Источник не подтвердил обязательное условие; предупреждение не делает его выполненным.", offer_id=offer["offer_id"], key=condition.key, stay_id=condition.stay_id, room_id=condition.room_id))
            if kind == "hotel" and details.get("allocation_verified") is not True:
                errors.append(_issue("allocation_unverified", "Provider не подтвердил распределение комнат/гостей.", offer_id=offer["offer_id"]))
            request = details.get("search_request", {})
            requested_rooms = details.get("occupancies", request.get("occupancies", request.get("guests"))) if isinstance(request, dict) else None
            if kind == "hotel" and isinstance(requested_rooms, list) and len(requested_rooms) != len(binding.get("room_ids", [])):
                errors.append(_issue("room_quote_scope_mismatch", "Сумма получена для другого количества комнат.", offer_id=offer["offer_id"]))
            if kind in {"flight", "train"} and (details.get("group_quote") is not True
                    or type(details.get("passengers_count")) is not int
                    or details["passengers_count"] != len(state.travelers)):
                errors.append(_issue("group_quote_unknown", "Справочная цена не покрывает всю группу.", offer_id=offer["offer_id"]))
            if kind in {"flight", "train"} and details.get("itinerary_complete") is False and len(binding.get("leg_ids", [])) > 1:
                errors.append(_issue("incomplete_itinerary", "Поисковая выдача не подтверждает все заявленные плечи.", offer_id=offer["offer_id"]))
            retrieved, expiry = _stamp(offer.get("retrieved_at")), _stamp(offer.get("expires_at"))
            if clock is None:
                errors.append(_issue("clock_required", "Для проверки provider snapshots нужен явный now с timezone."))
            if retrieved is None or expiry is None or (retrieved and expiry and expiry <= retrieved):
                errors.append(_issue("invalid_source_lifetime", "Нет корректного срока provider snapshot.", offer_id=offer["offer_id"]))
            elif clock and (expiry <= clock or retrieved > clock):
                errors.append(_issue("stale_source_snapshot", "Provider snapshot устарел или находится в будущем.", offer_id=offer["offer_id"]))
            warnings.append(f"{offer['offer_id']}: статус {source}; supplier booking не создаётся, точные стыковки/инвентарь не гарантируются.")
        offer["source_type"] = source
        selected.append(offer)
        components.append({"component_id": offer["offer_id"], "covers": covers, "binding": deepcopy(binding),
                           "source_type": source, "provider": offer.get("provider", "demo_inventory"),
                           "amount_minor": amount if priced else None, "currency": currency.upper() if priced else None,
                           "price_kind": offer.get("price_kind"), "snapshot_hash": _digest(offer), "demo_fallback": fallback})

    if not isinstance(offers, list):
        errors.append(_issue("invalid_offers", "Нужен список предложений."))
        offers = []
    for offer in offers:
        try:
            add(offer)
        except (TypeError, ValueError):
            errors.append(_issue("invalid_offer_data", "Предложение не является конечным JSON-снимком."))
    covered = {key for component in components for key in component["covers"]}
    if demo_fallback and set(required) - covered:
        candidates = demo["plans"][0]["offers"] if demo["plans"] else []
        for offer in candidates:
            binding = _demo_binding(state, offer)
            local_errors: list[dict] = []
            coverage = set(_covers(state, offer, binding, local_errors))
            if coverage and not coverage.intersection(covered):
                add(offer, True)
                covered.update(coverage)
            elif coverage - covered:
                warnings.append("Частично покрытая остановка не дополняется вторым отелем молча; требуется полный согласованный набор комнат.")
        if not candidates:
            warnings.extend(demo["questions"] + [v["message"] for v in demo["violations"]])
    counts = Counter(key for component in components for key in component["covers"])
    missing = [key for key in required if counts[key] == 0]
    repeated = [key for key, count in counts.items() if count > 1]
    if missing:
        errors.append(_issue("missing_components", "Корзина покрывает не все комнаты, плечи и трансферы.", components=missing))
    if repeated:
        errors.append(_issue("overlapping_components", "Одни компоненты оплачиваются дважды.", components=repeated))
    assignments: dict[str, list] = {}
    for stay in state.stays:
        hotel_components = [c for c in components if any(key.startswith(f"hotel:{stay.id}:") for key in c["covers"])]
        hotels = {c["binding"].get("hotel_id") for c in hotel_components}
        if len(hotels) > 1:
            errors.append(_issue("split_hotel_identity", "Комнаты одной остановки относятся к разным отелям.", stay_id=stay.id))
        rows = [r for c in hotel_components
                for r in (c["binding"].get("room_assignments") if isinstance(c["binding"].get("room_assignments"), list) else [])
                if isinstance(r, dict)]
        assignments[stay.id] = deepcopy(rows)
        people = [p for row in rows for p in (row.get("traveler_ids") if isinstance(row.get("traveler_ids"), list) else []) if isinstance(p, str)]
        if Counter(people) != Counter(p.id for p in state.travelers):
            errors.append(_issue("stay_guest_coverage", "На остановке каждый участник должен быть размещён ровно один раз.", stay_id=stay.id))
    totals: dict[str, int] = {}
    if len({offer["offer_id"] for offer in selected}) != len(selected):
        errors.append(_issue("duplicate_offer_id", "Offer ID повторяется в корзине."))
    for component in components:
        if component["amount_minor"] is not None:
            totals[component["currency"]] = totals.get(component["currency"], 0) + component["amount_minor"]
    currency = state.budget.currency.upper() if state.budget else next(iter(totals), "RUB")
    needs_fx = any(native != currency for native in totals)
    pinned_fx = None
    if needs_fx:
        if fx_snapshot is None:
            errors.append(_issue("fx_required", "Разные валюты не складываются без явного подтверждённого FX.", currencies=sorted(totals), target_currency=currency))
        else:
            try:
                pinned_fx = validate_snapshot(fx_snapshot, clock)
                # Preserve source-native offers/components. Only these separate
                # reference projections participate in the budget comparison.
                for component in components:
                    amount = component["amount_minor"]
                    component["budget_amount_minor"] = (convert_minor(amount, component["currency"], currency, pinned_fx, clock)
                                                        if amount is not None else None)
                    component["budget_currency"] = currency
            except FxUnavailable as exc:
                pinned_fx = None
                errors.append(_issue(exc.code, exc.message, target_currency=currency))
    for component in components:
        if pinned_fx is None:
            component["budget_amount_minor"] = component["amount_minor"] if component["currency"] == currency else None
            component["budget_currency"] = currency
    projected_subtotal = (sum(component["budget_amount_minor"] or 0 for component in components)
                          if pinned_fx is not None else totals.get(currency, 0))
    total = projected_subtotal if not errors else None
    if total is not None and state.budget and total > state.budget.amount_minor:
        errors.append(_issue("budget_exceeded", "Корзина превышает общий бюджет.", total_minor=total, budget_minor=state.budget.amount_minor))
    sources = sorted({component["source_type"] for component in components})
    source = sources[0] if len(sources) == 1 else "MIXED" if sources else "INCOMPLETE"
    if source in {"DEMO", "MIXED", "SANDBOX"}:
        warnings.append("Сумма является simulated estimate; DEMO/SANDBOX не подтверждают реальные цены и наличие.")
    if pinned_fx is not None:
        warnings.append(f"Справочный пересчёт ЦБ на {pinned_fx['as_of']}; банковские комиссии и курс оплаты неизвестны. Это reference estimate для simulated checkout, не цена покупки.")
    snapshots = [{"offer_id": offer["offer_id"], "source_type": offer["source_type"],
                  "retrieved_at": offer.get("retrieved_at"), "expires_at": offer.get("expires_at"),
                  "snapshot_hash": component["snapshot_hash"]} for offer, component in zip(selected, components)]
    identity = {"trip_id": state.trip_id, "state_version": state.state_version,
                "state_hash": _digest(state.model_dump(mode="json")), "source_snapshots": snapshots}
    if pinned_fx is not None:
        identity["fx_snapshot_id"] = pinned_fx["snapshot_id"]
    plan = {"plan_id": "assembled-" + _digest(identity)[:24], **identity, "offers": selected,
            "components": components, "source_type": source, "price_kind": "simulated_estimate",
            "total_minor": total, "known_subtotal_minor": projected_subtotal, "currency": currency,
            "currency_totals": totals, "coverage": {"required": required, "covered": sorted(counts), "missing": missing,
                "overlapping": repeated, "complete": not missing and not repeated}, "room_assignments": assignments,
            "blockers": errors, "violations": errors, "warnings": list(dict.fromkeys(warnings)),
            "ready_for_simulated_checkout": not errors and total is not None,
            "ready_for_real_booking": False, "booking_created": False, "checkout_mode": "SIMULATED",
            "snapshot_hash": _digest(identity)}
    if pinned_fx is not None:
        plan["fx_snapshot"] = pinned_fx
        plan["fx_reference_only"] = True
        plan["fx_fees_unknown"] = True
    if previous_plan is not None:
        plan["comparison"] = compare_plans(previous_plan, plan)
    return plan


def validate_quote(plan: dict, state: TripState | dict, *, current_offers: list[dict] | None = None,
                   now: datetime | None = None, fx_snapshot: dict | None = None) -> list[dict]:
    state = TripState.model_validate(state)
    if not isinstance(plan, dict):
        return [_issue("invalid_quote", "Корзина должна быть объектом.")]
    issues: list[dict] = []
    if (plan.get("trip_id") != state.trip_id or plan.get("state_version") != state.state_version
            or plan.get("state_hash") != _digest(state.model_dump(mode="json"))):
        issues.append(_issue("stale_quote_state", "Корзина относится к другой ревизии или содержимому поездки."))
    rebuilt = assemble_plan(state, plan.get("offers", []), now=now, fx_snapshot=plan.get("fx_snapshot"))
    integrity_fields = ("snapshot_hash", "total_minor", "currency", "coverage", "room_assignments",
                        "source_type", "source_snapshots", "ready_for_simulated_checkout", "ready_for_real_booking",
                        "checkout_mode", "booking_created", "known_subtotal_minor", "currency_totals",
                        "fx_snapshot_id", "fx_snapshot", "fx_reference_only", "fx_fees_unknown")
    def economic_components(value: dict) -> list:
        rows = value.get("components", [])
        return [{k: v for k, v in item.items() if k != "demo_fallback"} for item in rows if isinstance(item, dict)] if isinstance(rows, list) else []
    try:
        # JSON economics distinguish true/1/1.0; Python dictionary equality
        # would let a changed projection type compare equal to canonical money.
        economics_match = (_digest({key: rebuilt.get(key) for key in integrity_fields})
                           == _digest({key: plan.get(key) for key in integrity_fields})
                           and _digest(economic_components(rebuilt)) == _digest(economic_components(plan)))
    except (TypeError, ValueError):
        economics_match = False
    if not economics_match:
        issues.append(_issue("quote_snapshot_changed", "Снимки источников, покрытие или итог корзины изменены."))
    issues.extend(rebuilt["blockers"])
    if fx_snapshot is not None and plan.get("fx_snapshot_id") is not None:
        try:
            current_fx = validate_snapshot(fx_snapshot, now)
            if current_fx["snapshot_id"] != plan["fx_snapshot_id"]:
                issues.append(_issue("fx_snapshot_changed", "Справочный курс изменился; нужны новый preview и согласие."))
        except FxUnavailable as exc:
            issues.append(_issue(exc.code, exc.message))
    if current_offers is not None:
        current = {o.get("offer_id"): o for o in current_offers if isinstance(o, dict)}
        for snapshot in rebuilt["source_snapshots"]:
            offer = current.get(snapshot["offer_id"])
            try:
                matches = offer is not None and _digest(offer) == snapshot["snapshot_hash"]
            except (TypeError, ValueError):
                matches = False
            if not matches:
                issues.append(_issue("source_snapshot_changed", "Нужны новый preview и согласие после изменения источника.", offer_id=snapshot["offer_id"]))
    return issues


def compare_plans(before: dict, after: dict) -> dict:
    def index(plan: dict) -> dict:
        return {key: {"offer_id": c["component_id"], "amount_minor": c["amount_minor"], "currency": c["currency"],
                      "budget_amount_minor": c.get("budget_amount_minor"), "budget_currency": c.get("budget_currency"),
                      "source_type": c["source_type"], "snapshot_hash": c["snapshot_hash"]}
                for c in plan.get("components", []) for key in c["covers"]}
    old, new = index(before), index(after)
    changes = [{"component_id": key, "before": old.get(key), "after": new.get(key)}
               for key in sorted(set(old) | set(new)) if old.get(key) != new.get(key)]
    comparable = before.get("currency") == after.get("currency") and type(before.get("total_minor")) is int and type(after.get("total_minor")) is int
    return {"before_total_minor": before.get("total_minor"), "after_total_minor": after.get("total_minor"),
            "currency": after.get("currency"), "delta_minor": after["total_minor"] - before["total_minor"] if comparable else None,
            "changes": changes, "source_changed": before.get("source_type") != after.get("source_type"),
            "requires_new_confirmation": before.get("snapshot_hash") != after.get("snapshot_hash")}
