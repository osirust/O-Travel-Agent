"""Cached Router002 regression through controlled ASGI/PG, zero external calls."""
import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from test_api import StubLLM, _headers, _session
from test_api import api_factory as date_api_factory
from test_slot_continuation import _message, _reply

from otravel.domain import sample_trip

api_factory = date_api_factory
FIXTURE = json.loads((Path(__file__).parents[1] / "fixtures/regression/router8-date-label-002.json").read_text(encoding="utf-8"))


async def bind_question(client, sid, pending):
    store = client._transport.app.state.store
    current = await store.get_session(sid)
    run, _ = await store.intake_message(sid, str(uuid4()), "controlled cached pending question", current["state_version"])
    assert await store.claim_run(run["run_id"])
    bound = {**deepcopy(pending), "state_version": current["state_version"]}
    await store.finish_run(run["run_id"], current["state_version"], None, "WAITING_USER", bound["text"],
                           pending_question=bound, waiting_reason="clarification")


async def zero_external(client, sid, token, model):
    assert not model.calls
    events = await client.get(f"/api/v1/sessions/{sid}/events", headers=_headers(token))
    assert events.status_code == 200
    assert not any(e["event_type"] in {"MODEL_CALL_STARTED", "TOOL_CALL_STARTED", "LLM_REQUEST_STARTED", "LIVE_SEARCH_STARTED"}
                   for e in events.json()["events"])
    assert await client._transport.app.state.store.list_orders(sid) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("sentinel_budget", [None, {"amount_minor": 13_000_000, "currency": "RUB", "scope": "whole_trip"}])
async def test_router002_t3_explicit_checkout_and_followup_checkin_skip_wrong_cached_model(api_factory, sentinel_budget):
    initial = deepcopy(FIXTURE["before_trip"])
    initial["budget"] = sentinel_budget
    model = StubLLM([FIXTURE["observed_wrong_model_contract"]])
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, initial)
        await bind_question(client, sid, FIXTURE["pending_question"])
        run, after = await _message(client, sid, token, FIXTURE["user"])
        expected = deepcopy(created["trip"])
        expected["state_version"] += 1
        expected["stays"][0]["check_out"] = "2026-11-17"
        assert after["trip"] == expected
        assert after["trip"]["stays"][0]["check_in"] is None
        assert after["pending_question"]["field"] == "stay.check_in"
        assert after["pending_question"]["target_id"] == "new-stay-1"
        assert not run.get("llm")
        second, continued = await _message(client, sid, token, FIXTURE["test_added_continuation"])
        assert continued["trip"]["stays"][0]["check_in"] == "2026-11-10"
        assert continued["trip"]["stays"][0]["check_out"] == "2026-11-17"
        for field in ("origin", "travelers", "groups", "budget", "constraints", "preferences", "search_policy", "route_policy"):
            assert continued["trip"][field] == created["trip"][field]
        assert not second.get("llm")
        assert continued["pending_question"]["field"] == "stay.rooms"
        await zero_external(client, sid, token, model)


@pytest.mark.asyncio
async def test_month_date_target_selection_is_durable_and_never_guesses_first_stay(api_factory):
    model = StubLLM()
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, sample_trip().model_dump(mode="json"))
        _, proposed = await _message(client, sid, token, "Выезд 14 ноября 2026 года")
        assert proposed["trip"] == created["trip"]
        assert proposed["pending_question"]["target_id"] is None
        packet = proposed["pending_question"]["context"]["date_proposal"]
        assert packet["resolved_date"] == "2026-11-14"
        stored = await client._transport.app.state.store.get_session(sid)
        assert stored["pending_question"]["context"]["date_proposal"] == packet
        _, after = await _message(client, sid, token, "Анталья")
        assert after["trip"]["stays"][0] == created["trip"]["stays"][0]
        assert after["trip"]["stays"][1]["check_out"] == "2026-11-14"
        assert "date_proposal" not in (after.get("pending_question") or {}).get("context", {})
        await zero_external(client, sid, token, model)


@pytest.mark.asyncio
async def test_yearless_month_answer_requests_year_without_state_mutation_or_llm(api_factory):
    model = StubLLM()
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, deepcopy(FIXTURE["before_trip"]))
        await bind_question(client, sid, FIXTURE["pending_question"])
        _, asked = await _message(client, sid, token, "10 ноября")
        assert asked["trip"] == created["trip"]
        assert "год" in asked["pending_question"]["text"]
        assert asked["pending_question"]["field"] == "stay.check_in"
        _, finished = await _message(client, sid, token, "Заезд 10 ноября 2026 года")
        # Existing checkout10Nov makes this collision explicit instead of
        # guessing checkout17 or silently changing the wrong stored field.
        assert finished["trip"] == created["trip"]
        assert "раньше" in finished["pending_question"]["text"]
        await zero_external(client, sid, token, model)


@pytest.mark.asyncio
async def test_router004_short_owned_room_count_does_not_need_model_schema_repair(api_factory):
    initial = deepcopy(FIXTURE["before_trip"])
    initial["travelers"] = [initial["travelers"][0]]
    initial["stays"][0].update(check_in="2026-11-10", check_out=None, rooms=[])
    initial["budget"] = None
    model = StubLLM()
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, initial)
        stay = created["trip"]["stays"][0]
        await bind_question(client, sid, {"field": "stay.rooms", "target_id": stay["id"],
                                         "text": "Сколько гостиничных номеров нужно?"})
        run, after = await _message(client, sid, token, "Один номер, живу один.")
        assert run["status"] == "completed" and not run.get("error")
        assert len(after["trip"]["stays"][0]["rooms"]) == 1
        for field in ("origin", "travelers", "groups", "budget", "constraints", "preferences", "search_policy", "route_policy"):
            assert after["trip"][field] == created["trip"][field]
        reply = await _reply(client, sid, token, run)
        assert not any(key in reply for key in ("check_in", "stay.rooms", "destination_city"))
        await zero_external(client, sid, token, model)
