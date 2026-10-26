"""Cloudflare configuration/routing with a controlled gateway; no HTTP or keys."""
import json
from dataclasses import replace

import httpx
import pytest
from pydantic import ValidationError

from otravel.config import Settings, provider_status
from otravel.domain import sample_trip
from otravel.harness import Harness, _requested_model
from otravel.providers import LLMGateway, ProviderError
from test_api import _ModelPolicyStore


MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
TOKEN = "synthetic-cloudflare-token-not-a-real-key"
ACCOUNT = "1234567890abcdef1234567890abcdef"
RESULT = {"operations": [], "control": {"action": "none"}, "reply": "Уточним поездку.",
          "unknowns": [], "conflicts": [], "speech_act": "ASK"}


class ControlledCloudflareGateway(LLMGateway):
    def __init__(self, outcome=None, **overrides):
        super().__init__({"LLM_PROVIDER": "cloudflare", "CLOUDFLARE_API_TOKEN": TOKEN,
            "CLOUDFLARE_ACCOUNT_ID": ACCOUNT, "CLOUDFLARE_FREE_TIER_CONFIRMED": True,
            "CLOUDFLARE_MODEL": MODEL, "OPENROUTER_API_KEY": "synthetic-unused-router-key",
            "OPENROUTER_MODEL": "test/unused:free", "OPENROUTER_BACKUP_MODEL": "test/backup:free",
            "http_transport": httpx.MockTransport(lambda request: pytest.fail("HTTP is forbidden")), **overrides})
        self.calls = []
        self.outcome = outcome

    async def extract_operations(self, context):
        self.calls.append(context)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        self.last_call = {"provider": "cloudflare", "model": MODEL}
        return self.outcome if self.outcome is not None else RESULT


def setup(outcome=None, **overrides):
    gateway = ControlledCloudflareGateway(outcome, **overrides)
    store = _ModelPolicyStore()
    harness = Harness(Settings("unused", "cloudflare", 1, 8000, 40, 2), store, gateway, None)
    session = {"session_id": "policy-session", "state_version": 0,
        "state": sample_trip().model_dump(mode="json"), "pending_question": None}
    run = {"run_id": "policy-run", "input_message_id": "policy-message", "input_text": "Объясни поездку"}
    return gateway, store, harness, session, run


def test_cloudflare_settings_keep_token_and_account_internal_and_default_model_explicit(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "cloudflare")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", TOKEN)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", ACCOUNT)
    monkeypatch.setenv("CLOUDFLARE_FREE_TIER_CONFIRMED", "true")
    monkeypatch.delenv("CLOUDFLARE_MODEL", raising=False)
    config = Settings.from_env().provider_settings()
    assert config["LLM_PROVIDER"] == "cloudflare"
    assert config["CLOUDFLARE_API_TOKEN"] == TOKEN and config["CLOUDFLARE_ACCOUNT_ID"] == ACCOUNT
    assert config["CLOUDFLARE_MODEL"] == MODEL and config["CLOUDFLARE_FREE_TIER_CONFIRMED"] is True
    status = provider_status()
    assert status["_runtime"] == {"llm_provider": "cloudflare", "model": MODEL}
    assert status["cloudflare"] == {"configured": True, "model": MODEL,
        "free_tier_confirmed": True, "paid_fallback": False}
    assert TOKEN not in json.dumps(status) and ACCOUNT not in json.dumps(status)


