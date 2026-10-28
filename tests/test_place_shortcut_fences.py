"""Fresh country-pending shortcut fences through API/Harness/real test PG.

Country metadata, model and directory HTTP are controlled. No network provider,
Native process, global recovery or unrelated session is operated by these cases.
"""
import os
from copy import deepcopy

import pytest
from sqlalchemy.engine import make_url
from test_api import StubLLM, _session
from test_dynamic_place_conversation import world_app
from test_slot_continuation import _message


@pytest.fixture(autouse=True)
def only_isolated_pg():
    value = os.environ.get('TEST_DATABASE_URL')
    if not value:
        pytest.skip('Only isolated TEST_DATABASE_URL is used; no app contacted')
    if make_url(value).database != 'otravel_test':
        pytest.fail('Only otravel_test is authorized', pytrace=False)


async def country_pending(client, wires, travel, model):
    sid, token, initial = await _session(client)
    run, country = await _message(client, sid, token, 'хочу в аргентину наверное')
    assert run['status'] == 'completed'
    assert country['trip'] == initial['trip'] and country['state_version'] == 0
    pending = country['pending_question']
    assert pending['field'] == 'place.city_query'
    packet = pending['context']['place_resolution']
    assert packet['country_ref']['country_iso2'] == 'AR'
    assert packet['stage'] == 'city_query' and packet['candidates'] == []
    assert travel.calls == 1 and not wires and not model.calls
    return sid, token, country


def trace_source_entrypoints(monkeypatch, app):
    """A persistent cache hit must not hide an accidental directory shortcut."""
    calls = []
    for target, names in ((app.state.place_directory, ('search_city', 'get_place')),
            (app.state.harness.place_resolution, ('resolve', 'select'))):
        for name in names:
            original = getattr(target, name)
            async def traced(*args, _original=original, _name=name, **kwargs):
                calls.append(_name)
                return await _original(*args, **kwargs)
            monkeypatch.setattr(target, name, traced)
    return calls


@pytest.mark.asyncio
async def test_country_pending_compound_city_pause_defers_without_directory_or_new_country_lookup(monkeypatch):
    model = StubLLM([{'speech_act': 'ASK', 'control': {'action': 'wait'},
        'operations': [{'type': 'resolve_place', 'target_id': None, 'value': {
            'kind': 'city', 'query': 'Буэнос-Айрес', 'role': 'destination',
            'stay_id': 'new-stay-1', 'country_iso2': 'AR'}}],
        'reply': 'Сохраню направление и подожду явного продолжения.'}])
    async with world_app(monkeypatch, model) as (client, app, wires, travel):
        calls = trace_source_entrypoints(monkeypatch, app)
        sid, token, before = await country_pending(client, wires, travel, model)
        assert calls == ['resolve']
        calls.clear()
        run, after = await _message(client, sid, token, 'Буэнос-Айрес, подожди')
        assert run['status'] == 'completed', run.get('error')
        assert len(model.calls) == 1 and not wires and travel.calls == 1 and not calls
        assert after['waiting_reason'] == 'user_pause' and after['status'] == 'WAITING_USER'
        assert after['state_version'] == before['state_version'] + 1
        # Explicit pause owns one control revision, without inventing a city.
        expected = deepcopy(before['trip'])
        expected['state_version'] = after['state_version']
        assert after['trip'] == expected and after['trip']['stays'] == []
        pending = after['pending_question']
        assert pending['field'] == 'place.deferred' and pending['reason'] == 'user_pause'
        action = pending['context']['place_deferred_action']
        assert action['state_version'] == after['state_version'] and action['request']['query'] == 'Буэнос-Айрес'
        assert action['request']['country_ref']['country_iso2'] == 'AR'
        events = await app.state.store.list_events(sid)
        assert not [e for e in events if e['run_id'] == run['run_id'] and e['event_type'] == 'TOOL_CALL_STARTED']
        assert await app.state.store.list_orders(sid) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('text', ['объясни Буэнос-Айрес', 'где находится Буэнос-Айрес'])
async def test_country_pending_unpunctuated_information_request_does_not_become_city_resolution(monkeypatch, text):
    model = StubLLM([{'speech_act': 'ASK', 'control': {'action': 'none'}, 'operations': [],
        'unknowns': [], 'conflicts': [], 'information_subject': None,
        'reply': 'geography' if text.startswith('объясни') else 'Город для поездки пока не выбран. Я не меняю сохранённые условия.'}])
    async with world_app(monkeypatch, model) as (client, app, wires, travel):
        calls = trace_source_entrypoints(monkeypatch, app)
        sid, token, before = await country_pending(client, wires, travel, model)
        assert calls == ['resolve']
        calls.clear()
        run, after = await _message(client, sid, token, text)
        assert run['status'] == 'completed', run.get('error')
        assert not wires and travel.calls == 1 and len(model.calls) <= 1 and not calls
        assert after['trip'] == before['trip'] and after['state_version'] == before['state_version']
        assert after['trip']['stays'] == [] and 'location_refs' not in after['trip']
        assert after['pending_question'] == before['pending_question']
        assert after['waiting_reason'] == before['waiting_reason'] == 'clarification'
        # "объясни" takes the existing read-only model role. The unpunctuated
        # "где" can take the general ASK contract; neither grants selection.
        if model.calls and text.startswith('объясни'):
            assert model.calls[0].get('__response_mode') == 'readonly_answer_v1'
        events = await app.state.store.list_events(sid)
        own = [e for e in events if e['run_id'] == run['run_id']]
        assert not [e for e in own if e['event_type'] == 'TOOL_CALL_STARTED']
        assert not [e for e in own if e['event_type'] == 'TRIP_STATE_CHANGED']
        assert await app.state.store.list_orders(sid) == []
