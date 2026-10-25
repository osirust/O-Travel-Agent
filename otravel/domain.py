"""Canonical trip data and explicit, atomic edits. No network or persistence."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# Proposed implementation guards, not approved product limits. Rooms are per stay.
MVP_LIMITS = {"travelers": 10, "stays": 4, "rooms_per_stay": 5}
CITIES = ("Москва", "Санкт-Петербург", "Сочи", "Казань", "Стамбул", "Анталья", "Дубай")
_ALIASES = {
    "москва": "Москва", "мск": "Москва", "moscow": "Москва",
    "санкт-петербург": "Санкт-Петербург", "санкт петербург": "Санкт-Петербург",
    "спб": "Санкт-Петербург", "питер": "Санкт-Петербург",
    "saint petersburg": "Санкт-Петербург", "st petersburg": "Санкт-Петербург",
    "st. petersburg": "Санкт-Петербург", "saint-petersburg": "Санкт-Петербург",
    "сочи": "Сочи", "sochi": "Сочи", "казань": "Казань", "kazan": "Казань",
    "стамбул": "Стамбул", "istanbul": "Стамбул", "анталья": "Анталья",
    "antalya": "Анталья", "antalia": "Анталья", "дубай": "Дубай", "dubai": "Дубай",
}


class DomainError(ValueError):
    def __init__(self, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


def canonical_city(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DomainError("missing_city", "Укажите город.")
    key = " ".join(value.strip().casefold().replace("ё", "е").split())
    if key not in _ALIASES:
        from .geography import known_cities, parse_destination

        place = parse_destination(value, available_cities=CITIES)["requested_destination"]
        if place["kind"] == "city" and place["name"] in known_cities():
            return place["name"]
        raise DomainError("unsupported_city", f"Город «{value}» не распознан в справочнике известных городов. Уточните название города.")
    return _ALIASES[key]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Traveler(Model):
    id: str = Field(min_length=1)
    type: Literal["adult", "child"]
    age: int | None = Field(default=None, ge=0, le=120, strict=True)
    birth_date: date | None = None
    group_id: str | None = None

    def age_on(self, day: date) -> int | None:
        if self.birth_date is None:
            return self.age
        return day.year - self.birth_date.year - ((day.month, day.day) < (self.birth_date.month, self.birth_date.day))


class Group(Model):
    id: str = Field(min_length=1)
    traveler_ids: list[str] = Field(default_factory=list)
    keep_separate: bool = False


class RoomAllocation(Model):
    id: str = Field(min_length=1)
    traveler_ids: list[str] = Field(default_factory=list)


class Stay(Model):
    id: str = Field(min_length=1)
    city: str | None = None
    check_in: date | None = None
    check_out: date | None = None
    rooms: list[RoomAllocation] = Field(default_factory=list)
    hotel_id: str | None = None

    @field_validator("city")
    @classmethod
    def normalize_city(cls, value: str | None) -> str | None:
        return canonical_city(value) if value else None


class Leg(Model):
    id: str = Field(min_length=1)
    origin: str | None = None
    destination: str | None = None
    departure_date: date | None = None
    mode: Literal["flight", "train"] = "flight"

    @field_validator("origin", "destination")
    @classmethod
    def normalize_city(cls, value: str | None) -> str | None:
        return canonical_city(value) if value else None


class Budget(Model):
    amount_minor: int = Field(gt=0, strict=True)
    currency: str = "RUB"
    scope: str = "whole_trip"


class Constraint(Model):
    key: str = Field(min_length=1)
    value: Any
    hard: bool = True
    stay_id: str | None = None


class Preference(Model):
    key: str = Field(min_length=1)
    value: Any = True
    weight: float = Field(default=1.0, ge=0, le=1)


class SearchPolicy(Model):
    mode: Literal["DEMO", "HYBRID"] = "DEMO"
    hotel_provider: Literal["liteapi", "serpapi"] = "liteapi"
    flight_provider: Literal["duffel", "serpapi"] = "serpapi"
    demo_fallback: bool = True
    guest_nationality: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")


class TripState(Model):
    trip_id: str = Field(default_factory=lambda: str(uuid4()))
    state_version: int = Field(default=0, ge=0, strict=True)
    origin: str | None = None
    travelers: list[Traveler] = Field(default_factory=list)
    groups: list[Group] = Field(default_factory=list)
    legs: list[Leg] = Field(default_factory=list)
    stays: list[Stay] = Field(default_factory=list)
    budget: Budget | None = None
    constraints: list[Constraint] = Field(default_factory=list)
    preferences: list[Preference] = Field(default_factory=list)
    search_policy: SearchPolicy = Field(default_factory=SearchPolicy)

    @field_validator("origin")
    @classmethod
    def normalize_city(cls, value: str | None) -> str | None:
        return canonical_city(value) if value else None


class Operation(Model):
    type: str
    target_id: str | None = None
    value: dict[str, Any] = Field(default_factory=dict)


def _issue(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "message": message, "details": details}


def validate_trip(state: TripState, *, final: bool = True) -> list[dict[str, Any]]:
    """Structural checks; final additionally requires search inputs, never guesses them."""
    issues: list[dict[str, Any]] = []
    for name, items in (("travelers", state.travelers), ("groups", state.groups), ("legs", state.legs), ("stays", state.stays)):
        ids = [item.id for item in items]
        if len(ids) != len(set(ids)):
            issues.append(_issue("duplicate_id", f"Повторяются ID в {name}.", collection=name))
    for name, count, limit in (("travelers", len(state.travelers), MVP_LIMITS["travelers"]), ("stays", len(state.stays), MVP_LIMITS["stays"])):
        if count > limit:
            issues.append(_issue("unsupported_limit", "Превышен предварительный guard реализации; предел не утверждён владельцем.", field=name, count=count, proposed_limit=limit))
    people = {person.id: person for person in state.travelers}
    groups = {group.id: group for group in state.groups}
    for person in state.travelers:
        if person.group_id and person.group_id not in groups:
            issues.append(_issue("unknown_group", "Участник ссылается на неизвестную группу.", traveler_id=person.id))
        if person.type == "adult" and person.age is not None and person.age < 18:
            issues.append(_issue("invalid_age", "Возраст взрослого должен быть не меньше 18.", traveler_id=person.id))
        if person.type == "child" and person.age is not None and person.age >= 18:
            issues.append(_issue("invalid_age", "Для ребёнка укажите возраст младше 18.", traveler_id=person.id))
        if final and person.type == "child" and person.age is None and person.birth_date is None:
            issues.append(_issue("missing_child_age", "Сколько лет ребёнку на дату поездки?", traveler_id=person.id))
        if person.birth_date:
            for stay in state.stays:
                if stay.check_in and person.age_on(stay.check_in) < 0:
                    issues.append(_issue("invalid_age", "Дата рождения позже поездки.", traveler_id=person.id))
                elif stay.check_in and person.type == "adult" and person.age_on(stay.check_in) < 18:
                    issues.append(_issue("invalid_age", "Дата рождения не соответствует типу adult.", traveler_id=person.id))
                elif stay.check_in and person.type == "child" and person.age_on(stay.check_in) >= 18:
                    issues.append(_issue("invalid_age", "Дата рождения не соответствует типу child.", traveler_id=person.id))
    stay_ids = {stay.id for stay in state.stays}
    for constraint in state.constraints:
        if constraint.stay_id is not None and constraint.stay_id not in stay_ids:
            issues.append(_issue("unknown_constraint_target", "Условие ссылается на неизвестную остановку.", stay_id=constraint.stay_id))
    for group in state.groups:
        if len(group.traveler_ids) != len(set(group.traveler_ids)):
            issues.append(_issue("duplicate_group_member", "Участник дважды записан в группе.", group_id=group.id))
        for identifier in group.traveler_ids:
            if identifier not in people or people[identifier].group_id != group.id:
                issues.append(_issue("group_membership_mismatch", "Состав группы не совпадает с traveler.group_id.", group_id=group.id, traveler_id=identifier))
        if any(person.group_id == group.id and person.id not in group.traveler_ids for person in state.travelers):
            issues.append(_issue("group_membership_mismatch", "В группе отсутствует связанный участник.", group_id=group.id))
    previous: Stay | None = None
    for stay in state.stays:
        if len(stay.rooms) > MVP_LIMITS["rooms_per_stay"]:
            issues.append(_issue("unsupported_limit", "Превышен предварительный предел комнат на остановку.", stay_id=stay.id, proposed_limit=MVP_LIMITS["rooms_per_stay"]))
        if len({room.id for room in stay.rooms}) != len(stay.rooms):
            issues.append(_issue("duplicate_room_id", "Повторяются ID комнат одной остановки.", stay_id=stay.id))
        assigned = [identifier for room in stay.rooms for identifier in room.traveler_ids]
        if len(assigned) != len(set(assigned)):
            issues.append(_issue("duplicate_allocation", "Участник размещён дважды на одной остановке.", stay_id=stay.id))
        if any(identifier not in people for identifier in assigned):
            issues.append(_issue("unknown_traveler", "В размещении указан неизвестный участник.", stay_id=stay.id))
        if stay.check_in and stay.check_out and stay.check_out <= stay.check_in:
            issues.append(_issue("invalid_dates", "Выезд должен быть позже заезда.", stay_id=stay.id))
        if previous and previous.check_out and stay.check_in and stay.check_in < previous.check_out:
            issues.append(_issue("overlapping_stays", "Последовательные остановки пересекаются по датам.", stay_id=stay.id))
        if final and previous and previous.check_out and stay.check_in and stay.check_in > previous.check_out:
            issues.append(_issue("missing_gap_plan", "Между остановками есть дни без размещения. Где планируете провести этот промежуток?", stay_id=stay.id))
        previous = stay
        if final:
            for field in ("city", "check_in", "check_out"):
                if getattr(stay, field) is None:
                    issues.append(_issue("missing_" + field, f"Укажите {field} для остановки.", stay_id=stay.id))
            if not stay.rooms:
                issues.append(_issue("missing_rooms", "Сколько номеров нужно на этой остановке?", stay_id=stay.id))
    if final:
        if not state.origin:
            issues.append(_issue("missing_origin", "Из какого города выезжаете?"))
        if not state.travelers:
            issues.append(_issue("missing_travelers", "Кто поедет: сколько взрослых и детей?"))
        elif not any(person.type == "adult" for person in state.travelers):
            issues.append(_issue("adult_required", "DEMO не поддерживает поездку детей без взрослого."))
        if not state.stays:
            issues.append(_issue("missing_stays", "В какие города и на какие даты планируете поездку?"))
        if state.budget and (state.budget.currency != "RUB" or state.budget.scope != "whole_trip"):
            issues.append(_issue("unsupported_budget", "DEMO пока считает только общий бюджет поездки в RUB."))
        if state.legs and state.origin and state.stays and all(stay.city and stay.check_in and stay.check_out for stay in state.stays):
            expected = [(state.origin, state.stays[0].city, state.stays[0].check_in)]
            expected += [(a.city, b.city, b.check_in) for a, b in zip(state.stays, state.stays[1:])]
            expected.append((state.stays[-1].city, state.origin, state.stays[-1].check_out))
            actual = [(leg.origin, leg.destination, leg.departure_date or expected[index][2] if index < len(expected) else None) for index, leg in enumerate(state.legs)]
            if actual != expected:
                issues.append(_issue("leg_route_mismatch", "Плечи не соответствуют остановкам и датам поездки."))
    return issues


def _target(items: list[dict[str, Any]], identifier: str | None, kind: str) -> dict[str, Any]:
    for item in items:
        if item["id"] == identifier:
            return item
    raise DomainError("unknown_target", f"Не найден {kind} с ID {identifier}.")


def _keys(value: dict[str, Any], allowed: set[str]) -> None:
    extra = set(value) - allowed
    if extra:
        raise DomainError("unknown_fields", "Неизвестные поля операции.", {"fields": sorted(extra)})


def _synchronize_route(data: dict[str, Any]) -> None:
    """Rebuild affected derived legs; preserve modes and IDs of corresponding edges."""
    if not data["stays"]:
        data["legs"] = []
        return
    if not data["origin"] or any(not stay.get("city") or not stay.get("check_in") or not stay.get("check_out") for stay in data["stays"]):
        return
    old = data["legs"]
    edges = [(data["origin"], data["stays"][0]["city"], data["stays"][0]["check_in"])]
    edges += [(a["city"], b["city"], b["check_in"]) for a, b in zip(data["stays"], data["stays"][1:])]
    edges.append((data["stays"][-1]["city"], data["origin"], data["stays"][-1]["check_out"]))
    used = set()
    rebuilt = []
    for index, (origin, destination, day) in enumerate(edges):
        previous = next((leg for leg in old if leg["origin"] == origin and leg["destination"] == destination and leg["id"] not in used), None)
        if previous is None and len(old) == len(edges):
            previous = old[index]
        identifier = previous["id"] if previous and previous["id"] not in used else "leg-" + str(uuid4())
        used.add(identifier)
        rebuilt.append({"id": identifier, "origin": origin, "destination": destination, "departure_date": day, "mode": previous["mode"] if previous else "flight"})
    data["legs"] = rebuilt


def apply_operations(state: TripState, operations: list[Operation]) -> tuple[TripState, list[dict[str, Any]]]:
    """Atomic edits on a copy; version belongs to the persistent store, not this function."""
    data = deepcopy(state.model_dump(mode="json"))
    changes: list[dict[str, Any]] = []
    route_changed = False
    allowed = {"set_origin", "set_budget", "set_search_policy", "set_preference", "add_traveler", "remove_traveler", "upsert_stay", "remove_stay", "set_constraint", "clear_constraint", "shift_return", "replace_hotel", "update_traveler"}
    for raw_operation in operations:
        try:
            operation = raw_operation if isinstance(raw_operation, Operation) else Operation.model_validate(raw_operation)
            kind, value, target = operation.type, deepcopy(operation.value), operation.target_id
            if kind not in allowed:
                raise DomainError("unsupported_operation", f"Операция {kind} не поддерживается.")
            before = deepcopy(data)
            if kind == "set_origin":
                _keys(value, {"city", "origin"})
                data["origin"] = canonical_city(value.get("city", value.get("origin")))
            elif kind == "set_budget":
                _keys(value, {"amount_minor", "currency", "scope"})
                data["budget"] = Budget.model_validate(value).model_dump(mode="json")
            elif kind == "set_preference":
                _keys(value, {"key", "value", "weight"})
                if target is not None or (value.get("key"), value.get("value")) not in (("price_priority", "lowest_total"), ("departure_time", "early_morning")):
                    raise DomainError("invalid_preference", "Поддерживаются предпочтения минимальной стоимости и раннего вылета без привязки к объекту.")
                if "weight" in value and type(value["weight"]) not in {int, float}:
                    raise DomainError("invalid_preference", "Вес предпочтения задаётся числом от 0 до 1.")
                existing = next((i for i, item in enumerate(data["preferences"]) if item["key"] == value["key"]), None)
                prior = data["preferences"][existing] if existing is not None else {}
                preference = Preference.model_validate({**prior, **value}).model_dump(mode="json")
                if existing is None:
                    data["preferences"].append(preference)
                else:
                    data["preferences"][existing] = preference
                    data["preferences"] = [item for i, item in enumerate(data["preferences"]) if item["key"] != preference["key"] or i == existing]
            elif kind == "set_search_policy":
                _keys(value, set(SearchPolicy.model_fields))
                data["search_policy"] = SearchPolicy.model_validate({**data["search_policy"], **value}).model_dump(mode="json")
            elif kind == "add_traveler":
                _keys(value, {"id", "type", "age", "birth_date", "group_id"})
                value.setdefault("id", "traveler-" + str(uuid4()))
                person = Traveler.model_validate(value).model_dump(mode="json")
                if any(item["id"] == person["id"] for item in data["travelers"]):
                    raise DomainError("duplicate_id", "Участник с таким ID уже существует.")
                data["travelers"].append(person)
                if person["group_id"]:
                    group = next((item for item in data["groups"] if item["id"] == person["group_id"]), None)
                    if group is None:
                        group = {"id": person["group_id"], "traveler_ids": [], "keep_separate": False}
                        data["groups"].append(group)
                    group["traveler_ids"].append(person["id"])
            elif kind in {"remove_traveler", "update_traveler"}:
                person = _target(data["travelers"], target, "участник")
                if kind == "remove_traveler":
                    _keys(value, set())
                    data["travelers"].remove(person)
                    for group in data["groups"]:
                        group["traveler_ids"] = [item for item in group["traveler_ids"] if item != target]
                    for stay in data["stays"]:
                        for room in stay["rooms"]:
                            room["traveler_ids"] = [item for item in room["traveler_ids"] if item != target]
                else:
                    _keys(value, {"type", "age", "birth_date", "group_id"})
                    prior_group = person["group_id"]
                    person.update(value)
                    if person["group_id"] != prior_group:
                        for group in data["groups"]:
                            group["traveler_ids"] = [item for item in group["traveler_ids"] if item != target]
                        if person["group_id"]:
                            group = _target(data["groups"], person["group_id"], "группа")
                            group["traveler_ids"].append(target)
            elif kind == "upsert_stay":
                _keys(value, {"id", "city", "check_in", "check_out", "rooms", "hotel_id"})
                identifier = target or value.get("id")
                if not identifier:
                    raise DomainError("missing_target", "Для остановки нужен стабильный ID.")
                if value.get("id", identifier) != identifier:
                    raise DomainError("target_mismatch", "ID остановки не совпадает с target_id.")
                existing = next((item for item in data["stays"] if item["id"] == identifier), None)
                if existing is None:
                    data["stays"].append({"id": identifier, **value})
                else:
                    existing.update(value)
            elif kind == "remove_stay":
                _keys(value, set())
                stay = _target(data["stays"], target, "остановка")
                data["stays"].remove(stay)
            elif kind == "replace_hotel":
                _keys(value, {"hotel_id"})
                if not isinstance(value.get("hotel_id"), str) or not value["hotel_id"]:
                    raise DomainError("missing_hotel", "Укажите hotel_id из сохранённого предложения.")
                _target(data["stays"], target, "остановка")["hotel_id"] = value["hotel_id"]
            elif kind == "shift_return":
                _keys(value, {"days"})
                days = value.get("days")
                if type(days) is not int:
                    raise DomainError("invalid_days", "Сдвиг возвращения задаётся целым числом дней.")
                if not data["stays"] or not data["stays"][-1].get("check_out"):
                    raise DomainError("missing_return_date", "Сначала укажите дату возвращения.")
                last = data["stays"][-1]
                last["check_out"] = (date.fromisoformat(last["check_out"]) + timedelta(days=days)).isoformat()
                if data["legs"]:
                    data["legs"][-1]["departure_date"] = last["check_out"]
            elif kind == "set_constraint":
                _keys(value, {"key", "value", "hard", "stay_id"})
                constraint = Constraint.model_validate(value).model_dump(mode="json")
                data["constraints"] = [item for item in data["constraints"] if (item["key"], item.get("stay_id")) != (constraint["key"], constraint["stay_id"])]
                data["constraints"].append(constraint)
            elif kind == "clear_constraint":
                _keys(value, {"key", "stay_id"})
                key = value.get("key") or target
                if not key:
                    raise DomainError("missing_constraint", "Укажите key удаляемого условия.")
                data["constraints"] = [item for item in data["constraints"] if (item["key"], item.get("stay_id")) != (key, value.get("stay_id"))]
            candidate = TripState.model_validate(data)
            data = candidate.model_dump(mode="json")
            route_changed |= kind in {"set_origin", "upsert_stay", "remove_stay", "shift_return"}
            if before != data:
                for field in data:
                    if before.get(field) != data[field]:
                        changes.append({"operation": kind, "target_id": target, "field": field, "before": before.get(field), "after": deepcopy(data[field])})
        except ValidationError as exc:
            raise DomainError("invalid_operation", "Поля операции не прошли проверку.", exc.errors(include_url=False, include_context=False)) from exc
    if route_changed:
        previous_legs = deepcopy(data["legs"])
        _synchronize_route(data)
        if previous_legs != data["legs"]:
            changes.append({"operation": "rebuild_route", "target_id": None, "field": "legs", "before": previous_legs, "after": deepcopy(data["legs"])})
    result = TripState.model_validate(data)
    failures = validate_trip(result, final=False)
    if failures:
        raise DomainError(failures[0]["code"], failures[0]["message"], failures)
    return result, changes


def sample_trip() -> TripState:
    """An explicitly fictional nine-person/two-family example, never user defaults."""
    people = [Traveler(id=f"adult-{i}", type="adult", group_id="family-1" if i <= 3 else "family-2") for i in range(1, 7)]
    people += [Traveler(id="child-1", type="child", age=3, group_id="family-1"), Traveler(id="child-2", type="child", age=7, group_id="family-1"), Traveler(id="child-3", type="child", age=15, group_id="family-2")]
    allocations = [["adult-1", "adult-2", "child-1", "child-2"], ["adult-3"], ["adult-4", "adult-5", "adult-6", "child-3"]]
    stays = [Stay(id=identifier, city=city, check_in=start, check_out=end, rooms=[RoomAllocation(id=f"{identifier}-room-{i}", traveler_ids=members) for i, members in enumerate(allocations, 1)]) for identifier, city, start, end in (("stay-istanbul", "Стамбул", "2026-11-06", "2026-11-09"), ("stay-antalya", "Анталья", "2026-11-09", "2026-11-13"))]
    return TripState(trip_id="demo-kazan-istanbul-antalya", origin="Казань", travelers=people, groups=[Group(id=identifier, traveler_ids=[person.id for person in people if person.group_id == identifier], keep_separate=True) for identifier in ("family-1", "family-2")], stays=stays, legs=[Leg(id="leg-out", origin="Казань", destination="Стамбул", departure_date="2026-11-06"), Leg(id="leg-between", origin="Стамбул", destination="Анталья", departure_date="2026-11-09"), Leg(id="leg-return", origin="Анталья", destination="Казань", departure_date="2026-11-13")], budget=Budget(amount_minor=80_000_000), constraints=[Constraint(key="breakfast", value=True)])
