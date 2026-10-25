"""Payload acceptance/rejection for the native schema; no model or DB calls.

This small test interpreter exercises the module's vocabulary independently of
the runtime Draft202012Validator; it is not a general JSON Schema validator.
"""

import ast
import inspect
import re

import pytest

from otravel import domain
from otravel.domain import Operation, TripState, apply_operations, sample_trip
from otravel.llm_contract import OPERATION_TYPES, Build_operation_schema, build_operation_schema


def accepts(value, schema):
    if "oneOf" in schema and sum(accepts(value, child) for child in schema["oneOf"]) != 1:
        return False
    if "anyOf" in schema and not any(accepts(value, child) for child in schema["anyOf"]):
        return False
    kinds = schema.get("type")
    if kinds is not None:
        kinds = [kinds] if isinstance(kinds, str) else kinds
        types = {"object": isinstance(value, dict), "array": isinstance(value, list),
                 "string": isinstance(value, str), "integer": type(value) is int,
                 "number": type(value) in (int, float), "boolean": type(value) is bool, "null": value is None}
        if not any(types[kind] for kind in kinds):
            return False
    if "const" in schema and (type(value) is not type(schema["const"]) or value != schema["const"]):
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if isinstance(value, dict):
        props = schema.get("properties", {})
        if set(schema.get("required", [])) - value.keys():
            return False
        if schema.get("additionalProperties") is False and value.keys() - props.keys():
            return False
        if len(value) < schema.get("minProperties", 0):
            return False
        if any(not accepts(item, props[key]) for key, item in value.items() if key in props):
            return False
    if isinstance(value, list):
        if len(value) > schema.get("maxItems", float("inf")):
            return False
        if schema.get("uniqueItems") and any(item in value[:index] for index, item in enumerate(value)):
            return False
        if "items" in schema and not all(accepts(item, schema["items"]) for item in value):
            return False
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            return False
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            return False
    if type(value) in (int, float):
        if value < schema.get("minimum", float("-inf")) or value > schema.get("maximum", float("inf")):
            return False
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            return False
    return True


def response(*operations):
    return {"operations": list(operations), "control": {"action": "none"}, "reply": "Сохранил изменения.", "unknowns": [], "conflicts": []}


def op(kind, value, target=None):
    result = {"type": kind, "value": value}
    if target is not None:
        result["target_id"] = target
    return result


@pytest.fixture
def state():
    return sample_trip()


def test_existing_child_age_and_hotel_id_update_are_accepted_and_apply(state):
    payload = response(op("update_traveler", {"age": 4}, "child-1"), op("replace_hotel", {"hotel_id": "explicit-hotel-42"}, "stay-istanbul"))
    assert accepts(payload, build_operation_schema(state))
    updated, changes = apply_operations(state, [Operation.model_validate(o) for o in payload["operations"]])
    assert next(p.age for p in updated.travelers if p.id == "child-1") == 4
    assert updated.stays[0].hotel_id == "explicit-hotel-42"
    assert updated.origin == state.origin and len(updated.travelers) == len(state.travelers)
    assert changes


@pytest.mark.parametrize("kind,value", [("update_traveler", {"age": 4}), ("remove_traveler", {}), ("replace_hotel", {"hotel_id": "explicit-42"}), ("remove_stay", {}), ("upsert_stay", {"city": "Стамбул"})])
@pytest.mark.parametrize("bad_target", [None, "missing-id", ""])
def test_target_none_missing_or_unknown_is_rejected(state, kind, value, bad_target):
    operation = op(kind, value)
    operation["target_id"] = bad_target
    assert not accepts(response(operation), build_operation_schema(state))
    operation.pop("target_id")
    assert not accepts(response(operation), build_operation_schema(state))


