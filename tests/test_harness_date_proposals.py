"""H004-DATE-TARGET-001: persisted date flow, controlled model, isolated PG.

These are deterministic harness/transaction checks, not actual LLM NLU scores.
The shared factory injects NoTravelHTTP; no supplier/model HTTP is permitted.
"""

from copy import deepcopy
from uuid import uuid4

import pytest
from test_api import StubLLM, _headers, _session, _snapshot, _wait
from test_api import api_factory as date_api_factory
from test_slot_continuation import _message

from otravel.domain import apply_operations, sample_trip

api_factory = date_api_factory


@pytest.mark.asyncio
async def test_date_proposal_is_durable_and_exact_stay_change_clears_it_in_same_revision(api_factory):
    model = StubLLM()
    async with api_factory(model) as (client, _):
        sid, token, initial = await _session(client, sample_trip().model_dump(mode="json"))
        _, proposed = await _message(client, sid, token, "Выезд 14.11.2026")
        proposal = proposed["pending_question"]["context"]["date_proposal"]
        assert proposed["trip"] == initial["trip"]
        assert proposal["resolved_date"] == "2026-11-14"
        assert proposal["based_state_version"] == proposed["state_version"]
        assert proposal["trip_id"] == initial["trip"]["trip_id"]
        assert proposal["candidate_stay_ids"] == ["stay-istanbul", "stay-antalya"]
        repository = client._transport.app.state.store
        persisted = await repository.get_session(sid)
        assert persisted["pending_question"]["context"]["date_proposal"] == proposal
        _, after = await _message(client, sid, token, "Анталья")
        expected = deepcopy(initial["trip"])
        expected["state_version"] += 1
        expected["stays"][1]["check_out"] = "2026-11-14"
        expected["legs"][-1]["departure_date"] = "2026-11-14"
        assert after["trip"] == expected
        assert after["state_version"] == proposed["state_version"] + 1
        durable = await repository.get_session(sid)
        assert durable["state"] == expected
        assert "date_proposal" not in (durable.get("pending_question") or {}).get("context", {})
        assert not model.calls


@pytest.mark.asyncio
async def test_selected_date_collision_is_completed_question_without_partial_write_or_model(api_factory):
    model = StubLLM()
    async with api_factory(model) as (client, _):
        sid, token, initial = await _session(client, sample_trip().model_dump(mode="json"))
        _, proposed = await _message(client, sid, token, "Выезд 14.11.2026")
        _, collided = await _message(client, sid, token, "Стамбул")
        assert collided["trip"] == initial["trip"]
        assert collided["state_version"] == initial["state_version"]
        assert collided["waiting_reason"] == "clarification"
        assert "пересекается" in collided["pending_question"]["text"]
        assert collided["pending_question"]["context"]["date_proposal"] == proposed["pending_question"]["context"]["date_proposal"]
        _, resolved = await _message(client, sid, token, "2")
        assert resolved["trip"]["stays"][0] == initial["trip"]["stays"][0]
        assert resolved["trip"]["stays"][1]["check_out"] == "2026-11-14"
        assert not model.calls


@pytest.mark.asyncio
async def test_pause_before_date_finish_cas_rejects_result_without_resurrecting_state(api_factory, monkeypatch):
    model = StubLLM()
    async with api_factory(model) as (client, _):
        sid, token, initial = await _session(client, sample_trip().model_dump(mode="json"))
        await _message(client, sid, token, "Выезд 14.11.2026")
        repository = client._transport.app.state.store
        finish = repository.finish_run
        paused = False

        async def pause_then_finish(run_id, expected_version, state, *args, **kwargs):
            nonlocal paused
            if state is not None and not paused:
                paused = True
                await repository.set_control(sid, "pause")
            return await finish(run_id, expected_version, state, *args, **kwargs)

        monkeypatch.setattr(repository, "finish_run", pause_then_finish)
        accepted = await client.post(f"/api/v1/sessions/{sid}/messages", headers=_headers(token), json={
            "client_message_id": str(uuid4()), "expected_version": initial["state_version"], "text": "Анталья"})
        assert accepted.status_code == 202
        run = await _wait(client, accepted.json()["run_id"], token)
        assert paused and run["status"] == "conflict"
        after = await _snapshot(client, sid, token)
        expected = deepcopy(initial["trip"])
        expected["state_version"] += 1  # Pause intentionally invalidates stale work.
        assert after["trip"] == expected
        assert after["waiting_reason"] == "user_pause"
        assert after["pending_question"] is None
        assert (await repository.get_session(sid))["active_run_id"] is None
        assert not model.calls


