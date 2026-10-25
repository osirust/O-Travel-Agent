"""Durable model cooldown policy on isolated PG; no HTTP, inference or keys.

Each fixture uses the reserved test-provider namespace with a UUID. Classification
and SQL are identical to real providers, while global fail-closed events cannot
contaminate another test's provider or a runtime OpenRouter/Gemini ledger.
"""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
import pytest_asyncio

from otravel.store import Store, StoreError


BASE = datetime(2026, 10, 25, 23, 58, tzinfo=timezone.utc)
PRIMARY = "test/primary:free"
BACKUP = "test/backup:free"


@pytest_asyncio.fixture
async def ledger():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not configured; no database contacted")
    repository = Store(url)
    failed = False
    try:
        await repository.initialize()
    except Exception:
        failed = True
    if failed:
        await repository.close()
        pytest.fail("Cooldown test database unavailable; connection details redacted", pytrace=False)
    session = await repository.create_session({"fixture": "model-cooldown"})
    provider = "test_cooldown_" + uuid4().hex
    try:
        yield repository, session["session_id"], provider
    finally:
        await repository.close()


async def record(ledger, kind, at=BASE, model=PRIMARY, **payload):
    repository, sid, provider = ledger
    with patch("otravel.store._now", return_value=at):
        return await repository.save_event(sid, None, kind, {"provider": provider, "model": model, **payload})


async def availability(ledger, at=BASE, model=PRIMARY):
    repository, _, provider = ledger
    return await repository.model_availability(provider, model, now=at)


@pytest.mark.asyncio
async def test_empty_ledger_is_available_and_lookup_performs_no_write(ledger):
    repository, sid, _ = ledger
    before = await repository.list_events(sid)
    result = await availability(ledger)
    assert result["available"] is True and result["retry_after"] is None
    assert result["allow_fallback"] is False
    assert await repository.list_events(sid) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,reason", [
    ({"code": "RATE_LIMITED", "http_status": 429, "limit_source": "upstream_provider_shared_pool"}, "upstream_pool_cooldown"),
    ({"code": "HTTP_ERROR", "http_status": 500}, "upstream_http_cooldown"),
    ({"code": "HTTP_ERROR", "http_status": 503}, "upstream_http_cooldown"),
    ({"code": "HTTP_ERROR", "http_status": 599}, "upstream_http_cooldown"),
    ({"code": "NETWORK_ERROR"}, "network_cooldown"),
    ({"code": "REQUEST_TIMEOUT"}, "timeout_cooldown"),
])
async def test_known_transient_failures_have_exact_ten_minute_cooldown(ledger, payload, reason):
    await record(ledger, "MODEL_CALL_FAILED", **payload)
    result = await availability(ledger)
    assert result["available"] is False and result["allow_fallback"] is True
    assert result["scope"] == "model" and result["reason"] == reason
    assert result["retry_after"] == 600
    assert datetime.fromisoformat(result["retry_at"]) == BASE + timedelta(minutes=10)
    assert (await availability(ledger, BASE + timedelta(seconds=599)))["retry_after"] == 1
    assert (await availability(ledger, BASE + timedelta(seconds=600)))["available"] is True


@pytest.mark.asyncio
async def test_cooldown_is_global_across_sessions_and_new_store_instances(ledger):
    repository, _, provider = ledger
    await record(ledger, "MODEL_CALL_FAILED", code="NETWORK_ERROR")
    await repository.create_session({"fixture": "second-session"})
    another = Store(os.environ["TEST_DATABASE_URL"])
    try:
        result = await another.model_availability(provider, PRIMARY, now=BASE + timedelta(seconds=25))
        assert result["available"] is False and result["retry_after"] == 575
    finally:
        await another.close()


@pytest.mark.asyncio
async def test_other_model_or_provider_does_not_inherit_model_cooldown(ledger):
    repository, _, provider = ledger
    await record(ledger, "MODEL_CALL_FAILED", code="NETWORK_ERROR")
    assert (await availability(ledger, model=BACKUP))["available"] is True
    assert (await repository.model_availability("test_other_" + uuid4().hex, PRIMARY, now=BASE))["available"] is True
    await record(ledger, "MODEL_RESPONSE_RECEIVED", at=BASE + timedelta(seconds=1), model=BACKUP)
    assert (await availability(ledger, BASE + timedelta(seconds=2)))["available"] is False


@pytest.mark.asyncio
async def test_latest_success_clears_its_model_then_new_failure_restarts_timer(ledger):
    await record(ledger, "MODEL_CALL_FAILED", code="NETWORK_ERROR")
    await record(ledger, "MODEL_RESPONSE_RECEIVED", at=BASE + timedelta(seconds=20))
    result = await availability(ledger, BASE + timedelta(seconds=21))
    assert result["available"] is True and result["reason"] == "confirmed_model_response"
    await record(ledger, "MODEL_CALL_FAILED", at=BASE + timedelta(seconds=30), code="REQUEST_TIMEOUT")
    assert (await availability(ledger, BASE + timedelta(seconds=30)))["retry_after"] == 600


@pytest.mark.asyncio
async def test_same_timestamp_uses_durable_event_sequence_for_latest_result(ledger):
    await record(ledger, "MODEL_CALL_FAILED", code="NETWORK_ERROR")
    await record(ledger, "MODEL_RESPONSE_RECEIVED")
    assert (await availability(ledger))["available"] is True
    await record(ledger, "MODEL_CALL_FAILED", code="NETWORK_ERROR")
    assert (await availability(ledger))["retry_after"] == 600


