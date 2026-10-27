"""Per-trip extraction schema. No model calls, execution, or shared configuration.

The schema constrains the *shape* of proposed edits. Domain validation, reference
resolution, atomic conflict checks, dates and user consent remain server duties.
New IDs are deterministic aliases for this snapshot, not invented identities.
"""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from .domain import CITIES, MVP_LIMITS, TripState
from .geography import known_cities


DATE_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
CONTROL_ACTIONS = ("continue", "wait", "resume", "cancel", "request_checkout", "confirm", "none")
OPERATION_TYPES = frozenset({
    "set_origin", "set_budget", "set_search_policy", "add_traveler",
    "update_traveler", "remove_traveler", "upsert_stay", "remove_stay",
    "replace_hotel", "shift_return", "set_constraint", "clear_constraint", "undo", "set_preference", "clear_preference", "set_route_policy", "set_leg_mode", "set_room_members", "upsert_group",
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


def _local_definitions(schema: dict) -> dict:
    """Intern identical schema nodes only, using deterministic local references.

    Property maps and literal values are not schemas and must not be replaced.
    Small or single-use definitions are inlined again when a parent definition
    has removed their original repetition. No shared/global state or external
    resolver is involved.
    """
    def encoded(value: dict) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def children(node: dict):
        for child in node.get("properties", {}).values():
            yield child
        if isinstance(node.get("items"), dict):
            yield node["items"]
        for keyword in ("oneOf", "anyOf", "allOf"):
            yield from node.get(keyword, [])

    counts: dict[str, int] = {}

    def collect(node: dict) -> None:
        key = encoded(node)
        counts[key] = counts.get(key, 0) + 1
        for child in children(node):
            collect(child)

    collect(schema)
    names = {}
    for key, count in counts.items():
        size = len(key.encode("utf-8"))
        name = "s" + str(len(names))
        reference_size = len(encoded({"$ref": "#/$defs/" + name}))
        if (count - 1) * size > count * reference_size + len(name) + 4:
            names[key] = name
    definitions: dict[str, dict] = {}

    def rewrite(node: dict, *, define=False) -> dict:
        key = encoded(node)
        if not define and key in names:
            name = names[key]
            if name not in definitions:
                definitions[name] = rewrite(node, define=True)
            return {"$ref": "#/$defs/" + name}
        result = deepcopy(node)
        if "properties" in result:
            result["properties"] = {key: rewrite(child) for key, child in node["properties"].items()}
        if isinstance(result.get("items"), dict):
            result["items"] = rewrite(node["items"])
        for keyword in ("oneOf", "anyOf", "allOf"):
            if keyword in result:
                result[keyword] = [rewrite(child) for child in node[keyword]]
        return result

    result = rewrite(schema)

    def refs(value: Any, counts: dict[str, int]) -> None:
        if isinstance(value, dict):
            if set(value) == {"$ref"}:
                name = value["$ref"].removeprefix("#/$defs/")
                counts[name] = counts.get(name, 0) + 1
            for child in value.values():
                refs(child, counts)
        elif isinstance(value, list):
            for child in value:
                refs(child, counts)

    def inline(value: Any, name: str, replacement: dict) -> Any:
        if isinstance(value, dict):
            if value == {"$ref": "#/$defs/" + name}:
                return deepcopy(replacement)
            return {key: inline(child, name, replacement) for key, child in value.items()}
        if isinstance(value, list):
            return [inline(child, name, replacement) for child in value]
        return value

    while definitions:
        uses: dict[str, int] = {}
        refs(result, uses)
        refs(definitions, uses)
        unprofitable = next((name for name, node in definitions.items()
            if (uses.get(name, 0) - 1) * len(encoded(node).encode("utf-8")) <=
               uses.get(name, 0) * len(encoded({"$ref": "#/$defs/" + name})) + len(name) + 4), None)
        if unprofitable is None:
            break
        replacement = definitions.pop(unprofitable)
        result = inline(result, unprofitable, replacement)
        definitions = inline(definitions, unprofitable, replacement)
    if definitions:
        result["$defs"] = definitions
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
    leg_ids = [leg.id for leg in state.legs]
    new_people = _candidates("traveler", set(traveler_ids), max(0, MVP_LIMITS["travelers"] - len(traveler_ids)))
    new_stays = _candidates("stay", set(stay_ids), max(0, MVP_LIMITS["stays"] - len(stay_ids)))
    new_groups = _candidates("group", set(group_ids), max(1, len(new_people)))
    all_people, all_stays = traveler_ids + new_people, stay_ids + new_stays
    all_groups = group_ids + new_groups
    # A room ID is local to its stay. Existing IDs are retained; new aliases can
    # be reused across different stays, exactly as RoomAllocation permits.
    room_ids = list(dict.fromkeys(room.id for stay in state.stays for room in stay.rooms))
    room_ids += _candidates("room", set(room_ids), MVP_LIMITS["rooms_per_stay"])
    date = _nullable(_text(pattern=DATE_PATTERN))
    city = _enum(known_cities())
    group = _nullable(_enum(all_groups)) if all_groups else {"type": "null"}
    stay_reference = _nullable(_enum(all_stays)) if all_stays else {"type": "null"}
    variants: list[dict] = []

    def operation(kind: str, value: dict, targets: list[str] | None = None) -> None:
        properties = {"type": {"const": kind}, "value": value,
                      "target_id": _enum(targets) if targets is not None else {"type": "null"}}
        variant = {"properties": properties}
        if targets is not None:
            variant["required"] = ["target_id"]
        variants.append(variant)

    operation("set_origin", _object({"city": city}, ("city",)))
    operation("set_budget", _object({"amount_minor": {"type": "integer", "minimum": 1},
        "currency": _text(pattern=r"^[A-Z]{3}$"), "scope": {"type": "string", "const": "whole_trip"}}, ("amount_minor",)))
    operation("set_preference", _object({"key": {}, "value": {},
        "weight": {"type": "number", "minimum": 0, "maximum": 1}}, ("key", "value"),
        oneOf=[{"properties": {"key": {"const": key}, "value": {"const": value}}}
            for key, value in (("price_priority", "lowest_total"), ("departure_time", "early_morning"))]))
    clear_keys = list(dict.fromkeys(["price_priority", "departure_time", *[p.key for p in state.preferences]]))
    operation("clear_preference", _object({"key": _enum(clear_keys)}, ("key",)))
    operation("set_route_policy", _object({"kind": _enum(("round_trip", "one_way", "open_jaw", "undecided")),
        "end_city": _nullable(city)}, ("kind",), oneOf=[
            {"properties": {"kind": {"const": "open_jaw"}}},
            {"properties": {"kind": _enum(("round_trip", "one_way", "undecided")), "end_city": {"type": "null"}}}]))
    if leg_ids:
        operation("set_leg_mode", _object({"mode": _enum(("flight", "train"))}, ("mode",)), leg_ids)
    operation("set_search_policy", _object({
        "mode": _enum(("DEMO", "HYBRID")), "hotel_provider": _enum(("liteapi", "serpapi")),
        "flight_provider": _enum(("duffel", "serpapi")), "demo_fallback": {"type": "boolean"},
        "guest_nationality": _nullable(_text(pattern=r"^[A-Z]{2}$")),
    }, minProperties=1))

    if new_people:
        people = []
        for kind, lower, upper in (("adult", 18, 120), ("child", 0, 17)):
            people.append({"properties": {"type": {"const": kind},
                "age": _nullable({"type": "integer", "minimum": lower, "maximum": upper})}})
        operation("add_traveler", _object({"id": _enum(new_people), "type": {}, "age": {},
            "birth_date": date, "group_id": group}, ("id", "type"), oneOf=people))
    if traveler_ids:
        operation("update_traveler", _object({"type": _enum(("adult", "child")),
            "age": _nullable({"type": "integer", "minimum": 0, "maximum": 120}),
            "birth_date": date, "group_id": group}, minProperties=1), traveler_ids)
        operation("remove_traveler", _object({}), traveler_ids)

    members = {"type": "array", "items": _enum(all_people), "uniqueItems": True,
               "maxItems": MVP_LIMITS["travelers"]} if all_people else {"type": "array", "maxItems": 0}
    operation("upsert_group", _object({"traveler_ids": members, "keep_separate": {"type": "boolean"}},
        ("traveler_ids", "keep_separate")), all_groups)

    if all_stays:
        # Arrays refer only to existing or server-issued same-batch aliases.
        room = _object({"id": _enum(room_ids), "traveler_ids": members}, ("id", "traveler_ids"))
        operation("upsert_stay", _object({"city": _nullable(city), "check_in": date, "check_out": date,
            "requested_nights": _nullable({"type": "integer", "minimum": 1, "maximum": 365}),
            "rooms": {"type": "array", "items": room, "maxItems": MVP_LIMITS["rooms_per_stay"],
                "description": "Полная замена раскладки номеров проживания. Все сохраняемые номера должны присутствовать; для изменения состава одного существующего номера используйте set_room_members."},
            "hotel_id": _nullable(_text())}, minProperties=1), all_stays)
    for stay in state.stays:
        if stay.rooms:
            operation("set_room_members", _object({
                "stay_id": {"type": "string", "const": stay.id},
                "traveler_ids": members,
            }, ("stay_id", "traveler_ids")), [room.id for room in stay.rooms])
    if stay_ids:
        operation("remove_stay", _object({}), stay_ids)
        operation("replace_hotel", _object({"hotel_id": _text()}, ("hotel_id",)), stay_ids)
    operation("shift_return", _object({"days": {"type": "integer"}}, ("days",)))

    # Existing unsupported conditions can still be edited as typed scalar values.
    # A new unsupported key belongs in unknowns, not an invented operation. No
    # arbitrary object is permitted: the only structured value is a known pet.
    existing_unknown_keys = [c.key for c in state.constraints if c.key not in {"breakfast", "refundable", "pets_allowed", "pets", "nonstop"}]
    condition_values = [{"properties": {"key": _enum(("breakfast", "refundable", "pets_allowed", "nonstop")),
        "value": {"type": "boolean"}}},
        {"properties": {"key": {"const": "pets"},
            "value": _object({"kind": _enum(("dog", "cat")),
                "weight_kg": {"type": "number", "exclusiveMinimum": 0}}, ("kind",))}}]
    if existing_unknown_keys:
        condition_values.append({"properties": {"key": _enum(list(dict.fromkeys(existing_unknown_keys))),
            "value": {"type": ["boolean", "string", "number", "null"]}}})
    room_scope = {"if": {"required": ["room_id"], "properties": {"room_id": {"type": "string"}}},
        "then": {"required": ["stay_id"], "properties": {"key": _enum(("breakfast", "refundable")),
            "stay_id": _enum(all_stays)}}}
    flight_scope = {"if": {"required": ["key"], "properties": {"key": {"const": "nonstop"}}},
        "then": {"properties": {"stay_id": {"type": "null"}, "room_id": {"type": "null"}}}}
    operation("set_constraint", _object({"key": {}, "value": {}, "hard": {"type": "boolean"},
        "stay_id": stay_reference, "room_id": _nullable(_enum(room_ids))}, ("key", "value"),
        oneOf=condition_values, allOf=[room_scope, flight_scope]))
    operation("clear_constraint", _object({"key": _text(), "stay_id": stay_reference,
        "room_id": _nullable(_enum(room_ids))}, ("key",), allOf=[room_scope, flight_scope]))
    operation("undo", _object({}))

    schema = _object({
        "operations": {"type": "array", "maxItems": 30,
            "items": _object({"type": {}, "value": {}, "target_id": {}}, ("type", "value"), oneOf=variants)},
        "control": _object({"action": _enum(CONTROL_ACTIONS)}, ("action",)),
        "reply": {"type": "string"},
        "unknowns": {"type": "array", "items": {"type": "string"}},
        "conflicts": {"type": "array", "items": {"type": "string"}},
        "speech_act": _enum(("CHANGE", "ASK", "PROPOSE")),
        "information_subject": _nullable(_text(maxLength=80)),
    }, ("operations", "control", "reply", "unknowns", "conflicts"))
    # Every invocation owns its nested containers; callers may attach the schema
    # to one request context without mutating settings or another session.
    return _local_definitions(schema)


# Compatibility with the task's spelling; Python callers normally use snake_case.
Build_operation_schema = build_operation_schema
