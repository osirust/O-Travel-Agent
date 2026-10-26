"""Explicit Groq evaluation budget; controlled extraction and synthetic PG ledger.

The 1000 ceiling is an application guard, not a claim about vendor quota. No
model, travel HTTP, credentials, native process or runtime database is used.
"""
import asyncio
import os
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

import httpx
import pytest
from test_api import _ModelPolicyStore
from test_local_model_budget import ledger as budget_ledger
from test_local_model_budget import new_run, reserve, seed, started

from otravel.config import Settings
from otravel.domain import sample_trip
from otravel.harness import Harness
from otravel.providers import LLMGateway, ProviderError
from otravel.store import Store

ledger = budget_ledger
MODEL = "qwen/qwen3.8-27b"
RESULT = {"operations": [], "control": {"action": "none"}, "reply": "Уточним поездку.",
          "unknowns": [], "conflicts": [], "speech_act": "ASK"}


class ControlledGroqGateway(LLMGateway):
    def __init__(self, outcome=None):
        super().__init__({"LLM_PROVIDER": "groq", "GROQ_API_KEY": "synthetic-unused-groq-key",
            "GROQ_MODEL": MODEL, "GROQ_FREE_TIER_CONFIRMED": True,
            "http_transport": httpx.MockTransport(lambda request: pytest.fail("HTTP is forbidden"))})
        self.calls = []
        self.outcome = outcome

    async def extract_operations(self, context):
        self.calls.append(context)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        self.last_call = {"provider": "groq", "model": MODEL}
        return RESULT if self.outcome is None else self.outcome


def setup(*, daily=40, generic=40, per_run=1, outcome=None):
    gateway = ControlledGroqGateway(outcome)
    store = _ModelPolicyStore()

    async def reserve_explicit(session_id, run_id, limit, provider):
        assert (session_id, run_id, provider) == ("policy-session", "policy-run", "groq")
        store.reservations.append({"provider": provider, "limit": limit})
        return limit > 0

    store.reserve_model_call = reserve_explicit
    settings = Settings("unused", "groq", 1, 8000, generic, per_run,
        max_groq_llm_calls_per_day=daily)
    harness = Harness(settings, store, gateway, None)
    session = {"session_id": "policy-session", "state_version": 0,
        "state": sample_trip().model_dump(mode="json"), "pending_question": None}
    run = {"run_id": "policy-run", "input_message_id": "policy-message", "input_text": "Уточни поездку"}
    return gateway, store, harness, session, run


def test_groq_default40_is_separate_from_generic_or_cloudflare_budget(monkeypatch):
    monkeypatch.delenv("MAX_GROQ_LLM_CALLS_PER_DAY", raising=False)
    monkeypatch.setenv("MAX_LLM_CALLS_PER_DAY", "2000")
    monkeypatch.setenv("MAX_CLOUDFLARE_LLM_CALLS_PER_DAY", "2000")
    settings = Settings.from_env()
    assert settings.max_groq_llm_calls_per_day == 40
    assert settings.max_llm_calls_per_day == settings.max_cloudflare_llm_calls_per_day == 2000
    assert Settings("unused", "groq", 1, 8000, 40, 1).max_groq_llm_calls_per_day == 40


@pytest.mark.parametrize("value", [0, 1, 40, 999, 1000])
def test_groq_explicit_environment_and_constructor_boundaries(value, monkeypatch):
    monkeypatch.setenv("MAX_GROQ_LLM_CALLS_PER_DAY", str(value))
    settings = Settings.from_env()
    assert settings.max_groq_llm_calls_per_day == value
    assert settings.max_cloudflare_llm_calls_per_day == 40
    assert replace(Settings("unused", "groq", 1, 8000, 40, 1), max_groq_llm_calls_per_day=value).max_groq_llm_calls_per_day == value


@pytest.mark.parametrize("value", [-1, 1001, True, False, 1.0, "40", None])
def test_groq_budget_constructor_rejects_invalid_types_and_out_of_range(value):
    with pytest.raises(ValueError, match="Groq application call budget"):
        Settings("unused", "groq", 1, 8000, 40, 1, max_groq_llm_calls_per_day=value)


@pytest.mark.parametrize("value", ["-1", "1001", "true", "1.0", "bad"])
def test_groq_environment_budget_invalid_values_are_not_coerced_or_clamped(value, monkeypatch):
    monkeypatch.setenv("MAX_GROQ_LLM_CALLS_PER_DAY", value)
    with pytest.raises(ValueError):
        Settings.from_env()


@pytest.mark.asyncio
@pytest.mark.parametrize("daily,generic", [(40, 2000), (1, 2000), (1000, 40)])
async def test_harness_reserves_only_explicit_groq_budget_not_generic(daily, generic):
    gateway, store, harness, session, run = setup(daily=daily, generic=generic)
    try:
        _, meta = await harness._extract(session, run)
    finally:
        await gateway.close()
    assert store.reservations == [{"provider": "groq", "limit": daily}]
    assert len(gateway.calls) == 1
    assert meta["provider"] == "groq" and meta["model"] == MODEL


