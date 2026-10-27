"""Real PG/API checkout for trusted refs and explicit synthetic transfers.

Provider reads/refreshes are controlled; no actual API, booking or LLM calls.
"""
import os
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.engine import make_url
from test_dynamic_place_suppliers import dynamic_trip
from test_geo_catalog import admit, fact, key
from test_mixed_checkout import CheckoutTravel, NoLLM, confirmation, headers, plans, preview, wait

from otravel.api import create_app
from otravel.config import Settings
from otravel.domain import Operation, apply_operations
from otravel.geo_catalog import GeoCatalog
from otravel.inventory import PLACE_TRANSFER_NOTICE, PLACE_TRANSFER_PROVIDER
from otravel.place_refs import CityRefSnapshot
from otravel.store import Store


class NoDirectoryHTTP:
    async def search_city(self, *args, **kwargs):
        raise AssertionError('Trusted selected ref must not cause a geocoding query during checkout')

    async def get_place(self, *args, **kwargs):
        raise AssertionError('No new geographic selection was requested')

    async def close(self):
        pass


class WorldCheckoutTravel(CheckoutTravel):
    async def resolve_hotels(self, request, limit=3):
        assert isinstance(request, dict) and 'place_ref' in request
        ref = CityRefSnapshot.model_validate(request['place_ref'])
        return [{'id': 'controlled-world-hotel', 'identity_verified': True,
            'place_id': ref.place_id, 'place_ref_sha256': ref.snapshot_sha256()}]

    async def resolve_air_place(self, request):
        ref = CityRefSnapshot.model_validate(request['place_ref'])
        return {'status': 'RESOLVED', 'identity_verified': True, 'country_iso2': ref.country_iso2,
            'place_id': ref.place_id, 'place_ref_sha256': ref.snapshot_sha256(), 'iata_code': 'BUE'}

    async def search_hotels(self, request, provider='liteapi'):
        rows = await super().search_hotels(request, provider)
        ref = CityRefSnapshot.model_validate(request['place_ref'])
        for row in rows:
            row['details'].update(place_id=ref.place_id, place_ref_sha256=ref.snapshot_sha256())
        return rows


@pytest_asyncio.fixture
async def world_checkout(monkeypatch):
    url = os.environ.get('TEST_DATABASE_URL')
    if not url:
        pytest.skip('Only isolated TEST_DATABASE_URL is used')
    if make_url(url).database != 'otravel_test':
        pytest.fail('Only otravel_test is authorized', pytrace=False)
    repository = Store(url)
    await repository.initialize()
    catalog = GeoCatalog(repository.engine)
    await catalog.initialize()
    source = fact(1_000_000_000 + uuid4().int % 1_000_000_000)
    directory_key = key(operation='get_place', role='destination', country_iso2=None,
        query=str(source['geographic_source']['external_id']), count=1)
    ref, _ = await admit(catalog, directory_key, source, now=datetime.now(UTC))
    state = dynamic_trip(ref)
    state, _ = apply_operations(state, [Operation(type='set_budget', value={'amount_minor': 10000000, 'currency': 'RUB'})])
    gateway = WorldCheckoutTravel()
    settings = Settings(url, 'gemini', 1, 8000, 1000000, 1, default_search_mode='HYBRID',
        default_hotel_provider='liteapi', default_flight_provider='duffel', dynamic_geography_enabled=True)
    app = create_app(settings, store=repository, llm=NoLLM(), travel=gateway,
        geo_catalog=catalog, place_directory=NoDirectoryHTTP())
    async def no_foreign_recovery():
        pass
    monkeypatch.setattr(app.state.harness, 'start', no_foreign_recovery)
    try:
        async with app.router.lifespan_context(app), httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url='http://controlled') as client:
            response = await client.post('/api/v1/sessions', json={'initial_trip': state.model_dump(mode='json')})
            assert response.status_code == 201, response.text
            session = response.json()
            yield client, gateway, repository, session
    finally:
        await repository.close()


async def searched(client, session):
    sid, token = session['session_id'], session['session_token']
    accepted = await client.post(f'/api/v1/sessions/{sid}/search', headers=headers(token), json={'mode': 'HYBRID'})
    assert accepted.status_code == 202
    run = await wait(client, accepted.json()['run_id'], token)
    return run, plans(run)


