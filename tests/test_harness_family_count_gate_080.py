"""Captured080 independent edits while family membership remains unresolved."""
import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_api import StubLLM, _session
from test_api import api_factory as family_api_factory
from test_slot_continuation import _message

from otravel.harness import ModelResult

api_factory = family_api_factory
FIXTURE = json.loads((Path(__file__).parents[1] / "fixtures/regression/group-pending-budget-080-20261028.json").read_text(encoding="utf-8"))


async def _captured_pending(client, model, monkeypatch):
    initial = deepcopy(FIXTURE["captured_before_trip"])
    initial.pop("trip_id", None)
    initial.pop("state_version", None)
    sid, token, created = await _session(client, initial)
    harness = client._transport.app.state.harness
    original = harness._interpret
    pending = FIXTURE["captured_pending_question"]

    async def bootstrap(session, run):
        return ModelResult(operations=[], control={"action": "none"}, reply=pending["text"],
            unknowns=[pending["text"]], conflicts=[], speech_act="CHANGE",
            server_questions=[pending["text"]], pending_context={**pending["context"], "field": "group.membership"},
            response_source="contextual_slot"), None

    monkeypatch.setattr(harness, "_interpret", bootstrap)
    _, before = await _message(client, sid, token, "Контролируемая установка ранее полученного вопроса о семьях.")
    monkeypatch.setattr(harness, "_interpret", original)
    assert before["trip"] == created["trip"] and before["state_version"] == created["state_version"]
    assert before["pending_question"]["field"] == "group.membership"
    assert before["pending_question"]["context"]["membership_unknown"] is True
    assert not model.calls, "The declared setup does not exercise model NLU"
    return sid, token, before


@pytest.mark.asyncio
async def test_captured080_budget_and_preference_reach_model_and_preserve_family_question(api_factory, monkeypatch):
    model = StubLLM([FIXTURE["controlled_response"]])
    async with api_factory(model) as (client, _):
        sid, token, before = await _captured_pending(client, model, monkeypatch)
        _, after = await _message(client, sid, token, FIXTURE["user"])
        assert len(model.calls) == 1, "A pending family count cannot swallow independent current edits"
        assert after["state_version"] == before["state_version"] + 1
        assert after["trip"]["budget"]["amount_minor"] == 47000000
        assert after["trip"]["preferences"] == [{"key": "price_priority", "value": "lowest_total", "weight": 1.0}]
        for field in ("travelers", "groups", "stays", "legs", "origin", "search_policy", "route_policy"):
            assert after["trip"][field] == before["trip"][field]
        assert after["trip"]["groups"] == []
        assert after["waiting_reason"] == "clarification"
        assert after["pending_question"]["field"] == "group.membership"
        store = client._transport.app.state.store
        events = await store.list_events(sid)
        assert not any(e["event_type"] in {"TOOL_CALL_STARTED", "QUOTE_CREATED", "DEMO_ORDER_CREATED", "ORDER_CREATED"} for e in events)
        assert await store.list_orders(sid) == []


@pytest.mark.asyncio
async def test_closed_two_creates_only_empty_groups_without_model_or_room_changes(api_factory, monkeypatch):
    model = StubLLM([])
    async with api_factory(model) as (client, _):
        sid, token, before = await _captured_pending(client, model, monkeypatch)
        _, after = await _message(client, sid, token, "2")
        assert not model.calls
        assert len(after["trip"]["groups"]) == 2
        assert all(g["traveler_ids"] == [] and g["keep_separate"] is True for g in after["trip"]["groups"])
        for field in ("travelers", "stays", "legs", "budget", "preferences"):
            assert after["trip"][field] == before["trip"][field]
        assert after["pending_question"]["field"] == "group.membership"
