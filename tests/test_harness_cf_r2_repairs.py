"""Cached real CF output through fresh own PG state; no new model or supplier calls."""
import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from test_api import StubLLM, _headers, _session, _wait
from test_api import api_factory as repair_api_factory
from test_slot_continuation import _message, _reply

from otravel.answering import order_status_question
from otravel.domain import sample_trip

api_factory = repair_api_factory
ROOT = Path(__file__).parents[1]
MODES = json.loads((Path(__file__).parent / 'fixtures/demo-mode-cf-r2-20261027.json').read_text(encoding='utf-8'))['cases']
ORDER = json.loads((ROOT / 'fixtures/regression/order-reply-cf-r2-20261027.json').read_text(encoding='utf-8'))


@pytest.mark.asyncio
@pytest.mark.parametrize('case', MODES, ids=lambda c: c['id'])
async def test_exact_cf_demoregime_keeps_all_explicit_trip_facts_in_pg(api_factory, case):
    raw = deepcopy(case['raw_contract'])
    model = StubLLM([raw])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, case['before_trip'])
        run, after = await _message(client, sid, token, case['text'])
        assert after['trip']['search_policy']['mode'] == 'DEMO'
        assert after['trip']['search_policy']['hotel_provider'] == 'liteapi'
        assert after['trip']['search_policy']['flight_provider'] == 'duffel'
        assert after['trip']['origin'] == 'Москва' if case['id'] == 'PF-DURATION-03' else after['trip']['origin'] is not None
        assert len(model.calls) == 1
        assert any(note['code'] == 'search_policy_literal_grounded' for note in run['result']['result']['operation_normalizations'])
        events = await client._transport.app.state.store.list_events(sid)
        assert not any(e['event_type'] == 'TOOL_CALL_STARTED' for e in events)
        assert await client._transport.app.state.store.list_orders(sid) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('question,expected_calls', [('Есть ли у нас тестовый заказ и создана ли настоящая бронь?', 0),
    ('А настоящий есть?', 1)])
async def test_order_reply_is_rendered_from_existing_order_without_changes(api_factory, question, expected_calls):
    source = deepcopy(ORDER['captured_source_validated_contracts']['MODEL_EXTRACTION_VALIDATED']['value'])
    model = StubLLM([source])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, sample_trip().model_dump(mode='json'))
        base, headers = f'/api/v1/sessions/{sid}', _headers(token)
        accepted = await client.post(base + '/search', headers=headers, json={})
        searched = await _wait(client, accepted.json()['run_id'], token)
        plan = searched['result']['result']['plans'][0]
        quote = (await client.post(base + '/checkout/preview', headers=headers,
            json={'plan_id': plan['plan_id'], 'state_version': plan['state_version']})).json()
        order = await client.post(base + '/checkout/confirm', headers=headers, json={
            'quote_id': quote['quote_id'], 'quote_version': quote['quote_version'],
            'state_version': quote['state_version'], 'confirmed': True, 'idempotency_key': str(uuid4())})
        assert order.status_code == 200
        store = client._transport.app.state.store
        before, old_orders = await store.get_session(sid), await store.list_orders(sid)
        run, after = await _message(client, sid, token, question)
        reply = await _reply(client, sid, token, run)
        assert 'тестов' in reply.casefold() and 'реальной брони нет' in reply.casefold()
        assert after['trip'] == before['state'] and after['state_version'] == before['state_version']
        assert await store.list_orders(sid) == old_orders and len(old_orders) == 1
        assert len(model.calls) == expected_calls
        events = [e for e in await store.list_events(sid) if e['run_id'] == run['run_id']]
        assert not any(e['event_type'] in {'TOOL_CALL_STARTED', 'QUOTE_CREATED', 'DEMO_ORDER_CREATED'} for e in events)


@pytest.mark.parametrize('text', ['Если есть тестовый заказ, можно ли создать настоящую бронь?',
    'Есть ли заказ? Уменьши бюджет на10000.', 'Отмени тестовый заказ.',
    'Есть ли заказ и какой наш бюджет?', 'Есть один взрослый, детей нет.',
    'Есть ли заказ? Я отменяю текущий поиск.',
    'Есть ли заказ? Теперь вылетаем из Казани.',
    'Есть ли заказ? Отправление теперь Москва.',
    'Есть ли заказ? Дети едут с нами.'])
def test_nonstatus_or_mixed_operations_are_not_swallowed(text):
    assert not order_status_question(text)
