"""Z.ai routing/configuration against a controlled adapter: no HTTP/inference."""
import json

import httpx
import pytest
from pydantic import ValidationError

from otravel.config import Settings, provider_status
from otravel.domain import sample_trip
from otravel.harness import Harness, _requested_model
from otravel.providers import LLMGateway, ProviderError
from test_api import _ModelPolicyStore


MODEL = "glm-4.7-flash"
KEY = "synthetic-zai-secret-not-a-real-key"
RESULT = {"operations": [], "control": {"action": "none"}, "reply": "Уточним поездку.",
          "unknowns": [], "conflicts": [], "speech_act": "ASK"}


class BudgetPolicyStore(_ModelPolicyStore):
    async def reserve_model_call(self, session_id, run_id, limit, provider, *, run_attempt_limit=None):
        assert run_attempt_limit is None or run_attempt_limit in {1, 2}
        self.reservations.append({"limit": limit, "provider": provider})
        return True


class ControlledZAIGateway(LLMGateway):
    def __init__(self, outcome=None, **overrides):
        super().__init__({"LLM_PROVIDER": "zai", "ZAI_API_KEY": KEY,
            "ZAI_FREE_TIER_CONFIRMED": True, "ZAI_MODEL": MODEL,
            "OPENROUTER_API_KEY": "synthetic-unused-router-key", "OPENROUTER_MODEL": "test/unused:free",
            "OPENROUTER_BACKUP_MODEL": "test/backup:free",
            "http_transport": httpx.MockTransport(lambda request: pytest.fail("HTTP forbidden in controlled harness test")),
            **overrides})
        self.calls = []
        self.outcome = outcome

    async def extract_operations(self, context):
        self.calls.append(context)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        self.last_call = {"provider": self.settings["LLM_PROVIDER"],
                          "model": _requested_model(self, self.settings["LLM_PROVIDER"])}
        return self.outcome if self.outcome is not None else RESULT


def setup(outcome=None, limit=40, run_attempts=1, **overrides):
    gateway = ControlledZAIGateway(outcome, **overrides)
    store = BudgetPolicyStore()
    harness = Harness(Settings("unused", gateway.settings["LLM_PROVIDER"], 1, 8000, limit, run_attempts), store, gateway, None)
    session = {"session_id": "policy-session", "state_version": 0,
        "state": sample_trip().model_dump(mode="json"), "pending_question": None}
    run = {"run_id": "policy-run", "input_message_id": "policy-message", "input_text": "Объясни поездку"}
    return gateway, store, harness, session, run


def test_zai_settings_and_status_default_to_free_flash_without_exposing_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "zai")
    monkeypatch.setenv("ZAI_API_KEY", KEY)
    monkeypatch.setenv("ZAI_FREE_TIER_CONFIRMED", "true")
    monkeypatch.delenv("ZAI_MODEL", raising=False)
    monkeypatch.delenv("MAX_LLM_CALLS_PER_DAY", raising=False)
    settings = Settings.from_env()
    configured = settings.provider_settings()
    assert settings.max_llm_calls_per_day == 40
    assert settings.llm_mode == configured["LLM_PROVIDER"] == "zai"
    assert configured["ZAI_API_KEY"] == KEY and configured["ZAI_MODEL"] == MODEL
    assert configured["ZAI_FREE_TIER_CONFIRMED"] is True
    status = provider_status()
    assert status["_runtime"] == {"llm_provider": "zai", "model": MODEL}
    assert status["zai"] == {"configured": True, "model": MODEL,
        "free_tier_confirmed": True, "paid_fallback": False}
    assert KEY not in json.dumps(status)


def test_zai_eval_limit_requires_explicit_setting_and_free_confirmation_is_false_by_default(monkeypatch):
    monkeypatch.setenv("MAX_LLM_CALLS_PER_DAY", "2000")
    monkeypatch.delenv("ZAI_FREE_TIER_CONFIRMED", raising=False)
    assert Settings.from_env().max_llm_calls_per_day == 2000
    assert Settings.from_env().provider_settings()["ZAI_FREE_TIER_CONFIRMED"] is False
    assert provider_status()["zai"]["free_tier_confirmed"] is False


