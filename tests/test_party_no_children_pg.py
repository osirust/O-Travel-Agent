"""Owned clarification chain, real isolated PG; no real model or travel HTTP."""
from uuid import uuid4

import pytest
from test_api import StubLLM, _session
from test_api import api_factory as party_api_factory
from test_slot_continuation import _message, _reply

api_factory = party_api_factory


@pytest.mark.asyncio
@pytest.mark.parametrize("budget,nationality,expected", [(None, None, "search.guest_nationality"),
    ({"amount_minor": 30000000, "currency": "RUB", "scope": "whole_trip"}, None, "search.guest_nationality")])
async def test_one_known_adult_then_real_owner_no_child_phrase_advances_without_model_or_trip_revision(api_factory, budget, nationality, expected):
    initial = {"origin": "Москва", "travelers": [{"id": "adult", "type": "adult"}],
        "stays": [{"id": "stay", "city": "Стамбул", "check_in": "2026-12-01", "check_out": "2026-12-08",
            "rooms": [{"id": "room", "traveler_ids": ["adult"]}]}], "budget": budget,
        "search_policy": {"mode": "HYBRID", "hotel_provider": "liteapi", "flight_provider": "duffel", "guest_nationality": nationality}}
    model = StubLLM([])
    async with api_factory(model) as (client, _app):
        sid, token, _created = await _session(client, initial)
        store = client._transport.app.state.store
        own = await store.get_session(sid)
        run, _duplicate = await store.intake_message(sid, str(uuid4()), "Controlled adult clarification", own["state_version"])
        assert await store.claim_run(run["run_id"])
        pending = {"question_id": "controlled-party", "state_version": own["state_version"],
            "field": "party.adults", "target_id": None, "text": "Сколько взрослых поедет в поездку?",
            "questions": ["Сколько взрослых поедет в поездку?"], "context": {"children_known": False}}
        await store.finish_run(run["run_id"], own["state_version"], None, "WAITING_USER", pending["text"],
            pending_question=pending, waiting_reason="clarification")
        _, adults = await _message(client, sid, token, "1")
        assert adults["pending_question"]["field"] == "party.children"
        answered, after = await _message(client, sid, token, "Дети не едут")
        assert answered["status"] == "completed" and after["trip"] == adults["trip"]
        assert after["state_version"] == adults["state_version"]
        question = after["pending_question"]
        assert question["field"] == expected and question["context"]["children_known"] is True
        assert question["context"]["children_count"] == 0 and not model.calls
        assert "дет" not in question["text"].casefold()
        reply = await _reply(client, sid, token, answered)
        assert "новые изменения" not in reply.casefold()
        events = await store.list_events(sid)
        assert not any(event["event_type"] in {"MODEL_CALL_STARTED", "TOOL_CALL_STARTED"} for event in events)
        assert await store.list_orders(sid) == []
