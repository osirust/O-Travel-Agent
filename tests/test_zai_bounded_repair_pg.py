"""Free Z.ai repair is bounded and counts both real gateway attempts."""
import json
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from test_api import _headers, _session, _snapshot, _wait
from test_api import api_factory as repair_api_factory

from otravel.providers import LLMGateway

api_factory = repair_api_factory
SECRET = "synthetic-zai-repair-test-only-key"
BAD = {"operations": [{"type": "set_preference", "value": {"value": "early_morning", "weight": None}}],
    "control": {"action": "continue"}, "reply": "Предлагаю ранний вылет.", "unknowns": [], "conflicts": [], "speech_act": "CHANGE"}
GOOD = {**BAD, "operations": [{"type": "set_preference", "value": {"key": "departure_time", "value": "early_morning"}}]}


@pytest.mark.asyncio
@pytest.mark.parametrize("max_calls,second_valid", [(2, True), (2, False), (1, True)])
async def test_schema_feedback_repair_obeys_run_bound_without_applying_failed_attempt(api_factory, max_calls, second_valid):
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        answer = GOOD if len(calls) == 2 and second_valid else BAD
        return httpx.Response(200, json={"model": "glm-4.5-flash", "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(answer)}}]})
    gateway = LLMGateway({"LLM_PROVIDER": "zai", "ZAI_API_KEY": SECRET, "ZAI_MODEL": "glm-4.5-flash",
        "ZAI_FREE_TIER_CONFIRMED": True, "http_transport": httpx.MockTransport(handler)})
    async with api_factory(gateway) as (client, _):
        app = client._transport.app
        app.state.harness.settings = replace(app.state.harness.settings, max_llm_calls_per_run=max_calls)
        sid, token, before = await _session(client, {})
        original = deepcopy(before["trip"])
        accepted = await client.post(f"/api/v1/sessions/{sid}/messages", headers=_headers(token),
            json={"client_message_id": str(uuid4()), "expected_version": 0, "text": "Предпочитаю вылет рано утром."})
        assert accepted.status_code == 202
        run = await _wait(client, accepted.json()["run_id"], token)
        after = await _snapshot(client, sid, token)
        events = await app.state.store.list_events(sid)
        assert len(calls) == max_calls, {"error": run.get("error"), "events": [
            {"type": row["event_type"], "code": row["payload"].get("code")} for row in events]}
        assert len([row for row in events if row["event_type"] == "MODEL_CALL_STARTED"]) == max_calls
        assert not any(row["event_type"] == "TOOL_CALL_STARTED" for row in events)
        if max_calls == 2:
            assert "validation_feedback" in calls[1]["messages"][-1]["content"]
            retried = json.loads(calls[1]["messages"][-1]["content"])
            assert retried["validation_feedback"]["rejected_contract"] == BAD
        if max_calls == 2 and second_valid:
            assert run["status"] == "completed" and after["state_version"] == 1
            assert after["trip"]["preferences"] == [{"key": "departure_time", "value": "early_morning", "weight": 1.0}]
            assert after["trip"]["travelers"] == original["travelers"] and after["trip"]["stays"] == original["stays"]
        else:
            assert run["status"] == "failed" and after["trip"] == original and after["state_version"] == 0
        assert SECRET not in json.dumps({"run": run, "events": events}, default=str)
        assert await app.state.store.list_orders(sid) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("providers,limits,expected,readonly", [
    (["zai", "zai", "zai"], [2, 2, 2], [True, True, False], False),
    (["zai", "zai"], [None, None], [True, False], False),
    (["gigachat", "zai"], [None, 2], [True, False], False),
    (["zai", "gigachat"], [2, None], [True, False], False),
    (["zai", "zai"], [2, 2], [True, False], True),
])
async def test_durable_opt_in_never_allows_third_attempt_cross_provider_or_readonly_retry(api_factory, providers, limits, expected, readonly):
    async with api_factory() as (client, _):
        sid, _token, _before = await _session(client, {})
        store = client._transport.app.state.store
        run, _ = await store.intake_message(sid, str(uuid4()), "Controlled reservation test", 0)
        assert await store.claim_run(run["run_id"])
        if readonly:
            await store.select_model_role(sid, run["run_id"], 0, "readonly_answer_v1")
        outcomes = []
        for provider, bound in zip(providers, limits, strict=True):
            outcomes.append(await store.reserve_model_call(sid, run["run_id"], 2000, provider, run_attempt_limit=bound))
        assert outcomes == expected
        events = await store.list_events(sid)
        assert sum(row["event_type"] == "MODEL_CALL_STARTED" for row in events) == sum(expected)
        await store.finish_run(run["run_id"], 0, None, "TRIP_DRAFT", "Controlled budget test completed.")
