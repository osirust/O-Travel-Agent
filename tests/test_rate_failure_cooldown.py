"""Classified and legacy429 waits through PostgreSQL; zero real inference."""
from datetime import timedelta
from unittest.mock import patch

import pytest
from test_local_model_budget import ledger as rate_ledger

ledger = rate_ledger


async def fail(ledger, provider, *, at=None, **facts):
    store, sid, day = ledger
    instant = at or day
    with patch('otravel.store._now', return_value=instant):
        await store.save_event(sid, None, 'MODEL_CALL_FAILED', {
            'provider': provider, 'model': 'synthetic-rate-test', 'code': 'RATE_LIMITED',
            'http_status': 429, 'limit_source': 'unknown', **facts})
    return store, instant


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['zai', 'cloudflare'])
@pytest.mark.parametrize('hint', [None, True, 300.0, '300', -1])
async def test_unknown429_does_not_claim_a_daily_quota_and_requires_new_explicit_request(ledger, provider, hint):
    store, at = await fail(ledger, provider, retry_after_seconds=hint)
    blocked = await store.model_availability(provider, 'synthetic-rate-test', now=at + timedelta(seconds=1))
    assert not blocked['available'] and blocked['retry_after'] == 299
    assert blocked['reason'] == 'unconfirmed_429_engineering_wait' and not blocked['allow_fallback']
    assert (await store.model_availability(provider, 'synthetic-rate-test', now=at + timedelta(seconds=300)))['available']
    # Availability checks never create reservations or HTTP attempts.
    assert not any(e['event_type'] == 'MODEL_CALL_STARTED' for e in await store.list_events(ledger[1]))


@pytest.mark.asyncio
async def test_confirmed_cf_daily_quota_ignores_short_retry_header_until_utc_reset(ledger):
    store, at = await fail(ledger, 'cloudflare', business_code=3036, retry_after_seconds=10)
    blocked = await store.model_availability('cloudflare', 'synthetic-rate-test', now=at + timedelta(seconds=60))
    assert not blocked['available'] and blocked['reason'] == 'confirmed_daily_quota'
    assert blocked['retry_after'] == 86340
    assert (await store.model_availability('cloudflare', 'synthetic-rate-test', now=at + timedelta(days=1)))['available']


@pytest.mark.asyncio
@pytest.mark.parametrize('code', [1113, 1308, 1310])
async def test_documented_billing_or_usage_is_not_downgraded_to_a_brief_wait(ledger, code):
    store, at = await fail(ledger, 'zai', business_code=code, retry_after_seconds=1)
    blocked = await store.model_availability('zai', 'synthetic-rate-test', now=at + timedelta(seconds=300))
    assert not blocked['available']
    assert blocked['reason'] == 'provider_billing_or_usage_limit_reset_unconfirmed'


@pytest.mark.asyncio
async def test_unknown_wait_persists_across_midnight_and_uses_valid_provider_hint(ledger):
    at = ledger[2] + timedelta(hours=23, minutes=59, seconds=50)
    store, _ = await fail(ledger, 'zai', at=at, business_code=1302, retry_after_seconds=60)
    blocked = await store.model_availability('zai', 'synthetic-rate-test', now=at + timedelta(seconds=30))
    assert not blocked['available'] and blocked['retry_after'] == 30
    assert blocked['reason'] == 'provider_retry_after'
    assert (await store.model_availability('zai', 'synthetic-rate-test', now=at + timedelta(seconds=60)))['available']
