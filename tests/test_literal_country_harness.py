"""Literal fresh country wishes are intake, not an encyclopedia question."""
import pytest
from test_api import StubLLM, _session
from test_api import api_factory as country_api_factory
from test_slot_continuation import _message, _reply

api_factory = country_api_factory


@pytest.mark.asyncio
@pytest.mark.parametrize('utterance', ['хочу в Сербию', 'В Сербию хочу!', 'Я хочу поехать в Сербию.'])
async def test_fresh_literal_country_wish_creates_city_question_without_model_or_trip_mutation(api_factory, utterance):
    model = StubLLM([{'speech_act':'ASK', 'information_subject':'Сербия'}])
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client)
        run, after = await _message(client, sid, token, utterance)
        assert after['trip'] == before['trip'] and after['state_version'] == before['state_version']
        pending = after['pending_question']
        assert after['waiting_reason'] == 'clarification' and pending['field'] == 'destination.city'
        country = pending.get('requested_destination') or pending['context']['requested_destination']
        assert country['country_code'] == 'RS' and country['kind'] == 'country'
        text = await _reply(client, sid, token, run)
        assert 'Серби' in text and 'город' in text.casefold()
        assert 'ваш вопрос сам по себе' not in text and not model.calls
        events = await client._transport.app.state.store.list_events(sid)
        assert not any(event['event_type'] in {'MODEL_CALL_STARTED','TOOL_CALL_STARTED','QUOTE_CREATED','ORDER_CREATED'} for event in events)


@pytest.mark.asyncio
async def test_country_knowledge_question_keeps_the_information_path(api_factory):
    model = StubLLM([{'speech_act':'ASK', 'information_subject':'Сербия'}])
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client)
        _, after = await _message(client, sid, token, 'Сербию знаешь?')
        assert after['trip'] == before['trip'] and after['pending_question'] is None
        # Exact capability answers may also be server-owned, but must not start intake.
