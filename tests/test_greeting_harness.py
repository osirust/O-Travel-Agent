"""Exact social input through ASGI/PG preserves the trip and consumes no model quota."""
from uuid import uuid4

import pytest
from test_api import StubLLM, _headers, _session, _snapshot
from test_api import api_factory as greeting_api_factory
from test_harness_saved_answers import assert_unchanged, forbid_actions, seed_pending
from test_slot_continuation import _message, _reply

from otravel.domain import sample_trip

api_factory = greeting_api_factory


@pytest.mark.asyncio
@pytest.mark.parametrize('with_pending', [False, True])
async def test_greeting_preserves_state_question_and_avoids_extraction_search(api_factory, monkeypatch, with_pending):
    model = StubLLM()
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client)
        repository = client._transport.app.state.store
        if with_pending:
            await seed_pending(repository, sid, 'destination_city')
        before = await _snapshot(client, sid, token)
        await forbid_actions(client, monkeypatch)
        run, after = await _message(client, sid, token, 'Привет!')
        assert_unchanged(before, after)
        assert not model.calls
        assert 'Здравствуйте!' in await _reply(client, sid, token, run)
        assert run['result']['result']['conversational_response']['model_calls'] == 0
        assert not [event for event in await repository.list_events(sid)
                    if event.get('run_id') == run['run_id'] and event['event_type'] in {
                        'MODEL_CALL_STARTED', 'TOOL_CALL_STARTED', 'QUOTE_CREATED', 'ORDER_CREATED'}]


@pytest.mark.asyncio
async def test_greeting_during_explicit_pause_remains_queued_without_model(api_factory):
    model = StubLLM()
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client)
        await client.post(f'/api/v1/sessions/{sid}/control', headers=_headers(token), json={'action':'pause'})
        before = await _snapshot(client, sid, token)
        accepted = await client.post(f'/api/v1/sessions/{sid}/messages', headers=_headers(token),
            json={'client_message_id':str(uuid4()), 'text':'Привет!'})
        assert accepted.status_code == 202
        after = await _snapshot(client, sid, token)
        assert_unchanged(before, after)
        result = await client.get(f"/api/v1/runs/{accepted.json()['run_id']}", headers=_headers(token))
        assert result.json()['status'] == 'pending' and not model.calls


@pytest.mark.asyncio
async def test_greeting_plus_trip_request_still_reaches_model(api_factory):
    model = StubLLM([{'operations':[{'type':'set_origin','value':{'city':'Москва'}}]}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client)
        _, after = await _message(client, sid, token, 'Привет, хочу выехать из Москвы')
        assert len(model.calls) == 1 and after['trip']['origin'] == 'Москва'


@pytest.mark.asyncio
async def test_greeting_repeats_date_question_for_the_owned_stay(api_factory):
    async with api_factory(StubLLM()) as (client, _):
        trip = sample_trip().model_dump(mode='json')
        sid, token, _ = await _session(client, trip)
        repository = client._transport.app.state.store
        stay = next(stay for stay in trip['stays'] if stay['city'] == 'Анталья')
        await seed_pending(repository, sid, 'stay.check_in', target=stay['id'])
        before = await _snapshot(client, sid, token)
        run, after = await _message(client, sid, token, 'Здравствуйте!')
        reply = await _reply(client, sid, token, run)
        assert_unchanged(before, after)
        assert 'Анталья' in reply and 'Стамбул' not in reply
