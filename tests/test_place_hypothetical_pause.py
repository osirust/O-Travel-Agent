"""Non-consented geography hypotheses may pause, but may not edit the trip.

Both the dispatcher contract boundary and normal controlled extraction run
against real Store/API/Harness on otravel_test. No actual network/model occurs.
"""
import os
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy.engine import make_url
from test_api import StubLLM, _headers, _session, _snapshot, _wait
from test_dynamic_place_conversation import world_app
from test_place_shortcut_fences import trace_source_entrypoints

from otravel.domain import sample_trip
from otravel.harness import ModelResult

TEXT = ('А если поехать в Аргентину, сделать бюджет 70 тысяч и возраст ребёнка 7 лет? '
    'Подожди, я ещё дополню.')


@pytest.fixture(autouse=True)
def only_isolated_database():
    value = os.environ.get('TEST_DATABASE_URL')
    if not value:
        pytest.skip('Only isolated TEST_DATABASE_URL is used')
    if make_url(value).database != 'otravel_test':
        pytest.fail('Only otravel_test is authorized', pytrace=False)


def request():
    return {'role': 'destination', 'kind': 'country', 'query': 'Argentina',
        'stay_id': 'stay-istanbul', 'country_iso2': 'AR'}


def proposed_ordinary_operations():
    return [{'type': 'set_budget', 'target_id': None, 'value': {'amount_minor': 7000000, 'currency': 'RUB'}},
        {'type': 'update_traveler', 'target_id': 'child-1', 'value': {'age': 7}}]


@pytest.mark.asyncio
@pytest.mark.parametrize('speech_act', ['ASK', 'PROPOSE'])
@pytest.mark.parametrize('boundary', ['dispatcher', 'controlled_extraction'])
async def test_hypothetical_ordinary_edits_cannot_be_applied_before_place_pause(monkeypatch, speech_act, boundary):
    answer = {'speech_act': speech_act, 'control': {'action': 'wait'}, 'operations': [
        {'type': 'resolve_place', 'target_id': None, 'value': request()}, *proposed_ordinary_operations()],
        'reply': 'Обсуждаем гипотетический вариант. Жду явного продолжения.'}
    model = StubLLM([answer] if boundary == 'controlled_extraction' else [])
    async with world_app(monkeypatch, model) as (client, app, wires, travel):
        source_entries = trace_source_entrypoints(monkeypatch, app)
        sid, token, _initial = await _session(client, sample_trip().model_dump(mode='json'))
        before_request = await _snapshot(client, sid, token)
        # Explicit API request establishes this owned country question first;
        # no pending packet or provider identity is seeded through private SQL.
        accepted = await client.post(f'/api/v1/sessions/{sid}/places/resolve', headers=_headers(token), json={
            **request(), 'client_message_id': str(uuid4()), 'expected_version': before_request['state_version']})
        assert accepted.status_code == 202
        bootstrap = await _wait(client, accepted.json()['run_id'], token)
        assert bootstrap['status'] == 'completed'
        before = await _snapshot(client, sid, token)
        assert before['pending_question']['field'] == 'place.city_query'
        assert before['trip'] == before_request['trip'] and source_entries == ['resolve']
        assert travel.calls == 1 and not model.calls and not wires
        source_entries.clear()
        original_age = next(p['age'] for p in before['trip']['travelers'] if p['id'] == 'child-1')
        assert original_age != 7 and before['trip']['budget']['amount_minor'] != 7000000

        if boundary == 'dispatcher':
            async def interpreted(session, run):
                assert run['input_text'] == TEXT and session['session_id'] == sid
                return ModelResult(operations=proposed_ordinary_operations(), speech_act=speech_act,
                    control={'action': 'wait'}, unknowns=[], conflicts=[], reply=answer['reply'],
                    response_source='llm', place_requests=[request()]), {
                        'provider': 'controlled', 'model': 'dispatcher-boundary-no-http'}
            monkeypatch.setattr(app.state.harness, '_interpret', interpreted)

        accepted = await client.post(f'/api/v1/sessions/{sid}/messages', headers=_headers(token), json={
            'client_message_id': str(uuid4()), 'expected_version': before['state_version'], 'text': TEXT})
        assert accepted.status_code == 202
        run = await _wait(client, accepted.json()['run_id'], token)
        after = await _snapshot(client, sid, token)
        assert run['status'] == 'completed', run.get('error')
        assert after['waiting_reason'] == 'user_pause'
        assert not source_entries and not wires and travel.calls == 1
        assert after['state_version'] == before['state_version'] + 1
        assert len(model.calls) == (1 if boundary == 'controlled_extraction' else 0)
        # This is the substantive non-consent boundary. A pause revision must
        # not accidentally commit the model's hypothetical ordinary setters.
        assert after['trip']['budget'] == before['trip']['budget']
        assert next(p['age'] for p in after['trip']['travelers'] if p['id'] == 'child-1') == original_age
        expected = deepcopy(before['trip'])
        expected['state_version'] = after['state_version']
        assert after['trip'] == expected
        assert await app.state.store.list_orders(sid) == []
        events = await app.state.store.list_events(sid)
        own = [e for e in events if e['run_id'] == run['run_id']]
        assert not [e for e in own if e['event_type'] == 'TOOL_CALL_STARTED']
