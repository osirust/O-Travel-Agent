"""Deterministic fictional inventory. No live prices, availability, or booking."""

from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from datetime import date
from hashlib import sha256
from typing import Any

from .domain import CITIES, MVP_LIMITS, DomainError, TripState, route_edges, validate_trip

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
PLACE_TRANSFER_NOTICE = "Демонстрационный трансфер; стоимость и наличие вымышлены. Трансфер поставщика не бронируется."
PLACE_TRANSFER_CATALOGUE = "verified_place_transfer_demo_v1"
PLACE_TRANSFER_PROVIDER = "demo_place_transfer"
PLACE_TRANSFER_ID_PREFIX = "demo-place-transfer-"
REGISTERED_TRANSFER_CATALOGUE = "registered_city_transfer_demo_v1"
REGISTERED_TRANSFER_PROVIDER = "demo_registered_transfer"
REGISTERED_TRANSFER_ID_PREFIX = "demo-registered-transfer-"
SUPPORTED_CONSTRAINTS = {"breakfast", "refundable", "pets_allowed", "pets", "nonstop"}


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


def _constraints(state: TripState, stay_id: str | None = None, room_id: str | None = None) -> list[Any]:
    return [item for item in state.constraints if item.hard and (item.stay_id is None or item.stay_id == stay_id)
            and (item.room_id is None or item.room_id == room_id)]


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
    scoped = any(c.hard and c.room_id is not None and c.stay_id == stay.id for c in state.constraints)
    room_rates = []
    for room in assignments:
        conditions = _constraints(state, stay.id, room["room_id"])
        options = [candidate for candidate in (rate, *[r for r in RATES if r != rate])
                   if all(c.key not in {"breakfast", "refundable"} or candidate[c.key] == c.value for c in conditions)]
        if not options:
            return None
        room_rates.append({"room_id": room["room_id"], "rate_type": options[0]["id"],
                           "breakfast": options[0]["breakfast"], "refundable": options[0]["refundable"]})
    nights = (stay.check_out - stay.check_in).days
    # Seasonal adjustment is fictional and deterministic, not a market model.
    season = 130 if stay.check_in.month in {6, 7, 8} else 120 if stay.check_in.month == 12 else 90 if stay.check_in.month == 11 else 100
    base = CITY_DATA[stay.city][1] * season * hotel["factor"] // 10_000
    breakdown: list[dict[str, Any]] = []
    people = {person.id: person for person in state.travelers}
    for room in assignments:
        room_rate = next(r for r in room_rates if r["room_id"] == room["room_id"])
        breakdown.append(_line("room_nights", base, nights, room_id=room["room_id"]))
        adults = sum(people[identifier].type == "adult" for identifier in room["traveler_ids"])
        breakdown.append(_line("extra_adult_nights", 120_000, max(0, adults - 2) * nights, room_id=room["room_id"]))
        for identifier in room["traveler_ids"]:
            person = people[identifier]
            age = person.age_on(stay.check_in)
            if person.type == "child":
                unit = 0 if age < 2 else 60_000 if age < 12 else 100_000
                breakdown.append(_line("child_nights", unit, nights, traveler_id=identifier, age=age))
            if room_rate["breakfast"]:
                breakdown.append(_line("breakfast_days", 70_000 if person.type == "adult" else 40_000, nights, traveler_id=identifier))
        if room_rate["refundable"]:
            breakdown.append(_line("flexible_rate_nights", base * 15 // 100, nights, room_id=room["room_id"]))
    if pets:
        breakdown.append(_line("pet_stay_fee", 200_000, len(pets)))
    total = sum(item["amount_minor"] for item in breakdown)
    hotel_id = f"demo-hotel-{CITY_DATA[stay.city][0]}-{hotel['suffix']}"
    rate_id = rate["id"] if not scoped else room_rates[0]["rate_type"] if len({r["rate_type"] for r in room_rates}) == 1 else "mixed"
    identifier = "demo-stay-" + _digest([stay.model_dump(mode="json"), [person.model_dump(mode="json") for person in state.travelers], pets, hotel_id, room_rates if scoped else rate["id"], total])
    result = {"offer_id": identifier, "component_id": stay.id, "kind": "hotel", "name": hotel["name"], "hotel_id": hotel_id, "city": stay.city, "check_in": stay.check_in.isoformat(), "check_out": stay.check_out.isoformat(), "nights": nights, "rate_type": rate_id, "breakfast": rate["breakfast"], "refundable": rate["refundable"], "pets_allowed": hotel["pets_allowed"], "max_pet_kg": hotel["max_pet_kg"], "room_assignments": assignments, "breakdown": breakdown, "total_minor": total, "currency": "RUB", "source_type": "DEMO", "price_kind": "synthetic", "is_demo": True, "availability": "synthetic_not_live"}
    if scoped:
        result["room_rates"] = room_rates
        for key in ("breakfast", "refundable"):
            values = {r[key] for r in room_rates}
            result[key] = room_rates[0][key] if len(values) == 1 else None
    return result


def _route(state: TripState) -> list[tuple[str, str, date, str]]:
    edges = route_edges(state)
    return [(origin, destination, day, state.legs[index].mode if len(state.legs) == len(edges) else "flight")
            for index, (origin, destination, day) in enumerate(edges)]


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
        offers.append({"offer_id": "demo-transport-" + _digest([index, origin, destination, mode, day.isoformat(), breakdown]), "component_id": state.legs[index].id if len(state.legs) == len(route_edges(state)) else f"leg-{index}", "kind": mode, "name": "DEMO — Синтетический перелёт" if mode == "flight" else "DEMO — Синтетический поезд", "origin": origin, "destination": destination, "departure_date": day.isoformat(), "arrival_date": day.isoformat(), "age_basis": "departure_date", "traveler_ids": [person.id for person in state.travelers], "pets_allowed": True, "max_pet_kg": 20, "breakdown": breakdown, "total_minor": sum(item["amount_minor"] for item in breakdown), "currency": "RUB", "source_type": "DEMO", "price_kind": "synthetic", "is_demo": True, "availability": "synthetic_not_live"})
        if mode == "flight":
            offers[-1].update(nonstop=True, segment_count=1, stops_count=0, route_facts_source="synthetic_demo_catalogue")
    for stay in state.stays:
        breakdown = [_line("demo_transfer_passenger", 40_000, len(state.travelers))]
        offers.append({"offer_id": "demo-transfer-" + _digest([stay.id, stay.city, stay.check_in.isoformat(), breakdown]), "component_id": f"transfer-{stay.id}", "kind": "transfer", "name": "DEMO — Синтетический трансфер", "city": stay.city, "date": stay.check_in.isoformat(), "traveler_ids": [person.id for person in state.travelers], "pets_allowed": True, "max_pet_kg": 20, "breakdown": breakdown, "total_minor": sum(item["amount_minor"] for item in breakdown), "currency": "RUB", "source_type": "DEMO", "price_kind": "synthetic", "is_demo": True, "availability": "synthetic_not_live"})
    return offers


def bounded_transport_catalog(state: TripState | dict) -> dict[str, Any]:
    """Independent native-RUB DEMO transport, never a hotel/pin substitution.

    It is a catalogue of canonical snapshots, not a complete checkout plan.
    Hotel availability/rates/pins cannot remove valid transport components;
    route/domain/coverage and transport constraints still fail closed.
    """
    state = TripState.model_validate(state)
    issues = [item for item in validate_trip(state)
              if item["code"] not in {"missing_budget", "unsupported_budget"}]
    pets, pet_questions = _pet_request(state)
    questions = [item["message"] for item in issues if item["code"].startswith("missing_")] + pet_questions
    blocking = [item for item in issues if not item["code"].startswith("missing_")]
    cities = [state.origin, state.route_policy.end_city, *[stay.city for stay in state.stays],
              *[city for leg in state.legs for city in (leg.origin, leg.destination)]]
    outside = list(dict.fromkeys(city for city in cities if city and city not in CITIES))
    if outside:
        blocking.append(_error("unsupported_city", "Текущее покрытие DEMO-транспорта не включает это направление.", cities=outside))
    for constraint in state.constraints:
        if not constraint.hard:
            continue
        if constraint.key not in SUPPORTED_CONSTRAINTS:
            blocking.append(_error("unsupported_constraint", "DEMO-транспорт не подтверждает неизвестное обязательное условие.", key=constraint.key))
        elif constraint.key in {"breakfast", "refundable", "pets_allowed", "nonstop"} and type(constraint.value) is not bool:
            blocking.append(_error("invalid_constraint", "Для обязательного условия нужно true/false.", key=constraint.key))
    if any(pet["weight_kg"] > 20 for pet in pets):
        blocking.append(_error("transport_pets", "Животное не соответствует пределу DEMO-транспорта 20 кг."))
    base = {"offers": [], "questions": list(dict.fromkeys(questions)), "violations": blocking,
            "source_type": "DEMO", "price_kind": "synthetic", "scope": "transport_only", "warnings": [NOTICE]}
    if questions or blocking:
        return base
    domestic = set(CITIES[:4])
    if any(mode == "train" and (origin not in domestic or destination not in domestic) for origin, destination, _, mode in _route(state)):
        base["violations"].append(_error("unsupported_transport", "DEMO-поезда поддерживаются только между четырьмя российскими городами."))
        return base
    offers = _transport_offers(state, pets)
    if any(offer["kind"] == "flight" and (offer.get("nonstop") is not True or offer.get("segment_count") != 1
            or offer.get("stops_count") != 0 or offer.get("route_facts_source") != "synthetic_demo_catalogue") for offer in offers):
        base["violations"].append(_error("flight_facts_mismatch", "Не подтверждены свойства конечного DEMO-перелёта."))
        return base
    base["offers"] = offers
    return base


def bounded_place_transfer_catalog(state: TripState | dict) -> dict[str, Any]:
    """Finite synthetic transfer snapshots for explicitly selected place refs.

    The server's persistent selection boundary supplies the canonical refs;
    this pure catalogue neither verifies live geography nor makes HTTP calls.
    It never creates a hotel/flight or joins a city/country by its display name.
    The existing HYBRID+demo_fallback policy is required even for validation.
    """
    state = TripState.model_validate(state)
    base = {"offers": [], "questions": [], "violations": [], "source_type": "DEMO",
        "price_kind": "synthetic", "scope": "verified_place_transfers_only", "warnings": [PLACE_TRANSFER_NOTICE]}
    if state.search_policy.mode != "HYBRID" or not state.search_policy.demo_fallback or not state.location_refs:
        return base
    issues = [issue for issue in validate_trip(state)
        if issue["code"] not in {"missing_budget", "unsupported_budget"}]
    pets, questions = _pet_request(state)
    base["questions"] = list(dict.fromkeys([i["message"] for i in issues if i["code"].startswith("missing_")] + questions))
    base["violations"] = [i for i in issues if not i["code"].startswith("missing_")]
    for condition in state.constraints:
        if condition.hard and condition.key not in SUPPORTED_CONSTRAINTS:
            base["violations"].append(_error("unsupported_constraint",
                "Демонстрационный трансфер не подтверждает неизвестное обязательное условие.", key=condition.key))
        elif (condition.hard and condition.key in {"breakfast", "refundable", "pets_allowed", "nonstop"}
                and type(condition.value) is not bool):
            base["violations"].append(_error("invalid_constraint", "Для обязательного условия нужно true/false.", key=condition.key))
    if any(pet["weight_kg"] > 20 for pet in pets):
        base["violations"].append(_error("transport_pets", "Демонстрационный трансфер поддерживает животных до20кг."))
    people = [person.id for person in state.travelers]
    if not 1 <= len(people) <= MVP_LIMITS["travelers"]:
        base["violations"].append(_error("transfer_capacity", "Число пассажиров вне конечного профиля DEMO-трансфера."))
    if base["questions"] or base["violations"]:
        return base
    seats_per_vehicle = 4
    vehicles = (len(people) + seats_per_vehicle - 1) // seats_per_vehicle
    for stay in state.stays:
        ref = state.location_refs.get(stay.place_id) if stay.place_id is not None else None
        if ref is None or stay.city != ref.canonical_name or stay.check_in is None or stay.check_out is None:
            base["violations"].append(_error("transfer_place_unverified",
                "Для этого DEMO-трансфера нужны точная выбранная географическая ссылка и даты.", stay_id=stay.id))
            continue
        breakdown = [_line("demo_transfer_passenger", 40_000, len(people))]
        if pets:
            breakdown.append(_line("demo_transfer_pet", 100_000, len(pets)))
        identity = {"catalogue": PLACE_TRANSFER_CATALOGUE, "trip_id": state.trip_id,
            "state_version": state.state_version, "stay_id": stay.id, "place_id": ref.place_id,
            "place_ref_sha256": ref.snapshot_sha256(), "check_in": stay.check_in.isoformat(),
            "check_out": stay.check_out.isoformat(), "travelers": [p.model_dump(mode="json") for p in state.travelers],
            "pets": pets, "breakdown": breakdown, "vehicle_count": vehicles, "seats_per_vehicle": seats_per_vehicle}
        base["offers"].append({"offer_id": PLACE_TRANSFER_ID_PREFIX + _digest(identity),
            "provider": PLACE_TRANSFER_PROVIDER, "component_id": "transfer-" + stay.id,
            "catalogue": PLACE_TRANSFER_CATALOGUE, "kind": "transfer", "name": PLACE_TRANSFER_NOTICE,
            "trip_id": state.trip_id, "state_version": state.state_version, "stay_id": stay.id,
            "city": ref.canonical_name, "country_iso2": ref.country_iso2, "place_id": ref.place_id,
            "place_ref_version": ref.ref_version, "place_ref_sha256": ref.snapshot_sha256(),
            "date": stay.check_in.isoformat(), "check_out": stay.check_out.isoformat(),
            "traveler_ids": people.copy(), "vehicle_count": vehicles, "seats_per_vehicle": seats_per_vehicle,
            "capacity": vehicles * seats_per_vehicle, "capacity_source": "synthetic_demo_profile",
            "pets_allowed": True, "max_pet_kg": 20, "breakdown": breakdown,
            "total_minor": sum(line["amount_minor"] for line in breakdown), "currency": "RUB",
            "fees_included": True, "fees_scope": "synthetic_transfer_rule_all_inclusive",
            "source_type": "DEMO", "price_kind": "synthetic", "is_demo": True,
            "availability": "synthetic_not_live", "booking_created_at_supplier": False})
    return base


def bounded_registered_transfer_catalog(state: TripState | dict) -> dict[str, Any]:
    """Synthetic transfers for the explicit fixed LiteAPI/Duffel registry only.

    Does not extend hotel/flight DEMO coverage, invent CityRefs, or assert any
    location, availability, distance or supplier transfer fact. Dynamic refs
    continue to use the separate exact-ref catalogue unchanged.
    """
    from .provider_cities import available_search_cities, flight_iata, liteapi_city_query

    state = TripState.model_validate(state)
    base = {"offers": [], "questions": [], "violations": [], "source_type": "DEMO",
        "price_kind": "synthetic", "scope": "registered_city_transfers_only", "warnings": [PLACE_TRANSFER_NOTICE]}
    policy = state.search_policy
    if (state.location_refs or policy.mode != "HYBRID" or not policy.demo_fallback
            or policy.hotel_provider != "liteapi" or policy.flight_provider != "duffel"):
        return base
    issues = [issue for issue in validate_trip(state)
        if issue["code"] not in {"missing_budget", "unsupported_budget"}]
    pets, questions = _pet_request(state)
    base["questions"] = list(dict.fromkeys([issue["message"] for issue in issues
        if issue["code"].startswith("missing_")] + questions))
    base["violations"] = [issue for issue in issues if not issue["code"].startswith("missing_")]
    for condition in state.constraints:
        if condition.hard and condition.key not in SUPPORTED_CONSTRAINTS:
            base["violations"].append(_error("unsupported_constraint",
                "Демонстрационный трансфер не подтверждает неизвестное обязательное условие.", key=condition.key))
        elif (condition.hard and condition.key in {"breakfast", "refundable", "pets_allowed", "nonstop"}
                and type(condition.value) is not bool):
            base["violations"].append(_error("invalid_constraint", "Для обязательного условия нужно true/false.", key=condition.key))
    if any(pet["weight_kg"] > 20 for pet in pets):
        base["violations"].append(_error("transport_pets", "Демонстрационный трансфер поддерживает животных до20кг."))
    if not 1 <= len(state.travelers) <= MVP_LIMITS["travelers"]:
        base["violations"].append(_error("transfer_capacity", "Число пассажиров вне конечного профиля DEMO-трансфера."))
    allowed = set(available_search_cities(state))
    if any(stay.city not in allowed or liteapi_city_query(stay.city) is None or flight_iata(stay.city) is None
            or stay.place_id is not None for stay in state.stays):
        base["violations"].append(_error("transfer_city_unregistered",
            "Этот DEMO-трансфер ограничен явно зарегистрированными направлениями LiteAPI/Duffel."))
    if base["questions"] or base["violations"]:
        return base
    people = [person.id for person in state.travelers]
    seats, vehicles = 4, (len(people) + 3) // 4
    # This existing deterministic generator is a price rule, never an external
    # route. Only its transfer rows leave this function.
    for original in _transport_offers(state, pets):
        if original["kind"] != "transfer":
            continue
        stay = next(stay for stay in state.stays if original["component_id"] == "transfer-" + stay.id)
        country, provider_city = liteapi_city_query(stay.city)
        identity = {"catalogue": REGISTERED_TRANSFER_CATALOGUE, "trip_id": state.trip_id,
            "state_version": state.state_version, "stay_id": stay.id, "city": stay.city,
            "check_in": stay.check_in.isoformat(), "check_out": stay.check_out.isoformat(),
            "travelers": [person.model_dump(mode="json") for person in state.travelers],
            "pets": pets, "breakdown": original["breakdown"], "vehicles": vehicles, "seats_per_vehicle": seats}
        offer = {**original, "offer_id": REGISTERED_TRANSFER_ID_PREFIX + _digest(identity),
            "provider": REGISTERED_TRANSFER_PROVIDER, "catalogue": REGISTERED_TRANSFER_CATALOGUE,
            "name": PLACE_TRANSFER_NOTICE, "trip_id": state.trip_id, "state_version": state.state_version,
            "stay_id": stay.id, "check_out": stay.check_out.isoformat(), "country_iso2": country,
            "registry_city_label": provider_city, "registry_iata_code": flight_iata(stay.city),
            "vehicle_count": vehicles, "seats_per_vehicle": seats, "capacity": vehicles * seats,
            "capacity_source": "synthetic_demo_profile", "booking_created_at_supplier": False,
            "fees_included": True, "fees_scope": "synthetic_transfer_rule_all_inclusive"}
        if pets:
            offer["breakdown"].append(_line("demo_transfer_pet", 100_000, len(pets)))
            offer["total_minor"] = sum(item["amount_minor"] for item in offer["breakdown"])
            # Pet charges participate in the canonical identity as well.
            identity["breakdown"] = offer["breakdown"]
            offer["offer_id"] = REGISTERED_TRANSFER_ID_PREFIX + _digest(identity)
        base["offers"].append(offer)
    return base


def validate_plan(state: TripState, plan: dict[str, Any], *, check_budget: bool = True) -> list[dict[str, Any]]:
    violations = validate_trip(state)
    if not check_budget:
        violations = [issue for issue in violations if issue["code"] not in {"missing_budget", "unsupported_budget"}]
    if not isinstance(plan, dict):
        return violations + [_error("invalid_plan", "План должен быть объектом.")]
    if check_budget and plan.get("catalogue_only") is True:
        return [_error("catalogue_not_checkout", "Каталог исходных DEMO-компонентов не является готовой корзиной.")]
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
    if check_budget and state.budget and total > state.budget.amount_minor:
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
        facts = {}
        room_rates = offer.get("room_rates")
        if room_rates is None:
            if known_rate is None:
                violations.append(_error("unknown_rate", "Тариф отсутствует в конечном DEMO-каталоге."))
            else:
                facts = {room.id: known_rate for room in stay.rooms}
                if any(offer.get(key) is not known_rate[key] for key in ("breakfast", "refundable")):
                    violations.append(_error("property_facts_mismatch", "Свойства тарифа не совпадают с данными каталога."))
            if any(c.hard and c.stay_id == stay.id and c.room_id is not None for c in state.constraints):
                violations.append(_error("missing_room_rate_evidence", "Для условий конкретного номера нужны его собственные тарифные факты.", stay_id=stay.id))
        elif (not isinstance(room_rates, list) or any(not isinstance(r, dict) or not isinstance(r.get("room_id"), str) for r in room_rates)
              or Counter(r.get("room_id") for r in room_rates) != Counter(room.id for room in stay.rooms)):
            violations.append(_error("invalid_room_rates", "Тарифы должны однозначно покрывать каждый номер остановки.", stay_id=stay.id))
        else:
            for row in room_rates:
                original_rate = next((r for r in RATES if r["id"] == row.get("rate_type")), None)
                if original_rate is None or any(row.get(key) is not original_rate[key] for key in ("breakfast", "refundable")):
                    violations.append(_error("property_facts_mismatch", "Тариф конкретного номера не совпадает с каталогом.", stay_id=stay.id, room_id=row.get("room_id")))
                else:
                    facts[row["room_id"]] = original_rate
            if len(facts) == len(stay.rooms):
                rate_ids = {row["id"] for row in facts.values()}
                aggregate_rate = next(iter(rate_ids)) if len(rate_ids) == 1 else "mixed"
                expected = {key: next(iter(values)) if len(values) == 1 else None
                            for key in ("breakfast", "refundable")
                            for values in [{row[key] for row in facts.values()}]}
                if offer.get("rate_type") != aggregate_rate or any(offer.get(key) is not value for key, value in expected.items()):
                    violations.append(_error("property_facts_mismatch", "Общие свойства не совпадают с тарифами отдельных номеров."))
        if known_hotel is None:
            violations.append(_error("unknown_rate", "Отель отсутствует в конечном DEMO-каталоге."))
        elif any(offer.get(key) != known_hotel[key] for key in ("pets_allowed", "max_pet_kg")):
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
        for constraint in [c for c in state.constraints if c.hard and (c.stay_id is None or c.stay_id == stay.id)]:
            if constraint.key not in SUPPORTED_CONSTRAINTS:
                violations.append(_error("unsupported_constraint", "DEMO не умеет подтверждать это жёсткое условие.", key=constraint.key))
            elif constraint.key in {"breakfast", "refundable"} and any(
                    facts.get(room.id, {}).get(constraint.key) is not constraint.value
                    for room in stay.rooms if constraint.room_id is None or constraint.room_id == room.id):
                violations.append(_error("constraint_violation", "Тариф номера нарушает обязательное условие.", key=constraint.key, stay_id=stay.id, room_id=constraint.room_id))
            elif constraint.key == "pets_allowed" and offer.get(constraint.key) != constraint.value:
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
        if offer.get("kind") == "flight" and (offer.get("nonstop") is not True or offer.get("segment_count") != 1
                or offer.get("stops_count") != 0 or offer.get("route_facts_source") != "synthetic_demo_catalogue"):
            violations.append(_error("flight_facts_mismatch", "Свойства перелёта не совпадают с конечным DEMO-каталогом."))
        if pets and offer.get("kind") in {"flight", "train", "transfer"} and (offer.get("pets_allowed") is not True or offer.get("max_pet_kg") != 20 or any(pet["weight_kg"] > 20 for pet in pets)):
            violations.append(_error("transport_pets", "Животное не соответствует DEMO-правилам транспорта."))
    transfers = [offer for offer in offers if offer.get("kind") == "transfer"]
    if len(transfers) != len(state.stays) or any(len([offer for offer in transfers if offer.get("component_id") == f"transfer-{stay.id}" and offer.get("city") == stay.city and offer.get("date") == (stay.check_in.isoformat() if stay.check_in else None)]) != 1 for stay in state.stays):
        violations.append(_error("transfer_mismatch", "Для каждой остановки нужен соответствующий трансфер."))
    return violations


class DemoInventory:
    def search_trip(self, state: TripState, *, catalogue_only: bool = False) -> dict[str, Any]:
        issues = validate_trip(state)
        if catalogue_only:
            # Discover native RUB components, then enforce the user's actual
            # currency/budget only after all selected sources are assembled.
            issues = [issue for issue in issues if issue["code"] not in {"missing_budget", "unsupported_budget"}]
        pets, pet_questions = _pet_request(state)
        questions = [item["message"] for item in issues if item["code"].startswith("missing_")] + pet_questions
        blocking = [item for item in issues if not item["code"].startswith("missing_")]
        outside = list(dict.fromkeys(city for city in [state.origin, *[stay.city for stay in state.stays],
            *[city for leg in state.legs for city in (leg.origin, leg.destination)]] if city and city not in CITIES))
        if outside:
            blocking.append(_error("unsupported_city", "Текущее покрытие DEMO-поиска не включает: " + ", ".join(outside)
                + ". Направление сохранено; предложения для него не выдумываются.", cities=outside))
        for constraint in state.constraints:
            if constraint.hard and constraint.key not in SUPPORTED_CONSTRAINTS:
                blocking.append(_error("unsupported_constraint", "DEMO не подтверждает это жёсткое условие.", key=constraint.key))
            if constraint.hard and constraint.key in {"breakfast", "refundable", "pets_allowed", "nonstop"} and type(constraint.value) is not bool:
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
            if catalogue_only:
                plan["catalogue_only"] = True
            violations = validate_plan(state, plan, check_budget=not catalogue_only)
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
