"""Exact pilot raw contract, controlled model/isolated PG; no vendor requests."""
from copy import deepcopy

import pytest
from test_api import StubLLM, _session, _snapshot
from test_api import api_factory as quote_money_api_factory
from test_harness_saved_answers import seed_pending
from test_money_quote_guard import PILOT_TEXT
from test_slot_continuation import _message

api_factory = quote_money_api_factory


def semantic_before():
    # Pilot002:T10 semantic state; fresh own session/trip/leg identities.
    return {"origin": "Казань", "budget": {"amount_minor": 13_000_000, "currency": "RUB", "scope": "whole_trip"},
        "travelers": [{"id": "new-traveler-1", "type": "adult"}, {"id": "new-traveler-2", "type": "adult"},
            {"id": "new-traveler-3", "type": "child", "age": 5}, {"id": "new-traveler-4", "type": "child", "age": 9}],
        "stays": [{"id": "new-stay-1", "city": "Анталья", "check_in": "2026-11-10", "check_out": "2026-11-17", "rooms": []}],
        "search_policy": {"mode": "HYBRID", "hotel_provider": "liteapi", "flight_provider": "duffel",
            "demo_fallback": True, "guest_nationality": "RU"}}


@pytest.mark.asyncio
async def test_exact_quote_confirmation_raw_budget_proposal_cannot_mutate_canonical_budget(api_factory, monkeypatch):
    raw = {"reply": "Понял. Обновил бюджет до 100 750 рублей для тестового оформления первого варианта. Настоящую бронь создавать не буду.",
        "control": {"action": "none"}, "unknowns": [], "conflicts": [],
        "operations": [{"type": "set_budget", "value": {"scope": "whole_trip", "currency": "RUB", "amount_minor": 10_075_000}, "target_id": None}],
        "speech_act": "CHANGE", "information_subject": None}
    async with api_factory(StubLLM([raw])) as (client, model):
        sid, token, _ = await _session(client, semantic_before())
        repository = client._transport.app.state.store
        await seed_pending(repository, sid, "stay.rooms", target="new-stay-1", context={
            "requested_destination": {"kind": "city", "name": "Анталья", "country_code": "TR"}})
        before = await _snapshot(client, sid, token)
        search = client._transport.app.state.harness._search_trip
        calls = []

        async def observed_search(*args, **kwargs):
            calls.append(True)
            return await search(*args, **kwargs)

        monkeypatch.setattr(client._transport.app.state.harness, "_search_trip", observed_search)
        run, after = await _message(client, sid, token, PILOT_TEXT)
        assert after["trip"] == before["trip"]
        assert after["trip"]["budget"]["amount_minor"] == 13_000_000
        assert after["state_version"] == before["state_version"]
        assert after["pending_question"] == before["pending_question"]
        assert not calls and len(model.calls) == 1
        notes = run["result"]["result"]["operation_normalizations"]
        assert any(note.get("finding_id") == "AFTER80-MONEY-QUOTE-001" for note in notes)
        events = [event for event in await repository.list_events(sid) if event["run_id"] == run["run_id"]]
        assert not any(event["event_type"] in {"TOOL_CALL_STARTED", "QUOTE_CREATED", "ORDER_CREATED"} for event in events)
        assert await repository.list_orders(sid) == []


@pytest.mark.asyncio
async def test_explicit_budget_confirmation_still_changes_only_budget(api_factory):
    raw = {"operations": [{"type": "set_budget", "value": {"amount_minor": 10_075_000, "currency": "RUB"}}]}
    async with api_factory(StubLLM([raw])) as (client, model):
        sid, token, before = await _session(client, semantic_before())
        _, after = await _message(client, sid, token, "Подтверждаю общий бюджет 100750 рублей.")
        expected = deepcopy(before["trip"])
        expected["budget"]["amount_minor"] = 10_075_000
        expected["state_version"] += 1
        assert after["trip"] == expected and len(model.calls) == 1
