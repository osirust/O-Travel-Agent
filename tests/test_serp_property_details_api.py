"""Source-derived Serp detail fixture through real PG/Store and public ASGI.

All supplier requests are intercepted by MockTransport; no LLM or Native calls.
"""
import json
import os
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from otravel.api import create_app
from otravel.config import Settings
from otravel.providers import TravelGateway
from otravel.store import Store, demo_orders, quotes

FIXTURE = json.loads((Path(__file__).parents[1] / 'fixtures/regression/serp-hotel-details-20261027.json').read_text(encoding='utf-8'))


class NoModel:
    async def extract_operations(self, *_args, **_kwargs):
        pytest.fail('Typed Serp detail lookup must not invoke a model')

    async def close(self):
        return None


@pytest_asyncio.fixture
async def serp_details_api():
    url = os.environ.get('TEST_DATABASE_URL')
    if not url:
        pytest.skip('An explicit isolated PostgreSQL URL is required')
    assert make_url(url).database == 'otravel_test', 'This test only permits the isolated test database'
    calls, response_body = [], deepcopy(FIXTURE['details_body'])

    def intercepted(request):
        assert request.method == 'GET' and request.url.host == 'serpapi.com' and request.url.path == '/search.json'
        assert request.url.params['engine'] == 'google_hotels'
        assert request.url.params['property_token'] == FIXTURE['request']['property_token']
        assert request.url.params['adults'] == '1' and request.url.params['children'] == '0'
        assert request.url.params['check_in_date'] == FIXTURE['request']['checkin']
        assert request.url.params['check_out_date'] == FIXTURE['request']['checkout']
        assert request.url.params['currency'] == 'RUB' and 'q' not in request.url.params
        calls.append({'host': request.url.host, 'path': request.url.path})
        return httpx.Response(200, json=deepcopy(response_body))

    gateway = TravelGateway({'SERPAPI_API_KEY': 'synthetic-serp-detail-api-key',
        'SERPAPI_FREE_TIER_CONFIRMED': True, 'http_transport': httpx.MockTransport(intercepted)})
    repository = Store(url)
    app = create_app(Settings(url, 'gemini', 1, 8000, 0, 1), store=repository, llm=NoModel(), travel=gateway)
    infrastructure_failure = None
    try:
        async with (app.router.lifespan_context(app),
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client):
            yield client, repository, calls, response_body
    except Exception as exc:
        if isinstance(exc, AssertionError):
            raise
        infrastructure_failure = type(exc).__name__
    finally:
        await gateway.close()
        await repository.close()
    if infrastructure_failure:
        pytest.fail('Serp detail integration infrastructure failure (' + infrastructure_failure + '); private DB details omitted', pytrace=False)


async def own_lookup(client):
    response = await client.post('/api/v1/sessions', json={})
    assert response.status_code == 201
    session = response.json()
    headers = {'Authorization': 'Bearer ' + session['session_token']}
    accepted = await client.post('/api/v1/sessions/' + session['session_id'] + '/live/search', headers=headers,
        json={'client_message_id': str(uuid4()), 'expected_version': session['state_version'], 'kind': 'hotels',
            'provider': 'serpapi', 'request': deepcopy(FIXTURE['request']), 'demo_fallback': False})
    assert accepted.status_code == 202
    import asyncio
    run_id = accepted.json()['run_id']
    for _ in range(150):
        result = await client.get('/api/v1/runs/' + run_id, headers=headers)
        assert result.status_code == 200
        body = result.json()
        if body['status'] in {'completed', 'failed', 'cancelled', 'conflict', 'interrupted'}:
            return session, headers, body
        await asyncio.sleep(0.02)
    pytest.fail('Own Serp detail run did not finish in the bounded polling window')


async def assert_no_checkout(repository, session_id):
    async with repository.engine.connect() as conn:
        assert await conn.scalar(select(func.count()).select_from(quotes).where(quotes.c.session_id == session_id)) == 0
        assert await conn.scalar(select(func.count()).select_from(demo_orders).where(demo_orders.c.session_id == session_id)) == 0
    events = await repository.list_events(session_id)
    assert not any(event['event_type'] == 'MODEL_CALL_STARTED' for event in events)
    assert sum(event['event_type'] == 'TOOL_CALL_STARTED' for event in events) == 1


