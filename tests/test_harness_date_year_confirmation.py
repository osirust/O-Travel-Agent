"""Controlled addressed December owner regression through API + real PG.

The initial stay and one question are labelled test setup, not actual NLU.
Supplier/model HTTP is forbidden by the shared isolated API factory.
"""
import pytest
from test_api import StubLLM, _session
from test_api import api_factory as year_api_factory
from test_duration_persistent import commands
from test_slot_continuation import _message, _reply

api_factory = year_api_factory


async def setup_checkout(client, model):
    sid, token, initial = await _session(client, {"origin": "Москва",
        "travelers": [{"id": "one-adult", "type": "adult"}],
        "stays": [{"id": "owned-stay", "city": "Стамбул", "check_in": "2026-12-28"}]})
    _, ready = await _message(client, sid, token, "Уточни дату выезда.")
    assert ready["pending_question"]["field"] == "stay.check_out"
    assert ready["pending_question"]["target_id"] == "owned-stay"
    assert ready["trip"] == initial["trip"] and len(model.calls) == 1
    return sid, token, ready


async def assert_no_source_or_order(client, sid, token):
    store = client._transport.app.state.store
    events = await store.list_events(sid)
    assert not any(event["event_type"] == "TOOL_CALL_STARTED" for event in events)
    assert await store.list_orders(sid) == []


@pytest.mark.asyncio
async def test_december_order_error_and_year_candidate_persist_then_yes_changes_only_checkout(api_factory):
    model = StubLLM([{"unknowns": ["stay.check_out"]}])
    async with api_factory(model) as (client, _):
        sid, token, before = await setup_checkout(client, model)
        run, invalid = await _message(client, sid, token, "на 27 декабря")
        reply = await _reply(client, sid, token, run)
        assert "27.12.2026" in reply and "28.12.2026" in reply and "позже" in reply
        assert invalid["trip"] == before["trip"] and invalid["state_version"] == before["state_version"]
        _, proposed = await _message(client, sid, token, "5 января")
        assert proposed["trip"] == before["trip"]
        packet = proposed["pending_question"]["context"]["date_proposal"]
        assert packet["candidate_date"] == "2027-01-05"
        assert proposed["pending_question"]["target_id"] == packet["target_id"] == "owned-stay"
        store = client._transport.app.state.store
        persisted = await store.get_session(sid)
        assert persisted["pending_question"]["context"]["date_proposal"] == packet
        _, accepted = await _message(client, sid, token, "да")
        assert accepted["trip"]["stays"][0]["check_out"] == "2027-01-05"
        assert accepted["trip"]["stays"][0]["check_in"] == "2026-12-28"
        assert accepted["trip"]["travelers"] == before["trip"]["travelers"]
        assert accepted["state_version"] == before["state_version"] + 1
        assert accepted["trip"]["legs"][-1]["departure_date"] == "2027-01-05"
        assert "date_proposal" not in (accepted.get("pending_question") or {}).get("context", {})
        assert len(model.calls) == 1
        await assert_no_source_or_order(client, sid, token)


@pytest.mark.asyncio
async def test_declined_candidate_and_late_yes_do_not_commit_checkout(api_factory):
    model = StubLLM([{"unknowns": ["stay.check_out"]}])
    async with api_factory(model) as (client, _):
        sid, token, before = await setup_checkout(client, model)
        await _message(client, sid, token, "5 января")
        _, declined = await _message(client, sid, token, "нет")
        assert declined["trip"] == before["trip"]
        assert "date_proposal" not in declined["pending_question"]["context"]
        _, later = await _message(client, sid, token, "да")
        assert later["trip"] == before["trip"] and later["trip"]["stays"][0]["check_out"] is None
        await assert_no_source_or_order(client, sid, token)


@pytest.mark.asyncio
async def test_unrelated_domain_revision_cannot_replay_old_year_confirmation(api_factory):
    model = StubLLM([{"unknowns": ["stay.check_out"]}])
    async with api_factory(model) as (client, _):
        sid, token, before = await setup_checkout(client, model)
        await _message(client, sid, token, "5 января")
        _, changed = await commands(client, sid, token, [{"type": "set_budget",
            "value": {"amount_minor": 30000000, "currency": "RUB"}}])
        assert changed["state_version"] == before["state_version"] + 1
        assert "date_proposal" not in (changed.get("pending_question") or {}).get("context", {})
        _, late = await _message(client, sid, token, "да")
        assert late["trip"] == changed["trip"] and late["trip"]["stays"][0]["check_out"] is None
        await assert_no_source_or_order(client, sid, token)
