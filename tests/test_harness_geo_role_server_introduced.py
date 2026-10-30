"""Exact raw-operation regression through ASGI + PG, no remote API or NLU.

Initial before-state is explicitly a controlled minimal bootstrap. The two
historical flight-mode no-ops receive their same opaque leg ID in this owned
bootstrap; this qualifies role preservation, not full old-run reproduction.
"""
import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_api import StubLLM, _session
from test_api import api_factory as geo_api_factory
from test_slot_continuation import _message

api_factory = geo_api_factory
CASES = json.loads((Path(__file__).resolve().parents[1] /
    "fixtures/regression/geo-role-server-introduced-20261028.json").read_text())["cases"]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case_id"])
async def test_exact_raw_operations_cannot_introduce_an_excluded_city_or_hotel_city_origin(api_factory, case):
    initial = deepcopy(case["before"])
    initial.pop("state_version", None)
    initial["search_policy"] = {"mode": "DEMO", "demo_fallback": True}
    mode_ops = [op for op in case["raw_operations"] if op["type"] == "set_leg_mode"]
    if mode_ops:
        stay = initial["stays"][0]
        initial["legs"] = [{"id": mode_ops[0]["target_id"], "mode": "flight",
            "origin": initial["origin"], "destination": stay["city"],
            "departure_date": stay["check_in"]}]
    model = StubLLM([{"speech_act": "CHANGE", "control": {"action": "continue"},
                     "operations": case["raw_operations"]}])
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, initial)
        run, after = await _message(client, sid, token, case["input"])
        assert run["status"] == "completed"
        assert after["trip"]["origin"] == created["trip"]["origin"]
        assert after["trip"]["travelers"] == created["trip"]["travelers"]
        assert len(after["trip"]["stays"]) == len(created["trip"]["stays"]) == 1
        assert after["trip"]["stays"][0]["city"] == created["trip"]["stays"][0]["city"]
        assert after["trip"]["stays"][0]["rooms"] == created["trip"]["stays"][0]["rooms"]
        if case["case_id"] == "ZAI100-040":
            assert after["trip"]["stays"] == created["trip"]["stays"]
            assert after["trip"]["budget"]["amount_minor"] == 7500000
            assert after["trip"]["constraints"] == []
        else:
            raw_stay = next(op for op in case["raw_operations"] if op["type"] == "upsert_stay")
            assert after["trip"]["stays"][0]["check_in"] == created["trip"]["stays"][0]["check_in"]
            assert after["trip"]["stays"][0]["check_out"] == raw_stay["value"]["check_out"]
            assert after["trip"]["budget"] == created["trip"]["budget"]
        assert len(model.calls) == 1
        repository = client._transport.app.state.store
        events = await repository.list_events(sid)
        assert not any(event["event_type"].startswith("ORDER") for event in events)
