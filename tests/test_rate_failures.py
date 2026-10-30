import json

import httpx
import pytest

from otravel.providers import LLMGateway, ProviderError
from otravel.rate_failures import classify_rate_failure


@pytest.mark.parametrize('provider,code,kind', [
    ('zai', 1113, 'billing'), ('zai', '1302', 'request_rate'), ('zai', 1305, 'capacity'),
    ('zai', 1308, 'usage_quota'), ('zai', 1310, 'usage_quota'), ('zai', 1321, 'usage_quota'),
    ('cloudflare', 3036, 'daily_quota'), ('cloudflare', 3040, 'capacity'),
])
def test_documented_business_codes_are_classified_without_provider_prose(provider, code, kind):
    facts = classify_rate_failure(provider, 429, {'error': {'code': code, 'message': 'PRIVATE-SECRET resets immediately'}})
    assert facts['failure_kind'] == kind and facts['business_code'] == int(code)
    assert facts['automatic_retry'] is False and facts['requires_explicit_new_request']
    assert 'PRIVATE' not in json.dumps(facts)
    assert facts['daily_quota_confirmed'] == (provider == 'cloudflare' and int(code) == 3036)


@pytest.mark.parametrize('body', [None, [], {}, {'message': '3036'}, {'code': 3036},
    {'error': {'code': True}}, {'error': {'code': 'secret'}}, {'error': {'code': 3036.0}},
    {'errors': [{'code': 3036}, {'code': 3040}]}, {'errors': [{'code': 3036}, {'code': 'private'}]},
    {'errors': [{'code': 3036}] * 10 + [{'code': 3040}]}])
def test_unknown_or_conflicting_shape_does_not_confirm_a_daily_quota(body):
    facts = classify_rate_failure('cloudflare', 429, body)
    assert facts['business_code'] is None and facts['failure_kind'] == 'unknown'
    assert facts['engineering_wait_seconds'] == 300 and facts['reset_basis'] is None


def test_cf_documented_error_array_and_error_object_can_agree():
    assert classify_rate_failure('cloudflare', 429, {'errors': [{'code': 3036}], 'error': {'code': '3036'}})['daily_quota_confirmed']
    assert classify_rate_failure('openrouter', 429, {'error': {'code': 3036}}) is None
    assert classify_rate_failure('cloudflare', 403, {'error': {'code': 3036}}) is None


@pytest.mark.asyncio
async def test_gateway_keeps_only_code_owned_rate_details_and_never_retries():
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(429, json={'error': {'code': '1302', 'message': 'PRIVATE-ERROR-BODY'}})
    gateway = LLMGateway({'http_transport': httpx.MockTransport(transport)})
    try:
        with pytest.raises(ProviderError) as caught:
            await gateway._request('zai', 'POST', 'https://api.z.ai/api/paas/v4/chat/completions', json={})
    finally:
        await gateway.close()
    assert len(calls) == 1
    assert caught.value.details['failure_kind'] == 'request_rate'
    assert caught.value.details['business_code'] == 1302
    assert 'PRIVATE-ERROR-BODY' not in json.dumps(caught.value.as_dict())
