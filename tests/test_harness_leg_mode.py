"""Addressed mode clarification follows a same-batch budget revision safely."""
from copy import deepcopy

import pytest
from test_api import StubLLM, _session
from test_api import api_factory as leg_api_factory
from test_duration_persistent import leg_trip
from test_slot_continuation import _message

api_factory = leg_api_factory


@pytest.mark.asyncio
async def test_new_mode_packet_rebases_after_budget_edit_and_first_answer_needs_no_model(api_factory):
    initial = leg_trip().model_dump(mode='json')
    new_budget = initial['budget']['amount_minor'] - 1000000
    model = StubLLM([{'operations': [
        {'type': 'set_budget', 'value': {'amount_minor': new_budget, 'currency': 'RUB'}},
        {'type': 'set_leg_mode', 'target_id': 'out', 'value': {'mode': 'train'}}]}])
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client, initial)
        _, asked = await _message(client, sid, token,
            'Уменьши общий бюджет на 10000 рублей и поменяй транспорт на поезд.')
        assert asked['trip']['budget']['amount_minor'] == new_budget
        assert asked['trip']['legs'] == before['trip']['legs']
        pending = asked['pending_question']
        assert pending['field'] == 'leg.mode'
        packet = pending['context']['leg_mode_proposal']
        assert packet['based_state_version'] == pending['state_version'] == asked['state_version']
        assert packet['mode'] == 'train' and len(model.calls) == 1
        expected = deepcopy(asked['trip'])
        second, after = await _message(client, sid, token, 'первый')
        assert after['trip']['legs'][0]['mode'] == 'train' and after['trip']['legs'][1]['mode'] == 'flight'
        for name in ('origin', 'stays', 'travelers', 'groups', 'budget', 'constraints', 'preferences'):
            assert after['trip'][name] == expected[name]
        assert not second.get('llm') and len(model.calls) == 1
        assert 'leg_mode_proposal' not in (after.get('pending_question') or {}).get('context', {})
        assert await client._transport.app.state.store.list_orders(sid) == []
