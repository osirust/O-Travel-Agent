"""Groq routing/accounting with intercepted HTTP; no real key or inference."""
import json

import httpx
import pytest

from otravel.api import _clean
from otravel.config import Settings
from otravel.domain import sample_trip
from otravel.harness import Harness
from otravel.providers import LLMGateway, ProviderError
from test_api import _ModelPolicyStore


def setup(handler, **overrides):
    gateway = LLMGateway({"LLM_PROVIDER": "groq", "GROQ_API_KEY": "synthetic-groq-key",
        "GROQ_FREE_TIER_CONFIRMED": True, "GROQ_MODEL": "qwen/qwen3.8-27b",
        "OPENROUTER_API_KEY": "synthetic-router-key", "OPENROUTER_BACKUP_MODEL": "test/backup:free",
        "http_transport": httpx.MockTransport(handler), **overrides})
    store = _ModelPolicyStore()
    harness = Harness(Settings("unused", "groq", 1, 8000, 40, 2), store, gateway, None)
    session = {"session_id": "policy-session", "state_version": 0,
        "state": sample_trip().model_dump(mode="json"), "pending_question": None}
    run = {"run_id": "policy-run", "input_message_id": "policy-message", "input_text": "Сербию знаешь?"}
    return gateway, store, harness, session, run


@pytest.mark.asyncio
async def test_groq_has_real_provider_identity_and_budget_reservation():
    requests = []
    def handler(request):
        requests.append(request)
        payload = {"operations": [], "control": {"action": "none"}, "reply": "", "unknowns": [],
            "conflicts": [], "speech_act": "ASK", "information_subject": "Сербия"}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload)}}]})
    gateway, store, harness, session, run = setup(handler)
    try:
        result, meta = await harness._extract(session, run)
    finally:
        await gateway.close()
    assert result.speech_act == "ASK" and result.operations == []
    assert len(requests) == 1 and requests[0].url.host == "api.groq.com"
    assert store.reservations == [{"provider": "groq", "limit": 40}]
    assert meta["provider"] == "groq" and meta["model"] == "qwen/qwen3.8-27b"
    event = next(e["payload"] for e in store.events if e["event_type"] == "MODEL_RESPONSE_RECEIVED")
    assert event["speech_act"] == "ASK" and event["speech_act_explicit"] is True


@pytest.mark.asyncio
async def test_groq_rate_limit_never_switches_to_configured_router_backup():
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(429, json={"error": {"message": "Synthetic quota"}})
    gateway, store, harness, session, run = setup(handler)
    try:
        with pytest.raises(ProviderError) as error:
            await harness._extract(session, run)
    finally:
        await gateway.close()
    assert error.value.code == "RATE_LIMITED"
    assert len(requests) == 1 and requests[0].url.host == "api.groq.com"
    assert store.reservations == [{"provider": "groq", "limit": 40}]


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [{"GROQ_API_KEY": ""}, {"GROQ_FREE_TIER_CONFIRMED": False}])
async def test_unconfigured_or_unconfirmed_groq_stops_before_budget_or_http(overrides):
    def forbidden(request):
        pytest.fail("Unconfigured provider dispatched HTTP")
    gateway, store, harness, session, run = setup(forbidden, **overrides)
    try:
        with pytest.raises(ProviderError) as error:
            await harness._extract(session, run)
    finally:
        await gateway.close()
    assert error.value.code in {"CONFIG_MISSING", "CONFIG_BLOCKED"}
    assert store.reservations == []


def test_groq_key_is_redacted_in_public_response_values_and_fields(monkeypatch):
    secret = "synthetic-groq-public-response-canary"
    monkeypatch.setenv("GROQ_API_KEY", secret)
    assert _clean({"message": "Blocked " + secret, "groq_api_key": secret}) == {"message": "Blocked [REDACTED]"}
