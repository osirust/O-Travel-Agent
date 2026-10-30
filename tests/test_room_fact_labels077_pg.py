"""Captured room-specific answer through the real API/Store without vendor HTTP."""
import pytest
from test_answering import answer
from test_api import StubLLM, _session, _snapshot
from test_api import api_factory as room_api_factory
from test_room_fact_labels077 import captured
from test_slot_continuation import _message, _reply

api_factory = room_api_factory


@pytest.mark.asyncio
async def test_captured_room_condition_question_keeps_each_room_label_and_state(api_factory, monkeypatch):
    record = captured()
    model = StubLLM([answer('preferences')])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, record['before_trip'])
        before = await _snapshot(client, sid, token)
        harness = client._transport.app.state.harness

        async def no_search(*args, **kwargs):
            raise AssertionError('A saved-room-condition question must not search')

        monkeypatch.setattr(harness, '_search_trip', no_search)
        run, after = await _message(client, sid, token, record['input'])
        assert len(model.calls) == 1
        assert model.calls[0]['__response_mode'] == 'readonly_answer_v1'
        assert run['result']['result']['response_source'] == 'readonly_answer'
        reply = await _reply(client, sid, token, run)
        refundable, breakfast = reply.split('. ')
        assert 'номера №1' in refundable and 'возвратность тарифа — да' in refundable
        assert 'номера №2' in breakfast and 'включённый завтрак — да' in breakfast
        assert 'Стамбул' in refundable and 'Стамбул' in breakfast
        for key in ('trip', 'state_version', 'status', 'waiting_reason', 'pending_question'):
            assert before[key] == after[key]
        store = client._transport.app.state.store
        assert await store.list_orders(sid) == []
        assert not any(event['event_type'] in {'TOOL_CALL_STARTED', 'QUOTE_CREATED', 'ORDER_CREATED',
                                               'DEMO_ORDER_CREATED'} for event in await store.list_events(sid))
