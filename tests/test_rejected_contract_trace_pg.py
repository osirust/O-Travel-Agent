"""Real gateway/worker/PG rejected final JSON; all vendor HTTP intercepted."""
import json
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from test_api import _headers, _session, _snapshot, _wait
from test_api import api_factory as rejected_api_factory

from otravel import providers
from otravel.domain import sample_trip
from otravel.providers import LLMGateway

api_factory = rejected_api_factory
SECRET = "synthetic-rejected-contract-credential"
HIDDEN = "HIDDEN_PROVIDER_REASONING_NOT_A_FINAL_CONTRACT"


@pytest.mark.asyncio
@pytest.mark.parametrize("credential_echo", [False, True])
async def test_rejected_final_contract_is_auditable_but_credential_echo_and_hidden_text_are_not(api_factory, monkeypatch, credential_echo):
    for field in providers._LLM_CREDENTIAL_FIELDS:
        monkeypatch.delenv(field, raising=False)
    monkeypatch.setattr(providers, "_SECRETS", set())
    monkeypatch.setattr(providers, "_EPHEMERAL_PUBLIC_SECRETS", set())
    raw = {"operations": [{"type": "set_budget", "value": {"amount_minor": True, "currency": "RUB"}}],
        "control": {"action": "none"}, "reply": SECRET if credential_echo else "Попытка изменить бюджет.",
        "unknowns": [], "conflicts": [], "speech_act": "CHANGE", "information_subject": None}
    calls = []

    def handler(request):
        assert str(request.url) == "https://api.z.ai/api/paas/v4/chat/completions"
        assert request.headers["authorization"] == "Bearer " + SECRET
        body = json.loads(request.content)
        assert body["model"] == "glm-4.5-flash" and body["response_format"] == {"type": "json_object"}
        calls.append(body)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(raw, ensure_ascii=False), "reasoning_content": HIDDEN}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 9, "total_tokens": 21}})

    gateway = LLMGateway({"LLM_PROVIDER": "zai", "ZAI_API_KEY": SECRET, "ZAI_MODEL": "glm-4.5-flash",
        "ZAI_FREE_TIER_CONFIRMED": True, "http_transport": httpx.MockTransport(handler)})
    async with api_factory(gateway) as (client, _):
        app = client._transport.app
        app.state.harness.settings = replace(app.state.harness.settings, max_llm_calls_per_run=1)
        sid, token, before = await _session(client, sample_trip().model_dump(mode="json"))
        accepted = await client.post(f"/api/v1/sessions/{sid}/messages", headers=_headers(token), json={
            "client_message_id": str(uuid4()), "expected_version": before["state_version"],
            "text": "Укажи новый общий бюджет поездки."})
        assert accepted.status_code == 202
        run = await _wait(client, accepted.json()["run_id"], token)
        after = await _snapshot(client, sid, token)
        assert run["status"] == "failed" and len(calls) == 1
        assert after["trip"] == before["trip"] and after["state_version"] == before["state_version"]
        events = await app.state.store.list_events(sid)
        started = [event for event in events if event["event_type"] == "MODEL_CALL_STARTED"]
        failed = [event["payload"] for event in events if event["event_type"] == "MODEL_CALL_FAILED"]
        assert len(started) == len(failed) == 1 and failed[0]["provider"] == "zai"
        assert not any(event["event_type"] in {"TOOL_CALL_STARTED", "MODEL_RESPONSE_RECEIVED", "MODEL_EXTRACTION_VALIDATED"} for event in events)
        serialized = json.dumps({"events": events, "public_run": run}, default=str)
        assert SECRET not in serialized and HIDDEN not in serialized
        if credential_echo:
            assert failed[0]["code"] == "INVALID_LLM_RESPONSE"
            assert "rejected_structured_contract" not in failed[0]
            assert run["error"]["code"] == "INVALID_LLM_RESPONSE"
        else:
            assert failed[0]["code"] == "INVALID_LLM_OPERATION"
            assert 1 <= len(failed[0]["schema_errors"]) <= 3
            assert all(set(error) == {"path", "rule"} for error in failed[0]["schema_errors"])
            snapshot = failed[0]["rejected_structured_contract"]
            assert snapshot["captured"] is True and snapshot["utf8_bytes"] <= 65536
            assert snapshot["value"] == raw
            assert type(snapshot["value"]["operations"][0]["value"]["amount_minor"]) is bool
            assert run["error"]["code"] == "INVALID_LLM_OPERATION"
        assert await app.state.store.list_orders(sid) == []
