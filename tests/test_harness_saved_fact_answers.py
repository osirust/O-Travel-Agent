"""Closed reminder invariants through real ASGI/PG; no remote inference/HTTP."""
import hashlib
import json

import pytest
from test_api import StubLLM, _confirmation, _headers, _session, _snapshot
from test_api import api_factory as fact_api_factory
from test_harness_saved_answers import assert_unchanged, event_baseline, forbid_actions, seed_pending
from test_saved_fact_answers import state as canonical_trip
from test_selected_quote_context_20261027 import prepare_third_plan
from test_slot_continuation import _message, _reply

api_factory = fact_api_factory
SIDE_EFFECTS = {"MODEL_CALL_STARTED", "LLM_REQUEST_STARTED", "TOOL_CALL_STARTED",
                "LIVE_SEARCH_STARTED", "QUOTE_CREATED", "ORDER_CREATED", "DEMO_ORDER_CREATED"}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, default=str, sort_keys=True,
        ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


async def assert_no_new_actions(store, sid, baseline):
    events = [event for event in await store.list_events(sid) if event["event_id"] not in baseline]
    assert not any(event["event_type"] in SIDE_EFFECTS for event in events)


@pytest.mark.asyncio
async def test_closed_roster_dates_budget_reminders_use_no_model_tool_or_trip_revision(api_factory, monkeypatch):
    async with api_factory(StubLLM()) as (client, model):
        sid, token, _ = await _session(client, canonical_trip().model_dump(mode="json"))
        store = client._transport.app.state.store
        before = await _snapshot(client, sid, token)
        baseline = await event_baseline(store, sid)
        await forbid_actions(client, monkeypatch)
        for text in ("Напомни наш состав", "Напомни наши даты", "Какой у нас сейчас бюджет?"):
            run, after = await _message(client, sid, token, text)
            assert run["result"]["result"]["response_source"] == "canonical_saved_answer"
            assert not run.get("llm")
            assert_unchanged(before, after)
            reply = await _reply(client, sid, token, run)
            if "состав" in text:
                assert "2 взрослых" in reply and "4 года" in reply and "9 лет" in reply
            elif "даты" in text:
                assert "10.11.2026" in reply and "17.11.2026" in reply
            else:
                assert "110 000,50 RUB" in reply
        assert model.calls == [] and await store.list_orders(sid) == []
        await assert_no_new_actions(store, sid, baseline)


@pytest.mark.asyncio
async def test_roster_reminder_preserves_exact_owned_pending_child_age_question(api_factory, monkeypatch):
    initial = canonical_trip().model_dump(mode="json")
    initial["travelers"][2]["age"] = None
    async with api_factory(StubLLM()) as (client, model):
        sid, token, _ = await _session(client, initial)
        store = client._transport.app.state.store
        await seed_pending(store, sid, "traveler.age", target="young",
            context={"children_known": True, "children_count": 2, "owner_case": "bounded-pending-child"})
        before = await _snapshot(client, sid, token)
        baseline = await event_baseline(store, sid)
        await forbid_actions(client, monkeypatch)
        run, after = await _message(client, sid, token, "Напомни возраста наших детей")
        assert_unchanged(before, after)
        assert after["pending_question"]["field"] == "traveler.age"
        assert after["pending_question"]["target_id"] == "young"
        assert run["result"]["result"]["response_source"] == "canonical_saved_answer"
        reply = await _reply(client, sid, token, run)
        assert "возраст не указан" in reply and "9 лет" in reply
        assert model.calls == [] and await store.list_orders(sid) == []
        await assert_no_new_actions(store, sid, baseline)


@pytest.mark.asyncio
async def test_saved_reminders_preserve_selected_quote_consent_and_same_confirmation_is_one_order(api_factory, monkeypatch):
    async with api_factory(StubLLM()) as (client, model):
        sid, token, _plans, quote, store, _ = await prepare_third_plan(client, model)
        before = await _snapshot(client, sid, token)
        quote_before = fingerprint(await store.get_quote(quote["quote_id"]))
        selection_before = fingerprint(await store.current_selected_plan(sid, before["state_version"]))
        baseline = await event_baseline(store, sid)

        async def forbidden(*args, **kwargs):
            raise AssertionError("A reminder must not infer, search, refresh, or recreate a quote")
        harness = client._transport.app.state.harness
        monkeypatch.setattr(harness, "_extract", forbidden)
        monkeypatch.setattr(harness, "_search_trip", forbidden)
        monkeypatch.setattr(store, "create_quote", forbidden)
        from otravel import api as api_module
        def forbidden_refresh(*args, **kwargs):
            raise AssertionError("A reminder must not refresh supplier/DEMO prices")
        monkeypatch.setattr(api_module, "reprice_plan", forbidden_refresh)
        for text in ("Кто у нас в итоге едет?", "Напомни наш маршрут"):
            run, after = await _message(client, sid, token, text)
            assert_unchanged(before, after)
            assert run["result"]["result"]["response_source"] == "canonical_saved_answer"
            assert fingerprint(await store.get_quote(quote["quote_id"])) == quote_before
            assert fingerprint(await store.current_selected_plan(sid, before["state_version"])) == selection_before
        assert model.calls == []
        await assert_no_new_actions(store, sid, baseline)
        body = _confirmation(quote, before["state_version"], key="closed-reminder-existing-consent")
        path = f"/api/v1/sessions/{sid}/checkout/confirm"
        first = await client.post(path, json=body, headers=_headers(token))
        repeated = await client.post(path, json=body, headers=_headers(token))
        assert first.status_code == repeated.status_code == 200
        one = first.json().get("order", first.json())
        again = repeated.json().get("order", repeated.json())
        assert one["order_id"] == again["order_id"]
        assert one["is_demo"] is True and one["booking_created_at_supplier"] is False
        assert len(await store.list_orders(sid)) == 1
        assert model.calls == []
