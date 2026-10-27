"""ASGI error diagnostics only; no PostgreSQL, provider HTTP or model calls."""
import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest

from otravel import api
from otravel.config import Settings
from otravel.domain import sample_trip
from otravel.providers import ProviderError


def app_for(failure):
    app = api.create_app(Settings('unused', 'groq', 1, 8000, 0, 1), store=SimpleNamespace(),
        llm=SimpleNamespace(), travel=SimpleNamespace(), media=SimpleNamespace())

    @app.get('/controlled-provider-failure', include_in_schema=False)
    async def controlled_provider_failure():
        raise failure

    return app


async def response_for(failure):
    # Deliberately no application lifespan: no DB initialization or workers.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(failure)), base_url='http://test') as client:
        return await client.get('/controlled-provider-failure')


@pytest.mark.asyncio
async def test_provider_failure_keeps_core_error_contract_and_only_three_optional_safe_fields(monkeypatch):
    secret = 'synthetic-private-token-never-a-real-key'
    monkeypatch.setenv('LITEAPI_API_KEY', secret)
    failure = ProviderError('HTTP_ERROR', 'Provider unavailable ' + secret, True, 'liteapi', {
        'http_status': 409, 'retry_after_seconds': 37, 'raw_response': {'secret': secret},
        'url': 'https://private.invalid/' + secret, 'headers': {'Authorization': 'Bearer ' + secret},
        'provider': 'arbitrary-provider-secret', 'upstream_http_status': 666,
        'prebook_id': 'private-prebook-selection', 'native_offer_id': 'private-offer-selection',
    })
    response = await response_for(failure)
    assert response.status_code == 503
    result = response.json()
    assert set(result) == {'error'}
    error = result['error']
    assert set(error) == {'code', 'message', 'retryable', 'provider', 'upstream_http_status', 'retry_after_seconds'}
    assert error['code'] == 'HTTP_ERROR' and error['retryable'] is True
    assert error['provider'] == 'liteapi' and error['upstream_http_status'] == 409
    assert error['retry_after_seconds'] == 37
    serialized = json.dumps(result)
    assert all(value not in serialized for value in [secret, 'private.invalid', 'Authorization',
        'raw_response', 'native_offer_id', 'prebook_id', 'private-prebook-selection', 'private-offer-selection'])


@pytest.mark.parametrize('provider', ['', None, 'not-configured-provider', 'https://private.invalid/key',
    {'provider': 'liteapi'}, ['duffel'], True, 42])
@pytest.mark.asyncio
async def test_missing_or_unknown_provider_preserves_exact_legacy_payload(provider):
    response = await response_for(ProviderError('CONFIG_MISSING', 'No provider configured.', False,
        provider, {'http_status': 401, 'retry_after_seconds': 10}))
    assert response.status_code == 503
    assert response.json() == {'error': {'code': 'CONFIG_MISSING', 'message': 'No provider configured.', 'retryable': False}}


@pytest.mark.parametrize('provider', sorted(api._PUBLIC_PROVIDER_NAMES))
@pytest.mark.asyncio
async def test_allowlisted_provider_timeout_adds_identity_without_inventing_upstream_status(provider):
    response = await response_for(ProviderError('REQUEST_TIMEOUT', 'Source timeout.', True, provider))
    assert response.json() == {'error': {'code': 'REQUEST_TIMEOUT', 'message': 'Source timeout.',
        'retryable': True, 'provider': provider}}


@pytest.mark.parametrize('value', [None, True, False, '429', 429.0, 99, 600, -1, 1000000,
    float('nan'), float('inf'), {}, []])
@pytest.mark.asyncio
async def test_status_is_not_coerced_and_invalid_values_never_reach_public_json(value):
    response = await response_for(ProviderError('HTTP_ERROR', 'Source unavailable.', True, 'duffel', {'http_status': value}))
    assert response.json()['error'] == {'code': 'HTTP_ERROR', 'message': 'Source unavailable.',
        'retryable': True, 'provider': 'duffel'}


@pytest.mark.parametrize('value', [None, True, False, '30', 30.0, -1, 86401, 1000000,
    float('nan'), float('inf'), {}, []])
