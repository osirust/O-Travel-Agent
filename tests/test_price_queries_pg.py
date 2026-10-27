"""Persisted price/nudge regressions; controlled model/source, not actual NLU."""

import pytest
from test_api import StubLLM, _plans, _preview, _session, _snapshot
from test_api import api_factory as price_api_factory
from test_harness_conversational import initial_search, known_trip
from test_harness_saved_answers import forbid_actions, payload, seed_pending
from test_slot_continuation import _message, _reply

api_factory = price_api_factory


@pytest.mark.asyncio
async def test_price_reminder_preserves_quote_trip_and_plans_without_model_search(api_factory, monkeypatch):
    model = StubLLM([])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, known_trip())
        searched = await initial_search(client, sid, token)
        plan = _plans(searched)[0]
        quote = await _preview(client, sid, token, plan, 0)
        store = client._transport.app.state.store
        before = await _snapshot(client, sid, token)
        quotes_before = await store.get_quote(quote["quote_id"])
        await forbid_actions(client, monkeypatch)
        run, after = await _message(client, sid, token, "сколько будет стоить")
        reply = await _reply(client, sid, token, run)
        assert "RUB" in reply and "демонстрационный" in reply and "сохранённая оценка" in reply
        assert after["trip"] == before["trip"] and not model.calls
        assert payload(run)["plans_preserved"] is True
        assert await store.get_quote(quote["quote_id"]) == quotes_before
        assert await store.list_orders(sid) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["так что?", "сколько будет стоить"])
async def test_nudge_or_price_reasks_unanswered_child_slot_without_llm(api_factory, message):
    model = StubLLM([])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, known_trip())
        store = client._transport.app.state.store
        await seed_pending(store, sid, "party.children", context={"children_known": False})
        before = await _snapshot(client, sid, token)
        run, after = await _message(client, sid, token, message)
        assert after["pending_question"] == before["pending_question"] and after["trip"] == before["trip"]
        assert "дет" in (await _reply(client, sid, token, run)).casefold() and not model.calls


@pytest.mark.asyncio
async def test_complete_trip_price_request_starts_search_without_model(api_factory, monkeypatch):
    model = StubLLM([])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, known_trip())
        harness = client._transport.app.state.harness
        original = harness._search_trip
        observed = []
        async def search(*args, **kwargs):
            observed.append(True)
            return await original(*args, **kwargs)
        monkeypatch.setattr(harness, "_search_trip", search)
        run, after = await _message(client, sid, token, "сколько будет стоить")
        assert len(observed) == 1 and _plans(run) and not model.calls
        assert after["state_version"] == 0
