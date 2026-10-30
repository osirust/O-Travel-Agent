"""Current preview identity selects facts without renumbering displayed plans."""
from uuid import uuid4

import pytest
from test_api import StubLLM, _headers, _plans, _session, _snapshot, _wait
from test_api import api_factory as selection_api_factory
from test_selected_quote_context_20261027 import initial_trip

from otravel.conversational import _selected_plan

api_factory = selection_api_factory


@pytest.mark.parametrize('text,index', [
    ('Что входит в выбранный вариант?', 2), ('Расскажи о выбранном варианте.', 2),
    ('Что входит в первый вариант?', 0), ('Не первый вариант.', None),
    ('Первый или второй вариант?', None), ('Одиннадцатый вариант?', None),
])
def test_preview_default_never_overrides_explicit_or_ambiguous_plan_reference(text, index):
    plans = [{'plan_id': 'p1'}, {'plan_id': 'p2'}, {'plan_id': 'p3'}]
    selected = _selected_plan(text, plans, default_plan_id='p3')
    assert selected == (plans[index] if index is not None else None)
    assert [plan['plan_id'] for plan in plans] == ['p1', 'p2', 'p3']


@pytest.mark.asyncio
async def test_readonly_answer_uses_third_ui_selection_and_preserves_quote_and_trip(api_factory):
    model = StubLLM([{'reply': 'options,rates', 'speech_act': 'ASK', 'information_subject': None},
        {'reply': 'Можно посмотреть выбранную тестовую корзину без покупки.', 'speech_act': 'ASK'}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, initial_trip())
        base, headers = f'/api/v1/sessions/{sid}', _headers(token)
        accepted = await client.post(base+'/search', headers=headers, json={})
        result = await _wait(client, accepted.json()['run_id'], token)
        plans = _plans(result)
        assert len(plans) >= 3
        chosen = plans[2]
        quote = await client.post(base+'/checkout/preview', headers=headers,
            json={'plan_id': chosen['plan_id'], 'state_version': chosen['state_version']})
        assert quote.status_code == 200
        app = client._transport.app
        selection = await app.state.store.current_selected_plan(sid, chosen['state_version'])
        assert selection['plan_id'] == chosen['plan_id']
        before = await _snapshot(client, sid, token)
        accepted = await client.post(base+'/messages', headers=headers, json={
            'client_message_id': str(uuid4()), 'expected_version': before['state_version'],
            'text': 'Что входит в выбранный вариант и включён ли завтрак?'})
        answer = await _wait(client, accepted.json()['run_id'], token)
        assert answer['status'] == 'completed'
        assert len(model.calls) == 1
        assert model.calls[0]['facts']['current_selected_plan'] == selection
        reply = answer['result']['reply']
        assert 'выбран вариант 3' in reply
        assert all(hotel['name'] in reply for hotel in selection['hotels'])
        assert 'Вариант 1.' not in reply
        after = await _snapshot(client, sid, token)
        assert after['trip'] == before['trip'] and after['state_version'] == before['state_version']
        assert await app.state.store.current_selected_plan(sid, chosen['state_version']) == selection
        assert await app.state.store.list_orders(sid) == []
        accepted = await client.post(base+'/messages', headers=headers, json={
            'client_message_id': str(uuid4()), 'expected_version': after['state_version'],
            'text': 'Это реальная цена выбранного плана или DEMO?'})
        source_answer = await _wait(client, accepted.json()['run_id'], token)
        assert source_answer['status'] == 'completed'
        source_reply = source_answer['result']['reply']
        assert 'Вариант 3.' in source_reply and 'Вариант 1.' not in source_reply
        assert len(model.calls) == 1
        assert (await _snapshot(client, sid, token))['trip'] == before['trip']
        accepted = await client.post(base+'/messages', headers=headers, json={
            'client_message_id': str(uuid4()), 'expected_version': after['state_version'],
            'text': 'Покажи тестовую корзину выбранного варианта, без покупки.'})
        cart_answer = await _wait(client, accepted.json()['run_id'], token)
        assert cart_answer['status'] == 'completed'
        action = cart_answer['result']['result']['suggested_action']
        assert action['type'] == 'preview_cart' and action['plan_id'] == chosen['plan_id']
        assert len(model.calls) == 2 and await app.state.store.list_orders(sid) == []
        assert model.calls[1]['current_selected_plan'] == selection
        assert await app.state.store.current_selected_plan(sid, chosen['state_version']) == selection
