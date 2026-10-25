"""Per-trip extraction schema. No model calls, execution, or shared configuration.

The schema constrains the *shape* of proposed edits. Domain validation, reference
resolution, atomic conflict checks, dates and user consent remain server duties.
New IDs are deterministic aliases for this snapshot, not invented identities.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .domain import CITIES, MVP_LIMITS, TripState


DATE_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
CONTROL_ACTIONS = ("continue", "wait", "resume", "cancel", "request_checkout", "confirm", "none")
OPERATION_TYPES = frozenset({
    "set_origin", "set_budget", "set_search_policy", "add_traveler",
    "update_traveler", "remove_traveler", "upsert_stay", "remove_stay",
    "replace_hotel", "shift_return", "set_constraint", "clear_constraint", "undo",
})


def _object(properties: dict, required: tuple[str, ...] = (), **extra: Any) -> dict:
    return {"type": "object", "properties": properties, "required": list(required),
            "additionalProperties": False, **extra}


def _text(**extra: Any) -> dict:
    return {"type": "string", "minLength": 1, **extra}


def _enum(values: list[str] | tuple[str, ...]) -> dict:
    return {"type": "string", "enum": list(values)}


def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


def _candidates(prefix: str, existing: set[str], count: int) -> list[str]:
    result: list[str] = []
    index = 1
    while len(result) < count:
        identifier = f"new-{prefix}-{index}"
        if identifier not in existing:
            result.append(identifier)
        index += 1
    return result


def build_operation_schema(state: TripState | dict[str, Any]) -> dict[str, Any]:
    """Build the complete five-field response envelope for one immutable snapshot.

    Existing-person/stay edits require an existing stable ID. Creation aliases
    can be referenced by rooms/constraints in the same ordered atomic batch;
    they must first be created. No alias reserves capacity or authorizes a call.
    Empty target sets remove impossible edit variants rather than guessing IDs.
    Bounds mirror current implementation guards, not owner-approved product limits.
    """
    state = state if isinstance(state, TripState) else TripState.model_validate(state)
    traveler_ids = [person.id for person in state.travelers]
    stay_ids = [stay.id for stay in state.stays]
    group_ids = [group.id for group in state.groups]
    new_people = _candidates("traveler", set(traveler_ids), max(0, MVP_LIMITS["travelers"] - len(traveler_ids)))
    new_stays = _candidates("stay", set(stay_ids), max(0, MVP_LIMITS["stays"] - len(stay_ids)))
    new_groups = _candidates("group", set(group_ids), len(new_people))
    all_people, all_stays = traveler_ids + new_people, stay_ids + new_stays
    all_groups = group_ids + new_groups
    # A room ID is local to its stay. Existing IDs are retained; new aliases can
    # be reused across different stays, exactly as RoomAllocation permits.
    room_ids = list(dict.fromkeys(room.id for stay in state.stays for room in stay.rooms))
    room_ids += _candidates("room", set(room_ids), MVP_LIMITS["rooms_per_stay"])
    date = _nullable(_text(pattern=DATE_PATTERN))
    city = _enum(CITIES)
    group = _nullable(_enum(all_groups)) if all_groups else {"type": "null"}
    stay_reference = _nullable(_enum(all_stays)) if all_stays else {"type": "null"}
    variants: list[dict] = []

    def operation(kind: str, value: dict, targets: list[str] | None = None) -> None:
        properties = {"type": {"type": "string", "const": kind}, "value": value,
                      "target_id": _enum(targets) if targets is not None else {"type": "null"}}
        required = ("type", "value", "target_id") if targets is not None else ("type", "value")
        variants.append(_object(properties, required))

    operation("set_origin", _object({"city": city}, ("city",)))
    operation("set_budget", _object({"amount_minor": {"type": "integer", "minimum": 1},
        "currency": _text(pattern=r"^[A-Z]{3}$"), "scope": {"type": "string", "const": "whole_trip"}}, ("amount_minor",)))
    operation("set_search_policy", _object({
        "mode": _enum(("DEMO", "HYBRID")), "hotel_provider": _enum(("liteapi", "serpapi")),
        "flight_provider": _enum(("duffel", "serpapi")), "demo_fallback": {"type": "boolean"},
        "guest_nationality": _nullable(_text(pattern=r"^[A-Z]{2}$")),
    }, minProperties=1))

    if new_people:
        people = []
        for kind, lower, upper in (("adult", 18, 120), ("child", 0, 17)):
            people.append(_object({"id": _enum(new_people), "type": {"type": "string", "const": kind},
                "age": _nullable({"type": "integer", "minimum": lower, "maximum": upper}),
                "birth_date": date, "group_id": group}, ("id", "type")))
        operation("add_traveler", {"oneOf": people})
    if traveler_ids:
        operation("update_traveler", _object({"type": _enum(("adult", "child")),
            "age": _nullable({"type": "integer", "minimum": 0, "maximum": 120}),
            "birth_date": date, "group_id": group}, minProperties=1), traveler_ids)
        operation("remove_traveler", _object({}), traveler_ids)

    if all_stays:
        # Arrays refer only to existing or server-issued same-batch aliases.
        members = {"type": "array", "items": _enum(all_people), "uniqueItems": True,
                   "maxItems": MVP_LIMITS["travelers"]} if all_people else {"type": "array", "maxItems": 0}
        room = _object({"id": _enum(room_ids), "traveler_ids": members}, ("id", "traveler_ids"))
        operation("upsert_stay", _object({"city": _nullable(city), "check_in": date, "check_out": date,
            "rooms": {"type": "array", "items": room, "maxItems": MVP_LIMITS["rooms_per_stay"]},
            "hotel_id": _nullable(_text())}, minProperties=1), all_stays)
    if stay_ids:
        operation("remove_stay", _object({}), stay_ids)
        operation("replace_hotel", _object({"hotel_id": _text()}, ("hotel_id",)), stay_ids)
    operation("shift_return", _object({"days": {"type": "integer"}}, ("days",)))

    # Existing unsupported conditions can still be edited as typed scalar values.
    # A new unsupported key belongs in unknowns, not an invented operation. No
    # arbitrary object is permitted: the only structured value is a known pet.
    existing_unknown_keys = [c.key for c in state.constraints if c.key not in {"breakfast", "refundable", "pets_allowed", "pets"}]
    condition_values = [_object({"key": _enum(("breakfast", "refundable", "pets_allowed")),
        "value": {"type": "boolean"}, "hard": {"type": "boolean"}, "stay_id": stay_reference}, ("key", "value")),
        _object({"key": {"type": "string", "const": "pets"},
            "value": _object({"kind": _enum(("dog", "cat")),
                "weight_kg": {"type": "number", "exclusiveMinimum": 0}}, ("kind",)),
            "hard": {"type": "boolean"}, "stay_id": stay_reference}, ("key", "value"))]
    if existing_unknown_keys:
        condition_values.append(_object({"key": _enum(list(dict.fromkeys(existing_unknown_keys))),
            "value": {"type": ["boolean", "string", "number", "null"]},
            "hard": {"type": "boolean"}, "stay_id": stay_reference}, ("key", "value")))
    operation("set_constraint", {"oneOf": condition_values})
    operation("clear_constraint", _object({"key": _text(), "stay_id": stay_reference}, ("key",)))
    operation("undo", _object({}))

    schema = _object({
        "operations": {"type": "array", "maxItems": 30, "items": {"oneOf": variants}},
        "control": _object({"action": _enum(CONTROL_ACTIONS)}, ("action",)),
        "reply": {"type": "string"},
        "unknowns": {"type": "array", "items": {"type": "string"}},
        "conflicts": {"type": "array", "items": {"type": "string"}},
    }, ("operations", "control", "reply", "unknowns", "conflicts"))
    # Every invocation owns its nested containers; callers may attach the schema
    # to one request context without mutating settings or another session.
    return deepcopy(schema)


# Compatibility with the task's spelling; Python callers normally use snake_case.
Build_operation_schema = build_operation_schema
