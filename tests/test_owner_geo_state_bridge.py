from copy import deepcopy

import pytest
from test_api import StubLLM, _plans, _preview, _session, _snapshot
from test_api import api_factory as bridge_api_factory
from test_harness_conversational import initial_search, known_trip
from test_harness_saved_answers import seed_pending
from test_slot_continuation import _message, _reply

from otravel.geography import parse_destination

api_factory = bridge_api_factory


@pytest.mark.asyncio
async def test_unsupported_keep_changes_owned_city_and_legs_together_and_revokes_old_quote(api_factory):
    initial = known_trip()
    initial['search_policy'] = {'mode':'DEMO'}
    model = StubLLM()
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, initial)
        searched = await initial_search(client, sid, token)
        before = await _snapshot(client, sid, token)
        quote = await _preview(client, sid, token, _plans(searched)[0], before['state_version'])
        store = client._transport.app.state.store
        target = before['trip']['stays'][0]['id']
        await seed_pending(store, sid, 'destination.city', target=target,
            context={'requested_destination':parse_destination('Белград')['requested_destination']})
        old = deepcopy(before['trip'])
        run, after = await _message(client, sid, token, 'сохряняем')
        assert not model.calls and after['state_version'] == before['state_version']+1
        stay = after['trip']['stays'][0]
        assert stay == {**old['stays'][0], 'city':'Белград', 'hotel_id':None}
        assert after['trip']['legs'][0]['destination']=='Белград'
        assert after['trip']['legs'][1]['origin']=='Белград'
        for field in ('origin','travelers','groups','budget','constraints','preferences','route_policy','search_policy'):
            assert after['trip'][field] == old[field]
        assert after['pending_question'] is None and after['status']=='TRIP_DRAFT'
        assert 'Белград' in await _reply(client, sid, token, run)
        assert (await store.get_quote(quote['quote_id']))['status'] != 'active'
        assert not any(e['event_type']=='MODEL_CALL_STARTED' for e in await store.list_events(sid))
        assert await store.list_orders(sid)==[]


@pytest.mark.asyncio
async def test_live_covered_city_keep_preserves_dates_and_moves_to_real_missing_nationality(api_factory):
    initial = known_trip()
    initial['stays'][0]['city']='Белгород'
    initial.pop('legs')
    initial['search_policy']={'mode':'HYBRID','hotel_provider':'liteapi','flight_provider':'duffel','demo_fallback':True}
    model = StubLLM()
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client, initial)
        store = client._transport.app.state.store
        await seed_pending(store, sid, 'destination.city', target=before['trip']['stays'][0]['id'],
            context={'requested_destination':parse_destination('Белград')['requested_destination']})
        _, after = await _message(client, sid, token, 'сохряняем')
        assert not model.calls
        assert after['trip']['stays'][0]['city']=='Белград'
        for field in ('check_in','check_out','requested_nights','rooms'):
            assert after['trip']['stays'][0].get(field) == before['trip']['stays'][0].get(field)
        assert after['trip']['legs'][0]['destination']=='Белград'
        assert after['trip']['legs'][1]['origin']=='Белград'
        assert after['pending_question']['field']=='search.guest_nationality'
        assert after['pending_question']['target_id'] is None
        assert 'каталог' not in after['pending_question']['text']
        assert not any(e['event_type']=='TOOL_CALL_STARTED' for e in await store.list_events(sid))
        _, unchanged = await _message(client, sid, token, 'да')
        assert unchanged['trip']==after['trip'] and not model.calls
