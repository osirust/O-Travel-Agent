"""Exact059 reminder through ASGI/PG; no model, supplier, or remote calls."""
import pytest
from test_api import StubLLM, _session, _snapshot
from test_api import api_factory as saved059_api_factory
from test_harness_saved_answers import assert_unchanged, event_baseline, forbid_actions
from test_slot_continuation import _message, _reply

api_factory = saved059_api_factory
TEXT = "Сверь пожалуста обе даты и число взрослых. Февраль был только вопросом."


@pytest.mark.asyncio
async def test_actual059_closed_reminder_keeps_null_adult_ages_and_calls_no_model_or_tool(api_factory, monkeypatch):
    initial = {"origin": "Казань", "search_policy": {"mode": "DEMO"},
        "travelers": [{"id": "adult1", "type": "adult"}, {"id": "adult2", "type": "adult"}],
        "stays": [{"id": "stay", "city": "Дубай", "check_in": "2026-11-05", "check_out": "2026-11-12",
            "rooms": [{"id": "room", "traveler_ids": ["adult1", "adult2"]}]}],
        "budget": {"scope": "whole_trip", "currency": "RUB", "amount_minor": 15000000}}
    model = StubLLM([{"operations": [{"type": "update_traveler", "target_id": "adult1",
                                    "value": {"age": 18}}]}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, initial)
        store = client._transport.app.state.store
        before = await _snapshot(client, sid, token)
        baseline = await event_baseline(store, sid)
        await forbid_actions(client, monkeypatch)
        run, after = await _message(client, sid, token, TEXT)
        assert_unchanged(before, after)
        assert [p["age"] for p in after["trip"]["travelers"]] == [None, None]
        assert run["result"]["result"]["response_source"] == "canonical_saved_answer"
        assert not run.get("llm") and not model.calls
        reply = await _reply(client, sid, token, run)
        assert "05.11.2026" in reply and "12.11.2026" in reply and "2 взрослых" in reply
        assert "18" not in reply
        new = [e for e in await store.list_events(sid) if e["event_id"] not in baseline]
        saved = [e for e in new if e["event_type"] == "SAVED_FACTS_ANSWER_PROPOSED"]
        assert len(saved) == 1 and saved[0]["payload"]["kind"] == "dates,party"
        assert saved[0]["payload"]["model_calls"] == 0
        assert not any(e["event_type"] in {"MODEL_CALL_STARTED", "TOOL_CALL_STARTED", "QUOTE_CREATED",
            "DEMO_ORDER_CREATED", "ORDER_CREATED"} for e in new)
        assert await store.list_orders(sid) == []
