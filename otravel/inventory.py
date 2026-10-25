"""Deterministic fictional inventory. No live prices, availability, or booking."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
from typing import Any

from .domain import CITIES, DomainError, TripState, validate_trip


CITY_DATA = {
    "Москва": ("moscow", 500_000), "Санкт-Петербург": ("petersburg", 450_000),
    "Сочи": ("sochi", 420_000), "Казань": ("kazan", 350_000),
    "Стамбул": ("istanbul", 600_000), "Анталья": ("antalya", 550_000),
    "Дубай": ("dubai", 900_000),
}
HOTELS = (
    {"suffix": "central", "name": "DEMO — Центральный эскиз", "factor": 100, "capacity": 4, "pets_allowed": False, "max_pet_kg": 0},
    {"suffix": "garden", "name": "DEMO — Тихий сад", "factor": 110, "capacity": 4, "pets_allowed": True, "max_pet_kg": 10},
    {"suffix": "family", "name": "DEMO — Семейный берег", "factor": 125, "capacity": 4, "pets_allowed": True, "max_pet_kg": 20},
)
RATES = (
    {"id": "basic", "breakfast": False, "refundable": False},
    {"id": "breakfast", "breakfast": True, "refundable": False},
    {"id": "flex", "breakfast": True, "refundable": True},
)
NOTICE = "DEMO: все отели, маршруты, тарифы, цены и наличие вымышлены. Реальная бронь не создаётся."
SUPPORTED_CONSTRAINTS = {"breakfast", "refundable", "pets_allowed", "pets"}


def _error(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "message": message, "details": details}


def _digest(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()[:20]


def _allocation(state: TripState, stay: Any, capacity: int) -> list[dict[str, Any]] | None:
    """Fill unspecified allocations while retaining every explicitly assigned person."""
    people = {person.id: person for person in state.travelers}
    separate = {group.id for group in state.groups if group.keep_separate}
    rooms = [{"room_id": room.id, "traveler_ids": list(room.traveler_ids), "capacity": capacity} for room in stay.rooms]
    assigned = [identifier for room in rooms for identifier in room["traveler_ids"]]
    if len(assigned) != len(set(assigned)) or any(identifier not in people for identifier in assigned):
        return None

    def allowed(room: dict[str, Any], identifier: str) -> bool:
        if len(room["traveler_ids"]) >= capacity:
            return False
        group = people[identifier].group_id
        for member in room["traveler_ids"]:
            other = people[member].group_id
            if group != other and (group in separate or other in separate):
                return False
        return True

    # Fixed assignments must satisfy the same property facts as generated ones.
    for room in rooms:
        if len(room["traveler_ids"]) > capacity:
            return None
        groups = {people[item].group_id for item in room["traveler_ids"]}
        if len(groups) > 1 and groups.intersection(separate):
            return None
    unassigned = [person for person in state.travelers if person.id not in assigned]
    unassigned.sort(key=lambda person: (person.group_id or "", person.type != "adult", person.id))
    failed: set[tuple] = set()

    def place(index: int) -> bool:
        if index == len(unassigned):
            return all(room["traveler_ids"] and any(people[item].type == "adult" for item in room["traveler_ids"]) for room in rooms)
        signature = (index, tuple((len(room["traveler_ids"]), sum(people[item].type == "adult" for item in room["traveler_ids"]), tuple(sorted({people[item].group_id or "" for item in room["traveler_ids"]}))) for room in rooms))
        if signature in failed:
            return False
        person = unassigned[index]
        equivalent: set[tuple] = set()
        for room in rooms:
            shape = (len(room["traveler_ids"]), sum(people[item].type == "adult" for item in room["traveler_ids"]), tuple(sorted({people[item].group_id or "" for item in room["traveler_ids"]})))
            if shape in equivalent or not allowed(room, person.id):
                continue
            equivalent.add(shape)
            room["traveler_ids"].append(person.id)
            if place(index + 1):
                return True
            room["traveler_ids"].pop()
        failed.add(signature)
        return False

    return rooms if place(0) else None


def _constraints(state: TripState, stay_id: str | None = None) -> list[Any]:
    return [item for item in state.constraints if item.hard and (item.stay_id is None or item.stay_id == stay_id)]


def _pet_request(state: TripState) -> tuple[list[dict[str, Any]], list[str]]:
    pets: list[dict[str, Any]] = []
    questions: list[str] = []
    for constraint in state.constraints:
        if constraint.hard and constraint.key == "pets" and constraint.value:
            value = constraint.value
            candidates = value if isinstance(value, list) else [value]
            for candidate in candidates:
                if not isinstance(candidate, dict) or candidate.get("kind") not in {"dog", "cat"} or type(candidate.get("weight_kg")) not in {int, float} or not 0 < candidate["weight_kg"] <= 100:
                    questions.append("Какое животное едет и сколько оно весит? DEMO поддерживает собак и кошек.")
                else:
                    pets.append({"kind": candidate["kind"], "weight_kg": candidate["weight_kg"]})
    return pets, questions


def _line(kind: str, unit_minor: int, quantity: int, **details: Any) -> dict[str, Any]:
    return {"kind": kind, "unit_minor": unit_minor, "quantity": quantity, "amount_minor": unit_minor * quantity, **details}


def _hotel_offer(state: TripState, stay: Any, hotel: dict[str, Any], rate: dict[str, Any], pets: list[dict[str, Any]]) -> dict[str, Any] | None:
    for constraint in _constraints(state, stay.id):
        if constraint.key in {"breakfast", "refundable"} and rate[constraint.key] != constraint.value:
            return None
        if constraint.key == "pets_allowed" and hotel["pets_allowed"] != constraint.value:
            return None
    if pets and (not hotel["pets_allowed"] or any(pet["weight_kg"] > hotel["max_pet_kg"] for pet in pets)):
        return None
    assignments = _allocation(state, stay, hotel["capacity"])
    if assignments is None:
        return None
    nights = (stay.check_out - stay.check_in).days
    # Seasonal adjustment is fictional and deterministic, not a market model.
    season = 130 if stay.check_in.month in {6, 7, 8} else 120 if stay.check_in.month == 12 else 90 if stay.check_in.month == 11 else 100
    base = CITY_DATA[stay.city][1] * season * hotel["factor"] // 10_000
    breakdown: list[dict[str, Any]] = []
    people = {person.id: person for person in state.travelers}
    for room in assignments:
        breakdown.append(_line("room_nights", base, nights, room_id=room["room_id"]))
        adults = sum(people[identifier].type == "adult" for identifier in room["traveler_ids"])
        breakdown.append(_line("extra_adult_nights", 120_000, max(0, adults - 2) * nights, room_id=room["room_id"]))
        for identifier in room["traveler_ids"]:
            person = people[identifier]
            age = person.age_on(stay.check_in)
            if person.type == "child":
                unit = 0 if age < 2 else 60_000 if age < 12 else 100_000
                breakdown.append(_line("child_nights", unit, nights, traveler_id=identifier, age=age))
            if rate["breakfast"]:
                breakdown.append(_line("breakfast_days", 70_000 if person.type == "adult" else 40_000, nights, traveler_id=identifier))
        if rate["refundable"]:
            breakdown.append(_line("flexible_rate_nights", base * 15 // 100, nights, room_id=room["room_id"]))
    if pets:
        breakdown.append(_line("pet_stay_fee", 200_000, len(pets)))
    total = sum(item["amount_minor"] for item in breakdown)
    hotel_id = f"demo-hotel-{CITY_DATA[stay.city][0]}-{hotel['suffix']}"
    identifier = "demo-stay-" + _digest([stay.model_dump(mode="json"), [person.model_dump(mode="json") for person in state.travelers], pets, hotel_id, rate["id"], total])
    return {"offer_id": identifier, "component_id": stay.id, "kind": "hotel", "name": hotel["name"], "hotel_id": hotel_id, "city": stay.city, "check_in": stay.check_in.isoformat(), "check_out": stay.check_out.isoformat(), "nights": nights, "rate_type": rate["id"], "breakfast": rate["breakfast"], "refundable": rate["refundable"], "pets_allowed": hotel["pets_allowed"], "max_pet_kg": hotel["max_pet_kg"], "room_assignments": assignments, "breakdown": breakdown, "total_minor": total, "currency": "RUB", "source_type": "DEMO", "price_kind": "synthetic", "is_demo": True, "availability": "synthetic_not_live"}


def _route(state: TripState) -> list[tuple[str, str, date, str]]:
    route = []
    origin = state.origin
    for index, stay in enumerate(state.stays):
        mode = state.legs[index].mode if len(state.legs) == len(state.stays) + 1 else "flight"
        route.append((origin, stay.city, stay.check_in, mode))
        origin = stay.city
    mode = state.legs[-1].mode if len(state.legs) == len(state.stays) + 1 else "flight"
    route.append((origin, state.origin, state.stays[-1].check_out, mode))
    return route


def _transport_offers(state: TripState, pets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    offers = []
    prices = {("Казань", "Стамбул"): 700_000, ("Стамбул", "Анталья"): 350_000, ("Анталья", "Казань"): 750_000}
    for index, (origin, destination, day, mode) in enumerate(_route(state)):
        if origin == destination:
            continue
        base = prices.get((origin, destination), 900_000)
        if mode == "train":
            base = base * 60 // 100
        breakdown = []
        for person in state.travelers:
            age = person.age_on(day)
            unit = 100_000 if person.type == "child" and age < 2 else base * 70 // 100 if person.type == "child" else base
            breakdown.append(_line("demo_passenger", unit, 1, traveler_id=person.id, age=age))
        if pets:
            breakdown.append(_line("demo_pet_transport", 300_000, len(pets)))
        offers.append({"offer_id": "demo-transport-" + _digest([index, origin, destination, mode, day.isoformat(), breakdown]), "component_id": state.legs[index].id if len(state.legs) == len(state.stays) + 1 else f"leg-{index}", "kind": mode, "name": "DEMO — Синтетический перелёт" if mode == "flight" else "DEMO — Синтетический поезд", "origin": origin, "destination": destination, "departure_date": day.isoformat(), "arrival_date": day.isoformat(), "age_basis": "departure_date", "traveler_ids": [person.id for person in state.travelers], "pets_allowed": True, "max_pet_kg": 20, "breakdown": breakdown, "total_minor": sum(item["amount_minor"] for item in breakdown), "currency": "RUB", "source_type": "DEMO", "price_kind": "synthetic", "is_demo": True, "availability": "synthetic_not_live"})
    for stay in state.stays:
        breakdown = [_line("demo_transfer_passenger", 40_000, len(state.travelers))]
        offers.append({"offer_id": "demo-transfer-" + _digest([stay.id, stay.city, stay.check_in.isoformat(), breakdown]), "component_id": f"transfer-{stay.id}", "kind": "transfer", "name": "DEMO — Синтетический трансфер", "city": stay.city, "date": stay.check_in.isoformat(), "traveler_ids": [person.id for person in state.travelers], "pets_allowed": True, "max_pet_kg": 20, "breakdown": breakdown, "total_minor": sum(item["amount_minor"] for item in breakdown), "currency": "RUB", "source_type": "DEMO", "price_kind": "synthetic", "is_demo": True, "availability": "synthetic_not_live"})
    return offers


def validate_plan(state: TripState, plan: dict[str, Any]) -> list[dict[str, Any]]:
    violations = validate_trip(state)
    if not isinstance(plan, dict):
        return violations + [_error("invalid_plan", "План должен быть объектом.")]
    offers = plan.get("offers", [])
    if not isinstance(offers, list) or not offers:
        return violations + [_error("missing_offers", "В плане отсутствуют компоненты.")]
    if plan.get("state_version") is not None and plan["state_version"] != state.state_version:
        violations.append(_error("stale_plan", "План относится к другой версии поездки."))
    if plan.get("trip_id") != state.trip_id:
        violations.append(_error("wrong_trip", "План принадлежит другой поездке."))
    if plan.get("source_type") != "DEMO" or plan.get("booking_created") is not False:
        violations.append(_error("invalid_source", "DEMO-план не может утверждать реальную покупку или источник."))
    if any(not isinstance(offer, dict) for offer in offers):
        return violations + [_error("invalid_offer", "Каждый компонент должен быть объектом.")]
    total = 0
    for offer in offers:
        amount = offer.get("total_minor")
        if type(amount) is not int or amount < 0 or offer.get("currency") != "RUB":
            violations.append(_error("invalid_price", "Цена должна быть целым числом копеек в RUB."))
            continue
        total += amount
        lines = offer.get("breakdown", [])
        invalid_lines = not isinstance(lines, list) or not lines or any(not isinstance(line, dict) or any(type(line.get(key)) is not int or line[key] < 0 for key in ("unit_minor", "quantity", "amount_minor")) for line in lines)
        if invalid_lines or any(line["amount_minor"] != line["unit_minor"] * line["quantity"] for line in lines) or sum(line["amount_minor"] for line in lines) != amount:
            violations.append(_error("invalid_breakdown", "Детализация не совпадает с ценой компонента."))
        if offer.get("source_type") != "DEMO" or offer.get("price_kind") != "synthetic" or offer.get("is_demo") is not True or offer.get("availability") != "synthetic_not_live":
            violations.append(_error("invalid_source", "DEMO-предложение не может объявляться реальным."))
    if type(plan.get("total_minor")) is not int or plan["total_minor"] != total or plan.get("currency") != "RUB":
        violations.append(_error("invalid_total", "Сумма плана не совпадает с компонентами."))
    if state.budget and total > state.budget.amount_minor:
        violations.append(_error("budget_exceeded", "План превышает общий бюджет.", total_minor=total, budget_minor=state.budget.amount_minor))
    people = {person.id: person for person in state.travelers}
    separate = {group.id for group in state.groups if group.keep_separate}
    pets, pet_questions = _pet_request(state)
    if pet_questions:
        violations.append(_error("missing_pet_details", pet_questions[0]))
    if len([offer for offer in offers if offer.get("kind") == "hotel"]) != len(state.stays):
        violations.append(_error("hotel_count", "Число отелей не совпадает с числом остановок."))
    for stay in state.stays:
        matches = [offer for offer in offers if offer.get("kind") == "hotel" and offer.get("component_id") == stay.id]
        if len(matches) != 1:
            violations.append(_error("missing_stay", "Для каждой остановки нужен ровно один отель.", stay_id=stay.id))
            continue
        offer = matches[0]
        known_hotel = next((hotel for hotel in HOTELS if stay.city in CITY_DATA and offer.get("hotel_id") == f"demo-hotel-{CITY_DATA[stay.city][0]}-{hotel['suffix']}"), None)
        known_rate = next((rate for rate in RATES if rate["id"] == offer.get("rate_type")), None)
        if known_hotel is None or known_rate is None:
            violations.append(_error("unknown_rate", "Отель или тариф отсутствует в конечном DEMO-каталоге."))
        elif any(offer.get(key) != known_rate[key] for key in ("breakfast", "refundable")) or any(offer.get(key) != known_hotel[key] for key in ("pets_allowed", "max_pet_kg")):
            violations.append(_error("property_facts_mismatch", "Свойства предложения не совпадают с данными каталога."))
        if stay.hotel_id and offer.get("hotel_id") != stay.hotel_id:
            violations.append(_error("selected_hotel_mismatch", "Выбранный отель подменён другим."))
        if pets and (not offer.get("pets_allowed") or any(pet["weight_kg"] > offer.get("max_pet_kg", 0) for pet in pets)):
            violations.append(_error("pets_not_supported", "Животное не соответствует правилам выбранного отеля."))
        if offer.get("city") != stay.city or offer.get("check_in") != (stay.check_in.isoformat() if stay.check_in else None) or offer.get("check_out") != (stay.check_out.isoformat() if stay.check_out else None):
            violations.append(_error("stay_mismatch", "Отель не совпадает с городом и датами остановки.", stay_id=stay.id))
        allocations = offer.get("room_assignments", [])
        if not isinstance(allocations, list) or any(not isinstance(room, dict) or not isinstance(room.get("traveler_ids"), list) for room in allocations):
            violations.append(_error("invalid_allocation", "Распределение комнат должно быть структурированным."))
            continue
        identifiers = [identifier for room in allocations for identifier in room.get("traveler_ids", [])]
        if sorted(identifiers) != sorted(people) or len(allocations) != len(stay.rooms) or sorted(room.get("room_id", "") for room in allocations) != sorted(room.id for room in stay.rooms):
            violations.append(_error("invalid_allocation", "Распределение должно учитывать каждого участника ровно один раз.", stay_id=stay.id))
        for room in allocations:
            members = [people[identifier] for identifier in room.get("traveler_ids", []) if identifier in people]
            expected = next((original for original in stay.rooms if original.id == room.get("room_id")), None)
            if expected and not set(expected.traveler_ids).issubset(room["traveler_ids"]):
                violations.append(_error("allocation_changed", "Явно заданное размещение участника изменено."))
            if type(room.get("capacity")) is not int or known_hotel and room.get("capacity") != known_hotel["capacity"] or len(members) > room.get("capacity", 0) or not any(person.type == "adult" for person in members):
                violations.append(_error("room_capacity", "Нарушена вместимость или дети остались без взрослого.", stay_id=stay.id))
            groups = {person.group_id for person in members}
            if len(groups) > 1 and groups.intersection(separate):
                violations.append(_error("group_allocation", "Семьи с условием раздельного размещения смешаны.", stay_id=stay.id))
        for constraint in _constraints(state, stay.id):
            if constraint.key not in SUPPORTED_CONSTRAINTS:
                violations.append(_error("unsupported_constraint", "DEMO не умеет подтверждать это жёсткое условие.", key=constraint.key))
            elif constraint.key in {"breakfast", "refundable", "pets_allowed"} and offer.get(constraint.key) != constraint.value:
                violations.append(_error("constraint_violation", "Тариф нарушает обязательное условие.", key=constraint.key, stay_id=stay.id))
    # A route and transfer cannot omit a member of the group.
    required_routes = [(a, b, day.isoformat(), mode) for a, b, day, mode in _route(state)] if state.origin and state.stays and all(stay.city and stay.check_in and stay.check_out for stay in state.stays) else []
    required_routes = [route for route in required_routes if route[0] != route[1]]
    actual_routes = [(offer.get("origin"), offer.get("destination"), offer.get("departure_date"), offer.get("kind")) for offer in offers if offer.get("kind") in {"flight", "train"}]
    if actual_routes != required_routes:
        violations.append(_error("route_mismatch", "Транспорт не соответствует полной цепочке городов и дат."))
    for offer in offers:
        if offer.get("kind") in {"flight", "train", "transfer"} and sorted(offer.get("traveler_ids", [])) != sorted(people):
            violations.append(_error("transport_occupancy", "В транспорте потерян или добавлен участник."))
        if pets and offer.get("kind") in {"flight", "train", "transfer"} and (offer.get("pets_allowed") is not True or offer.get("max_pet_kg") != 20 or any(pet["weight_kg"] > 20 for pet in pets)):
            violations.append(_error("transport_pets", "Животное не соответствует DEMO-правилам транспорта."))
    transfers = [offer for offer in offers if offer.get("kind") == "transfer"]
    if len(transfers) != len(state.stays) or any(len([offer for offer in transfers if offer.get("component_id") == f"transfer-{stay.id}" and offer.get("city") == stay.city and offer.get("date") == (stay.check_in.isoformat() if stay.check_in else None)]) != 1 for stay in state.stays):
        violations.append(_error("transfer_mismatch", "Для каждой остановки нужен соответствующий трансфер."))
    return violations


class DemoInventory:
    def search_trip(self, state: TripState) -> dict[str, Any]:
        issues = validate_trip(state)
        pets, pet_questions = _pet_request(state)
        questions = [item["message"] for item in issues if item["code"].startswith("missing_")] + pet_questions
        blocking = [item for item in issues if not item["code"].startswith("missing_")]
        for constraint in state.constraints:
            if constraint.hard and constraint.key not in SUPPORTED_CONSTRAINTS:
                blocking.append(_error("unsupported_constraint", "DEMO не подтверждает это жёсткое условие.", key=constraint.key))
            if constraint.hard and constraint.key in {"breakfast", "refundable", "pets_allowed"} and type(constraint.value) is not bool:
                blocking.append(_error("invalid_constraint", "Для этого условия нужно true/false.", key=constraint.key))
        base = {"plans": [], "questions": list(dict.fromkeys(questions)), "warnings": [NOTICE], "violations": blocking, "source_type": "DEMO", "price_kind": "synthetic", "limits_status": "proposed_implementation_guards_not_owner_approved"}
        if questions or blocking:
            return base
        domestic = set(CITIES[:4])
        if any(mode == "train" and (origin not in domestic or destination not in domestic) for origin, destination, _, mode in _route(state)):
            base["violations"].append(_error("unsupported_transport", "DEMO-поезда поддерживаются только между четырьмя российскими городами."))
            return base
        if state.preferences:
            base["warnings"].append("Мягкие предпочтения сохранены. Этот DEMO-каталог сортирует только по общей цене; неподтверждённые свойства не гарантируются.")
        for index, rate in enumerate(RATES):
            offers = []
            for stay in state.stays:
                hotel = HOTELS[index]
                if stay.hotel_id:
                    matches = [candidate for candidate in HOTELS if stay.hotel_id == f"demo-hotel-{CITY_DATA[stay.city][0]}-{candidate['suffix']}"]
                    if not matches:
                        base["violations"].append(_error("unknown_hotel", "hotel_id отсутствует в DEMO-каталоге этого города.", stay_id=stay.id))
                        return base
                    hotel = matches[0]
                offer = _hotel_offer(state, stay, hotel, rate, pets)
                if offer is None:
                    break
                offers.append(offer)
            if len(offers) != len(state.stays):
                continue
            offers += _transport_offers(state, pets)
            total = sum(offer["total_minor"] for offer in offers)
            plan = {"plan_id": "demo-plan-" + _digest([state.trip_id, state.state_version, [offer["offer_id"] for offer in offers]]), "trip_id": state.trip_id, "state_version": state.state_version, "offers": offers, "total_minor": total, "currency": "RUB", "violations": [], "warnings": [NOTICE, "Показаны фиксированные DEMO-профили, без обещания лучшей цены на рынке."], "room_assignments": {offer["component_id"]: offer["room_assignments"] for offer in offers if offer["kind"] == "hotel"}, "price_scope": "listed_hotels_transport_transfers_and_selected_demo_extras", "source_type": "DEMO", "price_kind": "synthetic", "booking_created": False}
            violations = validate_plan(state, plan)
            if not violations:
                base["plans"].append(plan)
        base["plans"].sort(key=lambda plan: (plan["total_minor"], plan["plan_id"]))
        if not base["plans"]:
            base["warnings"].append("В конечном DEMO-каталоге нет подходящих сочетаний. Бюджет и обязательные условия не ослаблены.")
        return base


def reprice_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """DEMO-only check; deterministic arithmetic, never an external price refresh."""
    if plan.get("source_type") != "DEMO":
        raise DomainError("unsupported_source", "Этот reprice работает только с DEMO.")
    result = deepcopy(plan)
    for offer in result.get("offers", []):
        if offer.get("source_type") != "DEMO":
            raise DomainError("unsupported_source", "Компонент не принадлежит DEMO.")
        for line in offer.get("breakdown", []):
            if any(type(line.get(key)) is not int or line[key] < 0 for key in ("unit_minor", "quantity")):
                raise DomainError("invalid_price", "Для DEMO reprice нужны целые единицы и количество.")
            line["amount_minor"] = line["unit_minor"] * line["quantity"]
        offer["total_minor"] = sum(line["amount_minor"] for line in offer.get("breakdown", []))
    result["total_minor"] = sum(offer["total_minor"] for offer in result.get("offers", []))
    result["repriced"] = True
    result["reprice_kind"] = "deterministic_demo_not_live"
    return result
