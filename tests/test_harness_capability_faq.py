"""Read-only capability/fact replies keep the exact durable quote and pending."""
from copy import deepcopy

import pytest
from test_api import StubLLM, _headers, _session, _wait
from test_api import api_factory as faq_api_factory
from test_slot_continuation import _message, _reply

from otravel.domain import sample_trip

api_factory = faq_api_factory
NIGHT = 'За гостинницу хочу не больше пяти тысяч за ночь. Это точно не замена общего бюджета; если такой лимит не умеешь, скажи прямо.'


@pytest.mark.asyncio
@pytest.mark.parametrize('text, expected', [(NIGHT, 'не поддерживается'),
    ('Анталья и Стамбул в одной стране? Ничего менять не надо.', 'Турция')])
async def test_capability_or_geo_faq_keeps_active_quote_without_model_or_tool(api_factory, text, expected):
    model = StubLLM([{'operations': [{'type': 'set_budget', 'value': {'amount_minor': 500000, 'currency': 'RUB'}}]}])
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, sample_trip().model_dump(mode='json'))
        base = f'/api/v1/sessions/{sid}'
        response = await client.post(base + '/search', headers=_headers(token), json={})
        assert response.status_code == 202
        searched = await _wait(client, response.json()['run_id'], token)
        plan = searched['result']['result']['plans'][0]
        response = await client.post(base + '/checkout/preview', headers=_headers(token),
                                     json={'plan_id': plan['plan_id'], 'state_version': created['state_version']})
        assert response.status_code == 200
        store = client._transport.app.state.store
        quote_id = response.json()['quote_id']
        quote, before = deepcopy(await store.get_quote(quote_id)), await store.get_session(sid)
        run, after = await _message(client, sid, token, text)
        assert expected in await _reply(client, sid, token, run)
        assert after['trip'] == before['state'] and after['state_version'] == before['state_version']
        assert after['status'] == before['status'] and after['pending_question'] == before['pending_question']
        assert await store.get_quote(quote_id) == quote
        assert not model.calls and await store.list_orders(sid) == []
        events = [event for event in await store.list_events(sid) if event['run_id'] == run['run_id']]
        assert not any(event['event_type'] in {'MODEL_CALL_STARTED', 'TOOL_CALL_STARTED', 'QUOTE_CREATED', 'DEMO_ORDER_CREATED'} for event in events)


@pytest.mark.asyncio
async def test_model_wait_without_domain_revision_blocks_new_preview_and_confirmation(api_factory):
    model = StubLLM([{'control': {'action': 'wait'}}])
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, sample_trip().model_dump(mode='json'))
        base, headers = f'/api/v1/sessions/{sid}', _headers(token)
        accepted = await client.post(base + '/search', headers=headers, json={})
        searched = await _wait(client, accepted.json()['run_id'], token)
        plan = searched['result']['result']['plans'][0]
        body = {'plan_id': plan['plan_id'], 'state_version': created['state_version']}
        preview = await client.post(base + '/checkout/preview', headers=headers, json=body)
        assert preview.status_code == 200
        quote = preview.json()
        _, paused = await _message(client, sid, token, 'Подожди, я ещё дополню условия поездки.')
        assert paused['waiting_reason'] == 'user_pause' and paused['state_version'] == created['state_version']
        assert paused['trip'] == created['trip']
        denied = await client.post(base + '/checkout/preview', headers=headers, json=body)
        assert denied.status_code == 409 and denied.json()['error']['code'] == 'user_paused'
        denied = await client.post(base + '/checkout/confirm', headers=headers, json={
            'quote_id': quote['quote_id'], 'quote_version': quote['quote_version'],
            'state_version': quote['state_version'], 'confirmed': True, 'idempotency_key': 'paused-order-must-not-exist'})
        assert denied.status_code == 409 and denied.json()['error']['code'] == 'user_paused'
        assert await client._transport.app.state.store.list_orders(sid) == []