@pytest.mark.asyncio
async def test_retry_delay_is_not_coerced_or_reconstructed_from_other_metadata(value):
    response = await response_for(ProviderError('RATE_LIMITED', 'Source limited.', True, 'cloudflare', {
        'http_status': 429, 'retry_after_seconds': value, 'retry_after': 60, 'rate_limits': {'reset': 90}}))
    assert response.json()['error'] == {'code': 'RATE_LIMITED', 'message': 'Source limited.', 'retryable': True,
        'provider': 'cloudflare', 'upstream_http_status': 429}


@pytest.mark.parametrize('status,retry', [(100, 0), (200, 1), (429, 86400), (599, 120)])
@pytest.mark.asyncio
async def test_diagnostic_numeric_boundaries_preserve_facts_without_changing_public_503(status, retry):
    response = await response_for(ProviderError('HTTP_ERROR', 'Source unavailable.', False, 'serpapi', {
        'http_status': status, 'retry_after_seconds': retry}))
    assert response.status_code == 503
    assert response.json()['error']['upstream_http_status'] == status
    assert response.json()['error']['retry_after_seconds'] == retry
    assert response.json()['error']['retryable'] is False


@pytest.mark.parametrize('details', [[], ['private-detail'], 'private-detail', 47, True])
@pytest.mark.asyncio
async def test_non_mapping_details_are_not_serialized(details):
    response = await response_for(ProviderError('HTTP_ERROR', 'Source unavailable.', True, 'liteapi', details))
    assert response.json()['error'] == {'code': 'HTTP_ERROR', 'message': 'Source unavailable.',
        'retryable': True, 'provider': 'liteapi'}


@pytest.mark.asyncio
async def test_owned_checkout_refresh_failure_retains_provider_attribution_and_does_not_create_quote(monkeypatch):
    class PreviewStore:
        def __init__(self):
            state = sample_trip().model_dump(mode='json')
            self.session = {'session_id': 'own', 'trip_id': state['trip_id'], 'state_version': state['state_version'],
                'state': state, 'owner_token_hash': hashlib.sha256(b'fixture-owner').hexdigest()}
            self.revocations = []
            self.plan = {'plan_id': 'priced', 'trip_id': state['trip_id'], 'state_version': state['state_version'],
                'components': [], 'offers': [{'offer_id': 'fixture-selected', 'provider': 'liteapi', 'source_type': 'SANDBOX'}]}

        async def get_session(self, session_id):
            assert session_id == 'own'
            return deepcopy(self.session)

        async def list_messages(self, session_id):
            return [{'run_id': 'search'}]

        async def get_run(self, run_id):
            return {'result': {'result': {'plans': [deepcopy(self.plan)]}}}

        async def begin_quote_preview(self, session_id, version):
            self.revocations.append((session_id, version))

        async def create_quote(self, *_args):
            raise AssertionError('A failed provider refresh must not create a new quote')

    class RefreshFailure:
        calls = 0

        async def reprice(self, selected):
            assert selected['offer_id'] == 'fixture-selected'
            self.calls += 1
            raise ProviderError('HTTP_ERROR', 'Source declined refresh.', False, 'liteapi', {
                'http_status': 409, 'raw_response': {'private': 'unpublished-source-body'}})

    # This test qualifies the exception boundary, not economic plan validation.
    monkeypatch.setattr(api, 'validate_quote', lambda *_args, **_kwargs: [])
    store, travel = PreviewStore(), RefreshFailure()
    app = api.create_app(Settings('unused', 'groq', 1, 8000, 0, 1), store=store,
        llm=SimpleNamespace(), travel=travel, media=SimpleNamespace())
    before = deepcopy(store.session)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/api/v1/sessions/own/checkout/preview',
            headers={'Authorization': 'Bearer fixture-owner'},
            json={'plan_id': 'priced', 'state_version': before['state_version']})
    assert response.status_code == 503
    assert response.json() == {'error': {'code': 'HTTP_ERROR', 'message': 'Source declined refresh.',
        'retryable': False, 'provider': 'liteapi', 'upstream_http_status': 409}}
    assert store.session == before and store.revocations == [('own', before['state_version'])]
    assert travel.calls == 1
