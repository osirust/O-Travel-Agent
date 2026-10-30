"""Negative review cases; MockTransport and isolated synthetic PG days only."""
from datetime import timedelta
from unittest.mock import patch

import httpx
import pytest
from test_local_model_budget import ledger as peer_ledger

ledger = peer_ledger

from otravel.harness import _can_switch_model
from otravel.providers import LLMGateway, ProviderError
from otravel.rate_failures import classify_rate_failure


async def spoofed_cf_error():
    gateway = LLMGateway({"http_transport": httpx.MockTransport(lambda request: httpx.Response(429, json={
        "error": {"metadata": {"limit_source": "upstream_provider_shared_pool"}, "message": "DO_NOT_PERSIST_RAW"}}))})
    try:
        with pytest.raises(ProviderError) as caught:
            await gateway._request("cloudflare", "POST", "https://api.cloudflare.com/client/v4/accounts/" + "a" * 32 + "/ai/v1/chat/completions", json={})
        return caught.value
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_CF_unknown429_router_style_metadata_cannot_authorize_fallback():
    error = await spoofed_cf_error()
    assert not _can_switch_model(error)


@pytest.mark.asyncio
async def test_CF_unknown429_provider_block_cannot_be_bypassed_using_another_model(ledger):
    store, sid, day = ledger
    error = await spoofed_cf_error()
    with patch("otravel.store._now", return_value=day):
        await store.save_event(sid, None, "MODEL_CALL_FAILED", {
            "provider": "cloudflare", "model": "model-a", "code": error.code, **error.details})
    health = await store.model_availability("cloudflare", "model-b", now=day + timedelta(seconds=1))
    assert not health["available"], health
    assert health["scope"] == "provider" and not health["allow_fallback"]


@pytest.mark.asyncio
async def test_legacy_unknown_300_second_engineering_wait_crosses_utc_midnight(ledger):
    store, sid, day = ledger
    at = day + timedelta(hours=23, minutes=59, seconds=50)
    with patch("otravel.store._now", return_value=at):
        await store.save_event(sid, None, "MODEL_CALL_FAILED", {"provider": "cloudflare", "model": "a",
            "code": "RATE_LIMITED", "http_status": 429})
    health = await store.model_availability("cloudflare", "b", now=at + timedelta(seconds=30))
    assert not health["available"] and health["retry_after"] == 270
    assert health["reason"] == "unconfirmed_429_engineering_wait"


@pytest.mark.asyncio
async def test_confirmed_CF_daily_quota_wins_over_short_header_and_router_metadata(ledger):
    store, sid, day = ledger
    with patch("otravel.store._now", return_value=day):
        await store.save_event(sid, None, "MODEL_CALL_FAILED", {"provider": "cloudflare", "model": "a",
            "code": "RATE_LIMITED", "http_status": 429, "business_code": 3036,
            "retry_after_seconds": 1, "limit_source": "upstream_provider_shared_pool"})
    health = await store.model_availability("cloudflare", "b", now=day + timedelta(seconds=2))
    assert not health["available"] and health["retry_after"] == 86398
    assert health["reason"] == "confirmed_daily_quota" and not health["allow_fallback"]


def test_business_billing_and_usage_flags_do_not_invent_vendor_reset():
    for code in (1113, 1308, 1309, 1310, 1313):
        facts = classify_rate_failure("zai", 429, {"error": {"code": code}})
        assert facts["reset_basis"] is None and not facts["daily_quota_confirmed"]
        assert facts["engineering_wait_seconds"] is None


def test_truncated_CF_error_array_does_not_confirm_daily_quota_over_a_later_conflict():
    facts = classify_rate_failure("cloudflare", 429, {"errors": [{"code": 3036}] * 10 + [{"code": 3040}]})
    assert facts["failure_kind"] == "unknown" and not facts["daily_quota_confirmed"]