@pytest.mark.asyncio
async def test_zero_groq_daily_budget_blocks_before_generation():
    gateway, store, harness, session, run = setup(daily=0, generic=2000)
    try:
        with pytest.raises(ProviderError) as error:
            await harness._extract(session, run)
    finally:
        await gateway.close()
    assert error.value.code == "MODEL_BUDGET_EXHAUSTED"
    assert store.reservations == [{"provider": "groq", "limit": 0}]
    assert gateway.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("daily", [40, 1000])
async def test_groq_generation_error_still_has_one_attempt_without_repair_or_fallback(daily):
    gateway, store, harness, session, run = setup(daily=daily, per_run=2,
        outcome=ProviderError("RATE_LIMITED", "Synthetic Groq limit", True, "groq",
                              {"http_status": 429, "retry_after_seconds": 60}))
    try:
        with pytest.raises(ProviderError) as error:
            await harness._extract(session, run)
    finally:
        await gateway.close()
    assert error.value.code == "RATE_LIMITED"
    assert store.reservations == [{"provider": "groq", "limit": daily}]
    assert len(gateway.calls) == len(error.value.llm_meta["attempts"]) == 1


@pytest.mark.asyncio
async def test_groq_cooldown_precedes_reservation_even_with_explicit_1000():
    gateway, store, harness, session, run = setup(daily=1000)

    async def blocked(provider, model):
        assert (provider, model) == ("groq", MODEL)
        return {"available": False, "allow_fallback": False, "reason": "provider_rate_limit",
                "retry_after": 60, "scope": "provider"}

    store.model_availability = blocked
    try:
        with pytest.raises(ProviderError) as error:
            await harness._extract(session, run)
    finally:
        await gateway.close()
    assert error.value.code == "MODEL_COOLDOWN"
    assert store.reservations == [] and gateway.calls == []


@pytest.mark.asyncio
async def test_explicit_caller40_remains40_and_larger_groq_budget_requires_explicit_choice(ledger):
    await seed(ledger, ["groq"] * 39)
    assert await reserve(ledger, await new_run(ledger), provider="groq", limit=40)
    assert not await reserve(ledger, await new_run(ledger), provider="groq", limit=40)
    assert await reserve(ledger, await new_run(ledger), provider="groq", limit=1000)
    assert sum(row["payload"]["provider"] == "groq" for row in await started(ledger)) == 41


@pytest.mark.asyncio
async def test_groq_999_to_1000_is_atomic_and_persists_across_store_instances(ledger):
    await seed(ledger, ["groq"] * 999)
    repository, sid, day = ledger
    runs = [await new_run(ledger) for _ in range(3)]
    with patch("otravel.store._now", return_value=day):
        admitted = await asyncio.gather(*(repository.reserve_model_call(sid, run, 100_000, "groq") for run in runs))
    assert admitted.count(True) == 1 and admitted.count(False) == 2
    assert sum(row["payload"]["provider"] == "groq" for row in await started(ledger)) == 1000
    another = Store(os.environ["TEST_DATABASE_URL"])
    try:
        with patch("otravel.store._now", return_value=day):
            assert not await another.reserve_model_call(sid, await new_run(ledger), 1000, "groq")
    finally:
        await another.close()


@pytest.mark.asyncio
async def test_groq_explicit1000_never_expands_shared_router_or_other_default_pools(ledger):
    await seed(ledger, ["groq"] * 999 + ["openrouter"] * 40 + ["cloudflare"] * 40 + ["zai"] * 40 + ["ollama"] * 500)
    assert await reserve(ledger, await new_run(ledger), provider="groq", limit=1000)
    assert not await reserve(ledger, await new_run(ledger), provider="groq", limit=100_000)
    for provider in ("gemini", "openrouter"):
        assert not await reserve(ledger, await new_run(ledger), provider=provider, limit=100_000)
    for provider in ("cloudflare", "zai"):
        assert not await reserve(ledger, await new_run(ledger), provider=provider, limit=40)
        assert await reserve(ledger, await new_run(ledger), provider=provider, limit=2000)
    assert not await reserve(ledger, await new_run(ledger), provider="ollama", limit=100_000)
    assert await reserve(ledger, await new_run(ledger), provider="injected_test_adapter", limit=1)


@pytest.mark.asyncio
async def test_groq_store_per_run_guard_is_not_raised_by_larger_daily_limit(ledger):
    run = await new_run(ledger)
    assert await reserve(ledger, run, provider="groq", limit=1000)
    assert await reserve(ledger, run, provider="groq", limit=1000)
    assert not await reserve(ledger, run, provider="groq", limit=1000)
    _repository, sid, day = ledger
    another = Store(os.environ["TEST_DATABASE_URL"])
    try:
        with patch("otravel.store._now", return_value=day + timedelta(days=1)):
            assert not await another.reserve_model_call(sid, run, 1000, "groq")
    finally:
        await another.close()
    assert sum(row["run_id"] == run for row in await started(ledger)) == 2