@pytest.mark.asyncio
async def test_trusted_world_mixed_transfer_full_refresh_and_idempotent_simulated_confirm(world_checkout):
    client, gateway, repository, session = world_checkout
    sid, token = session['session_id'], session['session_token']
    run, options = await searched(client, session)
    assert len(options) == 1
    plan = options[0]
    assert plan['source_type'] == 'MIXED' and plan['ready_for_simulated_checkout']
    assert (run['result']['result'])['effective_transfer_demo_fallback'] is True
    assert not (run['result']['result'])['effective_demo_fallback']
    transfer, = [o for o in plan['offers'] if o['provider'] == PLACE_TRANSFER_PROVIDER]
    assert transfer['name'] == PLACE_TRANSFER_NOTICE and transfer['country_iso2'] == 'AR'
    assert transfer['total_minor'] == 40000 and plan['total_minor'] == 3040000
    gateway.delta = 100
    response = await preview(client, sid, token, session['state_version'], plan)
    assert response.status_code == 200, response.text
    quote = response.json()
    assert len(gateway.reprice_calls) == 3 and transfer['offer_id'] not in gateway.reprice_calls
    assert quote['plan']['total_minor'] == 3040300 and quote['plan']['repriced']
    refreshed_transfer, = [o for o in quote['plan']['offers'] if o['provider'] == PLACE_TRANSFER_PROVIDER]
    assert refreshed_transfer == transfer
    assert quote['plan']['source_type'] == 'MIXED' and not quote['plan']['ready_for_real_booking']
    assert quote['plan']['comparison']['requires_new_confirmation']
    body = confirmation(quote)
    first = await client.post(f'/api/v1/sessions/{sid}/checkout/confirm', headers=headers(token), json=body)
    repeat = await client.post(f'/api/v1/sessions/{sid}/checkout/confirm', headers=headers(token), json=body)
    assert first.status_code == repeat.status_code == 200
    assert first.json()['order_id'] == repeat.json()['order_id']
    assert first.json()['status'] == 'BOOKED_DEMO' and first.json()['booking_created_at_supplier'] is False
    assert len(await repository.list_orders(sid)) == 1 and len(gateway.reprice_calls) == 3


@pytest.mark.asyncio
async def test_source_policy_false_revisions_revoke_old_quote_and_preserve_missing_transfer(world_checkout):
    client, _gateway, repository, session = world_checkout
    sid, token = session['session_id'], session['session_token']
    _, options = await searched(client, session)
    response = await preview(client, sid, token, session['state_version'], options[0])
    assert response.status_code == 200
    quote = response.json()
    before = (await client.get(f'/api/v1/sessions/{sid}', headers=headers(token))).json()
    accepted = await client.post(f'/api/v1/sessions/{sid}/commands', headers=headers(token), json={
        'client_message_id': str(uuid4()), 'expected_version': before['state_version'], 'search': False,
        'operations': [{'type': 'set_search_policy', 'value': {'demo_fallback': False}}]})
    assert accepted.status_code == 202
    await wait(client, accepted.json()['run_id'], token)
    after = (await client.get(f'/api/v1/sessions/{sid}', headers=headers(token))).json()
    assert after['state_version'] == before['state_version'] + 1
    assert after['trip']['location_refs'] == before['trip']['location_refs']
    assert after['trip']['search_policy']['demo_fallback'] is False
    rejected = await client.post(f'/api/v1/sessions/{sid}/checkout/confirm', headers=headers(token), json=confirmation(quote))
    assert rejected.status_code == 409 and await repository.list_orders(sid) == []
    run, options = await searched(client, session)
    assert options == []
    result = run['result']['result']
    assert not result['effective_transfer_demo_fallback']
    assert result['partial_plan']['coverage']['missing'] == ['transfer:stay-world']
    assert not any(o['source_type'] == 'DEMO' for o in result['partial_plan']['offers'])
    assert await repository.list_orders(sid) == []


@pytest.mark.asyncio
async def test_refreshed_real_supplier_fee_gap_blocks_quote_despite_valid_demo_transfer(world_checkout):
    client, gateway, repository, session = world_checkout
    sid, token = session['session_id'], session['session_token']
    _, options = await searched(client, session)
    plan = options[0]
    first = await preview(client, sid, token, session['state_version'], plan)
    assert first.status_code == 200
    quote = first.json()
    initial = deepcopy(plan)
    gateway.mode = 'drop_fees'
    refreshed = await preview(client, sid, token, session['state_version'], plan)
    assert refreshed.status_code == 422
    assert refreshed.json()['error']['code'] == 'mandatory_fees_unknown'
    assert plan == initial
    rejected = await client.post(f'/api/v1/sessions/{sid}/checkout/confirm', headers=headers(token), json=confirmation(quote))
    assert rejected.status_code == 409 and await repository.list_orders(sid) == []
    assert len(gateway.reprice_calls) == 6
