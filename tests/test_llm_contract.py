"""Payload acceptance/rejection for the native schema; no model or DB calls.

Use the runtime Draft202012Validator, including its local-reference resolution.
An unrecognised $ref must never silently turn a negative case into a PASS.
"""

import ast
import base64
from copy import deepcopy
import hashlib
import inspect
import json
import zlib

import pytest
from jsonschema import Draft202012Validator

from otravel import domain
from otravel.domain import Operation, TripState, apply_operations, sample_trip
from otravel.llm_contract import OPERATION_TYPES, Build_operation_schema, build_operation_schema


def accepts(value, schema):
    return Draft202012Validator(schema).is_valid(value)


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


def _baseline_schema(name):
    snapshot = _FROZEN_BASELINE_SCHEMAS[name]
    raw = zlib.decompress(base64.b85decode(snapshot["zlib_base85"]))
    assert len(raw) == snapshot["bytes"]
    assert hashlib.sha256(raw).hexdigest() == snapshot["sha256"]
    return json.loads(raw)


# After this refactor, same compact UTF-8 JSON encoding as the frozen originals.
# Byte counts are not model-token counts; no tokenizer or inference was run.
_COMPRESSED_SCHEMA_SERIALIZATION = {
    "empty": {"bytes": 5573, "sha256": "d249958ae144ce9337402fac28b6502888e07b2be9dcfbfed9c8d323961321fc"},
    "sample": {"bytes": 6712, "sha256": "bcf2ec2c071334f764b45293d3f3f2efbc7e0731a77c7c67c0142e6b5e0ec2a3"},
}