def test_cloudflare_free_confirmation_defaults_to_false(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_FREE_TIER_CONFIRMED", raising=False)
    assert Settings.from_env().provider_settings()["CLOUDFLARE_FREE_TIER_CONFIRMED"] is False
    assert provider_status()["cloudflare"]["free_tier_confirmed"] is False


@pytest.mark.asyncio
async def test_cloudflare_is_reserved_with_its_own_identity_and_requested_model():
    gateway, store, harness, session, run = setup()
    try:
        result, meta = await harness._extract(session, run)
    finally:
        await gateway.close()
    assert result.speech_act == "ASK" and len(gateway.calls) == 1
    assert store.reservations == [{"provider": "cloudflare", "limit": 40}]
    assert meta["provider"] == "cloudflare" and meta["model"] == MODEL
    assert _requested_model(gateway, "cloudflare") == MODEL


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [
    {"CLOUDFLARE_API_TOKEN": ""}, {"CLOUDFLARE_API_TOKEN": "   "},
    {"CLOUDFLARE_API_TOKEN": None}, {"CLOUDFLARE_API_TOKEN": 1},
    {"CLOUDFLARE_ACCOUNT_ID": ""}, {"CLOUDFLARE_ACCOUNT_ID": "a" * 31},
    {"CLOUDFLARE_ACCOUNT_ID": "g" * 32}, {"CLOUDFLARE_ACCOUNT_ID": ACCOUNT + "/"},
    {"CLOUDFLARE_ACCOUNT_ID": " " + ACCOUNT}, {"CLOUDFLARE_ACCOUNT_ID": None},
    {"CLOUDFLARE_FREE_TIER_CONFIRMED": False}, {"CLOUDFLARE_FREE_TIER_CONFIRMED": "false"},
])
async def test_cloudflare_preflight_blocks_before_reservation_or_model(overrides):
    gateway, store, harness, session, run = setup(**overrides)
    try:
        with pytest.raises(ProviderError) as error:
            await harness._extract(session, run)
    finally:
        await gateway.close()
    assert error.value.code in {"CONFIG_MISSING", "CONFIG_BLOCKED"}
    assert error.value.provider == "cloudflare"
    assert gateway.calls == [] and store.reservations == []
    assert TOKEN not in json.dumps(error.value.as_dict()) and ACCOUNT not in json.dumps(error.value.as_dict())


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [
    ProviderError("RATE_LIMITED", "Synthetic quota", provider="cloudflare", details={"http_status": 429}),
    ProviderError("HTTP_ERROR", "Synthetic upstream", provider="cloudflare", details={"http_status": 503}),
    ProviderError("INVALID_LLM_OPERATION", "Synthetic schema", provider="cloudflare", details={"schema_errors": []}),
    {"operations": []},
])
async def test_cloudflare_never_repairs_or_falls_back_to_router_in_same_run(outcome):
    gateway, store, harness, session, run = setup(outcome)
    try:
        with pytest.raises((ProviderError, ValidationError)) as error:
            await harness._extract(session, run)
    finally:
        await gateway.close()
    assert len(gateway.calls) == 1
    assert store.reservations == [{"provider": "cloudflare", "limit": 40}]
    assert error.value.llm_meta["provider"] == "cloudflare"
    assert len(error.value.llm_meta["attempts"]) == 1


@pytest.mark.asyncio
async def test_cloudflare_durable_cooldown_is_consulted_before_budget():
    gateway, store, harness, session, run = setup()
    checked = []
    async def unavailable(provider, model):
        checked.append((provider, model))
        return {"available": False, "allow_fallback": True, "reason": "synthetic",
                "retry_after": 30, "scope": "model"}
    store.model_availability = unavailable
    try:
        with pytest.raises(ProviderError) as error:
            await harness._extract(session, run)
    finally:
        await gateway.close()
    assert checked == [("cloudflare", MODEL)]
    assert error.value.code == "MODEL_COOLDOWN"
    assert store.reservations == [] and gateway.calls == []


@pytest.mark.asyncio
async def test_cloudflare_large_evaluation_requires_its_separate_explicit_setting():
    gateway, store, harness, session, run = setup()
    harness.settings = replace(harness.settings, max_cloudflare_llm_calls_per_day=2000)
    async def reserve_explicit(session_id, run_id, limit, provider):
        assert limit == 2000 and provider == "cloudflare"
        store.reservations.append({"provider": provider, "limit": limit})
        return True
    store.reserve_model_call = reserve_explicit
    try:
        await harness._extract(session, run)
    finally:
        await gateway.close()
    assert len(gateway.calls) == 1
    assert store.reservations == [{"provider": "cloudflare", "limit": 2000}]


def test_cloudflare_default_budget_and_explicit_env_override_are_distinct(monkeypatch):
    monkeypatch.setenv("MAX_LLM_CALLS_PER_DAY", "2000")
    monkeypatch.delenv("MAX_CLOUDFLARE_LLM_CALLS_PER_DAY", raising=False)
    assert Settings.from_env().max_cloudflare_llm_calls_per_day == 40
    monkeypatch.setenv("MAX_CLOUDFLARE_LLM_CALLS_PER_DAY", "2000")
    assert Settings.from_env().max_cloudflare_llm_calls_per_day == 2000
    monkeypatch.setenv("MAX_CLOUDFLARE_LLM_CALLS_PER_DAY", "2001")
    with pytest.raises(ValueError):
        Settings.from_env()
