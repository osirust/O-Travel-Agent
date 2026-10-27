"""Observed selected-rate refusal is not a service outage or payment action."""
import asyncio
import json
from copy import deepcopy
from pathlib import Path

import httpx
import pytest
from test_liteapi_prebook import NOW, selected

from otravel.providers import ProviderError, TravelGateway, public_offer_failure_fields

FIXTURE = json.loads((Path(__file__).parents[1] / 'fixtures/regression/liteapi-prebook2001-20261027.json').read_text(encoding='utf-8'))
URL = 'https://book.liteapi.travel/v3.0/rates/prebook'


def error_response(*, body=None, status=409, provider='liteapi', method='POST', url=URL, reprice=False):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json=deepcopy(FIXTURE['response_body'] if body is None else body))

    async def run():
        gateway = TravelGateway({'http_transport': httpx.MockTransport(handler), 'clock': lambda: NOW,
            'LITEAPI_API_KEY': 'sand_failure-fixture', 'LITEAPI_MODE': 'sandbox'})
        try:
            if reprice:
                return await gateway.reprice(selected())
            return await gateway._request(provider, method, url)
        finally:
            await gateway.close()

    with pytest.raises(ProviderError) as caught:
        asyncio.run(run())
    assert len(calls) == 1
    return caught.value, calls


@pytest.mark.parametrize('status', [400, 409])
def test_observed_business2001_is_safe_nonretryable_refresh_required(status):
    error, _ = error_response(status=status)
    assert error.code == 'OFFER_NOT_CONFIRMED'
    assert error.provider == 'liteapi' and error.retryable is False
    assert error.details == {'http_status': status, 'business_code': 2001, 'refresh_required': True}
    assert 'цену или наличие' in error.message and 'обновите варианты' in error.message.lower()


@pytest.mark.parametrize('extra', [{'data': None}, {'data': {}}, {'errors': []}, {'success': False}])
def test_error_only_envelope_may_contain_empty_optional_fields(extra):
    body = {**deepcopy(FIXTURE['response_body']), **extra}
    assert error_response(body=body)[0].code == 'OFFER_NOT_CONFIRMED'


def test_exact_numeric_string_code_is_canonicalized_without_copying_vendor_text():
    body = {'error': {'code': '2001', 'message': 'raw sand_failure-fixture',
        'description': 'do-not-copy-provider-offer-or-secret'}}
    error, _ = error_response(body=body)
    assert error.details['business_code'] == 2001 and type(error.details['business_code']) is int
    rendered = json.dumps(error.as_dict())
    assert 'sand_failure-fixture' not in rendered and 'do-not-copy' not in rendered
    assert public_offer_failure_fields(error.provider, error.code, error.details) == {'business_code': 2001, 'refresh_required': True}


@pytest.mark.parametrize('body', [
    {'error': {'code': 4002}}, {'error': {'code': 5000}}, {'error': {'code': 401}},
    {'error': {'code': 2001.0}}, {'error': {'code': True}}, {'error': {'code': '02001'}},
    {'error': {'code': '2001 '}}, {'error': {'code': None}}, {'error': 'no availability found'},
    {'errors': [{'code': 2001}]}, {'error': {'message': 'no availability found'}},
    {**deepcopy(FIXTURE['response_body']), 'errors': [{'code': 401}]},
    {**deepcopy(FIXTURE['response_body']), 'data': {'prebookId': 'contradictory-success'}},
    {**deepcopy(FIXTURE['response_body']), 'success': True},
    {**deepcopy(FIXTURE['response_body']), 'padding': 'x' * 1_000_001},
])
def test_unknown_malformed_conflicting_or_oversized_errors_keep_generic_category(body):
    error, _ = error_response(body=body)
    assert error.code == 'HTTP_ERROR' and public_offer_failure_fields(error.provider, error.code, error.details) == {}


@pytest.mark.parametrize('change', [
    {'provider': 'duffel'}, {'provider': 'zai'}, {'method': 'GET'},
    {'url': 'https://api.liteapi.travel/v3.0/hotels/rates'},
    {'url': 'https://book.liteapi.travel/v3.0/rates/book'},
    {'url': 'https://other.example/v3.0/rates/prebook'},
])
def test_normalization_requires_exact_provider_endpoint_and_method(change):
    error, _ = error_response(**change)
    assert error.code == 'HTTP_ERROR' and public_offer_failure_fields(error.provider, error.code, error.details) == {}


@pytest.mark.parametrize('status,code', [(200, 'PROVIDER_RESPONSE_ERROR'), (201, 'PROVIDER_RESPONSE_ERROR'),
    (401, 'HTTP_ERROR'), (403, 'HTTP_ERROR'), (404, 'HTTP_ERROR'), (408, 'HTTP_ERROR'),
    (422, 'HTTP_ERROR'), (429, 'RATE_LIMITED'), (500, 'HTTP_ERROR')])
def test_auth_quota_and_other_statuses_are_never_misclassified(status, code):
    error, _ = error_response(status=status)
    assert error.code == code and public_offer_failure_fields(error.provider, error.code, error.details) == {}


@pytest.mark.parametrize('field,value', [('http_status', 401), ('http_status', 429), ('http_status', 409.0),
    ('http_status', True), ('business_code', '2001'), ('business_code', 2001.0),
    ('business_code', True), ('business_code', 4002), ('refresh_required', 1), ('refresh_required', False)])
def test_public_projection_requires_canonical_code_owned_scalar_facts(field, value):
    details = {'http_status': 409, 'business_code': 2001, 'refresh_required': True, field: value}
    assert public_offer_failure_fields('liteapi', 'OFFER_NOT_CONFIRMED', details) == {}


def test_public_projection_drops_untrusted_extras_and_requires_error_identity():
    details = {'http_status': 409, 'business_code': 2001, 'refresh_required': True,
        'hotel_id': 'private', 'price': 9999, 'raw_message': 'secret', 'api_key': 'secret'}
    assert public_offer_failure_fields('liteapi', 'OFFER_NOT_CONFIRMED', details) == {'business_code': 2001, 'refresh_required': True}
    assert public_offer_failure_fields('zai', 'OFFER_NOT_CONFIRMED', details) == {}
    assert public_offer_failure_fields('liteapi', 'HTTP_ERROR', details) == {}
    assert public_offer_failure_fields('liteapi', 'OFFER_NOT_CONFIRMED', None) == {}


def test_selected_offer_adapter_makes_one_prebook_no_search_retry_or_replacement():
    original = selected()
    saved = deepcopy(original)
    error, calls = error_response(reprice=True)
    assert original == saved and error.code == 'OFFER_NOT_CONFIRMED' and error.retryable is False
    assert str(calls[0].url) == URL and calls[0].method == 'POST'
    assert json.loads(calls[0].content) == {'offerId': saved['details']['native_offer_id'], 'usePaymentSdk': False}
