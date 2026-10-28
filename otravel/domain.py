"""Canonical trip data and explicit, atomic edits. No network or persistence."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import date, timedelta
from typing import Any, Literal
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from .place_refs import MAX_LOCATION_REFS, CityRefSnapshot, location_label

# Proposed implementation guards, not approved product limits. Rooms are per stay.
MVP_LIMITS = {"travelers": 10, "stays": 4, "rooms_per_stay": 5}
MAX_REQUESTED_NIGHTS = 365  # Engineering bound, not an approved product maximum.
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
    keep_separate: bool = Field(default=False, strict=True)


class RoomAllocation(Model):
    id: str = Field(min_length=1)
    traveler_ids: list[str] = Field(default_factory=list)


class Stay(Model):
    id: str = Field(min_length=1)
    place_id: str | None = Field(default=None, min_length=1, max_length=128, exclude_if=lambda value: value is None)
    city: str | None = None
    check_in: date | None = None
    check_out: date | None = None
    rooms: list[RoomAllocation] = Field(default_factory=list)
    hotel_id: str | None = None
    requested_nights: int | None = Field(default=None, ge=1, le=MAX_REQUESTED_NIGHTS, strict=True,
        exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def derive_missing_checkout(self) -> Stay:
        if self.requested_nights is not None and self.check_in is not None and self.check_out is None:
            try:
                end = self.check_in + timedelta(days=self.requested_nights)
            except OverflowError:
                raise ValueError("Длительность выходит за пределы календаря; укажите другую дату или число ночей.") from None
            object.__setattr__(self, "check_out", end)
        return self

    @field_validator("city")
    @classmethod
    def normalize_city(cls, value: str | None, info: ValidationInfo) -> str | None:
        return location_label(value) if value is not None and info.data.get("place_id") else canonical_city(value) if value else None


class Leg(Model):
    id: str = Field(min_length=1)
    origin_place_id: str | None = Field(default=None, min_length=1, max_length=128, exclude_if=lambda value: value is None)
    destination_place_id: str | None = Field(default=None, min_length=1, max_length=128, exclude_if=lambda value: value is None)
    origin: str | None = None
    destination: str | None = None
    departure_date: date | None = None
    mode: Literal["flight", "train"] = "flight"

    @field_validator("origin", "destination")
    @classmethod
    def normalize_city(cls, value: str | None, info: ValidationInfo) -> str | None:
        return location_label(value) if value is not None and info.data.get(info.field_name + "_place_id") else canonical_city(value) if value else None


class Budget(Model):
    amount_minor: int = Field(gt=0, strict=True)
    currency: str = "RUB"
    scope: str = "whole_trip"


class Constraint(Model):
    key: str = Field(min_length=1)
    value: Any
    hard: bool = True
    stay_id: str | None = None
    room_id: str | None = None

    @model_validator(mode="after")
    def room_scope(self) -> Constraint:
        if self.key == "free_cancel_until_checkin" and type(self.value) is not bool:
            raise ValueError("free_cancel_until_checkin требует true/false; это требование, а не подтверждённый срок тарифа.")
        if self.key == "nonstop" and (type(self.value) is not bool or self.stay_id is not None or self.room_id is not None):
            raise ValueError("nonstop задаётся true/false для перелётов всей поездки, без привязки к отелю или номеру.")
        if self.room_id is not None and (not self.stay_id or self.key not in {"breakfast", "refundable", "free_cancel_until_checkin"}):
            raise ValueError("Условие конкретного номера требует stay_id и поддерживается только для breakfast/refundable/free_cancel_until_checkin.")
        return self


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


class RoutePolicy(Model):
    # The legacy default is retained for existing saved trips. A declared
    # one-way/undecided route must never inherit that implicit return edge.
    kind: Literal["round_trip", "one_way", "open_jaw", "undecided"] = "round_trip"
    end_place_id: str | None = Field(default=None, min_length=1, max_length=128, exclude_if=lambda value: value is None)
    end_city: str | None = None

    @field_validator("end_city")
    @classmethod
    def normalize_end_city(cls, value: str | None, info: ValidationInfo) -> str | None:
        return location_label(value) if value is not None and info.data.get("end_place_id") else canonical_city(value) if value is not None else None

    @model_validator(mode="after")
    def endpoint_scope(self) -> RoutePolicy:
        if self.kind != "open_jaw" and (self.end_city is not None or self.end_place_id is not None):
            raise ValueError("Отдельный конечный город задаётся только для open_jaw.")
        return self


class TripState(Model):
    trip_id: str = Field(default_factory=lambda: str(uuid4()))
    state_version: int = Field(default=0, ge=0, strict=True)
    origin_place_id: str | None = Field(default=None, min_length=1, max_length=128, exclude_if=lambda value: value is None)
    origin: str | None = None
    travelers: list[Traveler] = Field(default_factory=list)
    groups: list[Group] = Field(default_factory=list)
    legs: list[Leg] = Field(default_factory=list)
    stays: list[Stay] = Field(default_factory=list)
    budget: Budget | None = None
    constraints: list[Constraint] = Field(default_factory=list)
    preferences: list[Preference] = Field(default_factory=list)
    search_policy: SearchPolicy = Field(default_factory=SearchPolicy)
    route_policy: RoutePolicy = Field(default_factory=RoutePolicy)
    location_refs: dict[str, CityRefSnapshot] = Field(default_factory=dict, max_length=MAX_LOCATION_REFS,
        exclude_if=lambda value: not value)

    @field_validator("origin")
    @classmethod
    def normalize_city(cls, value: str | None, info: ValidationInfo) -> str | None:
        return location_label(value) if value is not None and info.data.get("origin_place_id") else canonical_city(value) if value else None


    @model_validator(mode="after")
    def reference_graph(self, info: ValidationInfo) -> TripState:
        physical_ids = set()
        for key, ref in self.location_refs.items():
            if key != ref.place_id:
                raise ValueError("Ключ location_refs не совпадает с place_id снимка.")
            physical = (ref.geographic_source.provider, ref.geographic_source.external_id)
            if physical in physical_ids:
                raise ValueError("Одна географическая идентичность записана под разными place_id.")
            physical_ids.add(physical)

        def pair(name, place_id):
            if place_id is not None:
                ref = self.location_refs.get(place_id)
                if ref is None:
                    raise ValueError("Место ссылается на неизвестный location_ref.")
                if name != ref.canonical_name:
                    raise ValueError("Название места не совпадает с выбранным location_ref.")

        pair(self.origin, self.origin_place_id)
        for stay in self.stays:
            pair(stay.city, stay.place_id)
        pair(self.route_policy.end_city, self.route_policy.end_place_id)
        for leg in self.legs:
            pair(leg.origin, leg.origin_place_id)
            pair(leg.destination, leg.destination_place_id)
        # During an atomic edit, derived legs still describe the previous
        # revision until synchronization. Only this path check is deferred.
        context = info.context if isinstance(info.context, dict) else {}
        has_refs = self.origin_place_id or self.route_policy.end_place_id or any(stay.place_id for stay in self.stays) or any(leg.origin_place_id or leg.destination_place_id for leg in self.legs)
        if has_refs and self.legs and not context.get("defer_derived_route"):
            expected = _route_layout_refs(self)
            if expected:
                actual = [(_place_key(leg.origin, leg.origin_place_id), _place_key(leg.destination, leg.destination_place_id)) for leg in self.legs]
                edges = [(_place_key(origin, origin_id), _place_key(destination, destination_id)) for origin, destination, _, origin_id, destination_id in expected]
                if actual != edges:
                    raise ValueError("Ссылки плеч маршрута не соответствуют порядку остановок.")
        return self


class Operation(Model):
    type: str
    target_id: str | None = None
    value: dict[str, Any] = Field(default_factory=dict)


def _issue(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "message": message, "details": details}


def _place_key(name: str | None, place_id: str | None) -> tuple[str, str | None]:
    return ("place", place_id) if place_id is not None else ("legacy", name)


def _route_layout_refs(state: TripState) -> list[tuple[str, str, date | None, str | None, str | None]]:
    if not state.origin or not state.stays or any(not stay.city for stay in state.stays):
        return []
    first = state.stays[0]
    edges = [(state.origin, first.city, first.check_in, state.origin_place_id, first.place_id)]
    edges += [(a.city, b.city, b.check_in, a.place_id, b.place_id) for a, b in zip(state.stays, state.stays[1:])]
    end = state.origin if state.route_policy.kind == "round_trip" else state.route_policy.end_city if state.route_policy.kind == "open_jaw" else None
    end_id = state.origin_place_id if state.route_policy.kind == "round_trip" else state.route_policy.end_place_id if state.route_policy.kind == "open_jaw" else None
    if end is not None:
        last = state.stays[-1]
        edges.append((last.city, end, last.check_out, last.place_id, end_id))
    return [edge for edge in edges if _place_key(edge[0], edge[3]) != _place_key(edge[1], edge[4])]


def _route_layout(state: TripState) -> list[tuple[str, str, date | None]]:
    return [(origin, destination, day) for origin, destination, day, _, _ in _route_layout_refs(state)]


def route_edges(state: TripState | dict) -> list[tuple[str, str, date]]:
    """Known transport edges from the declared route, never hotel/transit guesses.

    An unresolved terminal choice yields only the known prefix; validate_trip
    still blocks final search/quote until that choice is explicit.
    """
    state = TripState.model_validate(state)
    if any(not stay.check_in or not stay.check_out for stay in state.stays):
        return []
    return [(origin, destination, day) for origin, destination, day in _route_layout(state) if day is not None]


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
        elif constraint.room_id is not None:
            stay = next(stay for stay in state.stays if stay.id == constraint.stay_id)
            if constraint.room_id not in {room.id for room in stay.rooms}:
                issues.append(_issue("unknown_constraint_room", "Условие ссылается на неизвестный номер этой остановки.", stay_id=stay.id, room_id=constraint.room_id))
    for group in state.groups:
        if final and group.keep_separate and not group.traveler_ids:
            issues.append(_issue("missing_group_membership", "Какие участники относятся к группе с отдельным размещением?", group_id=group.id))
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
        if (stay.requested_nights is not None and stay.check_in and stay.check_out
                and stay.check_out > stay.check_in and (stay.check_out - stay.check_in).days != stay.requested_nights):
            actual_nights = (stay.check_out - stay.check_in).days
            issues.append(_issue("duration_mismatch",
                f"Указанные даты дают {actual_nights} ночей, а ранее выбрано {stay.requested_nights}. "
                "Сохранить выбранное число ночей и изменить выезд или оставить указанные даты?", stay_id=stay.id))
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
        if state.route_policy.kind == "undecided":
            issues.append(_issue("missing_route_end", "Нужна ли обратная дорога: в город отправления, в другой город или только в одну сторону?"))
        elif state.route_policy.kind == "open_jaw" and state.route_policy.end_city is None:
            issues.append(_issue("missing_route_end_city", "В каком городе должна закончиться поездка после последней остановки?"))
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
            expected = route_edges(state)
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
    state = TripState.model_validate(data, context={"defer_derived_route": True})
    old = data["legs"]
    complete = all(stay.city and stay.check_in and stay.check_out for stay in state.stays)
    edges = _route_layout_refs(state)
    used = set()
    rebuilt = []
    for index, (origin, destination, day, origin_id, destination_id) in enumerate(edges):
        previous = next((leg for leg in old if _place_key(leg["origin"], leg.get("origin_place_id")) == _place_key(origin, origin_id) and _place_key(leg["destination"], leg.get("destination_place_id")) == _place_key(destination, destination_id) and leg["id"] not in used), None)
        if previous is None and complete and len(old) == len(edges):
            previous = old[index]
        if not complete and previous is None:
            continue  # Partial edits may prune/update old legs, not invent dates/IDs.
        identifier = previous["id"] if previous and previous["id"] not in used else "leg-" + str(uuid4())
        used.add(identifier)
        rebuilt.append({"id": identifier, "origin": origin, "destination": destination,
                        "departure_date": day.isoformat() if day is not None else None,
                        "mode": previous["mode"] if previous else "flight",
                        **({"origin_place_id": origin_id} if origin_id is not None else {}),
                        **({"destination_place_id": destination_id} if destination_id is not None else {})})
    data["legs"] = rebuilt


def apply_operations(state: TripState, operations: list[Operation], *,
                     resolved_places: Mapping[str, CityRefSnapshot] | None = None,
                     replace_place_versions: Mapping[str, int] | None = None) -> tuple[TripState, list[dict[str, Any]]]:
    """Atomic edits on a copy; version belongs to the persistent store, not this function."""
    data = deepcopy(state.model_dump(mode="json"))
    changes: list[dict[str, Any]] = []
    if resolved_places is not None and not isinstance(resolved_places, Mapping):
        raise DomainError("invalid_place_refs", "Географические снимки передаются серверным словарём.")
    if replace_place_versions is not None and (not isinstance(replace_place_versions, Mapping)
            or any(not isinstance(key, str) or type(version) is not int or version <= 0
                for key, version in replace_place_versions.items())):
        raise DomainError("invalid_place_ref_replacement", "Обновление снимка требует точной предыдущей версии.")
    replacements = dict(replace_place_versions or {})
    replaced = set()
    if resolved_places:
        prior_refs = deepcopy(data.get("location_refs", {}))
        refs = deepcopy(prior_refs)
        for key, raw_ref in resolved_places.items():
            try:
                ref = CityRefSnapshot.model_validate(raw_ref)
            except ValidationError as exc:
                raise DomainError("invalid_place_ref", "Географический снимок не прошёл проверку.") from exc
            if key != ref.place_id:
                raise DomainError("place_ref_mismatch", "Ключ географического снимка не совпадает с place_id.")
            snapshot = ref.model_dump(mode="json")
            if key in refs and refs[key] != snapshot:
                old = CityRefSnapshot.model_validate(refs[key])
                if (replacements.get(key) != old.ref_version or ref.ref_version <= old.ref_version
                        or old.geographic_source != ref.geographic_source):
                    raise DomainError("place_ref_conflict", "Сохранённый снимок места нельзя неявно перезаписать.")
                replaced.add(key)
                # This opt-in is server-only, after an owned explicit revision
                # confirmation and fresh/current Catalog admission. Every use
                # of the same identity must keep its exact snapshot label.
                bound = []
                if data.get("origin_place_id") == key:
                    bound.append((data, "origin", "origin", None))
                policy = data.get("route_policy") or {}
                if policy.get("end_place_id") == key:
                    bound.append((policy, "end_city", "route_policy.end_city", None))
                for stay in data.get("stays", []):
                    if stay.get("place_id") == key:
                        bound.append((stay, "city", "stay.city", stay["id"]))
                        if stay.get("hotel_id") is not None:
                            changes.append({"operation": "refresh_place_reference", "target_id": stay["id"],
                                "field": "stay.hotel_id", "before": stay["hotel_id"], "after": None})
                            stay["hotel_id"] = None
                for leg in data.get("legs", []):
                    for direction in ("origin", "destination"):
                        if leg.get(direction + "_place_id") == key:
                            bound.append((leg, direction, "leg." + direction, leg["id"]))
                for container, field, label, target_id in bound:
                    if container.get(field) != ref.canonical_name:
                        changes.append({"operation": "refresh_place_reference", "target_id": target_id,
                            "field": label, "before": container.get(field), "after": ref.canonical_name})
                        container[field] = ref.canonical_name
            refs[key] = snapshot
        if len(refs) > MAX_LOCATION_REFS:
            raise DomainError("too_many_place_refs", "Превышен технический размер снимков географии поездки.")
        data["location_refs"] = refs
        if refs != prior_refs:
            changes.append({"operation": "resolve_places", "target_id": None, "field": "location_refs", "before": prior_refs, "after": deepcopy(refs)})
    if set(replacements) != replaced:
        raise DomainError("invalid_place_ref_replacement", "Подтверждение обновления не совпадает с изменённым снимком.")
    route_changed = bool(replaced)
    allowed = {"set_origin", "set_budget", "set_search_policy", "set_route_policy", "set_leg_mode", "set_room_members", "set_preference", "clear_preference", "add_traveler", "remove_traveler", "upsert_group", "upsert_stay", "remove_stay", "set_constraint", "clear_constraint", "shift_return", "replace_hotel", "update_traveler"}
    for raw_operation in operations:
        try:
            operation = raw_operation if isinstance(raw_operation, Operation) else Operation.model_validate(raw_operation)
            kind, value, target = operation.type, deepcopy(operation.value), operation.target_id
            if kind not in allowed:
                raise DomainError("unsupported_operation", f"Операция {kind} не поддерживается.")
            before = deepcopy(data)
            if kind == "set_origin":
                _keys(value, {"city", "origin", "place_id"})
                raw_city = value.get("city", value.get("origin"))
                place_id = value.get("place_id")
                data["origin"] = raw_city if place_id is not None else canonical_city(raw_city)
                if place_id is not None:
                    data["origin_place_id"] = place_id
                else:
                    data.pop("origin_place_id", None)
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
            elif kind == "clear_preference":
                _keys(value, {"key"})
                key = value.get("key")
                known = {"price_priority", "departure_time", *[item["key"] for item in data["preferences"]]}
                if target is not None or not isinstance(key, str) or key not in known:
                    raise DomainError("unknown_preference", "Уточните, какое сохранённое пожелание нужно снять.")
                data["preferences"] = [item for item in data["preferences"] if item["key"] != key]
            elif kind == "set_route_policy":
                _keys(value, {"kind", "end_city", "end_place_id"})
                if target is not None or "kind" not in value:
                    raise DomainError("invalid_route_policy", "Укажите тип маршрута без привязки к участнику или отелю.")
                data["route_policy"] = RoutePolicy.model_validate(value).model_dump(mode="json")
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
            elif kind == "upsert_group":
                _keys(value, {"traveler_ids", "keep_separate"})
                if not target or set(value) != {"traveler_ids", "keep_separate"}:
                    raise DomainError("missing_group_fields", "Укажите группу, полный состав и правило отдельного размещения.")
                group = Group.model_validate({"id": target, **value}).model_dump(mode="json")
                ids = group["traveler_ids"]
                if len(ids) != len(set(ids)) or any(identifier not in {p["id"] for p in data["travelers"]} for identifier in ids):
                    raise DomainError("invalid_group_membership", "В состав группы включаются известные участники без повторов.")
                for person in data["travelers"]:
                    if person["id"] in ids:
                        person["group_id"] = target
                    elif person["group_id"] == target:
                        person["group_id"] = None
                found = False
                for index, current in enumerate(data["groups"]):
                    if current["id"] == target:
                        data["groups"][index] = group
                        found = True
                    else:
                        current["traveler_ids"] = [identifier for identifier in current["traveler_ids"] if identifier not in ids]
                if not found:
                    data["groups"].append(group)
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
                _keys(value, {"id", "city", "place_id", "check_in", "check_out", "rooms", "hotel_id", "requested_nights"})
                if "place_id" in value and "city" not in value:
                    raise DomainError("place_pair_required", "Выбранное место и его точное название меняются вместе.")
                identifier = target or value.get("id")
                if not identifier:
                    raise DomainError("missing_target", "Для остановки нужен стабильный ID.")
                if value.get("id", identifier) != identifier:
                    raise DomainError("target_mismatch", "ID остановки не совпадает с target_id.")
                existing = next((item for item in data["stays"] if item["id"] == identifier), None)
                if existing is None:
                    data["stays"].append({"id": identifier, **value})
                else:
                    if "city" in value and "place_id" not in value:
                        existing.pop("place_id", None)
                    existing.update(value)
            elif kind == "set_room_members":
                _keys(value, {"stay_id", "traveler_ids"})
                stay_id = value.get("stay_id")
                if not isinstance(stay_id, str) or not stay_id:
                    raise DomainError("missing_target", "Для изменения состава номера укажите точный stay_id проживания.")
                if "traveler_ids" not in value:
                    raise DomainError("missing_room_members", "Укажите полный состав выбранного номера в traveler_ids.")
                stay = _target(data["stays"], stay_id, "проживание")
                room = _target(stay["rooms"], target, "номер выбранного проживания")
                members = RoomAllocation.model_validate({"id": room["id"], "traveler_ids": value["traveler_ids"]})
                room["traveler_ids"] = list(members.traveler_ids)
            elif kind == "set_leg_mode":
                _keys(value, {"mode"})
                if not isinstance(value.get("mode"), str) or value["mode"] not in {"flight", "train"}:
                    raise DomainError("invalid_leg_mode", "Для существующего плеча выберите самолёт или поезд.")
                _target(data["legs"], target, "плечо маршрута")["mode"] = value["mode"]
            elif kind == "remove_stay":
                _keys(value, set())
                stay = _target(data["stays"], target, "остановка")
                data["stays"].remove(stay)
                data["constraints"] = [item for item in data["constraints"] if item.get("stay_id") != target]
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
                if data["route_policy"]["kind"] in {"one_way", "undecided"}:
                    raise DomainError("missing_return_leg", "Обратный перелёт не запланирован. Уточните, меняете дату выезда из отеля или добавляете обратную дорогу.")
                if not data["stays"] or not data["stays"][-1].get("check_out"):
                    raise DomainError("missing_return_date", "Сначала укажите дату возвращения.")
                last = data["stays"][-1]
                last["check_out"] = (date.fromisoformat(last["check_out"]) + timedelta(days=days)).isoformat()
                if last.get("requested_nights") is not None and last.get("check_in"):
                    last["requested_nights"] = (date.fromisoformat(last["check_out"]) - date.fromisoformat(last["check_in"])).days
                if data["legs"]:
                    data["legs"][-1]["departure_date"] = last["check_out"]
            elif kind == "set_constraint":
                _keys(value, {"key", "value", "hard", "stay_id", "room_id"})
                constraint = Constraint.model_validate(value).model_dump(mode="json")
                data["constraints"] = [item for item in data["constraints"] if (item["key"], item.get("stay_id"), item.get("room_id")) != (constraint["key"], constraint["stay_id"], constraint["room_id"])]
                data["constraints"].append(constraint)
            elif kind == "clear_constraint":
                _keys(value, {"key", "stay_id", "room_id"})
                key = value.get("key") or target
                if not key:
                    raise DomainError("missing_constraint", "Укажите key удаляемого условия.")
                # Validate the scope even for an idempotent absent-key clear.
                Constraint.model_validate({**value, "key": key, "value": False if key in {"nonstop", "free_cancel_until_checkin"} else None})
                data["constraints"] = [item for item in data["constraints"] if (item["key"], item.get("stay_id"), item.get("room_id")) != (key, value.get("stay_id"), value.get("room_id"))]
            candidate = TripState.model_validate(data, context={"defer_derived_route": True})
            data = candidate.model_dump(mode="json")
            route_changed |= kind in {"set_origin", "set_route_policy", "upsert_stay", "remove_stay", "shift_return"}
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
    try:
        result = TripState.model_validate(data)
    except ValidationError as exc:
        raise DomainError("invalid_operation", "Географические ссылки поездки не прошли проверку.",
            exc.errors(include_url=False, include_context=False)) from exc
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