@pytest.mark.parametrize("operation", [op("1", {"adults": 1}), op("add_traveler", {"type": "1", "id": "new-traveler-1"}), op("set_budget", {"amount_minor": "100000"}), op("set_budget", {"amount_minor": 0}), op("set_budget", {"amount_minor": True}), op("set_origin", {"city": "Сербия"}), op("upsert_stay", {"city": "Serbia"}, "stay-istanbul"), op("update_traveler", {"age": "4"}, "child-1"), op("replace_hotel", {"hotel_id": None}, "stay-istanbul"), op("undo", {"trip_id": "evil"}), op("set_constraint", {"key": "breakfast", "value": "yes"}), op("set_constraint", {"key": "pets", "value": {"kind": "dog", "weight_kg": -2}})])
def test_observed_and_other_malformed_payloads_rejected(state, operation):
    assert not accepts(response(operation), build_operation_schema(state))


def test_same_batch_candidate_people_groups_rooms_and_stays_are_valid_and_apply():
    state = TripState(origin="Москва")
    payload = response(op("add_traveler", {"id": "new-traveler-1", "type": "adult", "group_id": "new-group-1"}),
        op("add_traveler", {"id": "new-traveler-2", "type": "child", "age": 4, "group_id": "new-group-1"}),
        op("upsert_stay", {"city": "Стамбул", "check_in": "2027-03-01", "check_out": "2027-03-05",
            "rooms": [{"id": "new-room-1", "traveler_ids": ["new-traveler-1", "new-traveler-2"]}]}, "new-stay-1"))
    assert accepts(payload, build_operation_schema(state))
    updated, _ = apply_operations(state, [Operation.model_validate(o) for o in payload["operations"]])
    assert updated.stays[0].rooms[0].traveler_ids == ["new-traveler-1", "new-traveler-2"]
    assert updated.groups[0].traveler_ids == ["new-traveler-1", "new-traveler-2"]


@pytest.mark.parametrize("person", [{"id": "arbitrary-invented-id", "type": "adult"}, {"id": "new-traveler-1", "type": "adult", "age": 4}, {"id": "new-traveler-1", "type": "child", "age": 18}, {"id": "new-traveler-1", "type": "child", "age": True}, {"id": "new-traveler-1", "type": "child", "birth_date": "01/02/2020"}, {"id": "new-traveler-1", "type": "child", "group_id": "made-up"}, {"type": "adult"}])
def test_invalid_new_person_age_date_type_group_and_identity(person):
    assert not accepts(response(op("add_traveler", person)), build_operation_schema(TripState()))


@pytest.mark.parametrize("person", [{"id": "new-traveler-1", "type": "adult"}, {"id": "new-traveler-1", "type": "child", "age": None}, {"id": "new-traveler-1", "type": "child", "birth_date": "2020-02-01"}])
def test_unknown_age_or_date_remains_a_valid_draft(person):
    assert accepts(response(op("add_traveler", person)), build_operation_schema(TripState()))


def test_operation_values_and_nested_objects_cannot_smuggle_private_fields(state):
    schema = build_operation_schema(state)
    for operation in (op("set_budget", {"amount_minor": 100, "api_key": "fake"}),
                      op("set_search_policy", {"mode": "HYBRID", "api_key": "fake"}),
                      op("upsert_stay", {"id": "stay-antalya", "city": "Стамбул"}, "stay-istanbul"),
                      op("set_constraint", {"key": "pets", "value": {"kind": "dog", "token": "fake"}})):
        assert not accepts(response(operation), schema)
    payload = response()
    payload["private_context"] = {}
    assert not accepts(payload, schema)


def test_same_batch_rooms_do_not_accept_arbitrary_unknown_members(state):
    payload = response(op("upsert_stay", {"rooms": [{"id": "new-room-1", "traveler_ids": ["made-up-child"]}]}, "stay-istanbul"))
    assert not accepts(payload, build_operation_schema(state))
    payload["operations"][0]["value"]["rooms"][0]["traveler_ids"] = ["child-1", "child-1"]
    assert not accepts(payload, build_operation_schema(state))


