import json
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator
from test_each_city_room_scope import before

from otravel.domain import DomainError, Operation, TripState, apply_operations
from otravel.groq_place_prompt import build_place_operation_hints
from otravel.groq_transport import build_compact_transport_schema, decode_compact_transport_response
from otravel.llm_contract import build_operation_schema


def scenario():
    data = before()
    data["stays"][0]["rooms"][0]["id"] = "new-room-1"
    data["stays"][1]["rooms"][0]["id"] = "new-room-2"
    state = TripState.model_validate(data)
    operations = [{"type": "upsert_stay", "target_id": "stay-first", "value": {"rooms": [
        {"id": "new-room-1", "traveler_ids": ["adult-a", "child-old"]},
        {"id": "new-room-3", "traveler_ids": ["adult-b"]}]}},
        {"type": "set_room_members", "target_id": "new-room-3",
            "value": {"stay_id": "stay-first", "traveler_ids": ["adult-b"]}}]
    envelope = {"operations": operations, "control": {"action": "continue"}, "reply": "Предлагаю расселение.",
        "unknowns": [], "conflicts": [], "speech_act": "CHANGE", "information_subject": None}
    return state, envelope


def test_v3_accepts_created_same_batch_room_and_atomic_domain_applies_it():
    state, envelope = scenario()
    before_state = state.model_dump(mode="json")
    schema = build_operation_schema(state, dynamic_places=True)
    assert Draft202012Validator(schema).is_valid(envelope)
    after, changes = apply_operations(state, [Operation.model_validate(op) for op in envelope["operations"]])
    assert len(after.stays[0].rooms) == 2 and after.stays[0].rooms[1].traveler_ids == ["adult-b"]
    assert after.stays[1] == state.stays[1] and changes and state.model_dump(mode="json") == before_state


def test_strict_v3_wire_accepts_and_decodes_the_future_room_reference():
    state, canonical = scenario()
    schema = build_operation_schema(state, dynamic_places=True)
    wire = deepcopy(canonical)
    wire["operations"] = [{"op": op["type"] + "@" + op["target_id"],
        "value_json": json.dumps(op["value"], ensure_ascii=False)} for op in canonical["operations"]]
    assert Draft202012Validator(build_compact_transport_schema(schema, allow_dynamic_places=True)).is_valid(wire)
    assert decode_compact_transport_response(json.dumps(wire), schema, allow_dynamic_places=True) == canonical
    hints, _ = build_place_operation_hints(schema, state)
    assert "SAME_BATCH_ROOM_ALIASES" in hints


@pytest.mark.parametrize("failure", ["not_created", "wrong_stay", "before_creation"])
def test_candidate_alias_does_not_authorize_missing_room_or_another_stay(failure):
    state, envelope = scenario()
    snapshot = state.model_dump(mode="json")
    if failure == "not_created": envelope["operations"] = envelope["operations"][1:]
    if failure == "wrong_stay": envelope["operations"][1]["value"]["stay_id"] = "stay-second"
    if failure == "before_creation": envelope["operations"].reverse()
    assert Draft202012Validator(build_operation_schema(state, dynamic_places=True)).is_valid(envelope)
    with pytest.raises(DomainError):
        apply_operations(state, [Operation.model_validate(op) for op in envelope["operations"]])
    assert state.model_dump(mode="json") == snapshot


def test_foreign_existing_room_is_not_allowed_as_a_different_stays_target():
    state, envelope = scenario()
    envelope["operations"] = [{"type": "set_room_members", "target_id": "new-room-1",
        "value": {"stay_id": "stay-second", "traveler_ids": ["adult-a"]}}]
    assert not Draft202012Validator(build_operation_schema(state, dynamic_places=True)).is_valid(envelope)


def test_legacy_contract_still_rejects_future_room_setter_before_new_v3_extension():
    state, envelope = scenario()
    assert not Draft202012Validator(build_operation_schema(state)).is_valid(envelope)
