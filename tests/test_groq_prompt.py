"""Pure compact guidance tests; full canonical validation remains authoritative."""
import hashlib
import json
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from otravel.domain import MVP_LIMITS, TripState, sample_trip
from otravel.groq_prompt import build_compact_operation_hints
from otravel.groq_transport import GroqTransportError, _prepare
from otravel.llm_contract import OPERATION_TYPES, build_operation_schema


def unintern(schema):
    _, _, resolve = _prepare(schema)

    def expand(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return expand(resolve(node))
            return {key: expand(value) for key, value in node.items() if key != "$defs"}
        if isinstance(node, list):
            return [expand(child) for child in node]
        return node
    return expand(schema)


def variants(schema, kind):
    return [variant for variant in schema["properties"]["operations"]["items"]["oneOf"]
        if variant["properties"]["type"]["const"] == kind]


def registry(text):
    return json.loads(next(line[4:] for line in text.splitlines() if line.startswith("IDS=")))


def hints(schema=None):
    return build_compact_operation_hints(schema or build_operation_schema(sample_trip()))


def test_all_current_operation_types_values_and_required_semantics_are_present():
    source = build_operation_schema(sample_trip())
    text, meta = hints(source)
    assert set(meta["operation_types"]) == OPERATION_TYPES
    assert len(meta["operation_types"]) == 19
    assert meta["operation_variant_count"] == 20
    lines = dict(line.split(": ", 1) for line in text.splitlines() if ": " in line)
    for variant in unintern(source)["properties"]["operations"]["items"]["oneOf"]:
        kind = variant["properties"]["type"]["const"]
        for key in variant["properties"]["value"]["properties"]:
            assert key in lines[kind]
        for required in variant["properties"]["value"].get("required", []):
            assert required + "!" in lines[kind] or required + "+" in lines[kind]
    assert "! marks required keys; others optional" in text
    assert "Omission != explicit null" in text
    assert "No defaults" in text and "Create supplied aliases before referencing them" in text
    assert "ISO3" in lines["set_budget"] and "currency!" not in lines["set_budget"]
    assert "group_id:$groups|null" in lines["add_traveler"]
    assert "adult18..120;child0..17" in lines["add_traveler"]
    assert "age:int[0,120]|null" in lines["update_traveler"]
    assert "rooms:[" in lines["upsert_stay"] and "rooms:null" not in lines["upsert_stay"]
    assert lines["clear_constraint"].startswith("key!:nonempty-text")


def test_guidance_is_not_claimed_as_schema_and_sha_binds_exact_original():
    original = build_operation_schema(sample_trip())
    snapshot = deepcopy(original)
    text, meta = hints(original)
    assert original == snapshot
    encoded = json.dumps(original, ensure_ascii=False, separators=(",", ":")).encode()
    assert meta["original_schema_sha256"] == hashlib.sha256(encoded).hexdigest()
    assert meta["hints_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert meta["hints_utf8_bytes"] == len(text.encode())
    assert meta["projection_equivalent_json_schema"] is False
    assert meta["server_original_schema_required"] is True
    assert meta["wire_exact_type_targets_required"] is True
    assert "server full schema/domain checks remain authoritative" in text
    assert "Server checks all bounds" in text
    assert "Москва" not in text and "RUB" not in text


@pytest.mark.parametrize("state", [TripState(), sample_trip()])
def test_empty_and_existing_trip_registries_are_complete_without_calls(state):
    source = unintern(build_operation_schema(state))
    text, _ = hints(source)
    ids = registry(text)
    create = variants(source, "add_traveler")
    new_ids = create[0]["properties"]["value"]["properties"]["id"]["enum"] if create else []
    assert ids["new_people"] == new_ids
    assert set(ids["people"]) == {person.id for person in state.travelers} | set(new_ids)
    assert {group.id for group in state.groups} <= set(ids["groups"])
    assert {stay.id for stay in state.stays} <= set(ids["stays"])
    assert {room.id for stay in state.stays for room in stay.rooms} <= set(ids["rooms"])
    assert ids["room_stay"] == [[stay.id, [room.id for room in stay.rooms]] for stay in state.stays if stay.rooms]


def test_existing_new_prefix_is_not_mistaken_for_creation_alias():
    state = TripState.model_validate({"travelers": [{"id": "new-traveler-1", "type": "adult"}],
        "groups": [{"id": "new-group-1", "traveler_ids": ["new-traveler-1"]}],
        "stays": [{"id": "new-stay-1", "city": "Казань", "rooms": [
            {"id": "new-room-1", "traveler_ids": ["new-traveler-1"]}]}]})
    text, _ = hints(build_operation_schema(state))
    ids = registry(text)
    assert "new-traveler-1" in ids["people"] and "new-traveler-1" not in ids["new_people"]
    assert "new-traveler-2" in ids["new_people"]
    assert ["new-stay-1", ["new-room-1"]] in ids["room_stay"]
    assert "never infer ownership from spelling" in text


def test_room_tokens_reused_across_stays_preserve_exact_pairings():
    state = sample_trip().model_dump(mode="json")
    for stay in state["stays"]:
        stay["rooms"] = [{"id": "shared-room-token", "traveler_ids": ["adult-1"]}]
    state = TripState.model_validate(state)
    text, _ = hints(build_operation_schema(state))
    assert registry(text)["room_stay"] == [["stay-istanbul", ["shared-room-token"]],
        ["stay-antalya", ["shared-room-token"]]]


def test_exhausted_creation_capacity_does_not_invent_new_people():
    state = TripState.model_validate({"travelers": [{"id": f"owned-{i}", "type": "adult"}
        for i in range(MVP_LIMITS["travelers"])]})
    text, meta = hints(build_operation_schema(state))
    assert registry(text)["new_people"] == []
    assert "add_traveler" not in meta["operation_types"]


def test_legacy_constraint_and_preference_keys_remain_exact_dynamic_enums():
    state = sample_trip().model_dump(mode="json")
    state["constraints"].append({"key": "legacy-accessibility", "value": None, "hard": True})
    state["preferences"].append({"key": "legacy-preference", "value": "known", "weight": 0.3})
    text, _ = hints(build_operation_schema(state))
    ids = registry(text)
    assert ids["scalar_keys"] == ["legacy-accessibility"]
    assert "legacy-preference" in ids["preference_keys"]
    assert "$scalar_keys+bool|string|number|null" in text


@pytest.mark.parametrize("keys", [[], ["breakfast"], ["pets"], ["nonstop"]])
def test_legacy_scalar_branch_cannot_be_empty_or_overlap_reserved_keys(keys):
    state = sample_trip().model_dump(mode="json")
    state["constraints"].append({"key": "legacy-accessibility", "value": None, "hard": True})
    schema = unintern(build_operation_schema(state))
    value = variants(schema, "set_constraint")[0]["properties"]["value"]
    value["oneOf"][-1]["properties"]["key"]["enum"] = keys
    with pytest.raises(GroqTransportError):
        hints(schema)


@pytest.mark.parametrize("kind", sorted(OPERATION_TYPES))
def test_future_unknown_value_field_fails_closed_before_any_partial_hints(kind):
    schema = unintern(build_operation_schema(sample_trip()))
    value = variants(schema, kind)[0]["properties"]["value"]
    value["properties"]["future_unsupported_field"] = {"type": "string"}
    with pytest.raises(GroqTransportError, match="unreviewed_operation_value_schema"):
        hints(schema)


@pytest.mark.parametrize("mutation", ["required", "nullable", "bounds", "enum", "condition", "nested_field"])
def test_changed_value_rules_not_silently_projected(mutation):
    schema = unintern(build_operation_schema(sample_trip()))
    budget = variants(schema, "set_budget")[0]["properties"]["value"]
    if mutation == "required":
        budget["required"].append("currency")
    elif mutation == "nullable":
        budget["properties"]["currency"] = {"anyOf": [budget["properties"]["currency"], {"type": "null"}]}
    elif mutation == "bounds":
        budget["properties"]["amount_minor"]["minimum"] = 0
    elif mutation == "enum":
        mode = variants(schema, "set_search_policy")[0]["properties"]["value"]
        mode["properties"]["mode"]["enum"].append("FUTURE_LIVE")
    elif mutation == "condition":
        constraint = variants(schema, "set_constraint")[0]["properties"]["value"]
        constraint["allOf"].pop()
    elif mutation == "nested_field":
        stay = variants(schema, "upsert_stay")[0]["properties"]["value"]
        stay["properties"]["rooms"]["items"]["properties"]["new_field"] = {"type": "string"}
    with pytest.raises(GroqTransportError, match="unreviewed_operation_value_schema"):
        hints(schema)


def test_inconsistent_member_alias_registry_is_rejected():
    schema = unintern(build_operation_schema(sample_trip()))
    stay = variants(schema, "upsert_stay")[0]["properties"]["value"]
    stay["properties"]["rooms"]["items"]["properties"]["traveler_ids"]["items"]["enum"].append("foreign-person")
    with pytest.raises(GroqTransportError, match="inconsistent_hint_reference_registry"):
        hints(schema)


def test_second_creation_variant_cannot_hide_extra_aliases_behind_first_variant():
    schema = unintern(build_operation_schema(sample_trip()))
    extra = deepcopy(variants(schema, "add_traveler")[0])
    extra["properties"]["value"]["properties"]["id"]["enum"] = ["review-only-new-person"]
    schema["properties"]["operations"]["items"]["oneOf"].append(extra)
    with pytest.raises(GroqTransportError, match="unreviewed_multi_variant_operation"):
        hints(schema)


def test_room_stay_const_not_owned_is_rejected_even_if_wire_target_is_valid():
    schema = unintern(build_operation_schema(sample_trip()))
    variants(schema, "set_room_members")[0]["properties"]["value"]["properties"]["stay_id"]["const"] = "foreign-stay"
    with pytest.raises(GroqTransportError, match="invalid_room_stay_binding"):
        hints(schema)


def test_changed_city_set_fails_closed_without_repeating_geography_list_in_hints():
    schema = unintern(build_operation_schema(sample_trip()))
    value = variants(schema, "set_origin")[0]["properties"]["value"]
    value["properties"]["city"]["enum"].append("Future unsupported destination")
    with pytest.raises(GroqTransportError, match="unreviewed_hint_city_enum"):
        hints(schema)


def test_route_hint_preserves_null_for_non_open_jaw_and_omission_for_all():
    text, _ = hints()
    line = next(line for line in text.splitlines() if line.startswith("set_route_policy:"))
    assert "open_jaw->known-city|null;other kinds->null" in line
    assert "end_city!" not in line


@pytest.mark.parametrize("bad", [None, {}, [], {"$ref": "https://example.com/remote"},
    {"$defs": {"loop": {"$ref": "#/$defs/loop"}}, "$ref": "#/$defs/loop"}])
def test_missing_malformed_remote_and_cyclic_schema_fail_closed(bad):
    with pytest.raises(GroqTransportError):
        build_compact_operation_hints(bad)


def test_original_validator_still_rejects_null_defaults_and_unlisted_room_members():
    schema = build_operation_schema(sample_trip())
    text, _ = hints(schema)
    envelope = {"operations": [{"type": "set_budget", "target_id": None,
        "value": {"amount_minor": 20000, "currency": None}}], "control": {"action": "none"},
        "reply": "proposal", "unknowns": [], "conflicts": []}
    assert not Draft202012Validator(schema).is_valid(envelope)
    envelope["operations"][0]["value"].pop("currency")
    assert Draft202012Validator(schema).is_valid(envelope)
    assert "null only where listed" in text