@pytest.mark.parametrize("name,state", [("empty", TripState()), ("sample", sample_trip())])
def test_legacy_schema_keeps_25_percent_reduction_and_added_features_have_bounded_wire_cost(name, state):
    original = _baseline_schema(name)
    schema = build_operation_schema(state)
    Draft202012Validator.check_schema(original)
    Draft202012Validator.check_schema(schema)
    raw = json.dumps(schema, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    definitions = schema.get("$defs", {})
    def expanded(node):
        if isinstance(node, dict):
            if "$ref" in node:
                assert set(node) == {"$ref"} and node["$ref"].startswith("#/$defs/")
                return expanded(definitions[node["$ref"].removeprefix("#/$defs/")])
            return {key: expanded(value) for key, value in node.items() if key != "$defs"}
        return [expanded(value) for value in node] if isinstance(node, list) else node
    # Historical wire comparisons keep the historical geographic feature set.
    # Only the newly curated city enum member is projected out; real schema
    # validation and the full current serialization keep accepting Тбилиси.
    geographic_projection = expanded(schema)
    removed_city_members = []
    def historical_city_enums(node):
        if isinstance(node, dict):
            values = node.get("enum")
            if (node.get("type") == "string" and isinstance(values, list) and "Тбилиси" in values
                    and {"Москва", "Стамбул"}.issubset(values)):
                node["enum"] = [value for value in values if value != "Тбилиси"]
                removed_city_members.append("Тбилиси")
            for value in node.values():
                historical_city_enums(value)
        elif isinstance(node, list):
            for value in node:
                historical_city_enums(value)
    historical_city_enums(geographic_projection)
    assert removed_city_members and set(removed_city_members) == {"Тбилиси"}
    from otravel.llm_contract import _local_definitions
    historical_geography_raw = json.dumps(_local_definitions(geographic_projection), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    new_curated_geography_bytes = len(raw) - len(historical_geography_raw)
    assert new_curated_geography_bytes == 17  # UTF-8 JSON bytes, not model tokens.
    assert accepts(response(op("set_origin", {"city": "Тбилиси"})), schema)
    group_room_scope = deepcopy(geographic_projection)
    for variant in group_room_scope["properties"]["operations"]["items"]["oneOf"]:
        kind = variant["properties"]["type"]["const"]
        if kind in {"set_constraint", "clear_constraint"}:
            value = variant["properties"]["value"]
            value["allOf"] = [scope for scope in value["allOf"]
                              if scope.get("if", {}).get("properties", {}).get("key", {}).get("const") != "nonstop"]
            if kind == "set_constraint":
                for condition in value["oneOf"]:
                    keys = condition.get("properties", {}).get("key", {}).get("enum", [])
                    if "nonstop" in keys:
                        keys.remove("nonstop")
    route_only = deepcopy(group_room_scope)
    # Compare like-for-like scopes. Groups/room-specific rates are new features;
    # remove only their exact additions from the historical projection.
    route_variants = route_only["properties"]["operations"]["items"]["oneOf"]
    route_only["properties"]["operations"]["items"]["oneOf"] = [
        v for v in route_variants if v["properties"]["type"]["const"] != "upsert_group"]
    for variant in route_only["properties"]["operations"]["items"]["oneOf"]:
        if variant["properties"]["type"]["const"] in {"set_constraint", "clear_constraint"}:
            value = variant["properties"]["value"]
            value["properties"].pop("room_id")
            value.pop("allOf")
    legacy = deepcopy(route_only)
    variants = legacy["properties"]["operations"]["items"]["oneOf"]
    added = {"set_route_policy", "clear_preference"}
    assert {v["properties"]["type"]["const"] for v in variants}.issuperset(added)
    legacy["properties"]["operations"]["items"]["oneOf"] = [
        variant for variant in variants if variant["properties"]["type"]["const"] not in added]
    # Preserve the historical performance contract on the SAME legacy feature
    # set. Added operations/scopes have explicit separate engineering budgets;
    # the full schema is no longer advertised as 25% smaller than old a93.
    legacy = _local_definitions(legacy)
    legacy_raw = json.dumps(legacy, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert len(legacy_raw) <= _FROZEN_BASELINE_SCHEMAS[name]["bytes"] * 0.75
    assert len(legacy_raw) == _COMPRESSED_SCHEMA_SERIALIZATION[name]["bytes"]
    assert hashlib.sha256(legacy_raw).hexdigest() == _COMPRESSED_SCHEMA_SERIALIZATION[name]["sha256"]
    route_raw = json.dumps(_local_definitions(route_only), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert 0 < len(route_raw) - len(legacy_raw) <= 800
    assert len(route_raw) == {"empty": 6289, "sample": 7429}[name]
    group_room_raw = json.dumps(_local_definitions(group_room_scope), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert 0 < len(group_room_raw) - len(route_raw) <= 900
    assert len(group_room_raw) == {"empty": 7125, "sample": 8281}[name]
    assert 0 < len(historical_geography_raw) - len(group_room_raw) <= 250  # Nonstop scope bytes, not tokens/product limits.
    assert len(historical_geography_raw) == {"empty": 7332, "sample": 8488}[name]
    assert len(raw) == {"empty": 7349, "sample": 8505}[name]
    assert json.dumps(build_operation_schema(state), ensure_ascii=False, separators=(",", ":")).encode() == raw


@pytest.mark.parametrize("state", [TripState(), sample_trip(), TripState(travelers=[{"id": "name/with~pointer", "type": "adult"}])])
def test_all_references_are_local_resolvable_and_schema_owns_its_definitions(state):
    schema = build_operation_schema(state)
    assert schema.get("$defs")
    def walk(value):
        if isinstance(value, dict):
            if "$ref" in value:
                ref = value["$ref"]
                assert ref.startswith("#/$defs/") and set(value) == {"$ref"}
                assert ref[len("#/$defs/"):] in schema["$defs"]
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(schema)
    fresh = build_operation_schema(state)
    first_definition = next(iter(schema["$defs"]))
    schema["$defs"][first_definition].clear()
    assert fresh == build_operation_schema(state)


@pytest.mark.parametrize("operation", [
    op("update_traveler", {"age": 4}, "stay-istanbul"),
    op("remove_traveler", {}, "new-traveler-1"),
    op("remove_traveler", {}, "new-group-1"),
    op("upsert_stay", {"city": "Стамбул"}, "child-1"),
    op("remove_stay", {}, "new-stay-1"),
    op("replace_hotel", {"hotel_id": "h"}, "new-stay-1"),
    op("add_traveler", {"id": "new-stay-1", "type": "adult"}),
    op("add_traveler", {"id": "new-traveler-1", "type": "adult", "group_id": "stay-istanbul"}),
    op("upsert_stay", {"rooms": [{"id": "child-1", "traveler_ids": ["child-1"]}]}, "stay-istanbul"),
    op("upsert_stay", {"rooms": [{"id": "new-room-1", "traveler_ids": ["new-room-1"]}]}, "stay-istanbul"),
    op("set_constraint", {"key": "breakfast", "value": True, "stay_id": "child-1"}),
    op("set_origin", {"city": "Москва"}, "stay-istanbul"),
    op("undo", {}, "child-1"),
])
def test_local_reference_enums_reject_cross_typed_and_new_edit_target_ids(operation):
    assert not accepts(response(operation), build_operation_schema(sample_trip()))


def _equivalence_payloads():
    """Fixed operation/value cases plus independent envelope mutations."""
    operations = [op("set_origin", {"city": "Москва"}), op("set_origin", {"city": "Белград"}),
        op("set_budget", {"amount_minor": 1}), op("set_budget", {"amount_minor": 123, "currency": "RUB", "scope": "whole_trip"}),
        op("set_search_policy", {"mode": "HYBRID", "guest_nationality": None}),
        op("set_preference", {"key": "price_priority", "value": "lowest_total", "weight": 0}),
        op("set_preference", {"key": "departure_time", "value": "early_morning", "weight": 1}),
        op("add_traveler", {"id": "new-traveler-1", "type": "adult", "age": 18}),
        op("add_traveler", {"id": "new-traveler-2", "type": "child", "age": 17, "group_id": "new-group-1"}),
        op("update_traveler", {"age": None, "birth_date": "2020-02-01"}, "child-1"),
        op("remove_traveler", {}, "child-1"),
        op("upsert_stay", {"city": None, "check_in": None, "rooms": []}, "new-stay-1"),
        op("upsert_stay", {"city": "Стамбул", "rooms": [{"id": "new-room-1", "traveler_ids": ["new-traveler-1"]}]}, "stay-istanbul"),
        op("remove_stay", {}, "stay-istanbul"), op("replace_hotel", {"hotel_id": "h"}, "stay-istanbul"),
        op("shift_return", {"days": -2}), op("set_constraint", {"key": "breakfast", "value": True, "stay_id": None}),
        op("set_constraint", {"key": "pets", "value": {"kind": "cat", "weight_kg": 0.1}, "hard": False, "stay_id": "new-stay-1"}),
        op("clear_constraint", {"key": "breakfast", "stay_id": "stay-istanbul"}), op("undo", {})]
    yield response()
    for operation in operations:
        yield response(operation)
        for name in ("type", "value"):
            changed = deepcopy(operation)
            changed.pop(name)
            yield response(changed)
        for target in (None, "", "missing-id", "new-traveler-1", "stay-istanbul"):
            yield response({**deepcopy(operation), "target_id": target})
        yield response({**deepcopy(operation), "private_extra": True})
        yield response({**deepcopy(operation), "value": {**operation["value"], "private_extra": True}})
        yield {**response(operation), "private_extra": True}
    for field in ("operations", "control", "reply", "unknowns", "conflicts"):
        changed = response()
        changed.pop(field)
        yield changed
    for length in (30, 31):
        yield response(*[op("set_origin", {"city": "Москва"}) for _ in range(length)])
    for subject in (None, "", "x" * 80, "x" * 81, 1):
        yield {**response(), "information_subject": subject}
    for speech_act in ("ASK", "CHANGE", "PROPOSE", "BOOK", None, True):
        yield {**response(), "speech_act": speech_act}
    for age in (-1, 0, 17, 18, 120, 121, None, True, "4"):
        for kind in ("adult", "child"):
            yield response(op("add_traveler", {"id": "new-traveler-1", "type": kind, "age": age}))
    for weight in (-0.1, 0, 1, 1.1, True, None):
        yield response(op("set_preference", {"key": "price_priority", "value": "lowest_total", "weight": weight}))
    for value in (True, False, None, "yes", {}, [], 1):
        yield response(op("set_constraint", {"key": "breakfast", "value": value}))
    for key in ("breakfast", "pets", "arbitrary_condition"):
        yield response(op("set_constraint", {"key": key, "value": {"kind": "dog", "weight_kg": 3}}))


@pytest.mark.parametrize("name,state", [("empty", TripState()), ("sample", sample_trip())])
def test_frozen_original_and_ref_schema_agree_on_operation_envelope_and_boundary_matrix(name, state):
    original = Draft202012Validator(_baseline_schema(name))
    current = Draft202012Validator(build_operation_schema(state))
    accepted, rejected = 0, 0
    for index, payload in enumerate(_equivalence_payloads()):
        before, after = original.is_valid(payload), current.is_valid(payload)
        assert before == after, (name, index, payload)
        accepted += before
        rejected += not before
    assert accepted > 20 and rejected > 100


def test_existing_unknown_constraint_scalar_union_and_shared_objects_remain_strict():
    state = TripState(constraints=[{"key": "custom", "value": "old"}])
    schema = build_operation_schema(state)
    for value in (None, True, 4, "new"):
        assert accepts(response(op("set_constraint", {"key": "custom", "value": value})), schema)
    for operation in (op("set_constraint", {"key": "custom", "value": []}),
                      op("set_constraint", {"key": "custom", "value": {"secret": True}}),
                      op("set_constraint", {"key": "new_unsupported", "value": True}),
                      op("set_preference", {"key": "price_priority", "value": "early_morning"}),
                      op("set_preference", {"key": "departure_time", "value": "lowest_total"}),
                      op("add_traveler", {"id": "new-traveler-1", "type": "adult", "age": 17})):
        assert not accepts(response(operation), schema)


def test_local_definitions_never_confuse_ids_with_json_pointer_paths():
    state = TripState(origin="Москва", travelers=[{"id": "person/with~pointer", "type": "adult"}],
                      stays=[{"id": "stay/with~pointer", "city": "Сочи"}])
    schema = build_operation_schema(state)
    assert accepts(response(op("update_traveler", {"age": 35}, "person/with~pointer")), schema)
    assert accepts(response(op("upsert_stay", {"check_in": "2027-03-01"}, "stay/with~pointer")), schema)
    assert not accepts(response(op("update_traveler", {"age": 35}, "stay/with~pointer")), schema)
    assert not accepts(response(op("upsert_stay", {"check_in": "2027-03-01"}, "person/with~pointer")), schema)


@pytest.mark.parametrize("count,expected", [(5, True), (6, False)])
def test_room_array_bound_survives_local_refs(count, expected):
    payload = response(op("upsert_stay", {"rooms": [{"id": "new-room-1", "traveler_ids": ["child-1"]}] * count}, "stay-istanbul"))
    assert accepts(payload, build_operation_schema(sample_trip())) is expected


# Synthetic pre-change schemas from a93f6f0, compact UTF-8 JSON. No provider keys.
# Frozen before equivalence checks; this is an oracle snapshot, not a second builder.
_FROZEN_BASELINE_SCHEMAS = {
    "empty": {
        "bytes": 7776,
        "sha256": "f716da2bb8d7f7c2633e0a3b80e2bfa5c5166f96c23ba9b0b293c35bcb716553",
        "zlib_base85": "c-rk*UvJws5dSX19%I0<o#xM5wgzjlbqU(1t?&d|I$4}4Ql+T4Mi8Lu9`;gT8?dKg!?16Vt|`_vTRwyQjdrALN0JrGO4GpE_K-k#JRZrr-yM(VBTJ1!uy!ob`wARoty!TIAxK3bvUZLv{Y?}V+)xu)A|)B2Xh5FcR}kP(ySZl3^b_ZB*!QfR$43>q)6tn!q$K5ilyY!2r5T`XA!(m->v+vNBrMK~U!v5ZYE-5acpTuKtmNn9eEfCtHu*g{L3Z+6a+3U=yd6JpB)=xF$ItQaS@LrH4gP<Xj-QXeP2T9wpHTD<Ek1r>Mo{=9`AZ8YXPWUnj%e1|_{I1;&G->VULxa9P5h9&PR^27sP`l})r?<~bKJz6hS|btMg&)UhGVBz_ZZJD;Ymy-xO(&EaNRYFxGvRBA?zS?c#p6MaE2nXkEWw;#tuAY%qprgV^hmksl5(w_F@-zR>PYl5HVMFK)H|^Y04G!L23w6P6O@PbyVuaQUZ5M{f~m50`DsyMR66P6iAK&Uq0S#e9=8>AAg{?6**$@DTcmaU?Vr2vu19ux7bOc1P`<wA?K)Nqj~_NMJt4oI)H0XjBEsR2^8k0FfoKk*-9wF^n@YMzOQn23wn7pG);1vBBNkYy~FD=XvJw=2#Hj&M99zpmO3pE$wqb{B-i|#J#KfUff=D`9N!KFqt2*C5($J0i>`#bdk^-Jy!ZKs5AWZ#v<-ZrfFVf5A$376n#=IkBOygj1TxfLdcMcB%a&5$#-0bPFyDnh*l0P{BhDZ*yC*RF5OW>&V;vKmq!yoCdW4ViF}iH}Ynk*{@OSb0S?pu8MwYw2vS32xn8L{+F!aq;a$N2_b1vmDY)sWR)^Q-mXcZalBBN7eY!n%Hij2)7W2?y6E;81e25Qx|ikZ2wNgqlfR(2O}W%E(1S%R!Jt4F2sZI>vxDsr2R?e0<MxRHLeE<Q@Jj3FaM98R!!`FdF|ni(?}rc^6OwR2P_M{VS&J2`4IM{VV(?HrZ*sUkErA}79l*WmdNVcYR3b8BKdchzqGkL=Y!_UaZdafp{Y)-Q^TuJDqrcIfST)0P3)@xd9`_|U(hfJxUAvAU@Rr4T_`3uV?cdDo<@7nsGfAzE3Yoh3S1Vk1qJlhlc!v&=Sc_q`Bv`Xq))>q^NOvaPK3W_bq68mRJhf1z$;_ME6n&snhDR4rcWN1dl;J~TutOSH2@XTC|5ORd>u-}@WUr=GGU;Cs7H$IvCCsHC@4JeCo=#cdt)EeRg)I@>x^-1H=nfkz@eQGyq9m-LuEU<fL*@g*yUc<yeAS2Ax}pGdcSL}RU1eNz|1m1W2cHjT8&YT$Q3`NEKPg;7qFF@M@kvIqULh7vq=SRBzqcrYt>=j@<Ys|<sGi6>XLq;$}p^0Jt|68iGj&x-izmgZYbe`0x$)VbN;*O86&ar;UNw#3gej@Gj%>1b7yn*v}+XnHF|aR4S0amRE#^<H%o(-h;n7B@Tas0=h#@M4;;EhY<B+DJmkN>FZUzX2CRUZcpJSjJ=2Q8lMUA%NrCxa&o>`sm*7XCLn&WB1V~`2XPH{=xpEJ(H~|_k;{glh=;o)EPBiELet4sjX&p#ASZ$y#T}1m>JV$?NQ;1e*jB;$5#"
    },
    "sample": {
        "bytes": 9107,
        "sha256": "fa4a61469b35bfd1f347b7828686101df994e30d432d938873fb7ee448adf1c7",
        "zlib_base85": "c-rk+QE%He5dJU19&Ny}lda2JwgzjlbqU(1t?&d|I@z2lQl-eaMi8Lu9`;gT8?dKg!?1slt|`_vTYiK5k9MSNMRF9&c2dt-`w-)JB#-2GAMZ#ihmIOX;A}gh^EG(NsX37p5lBTLcD4^4BNIghx217gA|)AO(~vyAt02UxR-@+7RKz|U_5x@7;bDdCv^HxMD@l15TX{H|G73<xkhDv=b5wH<2-ErIml*Y^8kQLau0x!Yll+pLjlM}<Cx0Zzs7`)Qj+1|q*Q2NP<hSJI=qdg^NuH0s#orfc{n_Zd<dqSB#-@Lo=A&n}1{)tIf1Ae1sZsoZHAZzhdN%srD1O44=cxG06n;!zCa1{@w0oSK7{#y28BXF=-A>^o*91p=f^{cO`v})9;gO~id^35wxOUAUzE6!)2;0vsJ|HXx?4gM4B6QTx;lMRx&a^pOH$k?_>~(mvqkWv&0^TH{&|JA8<wE9KQ?8&3(om3c8XC`Tpixgt3EV67KQ{afcvtnXDUL#v0?Dz#R}XjUU$zfhNAH<w#h#eGia}2>a8VmwK(jE{8|<V=LI4IwC^%}~UEPP_tPvtfJ-{(2?rbD;2^B7MVPXKWa+OeonGOS>-JUAo7IunkXxQL1Va61qW`@@VXvJ!Mh=^2LB4ua@bFCJLWJ5O;k{kVMm)l-zV#WxK*K;Gms5e|7h=jt2St;T6?!7%M-udFA`*&|U27{hZz>uWkfchY3^<|j#NJLQ+gN%%1`UlLoY%T-74gz3P{e1|9i^#DK@%p*m9f8q@*mT&{W>0XEB0f%fgb#~*bl&jSwBc{c-_z-5wvX)?dF{s9k_nAt2qy=?&^OD(xWalaxRk@7J~3b4unw)Bk!Ch8k}i}wvMl4>%J2GSqr`tTr$eRcxjs>_EI%6c58H>ENA*-_o(rXnL1Idwqp`;?A8&$HKtjrfh*E7LHc>PPT01Fx6}&X=7(;wj=3KjDy&h%$f{raj^AFy+^#-Rc9T^{$2b<+APY_NcMr};6_7xcR7DtY2%4p@zu~SQ>W+t^VX)}|yGU@$^Lv7zwYGzXF;)Sq4=KSQ|G_fU~P!5(M_96~=2lsg#!Q&yGfE5`o@fK3OnQqprNLldkdf@e4%2ySv((!~=s~S)W5tco|WzTL`IoTLu)hYh+j%IZ;l-&{~>SnpRHBl3exAY;IvQf}BOSP?W^v*hFg=W4`XttRzY^4XXaYVvhIS*PZizT(P)1&6}k%sXbs*)P=Z>k%O@&i$RftB&|+}u`nudT|kdCvZ4DYU?`DZ-4ww5pAKp}Bq;a<P#8CbHv9B-aVAh!GE5%h3grT%OZ2nLM@7tlwLOa@?bVawRA&7x}8<lVM!){yx1d+w2BEeOp-)T;6p)eNQOTkwE$ZiA_fdf#yExFtfl2RO}LF-nrAO+_YD6?|wZZ{qh>axVdjnONZCWTl)%M*9~q9kho9z%q{E-Yn&+SgY?<m?RU#w-r=#wbW9K6UUsFvfP)#WaxeU!+_}EbFdl*`FWcQqQLuPMS|?}S(C~}VP^|Qvbmb*m^l|!13O2`=Y|q%46@H8<%I$ezKxmpIVjY5wN!+s^iOgKhcOQHEG?!01gMiA=a0T<!#iPb};7W|l`KyHL_LkD;V!#);PK}WX81>YG-Qozq>$y1VSy+91XXo=zc2TkO;8XnFzrVM?_h8q?ZOQ{7L;GFKjdkjb1+M6LVVaa~HL7=9?*C)X$uI#k2VEYI3RnCK9x|5|"
    }
}
