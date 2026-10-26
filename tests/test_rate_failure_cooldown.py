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
@pytest.mark.parametrize('provider', ['zai', 'cloudflare', 'groq'])
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


@pytest.mark.asyncio
async def test_groq_token_minute_size_error_does_not_claim_daily_quota(ledger):
    store, at = await fail(ledger, 'groq', rate_limits={
        'token_window': 'TPM', 'token_limit_from_error': 8000, 'tokens_requested_from_error': 10000,
        'tokens_remaining': 8000, 'requests_remaining': 1000})
    early = await store.model_availability('groq', 'synthetic-rate-test', now=at + timedelta(seconds=60))
    assert not early['available'] and early['retry_after'] == 240 and not early['allow_fallback']
    late = await store.model_availability('groq', 'synthetic-rate-test', now=at + timedelta(seconds=300))
    assert late['available']
    assert not any(e['event_type'] == 'MODEL_CALL_STARTED' for e in await store.list_events(ledger[1]))


@pytest.mark.asyncio
async def test_groq_explicit_daily_token_limit_remains_blocked_and_reset_is_unconfirmed(ledger):
    store, at = await fail(ledger, 'groq', retry_after_seconds=1, rate_limits={
        'token_window': 'TPD', 'token_limit_from_error': 200000,
        'tokens_used_from_error': 199000, 'tokens_requested_from_error': 2000})
    status = await store.model_availability('groq', 'synthetic-rate-test', now=at + timedelta(minutes=10))
    assert not status['available'] and not status['allow_fallback']
    assert status['reason'] == 'provider_usage_limit_reset_unconfirmed'


@pytest.mark.asyncio
@pytest.mark.parametrize('source', ['account_quota', 'key_rate_limit', 'workspace_credit_limit'])
async def test_groq_explicit_account_restriction_cannot_be_downgraded_to_short_wait(ledger, source):
    store, at = await fail(ledger, 'groq', limit_source=source, retry_after_seconds=1)
    status = await store.model_availability('groq', 'synthetic-rate-test', now=at + timedelta(minutes=10))
    assert not status['available'] and status['scope'] == 'provider' and not status['allow_fallback']
