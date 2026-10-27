"""Cached NLU + real isolated PG: a pause must not erase a new clarification.

Only this narrow interaction is tested. Model/travel HTTP are replaced; no
supplier information or broad language understanding is qualified here.
"""
import asyncio
import json
from uuid import uuid4

import pytest
from test_api import StubLLM, _headers, _session, _snapshot, _wait
from test_api import api_factory as pause_api_factory
from test_slot_continuation import _message

api_factory = pause_api_factory
FIRST = "Стамбул в Сербии. Из Москвы. Еду один взрослый без детей, общий бюджет 90000 рублей."


def intake(*, wait=False):
    return {"operations": [{"type": "set_origin", "value": {"city": "Москва"}},
        {"type": "add_traveler", "value": {"id": "adult", "type": "adult"}},
        {"type": "set_budget", "value": {"amount_minor": 9_000_000, "currency": "RUB"}},
        {"type": "upsert_stay", "target_id": "new-stay-1", "value": {"city": "Стамбул"}}],
        "control": {"action": "wait" if wait else "none"}}


async def no_external(client, sid):
    store = client._transport.app.state.store
    events = await store.list_events(sid)
    assert not any(event["event_type"] in {"TOOL_CALL_STARTED", "LIVE_SEARCH_STARTED"} for event in events)
    assert await store.list_orders(sid) == []


def assert_conflict_pending(snapshot):
    pending = snapshot.get("pending_question")
    assert pending is not None, "The country-consent question created before pause must survive explicit resume"
    context = pending.get("context") or {}
    assert context.get("requested_destination", {}).get("country_code") == "RS", json.dumps(pending, ensure_ascii=False)
    assert context.get("proposed_destination", {}).get("country_code") == "TR"
    assert context.get("confirmation_required") is True
    assert all(word in pending["text"] for word in ("Стамбул", "Турц"))


@pytest.mark.asyncio
async def test_country_conflict_without_pause_produces_the_required_owned_consent_packet(api_factory):
    model = StubLLM([intake()])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, {"search_policy": {"mode": "DEMO"}})
        _, snapshot = await _message(client, sid, token, FIRST)
        assert snapshot["trip"]["origin"] == "Москва"
        assert snapshot["trip"]["stays"] == [] and len(snapshot["trip"]["travelers"]) == 1
        assert_conflict_pending(snapshot)
        assert len(model.calls) == 1
        await no_external(client, sid)


@pytest.mark.asyncio
@pytest.mark.parametrize("queued_budget", [False, True])
async def test_new_country_conflict_with_pause_survives_resume_and_unrelated_queued_addition(api_factory, queued_budget):
    answers = [intake(wait=True)]
    if queued_budget:
        answers.append({"operations": [{"type": "set_budget", "value": {"amount_minor": 8_000_000, "currency": "RUB"}}]})
    model = StubLLM(answers)
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, {"search_policy": {"mode": "DEMO"}})
        first, paused = await _message(client, sid, token, FIRST + " Подожди, я еще дополню.")
        assert paused["waiting_reason"] == "user_pause"
        assert paused["trip"]["origin"] == "Москва" and paused["trip"]["budget"]["amount_minor"] == 9_000_000
        assert len(paused["trip"]["travelers"]) == 1 and paused["trip"]["stays"] == []
        deferred = first["result"]["result"].get("deferred_questions", [])
        assert any("Стамбул" in question and "Турц" in question for question in deferred)
        assert len(model.calls) == 1
        await no_external(client, sid)
        accepted = None
        if queued_budget:
            accepted = await client.post(f"/api/v1/sessions/{sid}/messages", headers=_headers(token), json={
                "client_message_id": str(uuid4()), "expected_version": paused["state_version"],
                "text": "Общий бюджет установи 80000 рублей."})
            assert accepted.status_code == 202
            assert (await _snapshot(client, sid, token))["trip"] == paused["trip"]
            assert len(model.calls) == 1
        await _message(client, sid, token, "продолжай")
        if accepted:
            assert (await _wait(client, accepted.json()["run_id"], token))["status"] == "completed"
        await asyncio.sleep(0.02)
        resumed = await _snapshot(client, sid, token)
        expected_budget = 8_000_000 if queued_budget else 9_000_000
        assert resumed["trip"]["budget"]["amount_minor"] == expected_budget
        for field in ("origin", "travelers", "groups", "stays", "legs", "preferences", "constraints", "route_policy"):
            assert resumed["trip"][field] == paused["trip"][field]
        assert len(model.calls) == (2 if queued_budget else 1)
        await no_external(client, sid)
        diagnostic = {"pause_pending_present": paused.get("pending_question") is not None,
            "deferred_question_count": len(deferred), "resumed_pending": resumed.get("pending_question"),
            "resumed_waiting_reason": resumed.get("waiting_reason"), "queued_budget": queued_budget,
            "model_stub_calls": len(model.calls), "state_facts_preserved": True, "tool_calls": 0}
        assert resumed.get("pending_question") is not None, json.dumps(diagnostic, ensure_ascii=False)
        assert_conflict_pending(resumed)


@pytest.mark.asyncio
async def test_existing_country_conflict_survives_ui_pause_resume_and_owned_yes_without_new_model(api_factory):
    model = StubLLM([intake()])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, {"search_policy": {"mode": "DEMO"}})
        _, before = await _message(client, sid, token, FIRST)
        assert_conflict_pending(before)
        pause = await client.post(f"/api/v1/sessions/{sid}/control", headers=_headers(token), json={
            "action": "pause", "expected_version": before["state_version"]})
        assert pause.status_code == 200
        paused = await _snapshot(client, sid, token)
        assert paused["waiting_reason"] == "user_pause" and len(model.calls) == 1
        for field in ("origin", "travelers", "groups", "stays", "legs", "budget", "preferences", "constraints", "route_policy"):
            assert paused["trip"][field] == before["trip"][field]
        await no_external(client, sid)
        resume = await client.post(f"/api/v1/sessions/{sid}/control", headers=_headers(token), json={
            "action": "resume", "expected_version": paused["state_version"]})
        assert resume.status_code == 200
        await asyncio.sleep(0.02)
        resumed = await _snapshot(client, sid, token)
        for field in ("origin", "travelers", "groups", "stays", "legs", "budget", "preferences", "constraints", "route_policy"):
            assert resumed["trip"][field] == before["trip"][field]
        assert_conflict_pending(resumed)
        assert resumed["pending_question"]["state_version"] == resumed["state_version"]
        assert len(model.calls) == 1
        _, accepted = await _message(client, sid, token, "Да")
        assert len(accepted["trip"]["stays"]) == 1 and accepted["trip"]["stays"][0]["city"] == "Стамбул"
        for field in ("origin", "travelers", "groups", "budget", "preferences", "constraints", "route_policy"):
            assert accepted["trip"][field] == before["trip"][field]
        assert len(model.calls) == 1, "An owned confirmation must not call the model again"
        await no_external(client, sid)
