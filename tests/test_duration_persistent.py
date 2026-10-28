"""Duration and leg-mode contracts on isolated PostgreSQL/ASGI, zero external HTTP."""
from copy import deepcopy
from uuid import uuid4

import pytest
from test_api import StubLLM, _headers, _preview, _session, _snapshot, _wait
from test_api import api_factory as duration_api_factory
from test_requested_nights import trip
from test_set_leg_mode import trip as leg_trip
from test_slot_continuation import _message, _reply

api_factory = duration_api_factory


async def commands(client, sid, token, operations):
    current = await _snapshot(client, sid, token)
    response = await client.post(f"/api/v1/sessions/{sid}/commands", headers=_headers(token), json={
        "client_message_id": str(uuid4()), "expected_version": current["state_version"],
        "operations": operations, "search": False})
    assert response.status_code == 202
    run = await _wait(client, response.json()["run_id"], token)
    assert run["status"] == "completed", run.get("error")
    return run, await _snapshot(client, sid, token)


async def search(client, sid, token):
    state = await _snapshot(client, sid, token)
    response = await client.post(f"/api/v1/sessions/{sid}/search", headers=_headers(token), json={
        "client_message_id": str(uuid4()), "expected_version": state["state_version"], "mode": "DEMO"})
    assert response.status_code == 202
    run = await _wait(client, response.json()["run_id"], token)
    assert run["status"] == "completed", run.get("error")
    return run["result"]["result"]["plans"][0]


async def zero_external(client, sid, model, expected_model_calls=0):
    assert len(model.calls) == expected_model_calls
    events = await client._transport.app.state.store.list_events(sid)
    assert sum(event["event_type"] == "MODEL_CALL_STARTED" for event in events) == expected_model_calls
    assert not any(event["event_type"] in {"TOOL_CALL_STARTED", "LLM_REQUEST_STARTED"} for event in events)
    assert await client._transport.app.state.store.list_orders(sid) == []


@pytest.mark.asyncio
async def test_nights_survive_more_than_recent_history_window_and_then_derive_return_date(api_factory):
    model = StubLLM()
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, trip().model_dump(mode="json"))
        _, saved = await _message(client, sid, token, "На 5 ночей")
        assert saved["trip"]["stays"][0]["requested_nights"] == 5
        assert saved["pending_question"]["field"] == "stay.check_in"
        query, recalled = await _message(client, sid, token, "Сколько ночей мы выбрали?")
        assert recalled["trip"] == saved["trip"] and recalled["pending_question"] == saved["pending_question"]
        reply = await _reply(client, sid, token, query)
        assert "5" in reply and "ноч" in reply and "requested_nights" not in reply
        version = saved["state_version"]
        for _ in range(15):
            _, saved = await commands(client, sid, token, [])
            assert saved["state_version"] == version
        stored = await client._transport.app.state.store.get_session(sid)
        assert stored["state"]["stays"][0]["requested_nights"] == 5
        _, completed = await _message(client, sid, token, "2026-11-10")
        stay = completed["trip"]["stays"][0]
        assert (stay["check_in"], stay["check_out"], stay["requested_nights"]) == ("2026-11-10", "2026-11-15", 5)
        assert completed["trip"]["legs"][-1]["departure_date"] == "2026-11-15"
        assert completed["trip"]["travelers"] == created["trip"]["travelers"]
        assert completed["trip"]["budget"] == created["trip"]["budget"]
        await zero_external(client, sid, model)


@pytest.mark.asyncio
async def test_declined_duration_proposal_is_deleted_and_later_yes_cannot_reapply_it(api_factory):
    model = StubLLM()
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, trip(check_in="2026-11-10", check_out="2026-11-15", requested_nights=5).model_dump(mode="json"))
        _, proposed = await _message(client, sid, token, "Выезд 17.11.2026")
        assert proposed["trip"] == created["trip"]
        assert "duration_proposal" in proposed["pending_question"]["context"]
        _, rejected = await _message(client, sid, token, "нет")
        assert rejected["trip"] == created["trip"]
        assert "duration_proposal" not in rejected["pending_question"]["context"]
        _, later = await _message(client, sid, token, "да")
        assert later["trip"] == created["trip"] and "число ночей" in later["pending_question"]["text"].casefold()
        await zero_external(client, sid, model)


@pytest.mark.asyncio
async def test_old_duration_confirmation_does_not_survive_an_unrelated_new_revision(api_factory):
    model = StubLLM()
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, trip(check_in="2026-11-10", check_out="2026-11-15", requested_nights=5).model_dump(mode="json"))
        await _message(client, sid, token, "Выезд 17.11.2026")
        _, changed = await commands(client, sid, token, [{"type": "set_budget", "value": {"amount_minor": 9_000_000, "currency": "RUB"}}])
        _, after = await _message(client, sid, token, "да")
        assert after["trip"] == changed["trip"]
        assert after["trip"]["stays"][0] == created["trip"]["stays"][0]
        assert "duration_proposal" not in (after.get("pending_question") or {}).get("context", {})
        # Typed commands intentionally removed the pending question; the later
        # unrelated acknowledgement can use the controlled parser. No old
        # confirmation packet is replayed and no supplier HTTP is executed.
        await zero_external(client, sid, model, expected_model_calls=1)


@pytest.mark.asyncio
async def test_new_none_duration_field_does_not_change_old_revision_or_active_quote(api_factory):
    model = StubLLM()
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, leg_trip().model_dump(mode="json"))
        plan = await search(client, sid, token)
        before = await _snapshot(client, sid, token)
        quote = await _preview(client, sid, token, plan, before["state_version"])
        store = client._transport.app.state.store
        old_quote = deepcopy(await store.get_quote(quote["quote_id"]))
        _, after = await commands(client, sid, token, [{"type": "upsert_stay", "target_id": "s", "value": {"requested_nights": None}}])
        assert after["trip"] == before["trip"] and after["state_version"] == before["state_version"]
        assert await store.get_quote(quote["quote_id"]) == old_quote
        assert "requested_nights" not in after["trip"]["stays"][0]
        await zero_external(client, sid, model)


@pytest.mark.asyncio
async def test_public_leg_mode_change_preserves_hotel_and_invalidates_old_quote(api_factory):
    model = StubLLM()
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, leg_trip().model_dump(mode="json"))
        plan = await search(client, sid, token)
        before = await _snapshot(client, sid, token)
        quote = await _preview(client, sid, token, plan, before["state_version"])
        _, after = await commands(client, sid, token, [{"type": "set_leg_mode", "target_id": "out", "value": {"mode": "train"}}])
        assert after["state_version"] == before["state_version"] + 1
        assert after["trip"]["stays"] == before["trip"]["stays"]
        assert after["trip"]["travelers"] == before["trip"]["travelers"]
        assert [(leg["id"], leg["mode"]) for leg in after["trip"]["legs"]] == [("out", "train"), ("back", "flight")]
        current_quote = await client._transport.app.state.store.get_quote(quote["quote_id"])
        assert current_quote["status"] == "stale"
        await zero_external(client, sid, model)
