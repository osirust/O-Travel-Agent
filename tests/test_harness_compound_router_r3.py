"""Exact captured seven-op WOW batch through API/PG; no supplier/model HTTP."""
from copy import deepcopy
from uuid import uuid4

import pytest
from test_api import StubLLM, _headers, _session, _wait
from test_api import api_factory as compound_api_factory
from test_compound_router_r3 import SOURCE
from test_slot_continuation import _message, _reply

api_factory = compound_api_factory


@pytest.mark.asyncio
async def test_actual_seven_op_compound_rebuild_preserves_family_rooms_breakfast_and_invalidates_old_quote(api_factory):
    row = SOURCE['observation']
    model = StubLLM([row['validated']['value']])
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, row['before_state'])
        store = client._transport.app.state.store
        base = f'/api/v1/sessions/{sid}'
        accepted = await client.post(base + '/search', headers=_headers(token), json={})
        assert accepted.status_code == 202
        search = await _wait(client, accepted.json()['run_id'], token)
        assert search['status'] == 'completed'
        plans = search['result']['result']['plans']
        assert len(plans) >= 2
        first_hotel = next(offer['hotel_id'] for offer in plans[0]['offers']
                           if offer.get('kind') == 'hotel' and offer.get('city') == 'Стамбул')
        preview = await client.post(base + '/checkout/preview', headers=_headers(token),
            json={'plan_id': plans[0]['plan_id'], 'state_version': created['state_version']})
        assert preview.status_code == 200
        old_quote = preview.json()
        before = deepcopy(created['trip'])
        run, changed = await _message(client, sid, token, row['text'])
        assert run['status'] == 'completed' and not run.get('error')
        trip = changed['trip']
        assert trip['state_version'] == before['state_version'] + 1
        assert trip['origin'] == before['origin']
        assert len(trip['travelers']) == 4
        assert sorted(p['age'] for p in trip['travelers'] if p['type'] == 'child') == [7, 8]
        assert trip['travelers'][:3] == before['travelers']
        assert trip['groups'][0] == before['groups'][0]
        assert trip['groups'][1]['traveler_ids'] == ['new-traveler-2', 'new-traveler-4']
        assert trip['groups'][1]['keep_separate'] is True
        assert trip['budget']['amount_minor'] == 38_000_000 and trip['budget']['currency'] == 'RUB'
        assert trip['constraints'] == before['constraints']
        for prior, stay in zip(before['stays'], trip['stays'], strict=True):
            assert stay['id'] == prior['id'] and stay['city'] == prior['city']
            assert stay['check_in'] == prior['check_in']
            assert [room['id'] for room in stay['rooms']] == [room['id'] for room in prior['rooms']]
            assert stay['rooms'][0]['traveler_ids'] == ['new-traveler-1', 'new-traveler-3']
            assert stay['rooms'][1]['traveler_ids'] == ['new-traveler-2', 'new-traveler-4']
        assert trip['stays'][0]['check_out'] == '2026-11-13'
        assert trip['stays'][0]['hotel_id'] == first_hotel
        assert trip['stays'][1]['check_out'] == trip['legs'][-1]['departure_date'] == '2026-11-19'
        assert trip['stays'][1]['hotel_id'] == row['captures']['hotel_b_id']
        assert trip['stays'][1]['requested_nights'] == 6
        reply = await _reply(client, sid, token, run)
        assert '380' in reply and 'бюджет' in reply.casefold()
        assert 'Что нужно уточнить о поездке' not in reply
        assert len(model.calls) == 1 and not model.answers
        rejected = await client.post(base + '/checkout/confirm', headers=_headers(token), json={
            'quote_id': old_quote['quote_id'], 'quote_version': old_quote['quote_version'],
            'state_version': old_quote['state_version'], 'confirmed': True, 'idempotency_key': str(uuid4())})
        assert rejected.status_code == 409
        assert await store.list_orders(sid) == []
        _, budget_after = await _message(client, sid, token, 'Напомни общий бюджет поездки.')
        assert budget_after['trip'] == trip and len(model.calls) == 1
        _, dates_after = await _message(client, sid, token, 'Какие даты поездки?')
        assert dates_after['trip'] == trip and len(model.calls) == 1
        notes = run['result']['result']['operation_normalizations']
        assert any(note['code'] == 'equivalent_return_checkout_alias_removed' for note in notes)
        assert sum(note['code'] == 'existing_room_aliases_normalized' for note in notes) == 2
