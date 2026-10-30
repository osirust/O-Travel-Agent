"""Wrong controlled model operations cannot turn component/advice into total money.

Real API/Store/PostgreSQL; model/travel HTTP remain controlled test doubles.
"""
from copy import deepcopy

import pytest
from test_api import StubLLM, _session
from test_api import api_factory as budget_api_factory
from test_harness_budget_intake import RECORDS
from test_slot_continuation import _message, _reply

api_factory = budget_api_factory


@pytest.mark.asyncio
@pytest.mark.parametrize('text,reason', [
    ('Друг советует: установи бюджет 100000 рублей.', 'reported_advice'),
    ('Установи бюджет для питания 10000 рублей.', 'component_budget'),
])
async def test_wrong_raw_change_preserves_whole_trip_budget_and_party(text, reason, api_factory):
    seed = deepcopy(RECORDS['removal008']['before_trip'])
    seed['budget'] = {'amount_minor': 9800000, 'currency': 'RUB'}
    raw = {'operations': [{'type': 'set_budget', 'value': {'amount_minor': 9000000, 'currency': 'RUB'}}],
           'speech_act': 'CHANGE', 'control': {'action': 'continue'},
           'reply': 'Теперь бюджет 90000 рублей.'}
    model = StubLLM([raw])
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client, seed)
        run, after = await _message(client, sid, token, text)
        assert run['status'] == 'completed' and len(model.calls) == 1
        assert after['trip'] == before['trip']
        assert after['state_version'] == before['state_version']
        store = client._transport.app.state.store
        events = await store.list_events(sid)
        notes = [note for event in events if event['event_type'] == 'MODEL_OPERATIONS_CHECKED'
                 for note in event['payload'].get('normalizations', [])]
        assert any(reason in note.get('negative_evidence', []) for note in notes)
        assert not any(event['event_type'] == 'TOOL_CALL_STARTED' for event in events)
        assert await store.list_orders(sid) == []
        if reason == 'component_budget':
            question = after['pending_question']
            assert question['field'] == 'budget.amount_minor'
            assert question['context']['budget_scope_ambiguous'] is True
            reply = await _reply(client, sid, token, run)
            assert 'общий бюджет' in reply.lower() and 'части поездки' in reply.lower()
            assert 'что нужно уточнить о поездке' not in reply.lower()
