"""Controlled proposed budget mistake, ASGI + isolated PG, no actual NLU."""
import pytest
from test_api import StubLLM, _session
from test_api import api_factory as budget_api_factory
from test_slot_continuation import _message

api_factory = budget_api_factory


@pytest.mark.asyncio
async def test_explicit_budget_prohibition_keeps_budget_but_allows_requested_child(api_factory):
    initial = {"origin": "Москва", "budget": {"amount_minor": 13_000_000, "currency": "RUB", "scope": "whole_trip"},
        "travelers": [{"id": "existing-adult", "type": "adult"}],
        "stays": [{"id": "existing-stay", "city": "Стамбул", "rooms": []}]}
    model = StubLLM([{"speech_act": "CHANGE", "operations": [
        {"type": "add_traveler", "value": {"id": "requested-child", "type": "child", "age": 5}},
        {"type": "set_budget", "value": {"amount_minor": 6_000_000, "currency": "RUB", "scope": "whole_trip"}}]}])
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client, initial)

        run, after = await _message(client, sid, token, "Добавь ребёнка пяти лет. Бюджет не меняй.")
        assert len(model.calls) == 1
        assert after["trip"]["budget"] == before["trip"]["budget"]
        assert after["trip"]["travelers"][:-1] == before["trip"]["travelers"]
        child = after["trip"]["travelers"][-1]
        assert (child["id"], child["type"], child["age"]) == ("requested-child", "child", 5)
        assert after["trip"]["origin"] == before["trip"]["origin"]
        assert after["trip"]["stays"] == before["trip"]["stays"]
        assert after["trip"]["constraints"] == before["trip"]["constraints"]
        assert after["state_version"] == before["state_version"] + 1
        notes = run["result"]["result"]["operation_normalizations"]
        assert any(note.get("code") == "budget_change_prohibited" for note in notes)
        store = client._transport.app.state.store
        events = [event for event in await store.list_events(sid) if event["run_id"] == run["run_id"]]
        assert not any(event["event_type"] in {"TOOL_CALL_STARTED", "QUOTE_CREATED", "ORDER_CREATED"} for event in events)
        assert await store.list_orders(sid) == []