def test_search_policy_and_control_contract(state):
    payload = response(op("set_search_policy", {"mode": "HYBRID", "hotel_provider": "liteapi", "flight_provider": "duffel", "demo_fallback": False, "guest_nationality": "RS"}))
    assert accepts(payload, build_operation_schema(state))
    payload["operations"][0]["value"]["guest_nationality"] = "Serbia"
    assert not accepts(payload, build_operation_schema(state))
    payload = response(op("undo", {}))
    payload["control"] = {"action": "cancel"}
    assert accepts(payload, build_operation_schema(state))
    payload["control"]["action"] = "purchase"
    assert not accepts(payload, build_operation_schema(state))


def test_schema_is_deterministic_independent_and_does_not_mutate_state(state):
    prior = state.model_dump(mode="json")
    first = build_operation_schema(state)
    second = build_operation_schema(prior)
    assert first == second
    first["properties"]["control"]["properties"]["action"]["enum"].append("invented")
    assert second == build_operation_schema(state)
    assert state.model_dump(mode="json") == prior
    assert Build_operation_schema is build_operation_schema


def test_creation_candidates_skip_existing_ids_and_follow_implementation_guard():
    state = TripState.model_validate({"travelers": [{"id": "new-traveler-1", "type": "adult"}], "stays": [{"id": "new-stay-1"}]})
    schema = build_operation_schema(state)
    assert accepts(response(op("add_traveler", {"id": "new-traveler-2", "type": "adult"})), schema)
    assert not accepts(response(op("add_traveler", {"id": "new-traveler-1", "type": "adult"})), schema)
    full = TripState.model_validate({"travelers": [{"id": f"adult-{i}", "type": "adult"} for i in range(10)]})
    assert not accepts(response(op("add_traveler", {"id": "new-traveler-11", "type": "adult"})), build_operation_schema(full))


def test_schema_covers_domain_whitelist_plus_harness_undo(state):
    tree = ast.parse(inspect.getsource(domain.apply_operations))
    allowed = next(ast.literal_eval(node.value) for node in ast.walk(tree)
                   if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "allowed" for t in node.targets))
    assert OPERATION_TYPES == allowed | {"undo"}
    offered = {variant["properties"]["type"]["const"] for variant in build_operation_schema(state)["properties"]["operations"]["items"]["oneOf"]}
    assert offered == OPERATION_TYPES


def test_all_object_schemas_are_strict_and_dates_still_need_domain_validation(state):
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    schema = build_operation_schema(state)
    walk(schema)
    payload = response(op("upsert_stay", {"check_in": "2027-02-31"}, "stay-istanbul"))
    assert accepts(payload, schema)  # Shape is not a real calendar/date-range check.
    with pytest.raises(domain.DomainError):
        apply_operations(state, [Operation.model_validate(o) for o in payload["operations"]])


def test_envelope_all_five_fields_required_and_no_extra_operations(state):
    schema = build_operation_schema(state)
    for key in ("operations", "control", "reply", "unknowns", "conflicts"):
        payload = response()
        payload.pop(key)
        assert not accepts(payload, schema)
    payload = response(*[op("set_origin", {"city": "Москва"}) for _ in range(31)])
    assert not accepts(payload, schema)
    assert accepts(response(), schema)


def test_speech_act_metadata_and_minimum_price_are_bounded(state):
    schema = build_operation_schema(state)
    for act in ("ASK", "PROPOSE", "CHANGE"):
        assert accepts({**response(), "speech_act": act, "information_subject": "Сербия"}, schema)
    assert not accepts({**response(), "speech_act": "BOOK_NOW"}, schema)
    payload = response(op("set_preference", {"key": "price_priority", "value": "lowest_total"}))
    assert accepts(payload, schema)
    payload["operations"][0]["value"]["value"] = 100000
    assert not accepts(payload, schema)