@pytest.mark.asyncio
async def test_older_date_proposal_cannot_apply_after_another_domain_revision(api_factory):
    model = StubLLM()
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, sample_trip().model_dump(mode="json"))
        _, proposed = await _message(client, sid, token, "Выезд 14.11.2026")
        repository = client._transport.app.state.store
        stored = await repository.get_session(sid)
        # A controlled independent domain transaction deliberately retains the
        # old question, reproducing stale metadata without changing its bytes.
        state, changes = apply_operations(sample_trip(), [{"type": "set_preference", "value": {
            "key": "price_priority", "value": "lowest_total"}}])
        run, _ = await repository.intake_message(sid, str(uuid4()), "controlled concurrent revision", stored["state_version"])
        assert await repository.claim_run(run["run_id"])
        await repository.finish_run(run["run_id"], stored["state_version"], state.model_dump(mode="json"),
            "WAITING_USER", "Контролируемая правка сохранена.", pending_question=stored["pending_question"],
            waiting_reason="clarification", changes=changes)
        before = await _snapshot(client, sid, token)
        assert before["state_version"] == proposed["state_version"] + 1
        _, after = await _message(client, sid, token, "Анталья")
        assert after["trip"] == before["trip"] and after["state_version"] == before["state_version"]
        assert after["waiting_reason"] == "clarification"
        assert "заново" in after["pending_question"]["text"]
        assert "date_proposal" not in after["pending_question"]["context"]
        assert not model.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["Анталья?", "не первая", "вторая и бюджет 100000"])
async def test_ambiguous_target_reply_keeps_owned_question_and_never_calls_model(api_factory, text):
    model = StubLLM()
    async with api_factory(model) as (client, _):
        sid, token, initial = await _session(client, sample_trip().model_dump(mode="json"))
        _, proposed = await _message(client, sid, token, "Выезд 14.11.2026")
        _, after = await _message(client, sid, token, text)
        assert after["trip"] == initial["trip"]
        assert after["pending_question"]["target_id"] is None
        assert after["pending_question"]["context"]["date_proposal"] == proposed["pending_question"]["context"]["date_proposal"]
        assert not model.calls


@pytest.mark.asyncio
async def test_unrelated_preference_can_advance_to_origin_without_old_date_hijacking(api_factory):
    initial = sample_trip().model_dump(mode="json")
    initial["origin"] = None
    model = StubLLM([{"operations": [{"type": "set_preference", "value": {
        "key": "price_priority", "value": "lowest_total"}}], "unknowns": ["origin.city"]}])
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, initial)
        await _message(client, sid, token, "Выезд 14.11.2026")
        _, origin_question = await _message(client, sid, token, "Хочу минимальную стоимость")
        assert origin_question["trip"]["preferences"] == [{"key": "price_priority", "value": "lowest_total", "weight": 1.0}]
        assert origin_question["pending_question"]["field"] == "origin.city"
        assert "date_proposal" not in origin_question["pending_question"]["context"]
        _, after = await _message(client, sid, token, "Москва")
        assert after["trip"]["origin"] == "Москва"
        assert after["trip"]["stays"] == created["trip"]["stays"]
        assert len(model.calls) == 1  # Only the unrelated preference uses the controlled model.
