"""Opt-in schema/wire v3. Pure declared facts; no model, database or HTTP."""
import hashlib
import json
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator
from test_place_refs import place

from otravel.domain import DomainError, Operation, TripState, apply_operations, sample_trip
from otravel.groq_place_prompt import build_place_operation_hints
from otravel.groq_prompt import build_compact_operation_hints
from otravel.groq_transport import (
    GroqTransportError,
    _compile,
    build_compact_transport_schema,
    build_transport_schema,
    decode_compact_transport_response,
    decode_transport_response,
)
from otravel.llm_contract import OPERATION_TYPES, build_operation_schema


def state_with_refs():
    a, b = place(1, "Кордова", "AR"), place(2, "Кордова", "ES", timezone="Europe/Madrid")
    state = sample_trip()
    return apply_operations(state, [], resolved_places={a.place_id: a, b.place_id: b})[0]


def envelope(operations):
    return {"operations": operations, "control": {"action": "continue"}, "reply": "Уточняю город.",
        "unknowns": [], "conflicts": [], "speech_act": "CHANGE", "information_subject": None}


def canonical(kind="resolve_place", value=None, target=None):
    return {"type": kind, "target_id": target, "value": value if value is not None else {
        "role": "origin", "kind": "city", "query": "Буэнос-Айрес", "stay_id": None}}


def wire(operation, version):
    result = envelope([])
    if version == 1:
        result["operations"] = [{"type": operation["type"], "target_id": operation["target_id"],
            "value_json": json.dumps(operation["value"], ensure_ascii=False)}]
    else:
        result["operations"] = [{"op": operation["type"] if operation["target_id"] is None else
            operation["type"] + "@" + operation["target_id"],
            "value_json": json.dumps(operation["value"], ensure_ascii=False)}]
    return json.dumps(result, ensure_ascii=False)


