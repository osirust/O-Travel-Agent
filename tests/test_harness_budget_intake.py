"""Exact fresh Groq omissions replayed through a controlled model and real PG."""
import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_api import StubLLM, _session
from test_api import api_factory as budget_api_factory
from test_slot_continuation import _message, _reply

api_factory = budget_api_factory
RECORDS = {row['id']: row for row in json.loads((Path(__file__).parents[1]
    / 'fixtures/regression/r12-actual-budget-omission-20261028.json').read_text(encoding='utf-8'))['records']}


@pytest.mark.asyncio
@pytest.mark.parametrize('case_id,amount', [('budget002', 13000000), ('removal008', 8500000)])
async def test_exact_actual_ask_contract_cannot_drop_explicit_budget(case_id, amount, api_factory):
    row = RECORDS[case_id]
    model = StubLLM([deepcopy(row['validated_raw_contract']['value'])])
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client, row['before_trip'])
        run, after = await _message(client, sid, token, row['input'])
        assert run['status'] == 'completed' and len(model.calls) == 1
        assert after['trip']['budget']['amount_minor'] == amount
        assert after['state_version'] == before['state_version'] + 1
        for field in ('travelers', 'groups', 'origin', 'stays', 'legs', 'constraints', 'preferences', 'search_policy'):
            assert after['trip'][field] == before['trip'][field], field
        reply = await _reply(client, sid, token, run)
        assert ('130 000' if case_id == 'budget002' else '85 000') in reply
        if case_id == 'budget002':
            assert '5 лет' in reply and '9 лет' in reply
        else:
            assert '2 взрослых' in reply
        assert 'Уточните, что именно' not in reply
        store = client._transport.app.state.store
        assert await store.list_orders(sid) == []
        tools = [e for e in await store.list_events(sid) if e['event_type'] == 'TOOL_CALL_STARTED']
        # The previously complete trip with no budget becomes searchable after
        # its first explicit budget; NoTravelHTTP remains the controlled stub.
        assert len(tools) <= (16 if case_id == 'removal008' else 0)


@pytest.mark.asyncio
async def test_budget_amount_question_keeps_saved_money_and_replies_from_state(api_factory):
    before_state = deepcopy(RECORDS['removal008']['before_trip'])
    before_state['budget'] = {'amount_minor': 9800000, 'currency': 'RUB'}
    raw = {'operations': [{'type': 'set_budget', 'value': {'amount_minor': 9000000, 'currency': 'RUB'}}],
           'control': {'action': 'continue'}, 'speech_act': 'CHANGE',
           'reply': 'Теперь бюджет 90000 рублей.'}
    model = StubLLM([raw])
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client, before_state)
        run, after = await _message(client, sid, token, 'Стамбул в Турции, верно? И общий бюджет у нас сейчас90000 рублей? Ничего больше не меняй.')
        assert run['status'] == 'completed' and len(model.calls) == 1
        assert after['trip'] == before['trip'] and after['state_version'] == before['state_version']
        reply = await _reply(client, sid, token, run)
        assert '98 000' in reply and 'Теперь бюджет 90000' not in reply
        store = client._transport.app.state.store
        assert await store.list_orders(sid) == []
        assert not any(e['event_type'] == 'TOOL_CALL_STARTED' for e in await store.list_events(sid))


@pytest.mark.asyncio
@pytest.mark.parametrize('text', [
    'Как понять «Поставь общий бюджет100000 рублей»?',
    'Бюджет100000 рублей — моё предложение.',
    'Установи бюджет100000 рублей — это твой совет?',
    'Установи бюджет100000 рублей, но пока не применяй.',
    'Ты только не уменьши бюджет на20 тысяч рублей!',
])
async def test_wrong_model_change_cannot_authorize_quote_proposal_or_prohibition(text, api_factory):
    seed = deepcopy(RECORDS['removal008']['before_trip'])
    seed['budget'] = {'amount_minor': 9800000, 'currency': 'RUB'}
    model = StubLLM([{'operations': [{'type': 'set_budget', 'value': {'amount_minor': 10000000, 'currency': 'RUB'}}],
                     'speech_act': 'CHANGE', 'control': {'action': 'continue'}}])
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client, seed)
        _, after = await _message(client, sid, token, text)
        assert after['trip'] == before['trip'] and after['state_version'] == before['state_version']
        assert await client._transport.app.state.store.list_orders(sid) == []


@pytest.mark.asyncio
async def test_asserted_budget_and_different_question_amount_use_only_asserted_literal(api_factory):
    seed = deepcopy(RECORDS['removal008']['before_trip'])
    seed['budget'] = {'amount_minor': 9800000, 'currency': 'RUB'}
    model = StubLLM([{'operations': [{'type': 'set_budget', 'value': {'amount_minor': 9000000, 'currency': 'RUB'}}],
                     'speech_act': 'CHANGE', 'control': {'action': 'continue'}}])
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client, seed)
        _, after = await _message(client, sid, token, 'Установи бюджет100000 рублей. И общий бюджет у нас90000 рублей?')
        assert after['trip']['budget']['amount_minor'] == 10000000
        assert after['trip']['travelers'] == before['trip']['travelers']
        assert after['state_version'] == before['state_version'] + 1