@pytest.mark.asyncio
@pytest.mark.parametrize("source", [None, "", "account_quota", "key_limit", "workspace_credit_limit", "exhausted_global_quota"])
async def test_account_or_unknown_429_blocks_all_models_without_fallback_until_utc_day(ledger, source):
    payload = {"code": "RATE_LIMITED", "http_status": 429}
    if source is not None:
        payload["limit_source"] = source
    await record(ledger, "MODEL_CALL_FAILED", **payload)
    for model in (PRIMARY, BACKUP):
        result = await availability(ledger, model=model)
        assert result["available"] is False and result["allow_fallback"] is False
        assert result["scope"] == "provider" and result["retry_after"] == 120
    assert (await availability(ledger, BASE + timedelta(minutes=2)))["available"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 402, 403])
async def test_auth_and_credit_errors_are_provider_wide_until_confirmed_success(ledger, status):
    await record(ledger, "MODEL_CALL_FAILED", code="HTTP_ERROR", http_status=status)
    result = await availability(ledger, model=BACKUP)
    assert result["available"] is False and result["allow_fallback"] is False
    await record(ledger, "MODEL_RESPONSE_RECEIVED", at=BASE + timedelta(seconds=1), model=BACKUP)
    assert (await availability(ledger, BASE + timedelta(seconds=2)))["available"] is True


@pytest.mark.asyncio
async def test_http_error_without_status_fails_closed_not_assumed_to_be_5xx(ledger):
    await record(ledger, "MODEL_CALL_FAILED", code="HTTP_ERROR")
    result = await availability(ledger, model=BACKUP)
    assert result["available"] is False and result["allow_fallback"] is False
    assert result["reason"] == "unclassified_http_error"


@pytest.mark.asyncio
async def test_metadata_get_and_model_less_event_never_clear_provider_limit(ledger):
    repository, sid, provider = ledger
    await record(ledger, "MODEL_CALL_FAILED", code="RATE_LIMITED", http_status=429)
    await record(ledger, "MODEL_METADATA_RECEIVED", at=BASE + timedelta(seconds=5), model=BACKUP, healthy=True)
    with patch("otravel.store._now", return_value=BASE + timedelta(seconds=6)):
        await repository.save_event(sid, None, "MODEL_RESPONSE_RECEIVED", {"provider": provider})
    assert (await availability(ledger, BASE + timedelta(seconds=7), model=BACKUP))["available"] is False


@pytest.mark.asyncio
async def test_local_schema_failure_does_not_clear_or_extend_existing_cooldown(ledger):
    await record(ledger, "MODEL_CALL_FAILED", code="NETWORK_ERROR")
    await record(ledger, "MODEL_CALL_FAILED", at=BASE + timedelta(minutes=5), code="INVALID_LLM_RESPONSE")
    result = await availability(ledger, BASE + timedelta(minutes=5))
    assert result["available"] is False and result["retry_after"] == 300


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"code": "INVALID_LLM_RESPONSE"}, {"code": "INVALID_LLM_OPERATION"},
    {"code": "HTTP_ERROR", "http_status": 400}, {"code": "HTTP_ERROR", "http_status": 404},
])
async def test_non_availability_failures_do_not_disable_model(ledger, payload):
    await record(ledger, "MODEL_CALL_FAILED", **payload)
    assert (await availability(ledger))["available"] is True


@pytest.mark.asyncio
async def test_model_cooldown_crosses_midnight_but_account_block_resets_in_utc(ledger):
    await record(ledger, "MODEL_CALL_FAILED", code="NETWORK_ERROR")
    result = await availability(ledger, BASE + timedelta(minutes=4))
    assert result["available"] is False and result["retry_after"] == 360
    await record(ledger, "MODEL_RESPONSE_RECEIVED", at=BASE + timedelta(minutes=5))
    await record(ledger, "MODEL_CALL_FAILED", at=BASE + timedelta(minutes=6), code="RATE_LIMITED", http_status=429)
    plus_three = timezone(timedelta(hours=3))
    # Same UTC day as the later quota block; local midnight is irrelevant.
    query = (BASE + timedelta(minutes=7)).astimezone(plus_three)
    assert (await availability(ledger, query, model=BACKUP))["available"] is False
    tomorrow = datetime(2026, 10, 27, tzinfo=timezone.utc)
    assert (await availability(ledger, tomorrow, model=BACKUP))["available"] is True


@pytest.mark.asyncio
async def test_future_events_do_not_change_an_earlier_snapshot(ledger):
    await record(ledger, "MODEL_CALL_FAILED", at=BASE + timedelta(minutes=1), code="RATE_LIMITED", http_status=429)
    assert (await availability(ledger, BASE))["available"] is True


@pytest.mark.asyncio
async def test_arguments_fail_closed_without_querying_unknown_provider(ledger):
    repository, _, provider = ledger
    with pytest.raises(StoreError) as caught:
        await repository.model_availability("openrouter_typo", PRIMARY, now=BASE)
    assert caught.value.code == "invalid_provider"
    with pytest.raises(StoreError):
        await repository.model_availability(provider, "", now=BASE)
    with pytest.raises(StoreError) as caught:
        await repository.model_availability(provider, PRIMARY, now=BASE.replace(tzinfo=None))
    assert caught.value.code == "invalid_clock"
