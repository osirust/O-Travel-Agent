"""Documented billing business codes are not transient request-rate failures."""
import json

import httpx
import pytest

from otravel import providers
from otravel.providers import LLMGateway, ProviderError, public_rate_failure_fields


async def observed_error(code, *, status=429):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={'error': {'code': code, 'message': 'PRIVATE-UPSTREAM-MESSAGE'}})
    gateway = LLMGateway({'http_transport': httpx.MockTransport(handler)})
    try:
        with pytest.raises(ProviderError) as captured:
            await gateway._request('zai', 'POST', 'https://api.z.ai/api/paas/v4/chat/completions', json={})
        return captured.value, len(calls)
    finally:
        await gateway.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('code', [1113, '1113', 1309, 1311, 1314, 1315])
async def test_documented_billing_keeps_compatible_code_but_is_not_retryable_and_has_a_resource_message(code):
    error, calls = await observed_error(code)
    assert error.code == 'RATE_LIMITED' and calls == 1
    assert error.details['failure_kind'] == 'billing'
    assert error.retryable is False
    assert 'ресурс' in error.message.casefold() or 'баланс' in error.message.casefold()
    assert 'ограничил запросы' not in error.message
    assert 'PRIVATE-UPSTREAM-MESSAGE' not in json.dumps(error.as_dict())


@pytest.mark.asyncio
@pytest.mark.parametrize('code,kind', [(1302, 'request_rate'), (1305, 'capacity'), (1308, 'usage_quota'), ('unknown', 'unknown')])
async def test_other_429_categories_keep_the_existing_retry_contract_without_billing_claim(code, kind):
    error, calls = await observed_error(code)
    assert error.code == 'RATE_LIMITED' and error.retryable is True and calls == 1
    assert error.details['failure_kind'] == kind
    assert 'ограничил запросы' in error.message


@pytest.mark.parametrize('provider,details', [
    ('openrouter', {'http_status': 429, 'business_code': 1113, 'failure_kind': 'billing'}),
    ('zai', {'http_status': 500, 'business_code': 1113}),
    ('zai', {'http_status': 429, 'business_code': '1113'}),
    ('zai', {'http_status': 429, 'business_code': True}),
    ('zai', {'http_status': 429, 'business_code': 9999, 'failure_kind': 'billing'}),
    ('zai', {'http_status': 429, 'failure_kind': 'billing'}),
    ('zai', ['private']),
    ({'provider': 'zai'}, {'http_status': 429, 'business_code': 1113}),
    (['zai'], {'http_status': 429, 'business_code': 1113}),
    (None, {'http_status': 429, 'business_code': 1113}),
])
def test_public_projection_never_trusts_arbitrary_kind_or_coerces_invalid_numeric_facts(provider, details):
    assert public_rate_failure_fields(provider, details) == {}


def test_public_projection_recomputes_kind_from_known_code_and_drops_private_metadata():
    fields = public_rate_failure_fields('zai', {'http_status': 429, 'business_code': 1113,
        'failure_kind': 'PRIVATE-UNTRUSTED-KIND', 'raw_response': {'secret': 'private'}, 'message': 'private'})
    assert fields == {'business_code': 1113, 'failure_kind': 'billing'}
    assert public_rate_failure_fields('cloudflare', {'http_status': 429, 'business_code': 3036}) == {
        'business_code': 3036, 'failure_kind': 'daily_quota'}


def test_code_owned_public_enum_survives_short_credential_redaction_while_private_text_does_not():
    previous = set(providers._SECRETS)
    providers._SECRETS.add('billing')
    try:
        error = ProviderError('RATE_LIMITED', 'Private billing', False, 'zai', {
            'http_status': 429, 'business_code': 1113, 'failure_kind': 'billing', 'private_text': 'billing'})
        assert error.details['failure_kind'] == 'billing'
        assert error.details['private_text'] == '[REDACTED]'
        assert error.message == 'Private [REDACTED]'
    finally:
        providers._SECRETS.clear()
        providers._SECRETS.update(previous)


@pytest.mark.asyncio
@pytest.mark.parametrize('status,retryable', [(401, False), (403, False), (500, True), (503, True)])
async def test_non429_transport_failure_does_not_become_billing_from_a_body_code(status, retryable):
    error, calls = await observed_error(1113, status=status)
    assert error.code == 'HTTP_ERROR' and error.retryable is retryable and calls == 1
    assert 'failure_kind' not in error.details
    assert public_rate_failure_fields(error.provider, error.details) == {}
