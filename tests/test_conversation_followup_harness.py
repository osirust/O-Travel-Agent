from uuid import uuid4

import pytest
from test_api import StubLLM, _session
from test_api import api_factory as followup_api_factory
from test_harness_saved_answers import seed_pending
from test_slot_continuation import _message, _reply

api_factory = followup_api_factory


@pytest.mark.asyncio
async def test_assent_recovers_last_server_country_offer_in_existing_legacy_chat(api_factory):
    model = StubLLM()
    async with api_factory(model) as (client, _):
        sid, token, initial = await _session(client)
        store = client._transport.app.state.store
        run, _ = await store.intake_message(sid, str(uuid4()), 'хочу в сербию наверное', 0)
        assert await store.claim_run(run['id'])
        await store.finish_run(run['id'], 0, None, 'TRIP_DRAFT',
            'Да, Сербия — страна. Могу помочь обсудить маршрут и условия поездки.',
            result={'conversation':{'speech_act':'ASK','subject':{'kind':'country','name':'Сербия','country_code':'RS'}}})
        answered, current = await _message(client, sid, token, 'давай')
        assert not model.calls and current['trip'] == initial['trip']
        assert current['pending_question']['field'] == 'destination.city'
        assert current['pending_question']['context']['requested_destination']['country_code'] == 'RS'
        reply = await _reply(client, sid, token, answered)
        assert 'город' in reply.casefold() and 'Серби' in reply
        assert 'ваш вопрос сам по себе' not in reply


@pytest.mark.asyncio
async def test_help_with_owned_question_repeats_one_question_without_unverified_boilerplate(api_factory):
    model = StubLLM()
    async with api_factory(model) as (client, _):
        sid, token, initial = await _session(client)
        await seed_pending(client._transport.app.state.store, sid, 'search.guest_nationality')
        run, after = await _message(client, sid, token, 'ты поможешь или нет?')
        reply = await _reply(client, sid, token, run)
        assert not model.calls and after['trip'] == initial['trip']
        assert 'Гражданство' in reply and reply.count('?') == 1
        assert 'не подтверждена' not in reply


@pytest.mark.asyncio
async def test_fresh_generic_help_asks_one_needed_fact_without_model(api_factory):
    model = StubLLM()
    async with api_factory(model) as (client, _):
        sid, token, initial = await _session(client)
        run, after = await _message(client, sid, token, 'помоги с поездкой')
        assert not model.calls and after['trip'] == initial['trip']
        reply = await _reply(client, sid, token, run)
        assert reply.startswith('Да, помогу.') and reply.count('?') == 1
        assert after['pending_question']['field'] == 'origin.city'
