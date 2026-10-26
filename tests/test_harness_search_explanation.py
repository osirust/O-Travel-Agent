"""Hybrid source explanation through API/PG; external gateway is controlled."""
from uuid import uuid4

import pytest
from test_api import _headers, _session, _snapshot, _wait
from test_mixed_checkout import mixed_api as explanation_api

from otravel.domain import sample_trip

mixed_api = explanation_api


@pytest.mark.asyncio
async def test_mixed_search_explains_fx_rejection_and_sandbox_without_raw_ids(mixed_api, monkeypatch):
    client, gateway, store = mixed_api
    original_offer = gateway.offer

    def currency_mismatch(request, provider, kind):
        offer = original_offer(request, provider, kind)
        if kind == 'flight':
            offer['currency'] = 'EUR'
        return offer

    monkeypatch.setattr(gateway, 'offer', currency_mismatch)
    initial = sample_trip().model_dump(mode='json')
    initial['search_policy'] = {'mode': 'HYBRID', 'hotel_provider': 'liteapi', 'flight_provider': 'duffel',
                                'demo_fallback': True, 'guest_nationality': 'RU'}
    sid, token, before = await _session(client, initial)
    accepted = await client.post(f'/api/v1/sessions/{sid}/search', headers=_headers(token), json={
        'client_message_id': str(uuid4()), 'expected_version': before['state_version'], 'mode': 'HYBRID'})
    assert accepted.status_code == 202
    run = await _wait(client, accepted.json()['run_id'], token)
    assert run['status'] == 'completed'
    detail = run['result']['result']['search_explanation']
    assert detail['summary']['source_counts'] == {'SANDBOX': 2, 'DEMO': 5}
    assert any(reason['code'] == 'price_not_comparable_or_unpriced' for reason in detail['summary']['reasons'])
    text = detail['text']
    assert 'SANDBOX' in text and 'DEMO' in text and 'валюте бюджета' in text
    assert 'fixture-' not in text and 'EUR' not in text
    assert text in run['result']['reply']
    assert (await _snapshot(client, sid, token))['trip'] == before['trip']
    assert await store.list_orders(sid) == []
