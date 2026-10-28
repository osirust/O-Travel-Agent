"""PG regressions for actual ASK reply loss and punctuation semantics."""
from copy import deepcopy

import pytest
from test_api import StubLLM, _headers, _session, _wait
from test_api import api_factory as geo_api_factory
from test_geo_openrouter_r3 import CAPTURE
from test_harness_saved_answers import seed_pending
from test_slot_continuation import _message, _reply

from otravel.domain import sample_trip

api_factory = geo_api_factory


@pytest.mark.asyncio
async def test_exact_actual_literal_creates_confirmation_without_consuming_the_good_captured_ask_reply(api_factory):
    row = CAPTURE['observation']
    model = StubLLM([row['raw_validated']])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, row['before_state'])
        store = client._transport.app.state.store
        await seed_pending(store, sid, 'destination.city', context=row['before_pending']['context'])
        before = await store.get_session(sid)
        run, after = await _message(client, sid, token, row['text'])
        assert run['status'] == 'completed'
        question = after['pending_question']
        assert question['field'] == 'destination.city' and question['context'].get('confirmation_required') is True
        assert question['context']['requested_destination'] == {'kind': 'city', 'name': 'Белград', 'country_code': 'RS'}
        assert question['context']['proposed_destination'] == {'kind': 'city', 'name': 'Стамбул', 'country_code': 'TR'}
        reply = await _reply(client, sid, token, run)
        assert 'Стамбул' in reply and 'Турци' in reply
        assert after['trip'] == before['state'] and not model.calls and len(model.answers) == 1
        assert not any(e['event_type'] in {'MODEL_CALL_STARTED', 'TOOL_CALL_STARTED'}
            for e in await store.list_events(sid) if e['run_id'] == run['run_id'])


@pytest.mark.asyncio
@pytest.mark.parametrize('with_pending', [False, True])
async def test_question_variant_with_or_without_city_pending_keeps_trip_pending_quote_and_has_no_model_call(api_factory, with_pending):
    model = StubLLM([CAPTURE['observation']['raw_validated']])
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, sample_trip().model_dump(mode='json'))
        base = f'/api/v1/sessions/{sid}'
        searched = await client.post(base + '/search', headers=_headers(token), json={})
        run = await _wait(client, searched.json()['run_id'], token)
        plan = run['result']['result']['plans'][0]
        preview = await client.post(base + '/checkout/preview', headers=_headers(token),
            json={'plan_id': plan['plan_id'], 'state_version': created['state_version']})
        assert preview.status_code == 200
        store = client._transport.app.state.store
        quote = deepcopy(await store.get_quote(preview.json()['quote_id']))
        if with_pending:
            await seed_pending(store, sid, 'destination.city', target=created['trip']['stays'][0]['id'],
                context=CAPTURE['observation']['before_pending']['context'])
        before = await store.get_session(sid)
        run, after = await _message(client, sid, token, 'Стамбул в Сербии?')
        reply = await _reply(client, sid, token, run)
        assert 'Стамбул' in reply and 'Турция' in reply and 'Сербия' in reply and 'не находится' in reply
        assert after['trip'] == before['state'] and after['pending_question'] == before['pending_question']
        assert after['status'] == before['status'] and after['state_version'] == before['state_version']
        assert await store.get_quote(quote['quote_id']) == quote and not model.calls
        assert not any(e['event_type'] in {'MODEL_CALL_STARTED', 'TOOL_CALL_STARTED', 'QUOTE_CREATED', 'DEMO_ORDER_CREATED'}
            for e in await store.list_events(sid) if e['run_id'] == run['run_id'])
        assert await store.list_orders(sid) == []
