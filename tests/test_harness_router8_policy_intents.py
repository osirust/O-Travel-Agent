"""Router8 cached model output + fresh own PG sessions, no actual NLU/API."""
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from test_api import StubLLM, _session
from test_api import api_factory as policy_api_factory
from test_slot_continuation import _message, _reply

from otravel.domain import TripState
from otravel.planning import assemble_plan

api_factory = policy_api_factory
CASES = json.loads((Path(__file__).resolve().parents[1] / "fixtures/regression/router8-policy-0aba.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.asyncio
async def test_exact_004t1_does_not_invent_one_way_while_explicit_person_and_group_persist(api_factory):
    source = next(case for case in CASES if case["id"] == "ROUTER8-004-T01")
    model = StubLLM([source["raw"]])
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client, source["before_state"])
        run, after = await _message(client, sid, token, source["user"])
        assert len(model.calls) == 1
        state = after["trip"]
        assert state["route_policy"] == before["trip"]["route_policy"] == {"kind": "round_trip", "end_city": None}
        assert state["origin"] is None and state["budget"] == before["trip"]["budget"]
        assert [(person["id"], person["type"]) for person in state["travelers"]] == [("new-traveler-1", "adult")]
        assert state["groups"] == [{"id": "new-group-1", "traveler_ids": ["new-traveler-1"], "keep_separate": False}]
        assert state["stays"][0]["city"] == "Москва" and not state["legs"]
        notes = run["result"]["result"]["operation_normalizations"]
        assert any(note.get("code") == "route_policy_change_not_requested" for note in notes)
        store = client._transport.app.state.store
        events = [event for event in await store.list_events(sid) if event["run_id"] == run["run_id"]]
        assert not any(event["event_type"] in {"TOOL_CALL_STARTED", "QUOTE_CREATED", "DEMO_ORDER_CREATED"} for event in events)
        assert await store.list_orders(sid) == []


@pytest.mark.asyncio
async def test_exact_001t11_question_keeps_hybrid_mixed_plan_and_active_quote(api_factory, monkeypatch):
    source = next(case for case in CASES if case["id"] == "ROUTER8-001-T11")
    model = StubLLM([source["raw"]])  # Original wrong CHANGE remains frozen.
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, source["before_state"])
        store, harness = client._transport.app.state.store, client._transport.app.state.harness
        state = TripState.model_validate(created["trip"])
        now = datetime.now(UTC)
        offers = deepcopy(source["prior_plan"]["offers"])
        # A controlled, fresh receipt of the same monetary evidence. Do not
        # claim the original supplier snapshot is still available today.
        for offer in offers:
            if offer["source_type"] != "DEMO":
                offer["retrieved_at"], offer["expires_at"] = now.isoformat(), (now + timedelta(minutes=5)).isoformat()
                offer["details"]["binding"].update(trip_id=state.trip_id, state_version=state.state_version)
        plan = assemble_plan(state, offers, now=now)
        assert plan["ready_for_simulated_checkout"] and plan["source_type"] == "MIXED"
        assert plan["total_minor"] == 4_507_077
        setup, _ = await store.intake_message(sid, str(uuid4()), "controlled owned current plan setup", state.state_version)
        assert await store.claim_run(setup["id"])
        await store.finish_run(setup["id"], state.state_version, None, "OPTIONS_READY", "Controlled current plan",
            result={"plans": [plan]})
        quote = await store.create_quote(sid, state.state_version, plan)
        before = await store.get_session(sid)
        quote_before = await store.get_quote(quote["quote_id"])

        async def forbidden(*args, **kwargs):
            raise AssertionError("A source/capability question cannot search, refresh, quote or order")

        monkeypatch.setattr(harness, "_search_trip", forbidden)
        monkeypatch.setattr(store, "create_quote", forbidden)
        monkeypatch.setattr(store, "confirm_quote", forbidden)
        run, after = await _message(client, sid, token, source["user"])
        assert after["trip"] == before["state"] and after["state_version"] == before["state_version"]
        assert after["status"] == before["status"] and after["pending_question"] == before["pending_question"]
        assert after["trip"]["search_policy"]["mode"] == "HYBRID"
        assert await store.get_quote(quote["quote_id"]) == quote_before
        assert not model.calls, "The root canonical source/capability path must preempt the wrong cached model"
        reply = await _reply(client, sid, token, run)
        assert "SANDBOX" in reply and "DEMO" in reply and ("тестов" in reply.casefold() or "имитац" in reply.casefold())
        events = [event for event in await store.list_events(sid) if event["run_id"] == run["run_id"]]
        assert not any(event["event_type"] in {"MODEL_CALL_STARTED", "TOOL_CALL_STARTED", "QUOTE_CREATED", "DEMO_ORDER_CREATED"} for event in events)
        assert await store.list_orders(sid) == []