@pytest.mark.parametrize("provider,model", [("groq", "qwen/qwen3.8-27b"),
    ("cloudflare", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"),
    ("openrouter", "dots-studio/dots-3-note-preview:free"), ("ollama", "qwen3.5:4b")])
def test_zai_configuration_does_not_relabel_other_current_llm_profiles(monkeypatch, provider, model):
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.setenv("ZAI_MODEL", "glm-4.5-flash")
    for key in ("GROQ_MODEL", "CLOUDFLARE_MODEL", "OPENROUTER_MODEL", "OLLAMA_MODEL"):
        monkeypatch.delenv(key, raising=False)
    assert provider_status()["_runtime"] == {"llm_provider": provider, "model": model}


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["glm-4.7-flash", "glm-4.5-flash"])
@pytest.mark.parametrize("limit,expected", [(40, 40), (2000, 2000), (2001, 2000)])
async def test_zai_keeps_requested_model_and_limits_dedicated_eval_reservations(model, limit, expected):
    gateway, store, harness, session, run = setup(limit=limit, ZAI_MODEL=model)
    try:
        _, meta = await harness._extract(session, run)
    finally:
        await gateway.close()
    assert len(gateway.calls) == 1
    assert store.reservations == [{"provider": "zai", "limit": expected}]
    assert meta["provider"] == "zai" and meta["model"] == model
    assert _requested_model(gateway, "zai") == model


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [
    {"ZAI_API_KEY": ""}, {"ZAI_API_KEY": "   "}, {"ZAI_API_KEY": None}, {"ZAI_API_KEY": 1},
    {"ZAI_FREE_TIER_CONFIRMED": False}, {"ZAI_FREE_TIER_CONFIRMED": "false"},
    {"ZAI_MODEL": "glm-4.7"}, {"ZAI_MODEL": "glm-4.7-flash:free"}, {"ZAI_MODEL": ""},
    {"ZAI_MODEL": None}, {"ZAI_MODEL": []},
])
async def test_zai_unapproved_configuration_stops_before_reservation_or_model(overrides):
    gateway, store, harness, session, run = setup(limit=2000, **overrides)
    try:
        with pytest.raises(ProviderError) as error:
            await harness._extract(session, run)
    finally:
        await gateway.close()
    assert error.value.code in {"CONFIG_MISSING", "CONFIG_BLOCKED"} and error.value.provider == "zai"
    assert gateway.calls == [] and store.reservations == []
    assert KEY not in json.dumps(error.value.as_dict())


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [
    ProviderError("RATE_LIMITED", "Synthetic quota", provider="zai", details={"http_status": 429}),
    ProviderError("HTTP_ERROR", "Synthetic upstream", provider="zai", details={"http_status": 503}),
    ProviderError("INVALID_LLM_OPERATION", "Synthetic schema", provider="zai", details={"schema_errors": []}),
    {"operations": []},
])
async def test_zai_with_one_attempt_never_repairs_or_uses_router_backup(outcome):
    gateway, store, harness, session, run = setup(outcome, limit=2000)
    try:
        with pytest.raises((ProviderError, ValidationError)) as error:
            await harness._extract(session, run)
    finally:
        await gateway.close()
    assert len(gateway.calls) == 1 and store.reservations == [{"provider": "zai", "limit": 2000}]
    assert error.value.llm_meta["provider"] == "zai" and len(error.value.llm_meta["attempts"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,overrides", [
    ("groq", {"GROQ_API_KEY": "synthetic-groq-key", "GROQ_FREE_TIER_CONFIRMED": True}),
    ("cloudflare", {"CLOUDFLARE_API_TOKEN": "synthetic-cf-key", "CLOUDFLARE_ACCOUNT_ID": "a" * 32,
                    "CLOUDFLARE_FREE_TIER_CONFIRMED": True}),
    ("openrouter", {}),
])
async def test_zai_eval_limit_does_not_raise_other_remote_harness_caps(provider, overrides):
    gateway, store, harness, session, run = setup(limit=2000, LLM_PROVIDER=provider, **overrides)
    try:
        await harness._extract(session, run)
    finally:
        await gateway.close()
    assert store.reservations == [{"provider": provider, "limit": 40}]