def sha(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.mark.parametrize("name,state,expected", [
    ("empty", TripState(trip_id="legacy"), [
        "2abe67f2be73fe963c51140a6b16436bd91e448d6b11e7dfece5bbd103fd9996",
        "56d4b06fe4c1b27f9b13d9aad9891b0e4e6c0199d16cffa62243c84c35bd0403",
        "8997e2e8271cbd6c9c3199c607c13c0cce20ab85025abf3e667e10718a7ec262",
        "3e4f0b9ac5abee8f61fafdbb484e8242303617a54365a7c842f3c4563df44772",
        "afdbc8f8fa8985f45cc4adc84aef0ca4f63cb4a1148d318b30ead4220b70657c"]),
    ("sample", sample_trip(), [
        "253a7b20fc4f4d60264f29a03d90ddf5566112ebfcb9c3a16368f59c5b437066",
        "dc7800d497df9f0d504966f46a3a6acd74870831f242a2d25e372110a7b1fd13",
        "8a17c6a142e6cf9112845f78d6bb390f602a27c6b47d418e50e79ad8b89b9bf7",
        "e070c2ef60d05d88ba2f792de6e51c95bc686d2fec1f2c8229a2ed4b201a3ee8",
        "2c7d5eb1dadcf9f57cc6f7d71e1dddad16ec9db8539d2d6ec55b2ef9db388efb"]),
])
def test_legacy_default_schema_wire1_wire2_hints_and_metadata_are_byte_pinned(name, state, expected):
    schema = build_operation_schema(state)
    text, meta = build_compact_operation_hints(schema)
    assert [sha(schema), sha(build_transport_schema(schema)), sha(build_compact_transport_schema(schema)),
        sha(text), sha(meta)] == expected
    assert build_operation_schema(state, dynamic_places=False) == schema
    assert "resolve_place" not in OPERATION_TYPES


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("nullable", [False, True])
def test_resolve_place_roundtrip_preserves_optional_null_and_is_opt_in_only(version, nullable):
    schema = build_operation_schema(state_with_refs(), dynamic_places=True)
    operation = canonical()
    if nullable:
        operation["value"]["country_iso2"] = None
    encode = wire(operation, version)
    builder, decoder = (build_transport_schema, decode_transport_response) if version == 1 else (build_compact_transport_schema, decode_compact_transport_response)
    with pytest.raises(GroqTransportError):
        builder(schema)
    with pytest.raises(GroqTransportError):
        decoder(encode, schema)
    builder(schema, allow_dynamic_places=True)
    result = decoder(encode, schema, allow_dynamic_places=True)
    assert result == envelope([operation])


@pytest.mark.parametrize("role,stay_id,valid", [
    ("origin", None, True), ("route_end", None, True),
    ("destination", "stay-istanbul", True), ("destination", "new-stay-1", True),
    ("destination", None, False), ("destination", "invented-stay", False),
    ("origin", "stay-istanbul", False), ("route_end", "new-stay-1", False),
    ("nationality", None, False),
])
def test_resolution_role_owns_exact_stay_scope_and_never_domain_mutates(role, stay_id, valid):
    state = state_with_refs()
    schema = build_operation_schema(state, dynamic_places=True)
    op = canonical(value={"role": role, "kind": "country", "query": "Аргентина", "stay_id": stay_id})
    assert Draft202012Validator(schema).is_valid(envelope([op])) is valid
    if valid:
        with pytest.raises(DomainError) as error:
            apply_operations(state, [Operation.model_validate(op)])
        assert error.value.code == "unsupported_operation"


@pytest.mark.parametrize("patch", [
    {"query": "x"}, {"query": "x" * 161}, {"query": None},
    {"country_iso2": "Argentina"}, {"country_iso2": "ar"}, {"kind": "airport"},
    {"provider_id": "invented"}, {"snapshot": {}}, {"place_id": "geo:openmeteo:1"},
])
def test_resolution_query_is_bounded_and_cannot_forge_provider_or_snapshot_fields(patch):
    schema = build_operation_schema(state_with_refs(), dynamic_places=True)
    op = canonical(); op["value"].update(patch)
    assert not Draft202012Validator(schema).is_valid(envelope([op]))


def test_resolution_requires_null_envelope_target_and_explicit_stay_scope():
    schema = build_operation_schema(state_with_refs(), dynamic_places=True)
    for op in (canonical(target="stay-istanbul"), {"type": "resolve_place", "value": canonical()["value"]}):
        assert not Draft202012Validator(schema).is_valid(envelope([op]))
    op = canonical(); del op["value"]["stay_id"]
    assert not Draft202012Validator(schema).is_valid(envelope([op]))


def test_dynamic_fields_allow_only_trusted_ids_and_preserve_homonymous_choices():
    state = state_with_refs()
    schema = build_operation_schema(state, dynamic_places=True)
    for key in state.location_refs:
        assert Draft202012Validator(schema).is_valid(envelope([canonical("set_origin", {"city": "Кордова", "place_id": key})]))
    op = canonical("set_origin", {"city": "Кордова", "place_id": "geo:openmeteo:999"})
    assert not Draft202012Validator(schema).is_valid(envelope([op]))
    assert not Draft202012Validator(build_operation_schema(state)).is_valid(envelope([canonical("set_origin", {"city": "Кордова"})]))
    # Full domain checks still own the relation between a name and a reference.
    decoded = decode_compact_transport_response(wire(canonical("set_origin", {"city": "Москва", "place_id": "geo:openmeteo:1"}), 2), schema, allow_dynamic_places=True)
    with pytest.raises(DomainError):
        apply_operations(state, [Operation.model_validate(decoded["operations"][0])])


def test_ref_pair_and_non_open_endpoint_rules_match_domain_constraints():
    schema = build_operation_schema(state_with_refs(), dynamic_places=True)
    bad = [canonical("upsert_stay", {"place_id": "geo:openmeteo:1"}, "stay-istanbul"),
        canonical("upsert_stay", {"place_id": None}, "stay-istanbul"),
        canonical("set_route_policy", {"kind": "one_way", "end_place_id": "geo:openmeteo:1"}),
        canonical("set_route_policy", {"kind": "open_jaw", "end_place_id": "geo:openmeteo:1"})]
    assert all(not Draft202012Validator(schema).is_valid(envelope([op])) for op in bad)


@pytest.mark.parametrize("initial", [TripState(trip_id="empty"), sample_trip()])
def test_v3_guidance_without_refs_still_verifies_query_grammar_and_retains_old_fingerprint(initial):
    schema = build_operation_schema(initial, dynamic_places=True)
    text, meta = build_place_operation_hints(schema, initial)
    assert "PLACE_REFS=[]" in text and "target_id!:null" in text
    assert meta["trusted_place_reference_count"] == 0
    assert meta["legacy_projection"]["reviewed_city_enum_sha256"] == "7d429976f57f4837e90ff5ef10df22bf5adfd3f35597ca5107d338bd5bfb0f48"


def test_v3_guidance_uses_exact_trusted_ref_names_and_ids_not_full_snapshots():
    state = state_with_refs()
    schema = build_operation_schema(state, dynamic_places=True)
    text, meta = build_place_operation_hints(schema, state)
    assert "geo:openmeteo:1" in text and "geo:openmeteo:2" in text
    assert "Кордова" in text
    assert "latitude" not in text and "source_fact_sha256" not in text and "provider_bindings" not in text
    assert meta["trusted_place_reference_count"] == 2
    assert meta["hints_sha256"] == sha(text)
    assert not meta["projection_equivalent_json_schema"]


def test_a_different_trusted_snapshot_does_not_authorize_another_schema():
    state = state_with_refs()
    with pytest.raises(GroqTransportError) as error:
        build_place_operation_hints(build_operation_schema(state, dynamic_places=True), sample_trip())
    assert error.value.code == "untrusted_dynamic_place_schema"


@pytest.mark.parametrize("edit", ["new_name", "new_id", "query_min", "query_max", "extra_property", "extra_kind"])
def test_untrusted_dynamic_schema_changes_are_not_projected_away(edit):
    state = state_with_refs()
    schema = build_operation_schema(state, dynamic_places=True)
    original, _, resolve = _compile(schema, allow_dynamic_places=True)
    # Expand safely so targeted mutations do not depend on schema interning.
    from otravel.groq_place_prompt import _expanded
    changed = _expanded(original, resolve)
    variants = changed["properties"]["operations"]["items"]["oneOf"]
    resolver = next(v["properties"]["value"] for v in variants if v["properties"]["type"]["const"] == "resolve_place")
    origin = next(v["properties"]["value"] for v in variants if v["properties"]["type"]["const"] == "set_origin")
    if edit == "new_name": origin["properties"]["city"]["enum"].append("SECRET_UNTRUSTED_NAME")
    elif edit == "new_id": origin["properties"]["place_id"]["anyOf"][0]["enum"].append("SECRET_UNTRUSTED_ID")
    elif edit == "query_min": resolver["properties"]["query"]["minLength"] = 1
    elif edit == "query_max": resolver["properties"]["query"]["maxLength"] = 999999
    elif edit == "extra_property": resolver["properties"]["snapshot"] = {"type": "string"}
    else: resolver["properties"]["kind"]["enum"].append("airport")
    with pytest.raises(GroqTransportError) as error:
        build_place_operation_hints(changed, state)
    assert "SECRET_UNTRUSTED" not in str(error.value) and "SECRET_UNTRUSTED" not in str(error.value.details)


def test_static_query_fingerprint_rejects_even_a_changed_schema_builder(monkeypatch):
    import otravel.groq_place_prompt as module
    state = state_with_refs()
    schema = build_operation_schema(state, dynamic_places=True)
    original, _, resolve = _compile(schema, allow_dynamic_places=True)
    changed = module._expanded(original, resolve)
    for variant in changed["properties"]["operations"]["items"]["oneOf"]:
        if variant["properties"]["type"]["const"] == "resolve_place":
            variant["properties"]["value"]["properties"]["query"]["maxLength"] = 161
    old_builder = module.build_operation_schema
    monkeypatch.setattr(module, "build_operation_schema", lambda s, **kw: deepcopy(changed) if kw.get("dynamic_places") else old_builder(s))
    with pytest.raises(GroqTransportError) as error:
        module.build_place_operation_hints(changed, state)
    assert error.value.code == "unreviewed_place_query_grammar"


@pytest.mark.parametrize("payload", [
    '{"role":"origin","kind":"city","query":"AB","query":"CD","stay_id":null}',
    '{"role":"origin","kind":"city","query":"AB","stay_id":null,"country_iso2":NaN}',
    '[]',
])
def test_compact_v3_value_parser_retains_strict_duplicate_nan_object_rules(payload):
    schema = build_operation_schema(state_with_refs(), dynamic_places=True)
    raw = envelope([]); raw["operations"] = [{"op": "resolve_place", "value_json": payload}]
    with pytest.raises(GroqTransportError):
        decode_compact_transport_response(json.dumps(raw), schema, allow_dynamic_places=True)


@pytest.mark.parametrize("flag", [1, "true", None])
def test_dynamic_flags_are_explicit_boolean_only(flag):
    with pytest.raises(ValueError):
        build_operation_schema(sample_trip(), dynamic_places=flag)
    with pytest.raises(GroqTransportError):
        build_compact_transport_schema(build_operation_schema(sample_trip()), allow_dynamic_places=flag)


@pytest.mark.parametrize("field", ["place_id", "end_place_id"])
def test_changed_builder_cannot_widen_trusted_reference_field_projection(monkeypatch, field):
    import otravel.groq_place_prompt as module
    state = state_with_refs()
    schema = build_operation_schema(state, dynamic_places=True)
    original, _, resolve = _compile(schema, allow_dynamic_places=True)
    changed = module._expanded(original, resolve)
    for variant in changed["properties"]["operations"]["items"]["oneOf"]:
        value = variant["properties"]["value"]
        if field in value.get("properties", {}):
            value["properties"][field] = {"type": "string"}
    old_builder = module.build_operation_schema
    monkeypatch.setattr(module, "build_operation_schema", lambda s, **kw: deepcopy(changed) if kw.get("dynamic_places") else old_builder(s))
    with pytest.raises(GroqTransportError) as error:
        module.build_place_operation_hints(changed, state)
    assert error.value.code == "untrusted_place_reference_enum"


def test_dynamic_selector_never_splits_owned_stay_ids_or_interprets_resolver_target():
    raw_state = state_with_refs().model_dump(mode="json")
    raw_state["stays"][0]["id"] = "owned@:/stay"
    state = TripState.model_validate(raw_state)
    schema = build_operation_schema(state, dynamic_places=True)
    op = canonical("upsert_stay", {"check_out": "2026-11-09"}, "owned@:/stay")
    decoded = decode_compact_transport_response(wire(op, 2), schema, allow_dynamic_places=True)
    assert decoded["operations"] == [op]
    resolution = canonical(value={"role": "destination", "kind": "city", "query": "Буэнос-Айрес", "stay_id": "owned@:/stay"})
    assert decode_compact_transport_response(wire(resolution, 2), schema, allow_dynamic_places=True)["operations"] == [resolution]
    bad = envelope([]); bad["operations"] = [{"op": "resolve_place@owned@:/stay", "value_json": json.dumps(resolution["value"])}]
    with pytest.raises(GroqTransportError):
        decode_compact_transport_response(json.dumps(bad), schema, allow_dynamic_places=True)


def test_dynamic_wire_keeps_canonical_operation_count_limit_and_safe_error_fields():
    schema = build_operation_schema(state_with_refs(), dynamic_places=True)
    op = canonical()
    raw = json.loads(wire(op, 2)); raw["operations"] *= 31
    with pytest.raises(GroqTransportError) as error:
        decode_compact_transport_response(json.dumps(raw), schema, allow_dynamic_places=True)
    assert error.value.code == "operation_count_limit"
    assert error.value.details == {"validation_stage": "canonical_schema"}


@pytest.mark.parametrize("fault", ["cyclic", "unknown_ref", "huge_text", "unicode"])
def test_opt_in_dynamic_compile_retains_bounded_safe_original_schema_guards(fault):
    state = state_with_refs()
    schema = build_operation_schema(state, dynamic_places=True)
    if fault == "cyclic":
        schema["properties"]["reply"] = schema
    elif fault == "unknown_ref":
        schema["properties"]["reply"] = {"$ref": "#/$defs/SECRET_UNKNOWN_REF"}
    elif fault == "huge_text":
        schema["description"] = "SECRET_LARGE_TEXT" * 20000
    else:
        schema["description"] = "SECRET" + chr(0xD800)
    with pytest.raises(GroqTransportError) as error:
        build_compact_transport_schema(schema, allow_dynamic_places=True)
    assert "SECRET" not in str(error.value) and "SECRET" not in str(error.value.details)


def test_dynamic_extension_unexpected_internal_error_is_bounded_and_does_not_echo(monkeypatch):
    import otravel.groq_transport as module
    def broken(_):
        raise RecursionError("SECRET_INTERNAL_RECURSION")
    monkeypatch.setattr(module, "_compile_dynamic_places", broken)
    with pytest.raises(GroqTransportError) as error:
        module.build_compact_transport_schema(build_operation_schema(sample_trip(), dynamic_places=True),
            allow_dynamic_places=True)
    assert error.value.code == "unsupported_dynamic_place_schema"
    assert "SECRET" not in str(error.value) and "SECRET" not in str(error.value.details)
