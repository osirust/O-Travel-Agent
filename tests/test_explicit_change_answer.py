"""A partial topic selector cannot omit a directly requested saved comparison."""
import re
from uuid import uuid4

import pytest
from test_api import StubLLM, _headers, _session, _snapshot, _wait
from test_api import api_factory as change_api_factory
from test_selected_quote_context_20261027 import initial_trip

from otravel.answering import checked_reply

api_factory = change_api_factory
FACTS = {'sections': {'party': 'Состав сохранён.', 'budget': 'Сейчас 380000 RUB.',
                     'changes': '400000 RUB → 380000 RUB.'}}


@pytest.mark.parametrize('text', ['Что изменилось в составе и бюджете? Ничего не меняй.',
    'Покажи разницу.', 'Как изменился бюджет?', 'Что именно поменялось?'])
def test_explicit_comparison_is_added_from_saved_facts(text):
    reply, notes = checked_reply('party,budget', FACTS, text)
    assert FACTS['sections']['changes'] in reply and notes == ['explicit_saved_change_question_covered']


@pytest.mark.parametrize('text', ['Напомни текущий состав и бюджет.', 'А если бы бюджет изменился?',
    'Что изменилось бы, если добавить ребёнка?', 'Вчера спросил «что изменилось», сейчас напомни бюджет.',
    'Не показывай изменения, напомни текущий бюджет.'])
def test_hypothesis_history_and_other_questions_do_not_invent_requested_comparison(text):
    reply, notes = checked_reply('party,budget', FACTS, text)
    assert FACTS['sections']['changes'] not in reply and notes == []


@pytest.mark.asyncio
async def test_partial_actual_style_selector_still_delivers_before_after_without_state_write(api_factory):
    model = StubLLM([{'reply': 'party,budget', 'speech_act': 'ASK', 'information_subject': None}])
    async with api_factory(model) as (client, _):
        initial = initial_trip(); initial['budget']['amount_minor'] = 40000000
        sid, token, created = await _session(client, initial)
        base, headers = f'/api/v1/sessions/{sid}', _headers(token)
        response = await client.post(base+'/commands', headers=headers, json={
            'client_message_id': str(uuid4()), 'expected_version': created['state_version'], 'search': False,
            'operations': [{'type': 'set_budget', 'value': {'amount_minor': 38000000, 'currency': 'RUB'}}]})
        changed = await _wait(client, response.json()['run_id'], token)
        assert changed['status'] == 'completed'
        before = await _snapshot(client, sid, token)
        response = await client.post(base+'/messages', headers=headers, json={
            'client_message_id': str(uuid4()), 'expected_version': before['state_version'],
            'text': 'Что изменилось в составе и бюджете? Ничего больше не меняй.'})
        run = await _wait(client, response.json()['run_id'], token)
        assert run['status'] == 'completed' and len(model.calls) == 1
        reply = run['result']['reply']; compact = re.sub(r'\s+', '', reply)
        assert '→' in reply and '400000' in compact and '380000' in compact
        assert (await _snapshot(client, sid, token))['trip'] == before['trip']
        assert await client._transport.app.state.store.list_orders(sid) == []
