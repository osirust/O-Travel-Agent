from copy import deepcopy

import pytest
from test_api import StubLLM, _plans, _session
from test_api import api_factory as return_api_factory
from test_slot_continuation import _message

from otravel.clarification_policy import fulfilled_return_clarifications
from otravel.domain import Operation, apply_operations, sample_trip

api_factory = return_api_factory
FLAG = "return_flight_date_change"


def change():
    before = sample_trip()
    operation = Operation(type="shift_return", value={"days": 2})
    after, _ = apply_operations(before, [operation])
    return before, after, operation


def test_completed_backend_return_change_resolves_only_the_model_capability_flag():
    before, after, operation = change()
    snapshots = deepcopy((before, after, operation))
    unknowns, notes = fulfilled_return_clarifications(before, after, [operation], [FLAG, "visa_requirements"])
    assert unknowns == ["visa_requirements"] and len(notes) == 1
    assert notes[0]["source"] == "applied_domain_operation" and not notes[0]["booking_consent_granted"]
    assert (before, after, operation) == snapshots


@pytest.mark.parametrize("condition", ["no_change", "no_operation", "different_date", "supplier_availability", "server_question", "one_way"])
def test_known_date_or_unrelated_question_is_not_proof_of_completion(condition):
    before, after, operation = change()
    operations, unknowns, trusted = [operation], [FLAG], []
    if condition == "no_change": after = before.model_copy(deep=True)
    if condition == "no_operation": operations = []
    if condition == "different_date": operations = [Operation(type="shift_return", value={"days": 3})]
    if condition == "supplier_availability": unknowns = ["return_flight_availability"]
    if condition == "server_question": trusted = [FLAG]
    if condition == "one_way": after.route_policy.kind = "one_way"
    remaining, notes = fulfilled_return_clarifications(before, after, operations, unknowns, server_questions=trusted)
    assert remaining == unknowns and notes == []


def test_explicit_last_checkout_edit_can_prove_the_synced_return_date():
    before, after, _ = change()
    operation = Operation(type="upsert_stay", target_id=before.stays[-1].id,
        value={"check_out": after.stays[-1].check_out.isoformat()})
    remaining, notes = fulfilled_return_clarifications(before, after, [operation], [FLAG])
    assert remaining == [] and len(notes) == 1


@pytest.mark.asyncio
async def test_applied_date_change_does_not_leave_phantom_question_or_stop_search(api_factory):
    before, after, _ = change()
    operation = {"type": "upsert_stay", "target_id": before.stays[-1].id,
        "value": {"check_out": after.stays[-1].check_out.isoformat()}}
    model = StubLLM([{"operations": [operation], "control": {"action": "continue"}, "speech_act": "CHANGE",
        "unknowns": [FLAG], "conflicts": [], "reply": "Предлагаю продлить отель и перенести возвращение."}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, before.model_dump(mode="json"))
        run, saved = await _message(client, sid, token, "Продли последний отель и перенеси возвращение на два дня.")
        assert saved["trip"]["legs"][-1]["departure_date"] == after.legs[-1].departure_date.isoformat()
        assert _plans(run) and saved.get("pending_question") is None
        assert len(model.calls) == 1
        assert any(row["event_type"] == "MODEL_CLARIFICATION_RESOLVED"
            for row in await client._transport.app.state.store.list_events(sid))
