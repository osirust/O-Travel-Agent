"""Compact model guidance derived from a checked canonical operation schema.

This is deliberately not an equivalent JSON Schema or a business validator.
The strict wire grammar constrains actions and targets; the original complete
dynamic schema must still validate every decoded proposal on the server.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .groq_transport import GroqTransportError, _compile

_VERSION = "groq_operation_value_hints_v1"
_CITY_ENUM_SHA256 = "7d429976f57f4837e90ff5ef10df22bf5adfd3f35597ca5107d338bd5bfb0f48"
_EXPECTED_SHAPES: dict[str, set[str]] = {
    "set_origin": {"ff5f3d11f1c7f7122de0a90f11c6a9648c7ae212ff135bf8a92b701bc68d02c4"},
    "set_budget": {"902e2921b5c2a510193ab03af840a462d64a65d661e04006518d4e7ecbc994d9"},
    "set_preference": {"053408984a8d4c80d10067776b6d76ac6468246a0a4205131ddfb9bde800281c"},
    "clear_preference": {"d06cee202675cd5ead4653512a20106c5ee6393f9297ace85e4ce8c55c6d86f9"},
    "set_route_policy": {"a120b89ed5933e179da986ed0683667aa16b2d96516a50d120818d9842c0b233"},
    "set_search_policy": {"88d5139b7e2568397095ef49992a1d339c8f90ce056d964eabc92b59416b540d"},
    "add_traveler": {"a0c8e2838919934f94540698c972b12faa0b7682caabea0cfb096e70b88b9d8c"},
    "upsert_group": {"d68814e9e1f1e6055d1e41ee7e52aa15a389872296badbe43757f9335158f02a"},
    "upsert_stay": {"6ada9f2b7d6bd104d92fe8d3b161e92cd3cb7c9e39aeecc6d03debee8b972239"},
    "shift_return": {"d399253b15ff119a1612b2329ee83a3b8f76714c9d576b08a13a0df98d82f78b"},
    "set_constraint": {"e8b544e9388d081a7728de22269ac1199a0fd15e0e8ffdcdc3eb7906d454c565",
        "a88919d6fe96f6150001137cb3d18f351f6694b86a9ba30f2bc68d152c30ef6d"},
    "clear_constraint": {"52c9944c93c6f3afd75babc225041abfa01b48801a52332fe5dc79dc1ef8ac50"},
    "undo": {"d746974fa9afd5e951f76f9af38954b0ad7f436f2120dc974da65e5ee39f856f"},
    "set_leg_mode": {"069644c9fc065c9e1cc9b46397cd9ca8e30065abf0692d90e3017ad96943d582"},
    "update_traveler": {"ef7b266a4b0ff409a7a195d8b26da5560314e6017d4386dad24311235087f33f"},
    "remove_traveler": {"d746974fa9afd5e951f76f9af38954b0ad7f436f2120dc974da65e5ee39f856f"},
    "set_room_members": {"ed788acf4cf27c0b689373a90ff5d7276cba7e9aa4e66a3886380644b4e9b401"},
    "remove_stay": {"d746974fa9afd5e951f76f9af38954b0ad7f436f2120dc974da65e5ee39f856f"},
    "replace_hotel": {"4c39af5f39ed87937d909e09be67926787494e90e83974241b2785a83c3a6cf9"},
}
_SIGNATURES = {
    "set_origin": "city!:known-city",
    "set_budget": "amount_minor!:int>=1,currency:ISO3,scope:whole_trip",
    "set_preference": "key!+value!:price_priority+lowest_total OR departure_time+early_morning;weight:number[0,1]",
    "clear_preference": "key!:$preference_keys",
    "set_route_policy": "kind!:round_trip|one_way|open_jaw|undecided,end_city:open_jaw->known-city|null;other kinds->null",
    "set_leg_mode": "mode!:flight|train",
    "set_search_policy": "mode:DEMO|HYBRID,hotel_provider:liteapi|serpapi,flight_provider:duffel|serpapi,demo_fallback:bool,guest_nationality:ISO2|null; >=1 field",
    "add_traveler": "id!:$new_people,type!:adult|child,age:int|null(adult18..120;child0..17),birth_date:date|null,group_id:$groups|null",
    "update_traveler": "type:adult|child,age:int[0,120]|null,birth_date:date|null,group_id:$groups|null; >=1 field",
    "remove_traveler": "{}",
    "upsert_group": "traveler_ids!:[$people],keep_separate!:bool",
    "upsert_stay": "city:known-city|null,check_in:date|null,check_out:date|null,requested_nights:int[1,365]|null,rooms:[{id!:$rooms,traveler_ids!:[$people]}],hotel_id:nonempty-text|null; >=1 field",
    "set_room_members": "stay_id!:$room_stay binding,traveler_ids!:[$people]",
    "remove_stay": "{}",
    "replace_hotel": "hotel_id!:nonempty-text",
    "shift_return": "days!:int",
    "set_constraint": "key!+value!:breakfast|refundable|pets_allowed|nonstop+bool OR pets+{kind!:dog|cat,weight_kg:number>0} OR $scalar_keys+bool|string|number|null;hard:bool,stay_id:$stays|null,room_id:$rooms|null",
    "clear_constraint": "key!:nonempty-text,stay_id:$stays|null,room_id:$rooms|null",
    "undo": "{}",
}


def _fail(code: str) -> None:
    raise GroqTransportError(code, "compact_operation_hints")


def _encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
        separators=(",", ":")).encode("utf-8")


def _enum(node: dict, resolve) -> list[str]:
    node = resolve(node)
    if "anyOf" in node:
        branches = [resolve(branch) for branch in node["anyOf"]]
        strings = [branch for branch in branches if branch.get("type") != "null"]
        if len(strings) != 1:
            _fail("unsupported_hint_enum")
        return _enum(strings[0], resolve)
    values = node.get("enum")
    if values is None and "const" in node:
        values = [node["const"]]
    if (type(values) is not list or not 1 <= len(values) <= 256
            or any(type(value) is not str or not 0 < len(value) <= 200 for value in values)
            or len(values) != len(set(values))):
        _fail("unsupported_hint_enum")
    return values[:]


def _shape(kind: str, node: dict, resolve, path=(), scalar_keys=False):
    """Normalize only documented snapshot-specific enums, preserving grammar."""
    node = resolve(node)
    props = node.get("properties", {})
    scalar_keys = scalar_keys or ("key" in props and "value" in props
        and resolve(props["value"]).get("type") == ["boolean", "string", "number", "null"])
    result = {}
    for key, value in node.items():
        if key in {"description", "title", "$comment"}:
            continue
        if key == "properties":
            result[key] = {name: _shape(kind, child, resolve, (*path, name), scalar_keys)
                for name, child in value.items()}
        elif key == "items":
            result[key] = _shape(kind, value, resolve, (*path, "[]"), scalar_keys)
        elif key in {"oneOf", "anyOf", "allOf"}:
            result[key] = [_shape(kind, child, resolve, path, scalar_keys) for child in value]
        elif key in {"if", "then", "else", "not"}:
            result[key] = _shape(kind, value, resolve, path, scalar_keys)
        elif key == "enum" and path:
            field = path[-1]
            dynamic = (field in {"city", "end_city", "group_id", "stay_id", "room_id"}
                or field == "id" and (kind == "add_traveler" or "rooms" in path)
                or path[-2:] == ("traveler_ids", "[]")
                or field == "key" and (kind == "clear_preference" or scalar_keys))
            result[key] = ["<snapshot-enum>"] if dynamic else value
        elif key == "const" and kind == "set_room_members" and path == ("stay_id",):
            result[key] = "<owned-stay>"
        else:
            result[key] = value
    return result


def _check_registry_refs(kind: str, node: dict, resolve, registries: dict, path=()) -> None:
    """Dynamic enum projection may not silently widen or omit references."""
    node = resolve(node)
    if "enum" in node and path:
        expected = None
        if path[-1] in {"city", "end_city"}:
            # Names are not repeated in compact hints. A changed city universe
            # nevertheless requires explicit review, never silent projection.
            digest = hashlib.sha256(_encoded(sorted(_enum(node, resolve)))).hexdigest()
            if digest != _CITY_ENUM_SHA256:
                _fail("unreviewed_hint_city_enum")
        elif path[-2:] == ("traveler_ids", "[]"):
            expected = registries["people"]
        elif path[-1] == "group_id":
            expected = registries["groups"]
        elif path[-1] == "stay_id":
            expected = registries["stays"]
        elif path[-1] == "room_id" or path[-1] == "id" and "rooms" in path:
            expected = registries["rooms"]
        if expected is not None and set(_enum(node, resolve)) != set(expected):
            _fail("inconsistent_hint_reference_registry")
    for key, child in node.get("properties", {}).items():
        _check_registry_refs(kind, child, resolve, registries, (*path, key))
    if "items" in node:
        _check_registry_refs(kind, node["items"], resolve, registries, (*path, "[]"))
    for keyword in ("oneOf", "anyOf", "allOf"):
        for child in node.get(keyword, []):
            _check_registry_refs(kind, child, resolve, registries, path)
    for keyword in ("if", "then", "else", "not"):
        if keyword in node:
            _check_registry_refs(kind, node[keyword], resolve, registries, path)


def build_compact_operation_hints(original_dynamic_schema: dict) -> tuple[str, dict[str, Any]]:
    """Return bounded guidance, rejecting unreviewed value grammar changes.

    No fields, IDs or defaults are generated. ``?`` is not used: a suffix ``!``
    marks required value keys and all other listed keys are optional. Explicit
    null is documented separately from an omitted property.
    """
    try:
        original, _, resolve = _compile(original_dynamic_schema)
        items = resolve(resolve(original["properties"]["operations"])["items"])
        variants = []
        for variant in items["oneOf"]:
            props = resolve(variant)["properties"]
            kind = _enum(props["type"], resolve)[0]
            value = resolve(props["value"])
            digest = hashlib.sha256(_encoded(_shape(kind, value, resolve))).hexdigest()
            if digest not in _EXPECTED_SHAPES.get(kind, set()):
                _fail("unreviewed_operation_value_schema")
            variants.append((kind, value, _enum(props["target_id"], resolve)
                if resolve(props["target_id"]).get("type") != "null" else []))
        by_kind: dict[str, list] = {}
        for variant in variants:
            by_kind.setdefault(variant[0], []).append(variant)
        if any(kind != "set_room_members" and len(choices) != 1 for kind, choices in by_kind.items()):
            _fail("unreviewed_multi_variant_operation")
        registries: dict[str, Any] = {}
        if "add_traveler" in by_kind:
            registries["new_people"] = _enum(by_kind["add_traveler"][0][1]["properties"]["id"], resolve)
        else:
            registries["new_people"] = []
        group_value = by_kind["upsert_group"][0][1]
        members = resolve(group_value["properties"]["traveler_ids"])
        registries["people"] = _enum(members["items"], resolve) if "items" in members else []
        registries["groups"] = by_kind["upsert_group"][0][2]
        owned_people = by_kind.get("update_traveler", by_kind.get("remove_traveler", [(None, None, [])]))[0][2]
        if set(registries["people"]) != set(owned_people) | set(registries["new_people"]):
            _fail("inconsistent_people_alias_registry")
        if set(owned_people) & set(registries["new_people"]):
            _fail("overlapping_people_alias_registry")
        if "upsert_stay" in by_kind:
            stay = by_kind["upsert_stay"][0]
            registries["stays"] = stay[2]
            room = resolve(resolve(stay[1]["properties"]["rooms"])["items"])
            registries["rooms"] = _enum(room["properties"]["id"], resolve)
        else:
            registries.update(stays=[], rooms=[])
        room_stay = []
        owned_stays = by_kind.get("remove_stay", [(None, None, [])])[0][2]
        for _, value, targets in by_kind.get("set_room_members", []):
            stay_id = _enum(value["properties"]["stay_id"], resolve)
            if (len(stay_id) != 1 or stay_id[0] not in owned_stays
                    or not set(targets) <= set(registries["rooms"])
                    or any(binding[0] == stay_id[0] for binding in room_stay)):
                _fail("invalid_room_stay_binding")
            room_stay.append([stay_id[0], targets])
        registries["room_stay"] = room_stay
        registries["preference_keys"] = _enum(by_kind["clear_preference"][0][1]["properties"]["key"], resolve)
        scalar = []
        constraint = by_kind["set_constraint"][0][1]
        for branch in constraint.get("oneOf", []):
            props = resolve(branch).get("properties", {})
            if resolve(props.get("value", {})).get("type") == ["boolean", "string", "number", "null"]:
                scalar.extend(_enum(props["key"], resolve))
        registries["scalar_keys"] = list(dict.fromkeys(scalar))
        if set(scalar) & {"breakfast", "refundable", "pets_allowed", "pets", "nonstop"}:
            _fail("reserved_scalar_constraint_key")
        for kind, value, _ in variants:
            _check_registry_refs(kind, value, resolve, registries)
        guide = (
            "VALUE_JSON HINTS, not validation; server full schema/domain checks remain authoritative. "
            "value_json encodes an object. ! marks required keys; others optional. "
            "Omission != explicit null; null only where listed. No defaults, casts or invented IDs. "
            "date=YYYY-MM-DD; ISO2/ISO3=uppercase codes; money integer minor units in matching currency. "
            "[IDs] are unique-member arrays; empty registry=no choices. Create supplied aliases before referencing them. "
            "groups includes existing/creation IDs; never infer ownership from spelling. "
            "Wire action fixes type/target. Room IDs are stay-local; room_stay=[stay_id,allowed room targets]. "
            "Server checks all bounds, key/value/scope rules, dates, capacities and references; retain context guide semantics.\n"
        )
        text = guide + "IDS=" + _encoded(registries).decode() + "\n" + "\n".join(
            f"{kind}: {_SIGNATURES[kind]}" for kind in by_kind)
        if len(text.encode()) > 65_536:
            _fail("compact_hint_byte_limit")
        meta = {"version": _VERSION, "projection_equivalent_json_schema": False,
            "original_schema_sha256": hashlib.sha256(json.dumps(original_dynamic_schema, ensure_ascii=False,
                separators=(",", ":")).encode()).hexdigest(),
            "original_schema_canonical_sha256": hashlib.sha256(_encoded(original_dynamic_schema)).hexdigest(),
            "hints_sha256": hashlib.sha256(text.encode()).hexdigest(), "hints_utf8_bytes": len(text.encode()),
            "operation_types": list(by_kind), "operation_variant_count": len(variants),
            "reviewed_city_enum_sha256": _CITY_ENUM_SHA256,
            "server_original_schema_required": True, "wire_exact_type_targets_required": True}
        return text, meta
    except GroqTransportError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError, OverflowError, UnicodeError):
        _fail("unsupported_compact_hint_schema")