@pytest.mark.asyncio
async def test_source_derived_details_survive_gateway_store_and_public_run_wrapper_without_false_bookability(serp_details_api):
    client, repository, calls, _ = serp_details_api
    session, headers, public_run = await own_lookup(client)
    assert public_run['status'] == 'completed'
    # Exact public GET /runs/{id} pointer: /result/result/live_offers/0/details/supplier_comparison.
    result = public_run['result']['result']
    assert result['plans'] == [] and result['source_type'] == 'LIVE_INDICATIVE'
    assert len(result['live_offers']) == 1 and len(calls) == 1
    offer = result['live_offers'][0]
    assert offer['provider'] == 'serpapi' and offer['source_type'] == 'LIVE_INDICATIVE'
    assert offer['description'] == 'FIVE Jumeirah Village Hotel'
    assert offer['amount_minor'] == 3_980_600 and offer['currency'] == 'RUB'
    assert offer['price_kind'] == 'INDICATIVE_ROOM_RATE_STAY_TOTAL'
    details = offer['details']
    assert details['provider_data']['property_token'] == FIXTURE['request']['property_token']
    assert details['search_request'] == FIXTURE['request']
    assert details['ready_for_real_booking'] is False
    assert details['allocation_verified'] is False and details['availability_verified'] is False
    comparison = details['supplier_comparison']
    assert comparison['status'] == 'NOT_EQUIVALENT_OR_UNPROVEN'
    assert comparison['equivalent'] is False and comparison['supplier_bookable'] is False
    assert comparison['requested_guests'] == 1 and comparison['seller_count'] == 12
    assert len(comparison['comparison_groups']) == 1
    group = comparison['comparison_groups'][0]
    assert group['property_token'] == FIXTURE['request']['property_token'] and group['num_guests'] == 1
    assert group['checkin'] == '2026-11-10' and group['checkout'] == '2026-11-13'
    assert len(group['rates']) == 6 and all(rate['num_guests'] == 1 for rate in group['rates'])
    assert {rate['supplier_name'] for rate in group['rates']} == {'Agoda', 'Booking.com', 'Priceline'}
    selected = comparison['selected_reference_rate']
    assert selected['supplier_name'] == 'Agoda' and selected['room_name'] == 'Superior'
    assert selected['num_guests'] == 1 and selected['amount_minor'] == offer['amount_minor']
    assert comparison['excluded']['other_guest_count'] == 51
    assert 'bed_configuration' in selected['missing_evidence']
    assert selected['cancellation_deadline_verified'] is False
    assert all(rate['supplier_bookable'] is False and rate['equivalent'] is False for rate in group['rates'])
    # Compare the actual nested JSONB, not merely a flattened or reconstructed response.
    stored = await repository.get_run(public_run['run_id'])
    assert stored['result']['result']['live_offers'][0]['details']['supplier_comparison'] == comparison
    fresh_run = await client.get('/api/v1/runs/' + public_run['run_id'], headers=headers)
    assert fresh_run.json()['result']['result']['live_offers'][0]['details']['supplier_comparison'] == comparison
    current = await client.get('/api/v1/sessions/' + session['session_id'], headers=headers)
    assert current.json()['trip'] == session['trip'] and current.json()['state_version'] == session['state_version']
    await assert_no_checkout(repository, session['session_id'])


@pytest.mark.asyncio
async def test_wrong_property_details_fail_in_background_without_returning_substituted_offer_or_order(serp_details_api):
    client, repository, calls, body = serp_details_api
    body['property_token'] = 'other-property-test'
    session, headers, public_run = await own_lookup(client)
    assert public_run['status'] == 'failed' and public_run['error']['code'] == 'REPRICE_IDENTITY_MISMATCH'
    assert 'live_offers' not in ((public_run.get('result') or {}).get('result') or {})
    current = await client.get('/api/v1/sessions/' + session['session_id'], headers=headers)
    assert current.json()['trip'] == session['trip']
    assert len(calls) == 1
    await assert_no_checkout(repository, session['session_id'])
