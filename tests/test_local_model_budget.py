"""Actual-provider ledger policy, not inference: isolated PG and synthetic events.

Future unused UTC days isolate these provider-global counters from real/today
traffic and concurrent test suites. No Ollama, cloud HTTP or model is invoked.
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, insert, select
from sqlalchemy.engine import make_url

from otravel.store import LOCAL_MODEL_PROVIDERS, REAL_MODEL_PROVIDERS, Store, StoreError, events


@pytest_asyncio.fixture
async def ledger():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not configured; no database contacted")
    if make_url(url).database != "otravel_test":
        pytest.fail("Local model budget requires the explicit otravel_test database", pytrace=False)
    repository = Store(url)
    failed = False
    try:
        await repository.initialize()
    except Exception:
        failed = True
    if failed:
        await repository.close()
        pytest.fail("Local budget database unavailable; details redacted", pytrace=False)
    session = await repository.create_session({"fixture": "local-model-budget-no-inference"})
    for _ in range(20):
        day = datetime(2090, 1, 1, tzinfo=timezone.utc) + timedelta(days=uuid4().int % 1_000_000)
        async with repository.engine.connect() as conn:
            present = (await conn.execute(select(func.count()).select_from(events).where(
                events.c.created_at >= day, events.c.created_at < day + timedelta(days=1)
            ))).scalar_one()
        if not present:
            break
    else:
        await repository.close()
        pytest.fail("Could not isolate an unused synthetic UTC day", pytrace=False)
    try:
        yield repository, session["session_id"], day
    finally:
        await repository.close()


async def new_run(ledger):
    repository, sid, _ = ledger
    run, _ = await repository.intake_message(sid, str(uuid4()), "Ledger fixture; no inference", 0)
    return run["run_id"]


async def reserve(ledger, run_id, provider="ollama", limit=500, at=None):
    repository, sid, day = ledger
    with patch("otravel.store._now", return_value=at or day):
        return await repository.reserve_model_call(sid, run_id, limit, provider)


async def seed(ledger, providers):
    """Synthetic historical reservations, deliberately marked and not requests.

    Null run IDs are valid legacy audit data; daily caps must still count them.
    The normal reserve method below always supplies the actual owned run ID.
    """
    repository, sid, day = ledger
    rows = [{"event_id": str(uuid4()), "session_id": sid, "run_id": None,
             "event_type": "MODEL_CALL_STARTED", "state_version": 0,
             "payload": {"provider": provider, "fixture": "synthetic_historical_reservation"},
             "created_at": day} for provider in providers]
    if rows:
        async with repository.engine.begin() as conn:
            await conn.execute(insert(events), rows)


async def started(ledger):
    repository, sid, day = ledger
    return [row for row in await repository.list_events(sid) if row["event_type"] == "MODEL_CALL_STARTED"
            and datetime.fromisoformat(row["created_at"]).date() == day.date()]


def test_local_is_real_local_not_remote_or_test_namespace():
    assert LOCAL_MODEL_PROVIDERS == frozenset({"ollama"})
    assert "ollama" not in REAL_MODEL_PROVIDERS
    assert not "ollama".startswith("test_")


@pytest.mark.asyncio
async def test_local_reservation_records_provider_and_owned_run_exactly_once(ledger):
    run = await new_run(ledger)
    assert await reserve(ledger, run)
    assert not await reserve(ledger, run)
    rows = await started(ledger)
    assert len(rows) == 1 and rows[0]["run_id"] == run
    assert rows[0]["payload"] == {"provider": "ollama"}


@pytest.mark.asyncio
async def test_ollama_hard_500_day_cap_cannot_be_raised_by_caller(ledger):
    await seed(ledger, ["ollama"] * 499)
    assert await reserve(ledger, await new_run(ledger), limit=100_000)
    assert not await reserve(ledger, await new_run(ledger), limit=100_000)
    assert sum(row["payload"]["provider"] == "ollama" for row in await started(ledger)) == 500


@pytest.mark.asyncio
async def test_499_concurrent_boundary_allows_one_local_reservation(ledger):
    await seed(ledger, ["ollama"] * 499)
    run_ids = [await new_run(ledger) for _ in range(3)]
    repository, sid, day = ledger
    with patch("otravel.store._now", return_value=day):
        results = await asyncio.gather(*(repository.reserve_model_call(sid, run, 500, "ollama") for run in run_ids))
    assert results.count(True) == 1 and results.count(False) == 2


@pytest.mark.asyncio
async def test_remote_shared_40_cap_does_not_consume_local_or_test_bucket(ledger):
    await seed(ledger, ["gemini"] * 20 + ["openrouter"] * 19)
    assert await reserve(ledger, await new_run(ledger), provider="openrouter", limit=500)
    assert not await reserve(ledger, await new_run(ledger), provider="gemini", limit=500)
    assert await reserve(ledger, await new_run(ledger), provider="groq", limit=500)
    assert await reserve(ledger, await new_run(ledger), provider="ollama", limit=1)
    assert await reserve(ledger, await new_run(ledger), provider="test_ledger", limit=1)
    assert not await reserve(ledger, await new_run(ledger), provider="injected_test_adapter", limit=1)


@pytest.mark.asyncio
async def test_groq_independent_40_day_pool_does_not_reset_or_consume_other_provider_caps(ledger):
    await seed(ledger, ["groq"] * 39)
    assert await reserve(ledger, await new_run(ledger), provider="groq", limit=500)
    assert not await reserve(ledger, await new_run(ledger), provider="groq", limit=500)
    assert await reserve(ledger, await new_run(ledger), provider="openrouter", limit=1)
    assert not await reserve(ledger, await new_run(ledger), provider="gemini", limit=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("cross_midnight", [False, True])
async def test_groq_retry_after_expires_at_provider_hint_and_survives_midnight(ledger, cross_midnight):
    repository, sid, day = ledger
    at = day + timedelta(hours=23, minutes=59, seconds=50) if cross_midnight else day
    run = await new_run(ledger)
    with patch("otravel.store._now", return_value=at):
        await repository.save_event(sid, run, "MODEL_CALL_FAILED", {"provider": "groq", "model": "qwen/qwen3.8-27b",
            "code": "RATE_LIMITED", "http_status": 429, "limit_source": "unknown", "retry_after_seconds": 60})
    blocked = await repository.model_availability("groq", "qwen/qwen3.8-27b", now=at + timedelta(seconds=30))
    assert not blocked["available"] and blocked["scope"] == "provider"
    assert blocked["retry_after"] == 30 and not blocked["allow_fallback"]
    ready = await repository.model_availability("groq", "qwen/qwen3.8-27b", now=at + timedelta(seconds=60))
    assert ready["available"]


@pytest.mark.asyncio
@pytest.mark.parametrize("hint", [None, 0, -1, 1.5, True, 86401, "30"])
async def test_invalid_groq_retry_hint_keeps_conservative_provider_block(ledger, hint):
    repository, sid, day = ledger
    run = await new_run(ledger)
    with patch("otravel.store._now", return_value=day):
        await repository.save_event(sid, run, "MODEL_CALL_FAILED", {"provider": "groq", "model": "qwen/qwen3.8-27b",
            "code": "RATE_LIMITED", "http_status": 429, "retry_after_seconds": hint})
    health = await repository.model_availability("groq", "qwen/qwen3.8-27b", now=day + timedelta(seconds=60))
    assert not health["available"] and health["scope"] == "provider"


@pytest.mark.asyncio
async def test_groq_authentication_error_cannot_be_lifted_by_retry_hint(ledger):
    repository, sid, day = ledger
    run = await new_run(ledger)
    with patch("otravel.store._now", return_value=day):
        await repository.save_event(sid, run, "MODEL_CALL_FAILED", {"provider": "groq", "model": "qwen/qwen3.8-27b",
            "code": "HTTP_ERROR", "http_status": 403, "retry_after_seconds": 1})
    health = await repository.model_availability("groq", "qwen/qwen3.8-27b", now=day + timedelta(seconds=60))
    assert not health["available"] and health["reason"] == "provider_auth_blocked"


@pytest.mark.asyncio
async def test_full_local_bucket_cannot_exhaust_remote_or_injected_test_bucket(ledger):
    await seed(ledger, ["ollama"] * 500)
    assert not await reserve(ledger, await new_run(ledger))
    assert await reserve(ledger, await new_run(ledger), provider="gemini", limit=1)
    assert await reserve(ledger, await new_run(ledger), provider="injected_test_adapter", limit=1)


@pytest.mark.asyncio
async def test_test_bucket_cannot_exhaust_actual_local_or_remote(ledger):
    await seed(ledger, ["test_ledger"] * 700 + ["injected_test_adapter"] * 700)
    assert await reserve(ledger, await new_run(ledger), provider="ollama", limit=1)
    assert await reserve(ledger, await new_run(ledger), provider="openrouter", limit=1)


@pytest.mark.asyncio
async def test_remote_per_run_cap_is_shared_by_gemini_router_and_groq(ledger):
    run = await new_run(ledger)
    assert await reserve(ledger, run, provider="gemini", limit=40)
    assert await reserve(ledger, run, provider="groq", limit=40)
    assert not await reserve(ledger, run, provider="gemini", limit=40)
    assert not await reserve(ledger, run, provider="openrouter", limit=40)
    assert not await reserve(ledger, run, provider="groq", limit=40)
    assert await reserve(ledger, await new_run(ledger), provider="openrouter", limit=40)


@pytest.mark.asyncio
async def test_caller_can_reduce_local_daily_cap(ledger):
    assert await reserve(ledger, await new_run(ledger), limit=1)
    assert not await reserve(ledger, await new_run(ledger), limit=1)
    assert not await reserve(ledger, await new_run(ledger), limit=0)


@pytest.mark.asyncio
async def test_local_reservations_survive_new_store_instance(ledger):
    repository, sid, day = ledger
    run = await new_run(ledger)
    assert await reserve(ledger, run, limit=1)
    another = Store(os.environ["TEST_DATABASE_URL"])
    try:
        with patch("otravel.store._now", return_value=day):
            assert not await another.reserve_model_call(sid, await new_run(ledger), 1, "ollama")
            assert not await another.reserve_model_call(sid, run, 500, "ollama")
    finally:
        await another.close()


@pytest.mark.asyncio
async def test_utc_rollover_does_not_reset_local_per_run_identity(ledger):
    _, _, day = ledger
    run = await new_run(ledger)
    assert await reserve(ledger, run, limit=1, at=day + timedelta(hours=23, minutes=59))
    tomorrow = day + timedelta(days=1)
    assert not await reserve(ledger, run, limit=1, at=tomorrow)
    assert await reserve(ledger, await new_run(ledger), limit=1, at=tomorrow)


@pytest.mark.asyncio
async def test_reservation_timestamp_and_utc_counter_do_not_split_at_midnight(ledger):
    repository, sid, day = ledger
    before = day + timedelta(hours=23, minutes=59, seconds=59)
    after = day + timedelta(days=1, seconds=1)
    first = await new_run(ledger)
    with patch("otravel.store._now", side_effect=[before, after]):
        assert await repository.reserve_model_call(sid, first, 1, "ollama")
    first_rows = await started(ledger)
    assert datetime.fromisoformat(first_rows[-1]["created_at"]) == before
    assert await reserve(ledger, await new_run(ledger), limit=1, at=after)


@pytest.mark.asyncio
async def test_local_network_cooldown_is_not_remote_account_block(ledger):
    repository, sid, day = ledger
    with patch("otravel.store._now", return_value=day):
        await repository.save_event(sid, None, "MODEL_CALL_FAILED", {
            "provider": "ollama", "model": "qwen3.5:4b", "code": "REQUEST_TIMEOUT"})
    local = await repository.model_availability("ollama", "qwen3.5:4b", now=day)
    assert local["available"] is False and local["retry_after"] == 600
    assert local["scope"] == "model"
    remote = await repository.model_availability("openrouter", "test/remote:free", now=day)
    assert remote["available"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,limit", [("ollama_typo", 500), ("", 500), ("ollama", True), ("ollama", -1)])
async def test_bad_local_provider_or_limit_fails_closed(ledger, provider, limit):
    repository, sid, _ = ledger
    with pytest.raises(StoreError):
        await repository.reserve_model_call(sid, await new_run(ledger), limit, provider)


@pytest.mark.asyncio
async def test_cloudflare_40_day_pool_is_independent_of_all_other_pools(ledger):
    await seed(ledger, ["gemini"] * 20 + ["openrouter"] * 20 + ["groq"] * 40 + ["cloudflare"] * 39)
    assert await reserve(ledger, await new_run(ledger), provider="cloudflare", limit=500)
    assert not await reserve(ledger, await new_run(ledger), provider="cloudflare", limit=500)
    assert not await reserve(ledger, await new_run(ledger), provider="gemini", limit=500)
    assert not await reserve(ledger, await new_run(ledger), provider="groq", limit=500)
    assert await reserve(ledger, await new_run(ledger), provider="ollama", limit=1)
    assert await reserve(ledger, await new_run(ledger), provider="test_cloudflare_ledger", limit=1)


@pytest.mark.asyncio
async def test_cloudflare_boundary_is_atomic_across_concurrent_reservations(ledger):
    await seed(ledger, ["cloudflare"] * 39)
    run_ids = [await new_run(ledger) for _ in range(3)]
    repository, sid, day = ledger
    with patch("otravel.store._now", return_value=day):
        results = await asyncio.gather(*(repository.reserve_model_call(sid, run, 500, "cloudflare") for run in run_ids))
    assert results.count(True) == 1 and results.count(False) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("other", ["gemini", "openrouter", "groq"])
@pytest.mark.parametrize("cloudflare_first", [False, True])
async def test_cloudflare_one_per_run_cannot_be_bypassed_by_remote_switching(ledger, other, cloudflare_first):
    run = await new_run(ledger)
    first, second = ("cloudflare", other) if cloudflare_first else (other, "cloudflare")
    assert await reserve(ledger, run, provider=first, limit=40)
    assert not await reserve(ledger, run, provider=second, limit=40)
    if cloudflare_first:
        assert not await reserve(ledger, run, provider="cloudflare", limit=40)


@pytest.mark.asyncio
async def test_cloudflare_lower_cap_survives_new_store_and_rollover_preserves_run_identity(ledger):
    repository, sid, day = ledger
    run = await new_run(ledger)
    assert await reserve(ledger, run, provider="cloudflare", limit=1)
    another = Store(os.environ["TEST_DATABASE_URL"])
    try:
        with patch("otravel.store._now", return_value=day):
            assert not await another.reserve_model_call(sid, await new_run(ledger), 1, "cloudflare")
        with patch("otravel.store._now", return_value=day + timedelta(days=1)):
            assert not await another.reserve_model_call(sid, run, 1, "cloudflare")
            assert await another.reserve_model_call(sid, await new_run(ledger), 1, "cloudflare")
    finally:
        await another.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("cross_midnight", [False, True])
async def test_cloudflare_retry_hint_is_persistent_and_crosses_utc_midnight(ledger, cross_midnight):
    repository, sid, day = ledger
    at = day + timedelta(hours=23, minutes=59, seconds=50) if cross_midnight else day
    with patch("otravel.store._now", return_value=at):
        await repository.save_event(sid, None, "MODEL_CALL_FAILED", {"provider": "cloudflare", "model": "test/cf-model",
            "code": "RATE_LIMITED", "http_status": 429, "retry_after_seconds": 60})
    blocked = await repository.model_availability("cloudflare", "test/cf-model", now=at + timedelta(seconds=30))
    assert not blocked["available"] and blocked["retry_after"] == 30 and not blocked["allow_fallback"]
    assert (await repository.model_availability("cloudflare", "test/cf-model", now=at + timedelta(seconds=60)))["available"]


@pytest.mark.asyncio
async def test_cloudflare_auth_block_cannot_be_cleared_by_other_provider_success(ledger):
    repository, sid, day = ledger
    with patch("otravel.store._now", return_value=day):
        await repository.save_event(sid, None, "MODEL_CALL_FAILED", {"provider": "cloudflare", "model": "test/cf-model",
            "code": "HTTP_ERROR", "http_status": 403, "retry_after_seconds": 1})
    with patch("otravel.store._now", return_value=day + timedelta(seconds=1)):
        await repository.save_event(sid, None, "MODEL_RESPONSE_RECEIVED", {"provider": "groq", "model": "test/groq-model"})
    blocked = await repository.model_availability("cloudflare", "test/cf-model", now=day + timedelta(seconds=60))
    assert not blocked["available"] and blocked["reason"] == "provider_auth_blocked"
    assert blocked["retry_after"] == 86340 and not blocked["allow_fallback"]
    assert (await repository.model_availability("cloudflare", "test/cf-model", now=day + timedelta(days=1)))["available"]


@pytest.mark.asyncio
async def test_cloudflare_transient_model_cooldown_never_advertises_fallback(ledger):
    repository, sid, day = ledger
    with patch("otravel.store._now", return_value=day):
        await repository.save_event(sid, None, "MODEL_CALL_FAILED", {"provider": "cloudflare", "model": "test/cf-model",
            "code": "HTTP_ERROR", "http_status": 503})
    blocked = await repository.model_availability("cloudflare", "test/cf-model", now=day + timedelta(seconds=60))
    assert not blocked["available"] and blocked["scope"] == "model" and blocked["retry_after"] == 540
    assert blocked["allow_fallback"] is False
    assert (await repository.model_availability("cloudflare", "test/cf-model", now=day + timedelta(minutes=10)))["available"]


@pytest.mark.asyncio
async def test_zai_default_40_day_pool_can_only_be_raised_by_explicit_caller_limit(ledger):
    await seed(ledger, ["zai"] * 39)
    assert await reserve(ledger, await new_run(ledger), provider="zai", limit=40)
    assert not await reserve(ledger, await new_run(ledger), provider="zai", limit=40)
    assert await reserve(ledger, await new_run(ledger), provider="zai", limit=2000)
    assert await reserve(ledger, await new_run(ledger), provider="openrouter", limit=1)
    assert not await reserve(ledger, await new_run(ledger), provider="gemini", limit=1)
    assert await reserve(ledger, await new_run(ledger), provider="groq", limit=1)
    assert await reserve(ledger, await new_run(ledger), provider="cloudflare", limit=1)


@pytest.mark.asyncio
async def test_zai_explicit_eval_cap_is_hard_2000_and_does_not_raise_other_remote_40_caps(ledger):
    await seed(ledger, ["zai"] * 1999 + ["groq"] * 40 + ["cloudflare"] * 40 + ["openrouter"] * 40)
    assert await reserve(ledger, await new_run(ledger), provider="zai", limit=100_000)
    assert not await reserve(ledger, await new_run(ledger), provider="zai", limit=100_000)
    for provider in ("groq", "cloudflare", "gemini", "openrouter"):
        assert not await reserve(ledger, await new_run(ledger), provider=provider, limit=100_000)
    assert await reserve(ledger, await new_run(ledger), provider="ollama", limit=1)
    assert await reserve(ledger, await new_run(ledger), provider="injected_test_adapter", limit=1)


@pytest.mark.asyncio
async def test_zai_eval_cap_is_atomic_at_1999_and_persists_across_store_instances(ledger):
    await seed(ledger, ["zai"] * 1999)
    repository, sid, day = ledger
    run_ids = [await new_run(ledger) for _ in range(3)]
    with patch("otravel.store._now", return_value=day):
        results = await asyncio.gather(*(repository.reserve_model_call(sid, run, 2000, "zai") for run in run_ids))
    assert results.count(True) == 1 and results.count(False) == 2
    another = Store(os.environ["TEST_DATABASE_URL"])
    try:
        with patch("otravel.store._now", return_value=day):
            assert not await another.reserve_model_call(sid, await new_run(ledger), 2000, "zai")
    finally:
        await another.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("other", ["gemini", "openrouter", "groq", "cloudflare"])
@pytest.mark.parametrize("zai_first", [False, True])
async def test_zai_one_per_run_cannot_be_bypassed_by_switching_any_remote(ledger, other, zai_first):
    run = await new_run(ledger)
    first, second = ("zai", other) if zai_first else (other, "zai")
    assert await reserve(ledger, run, provider=first, limit=2000)
    assert not await reserve(ledger, run, provider=second, limit=2000)
    if zai_first:
        assert not await reserve(ledger, run, provider="zai", limit=2000)


@pytest.mark.asyncio
async def test_zai_per_run_identity_survives_utc_rollover_while_daily_counter_rolls(ledger):
    _, _, day = ledger
    run = await new_run(ledger)
    assert await reserve(ledger, run, provider="zai", limit=1)
    tomorrow = day + timedelta(days=1)
    assert not await reserve(ledger, run, provider="zai", limit=1, at=tomorrow)
    assert await reserve(ledger, await new_run(ledger), provider="zai", limit=1, at=tomorrow)


@pytest.mark.asyncio
@pytest.mark.parametrize("cross_midnight", [False, True])
async def test_zai_retry_hint_is_persistent_and_survives_utc_midnight(ledger, cross_midnight):
    repository, sid, day = ledger
    at = day + timedelta(hours=23, minutes=59, seconds=50) if cross_midnight else day
    with patch("otravel.store._now", return_value=at):
        await repository.save_event(sid, None, "MODEL_CALL_FAILED", {"provider": "zai", "model": "glm-4.7-flash",
            "code": "RATE_LIMITED", "http_status": 429, "retry_after_seconds": 60})
    another = Store(os.environ["TEST_DATABASE_URL"])
    try:
        blocked = await another.model_availability("zai", "glm-4.7-flash", now=at + timedelta(seconds=30))
        assert not blocked["available"] and blocked["retry_after"] == 30 and not blocked["allow_fallback"]
        assert (await another.model_availability("zai", "glm-4.7-flash", now=at + timedelta(seconds=60)))["available"]
    finally:
        await another.close()


@pytest.mark.asyncio
async def test_zai_network_cooldown_never_advertises_fallback(ledger):
    repository, sid, day = ledger
    with patch("otravel.store._now", return_value=day):
        await repository.save_event(sid, None, "MODEL_CALL_FAILED", {"provider": "zai", "model": "glm-4.7-flash",
            "code": "NETWORK_ERROR"})
    blocked = await repository.model_availability("zai", "glm-4.7-flash", now=day + timedelta(seconds=60))
    assert not blocked["available"] and blocked["retry_after"] == 540 and not blocked["allow_fallback"]
    assert (await repository.model_availability("zai", "glm-4.7-flash", now=day + timedelta(minutes=10)))["available"]
