"""Replay an exact real-model contract on a controlled world API/PG context.

The original actual failure remains unchanged. Directory/country/model sources
here are controlled, while selection, storage, conflict checking and writes use
the real API/Harness/Catalog/Store. This is not a new actual model call.
"""
import hashlib
import json
import os
from copy import deepcopy
from datetime import date

import pytest
from sqlalchemy.engine import make_url
from test_api import StubLLM, _session
from test_dynamic_place_conversation import world_app
from test_slot_continuation import _message, _reply

SOURCE_EVENT_SHA256 = '5bb435a2cd85743eed6e23ed5d493fc4067df6607b54e9e0d7e6ae09a00f87b5'
RAW_CONTRACT_SHA256 = '9aeb40dbfa2747c9f750a035835e85da092898841b28820af6952fbf58547786'
RAW_CONTRACT_CANONICAL_SHA256 = 'c7bdc35b6c3adbb17c23401804546448ace6700f57a9dc9761a136b119024e04'
TEXT = ('Из Москвы, один взрослый, без детей. Заезд 10 ноября 2026, выезд 17 ноября 2026, '
    'один номер для меня. Бюджет на всю поездку 300 тысяч рублей. Гражданство Россия.')
RAW_ACTUAL = {
    'reply': '', 'control': {'action': 'none'}, 'unknowns': [], 'conflicts': [],
    'operations': [
        {'type': 'set_origin', 'value': {'city': 'Москва'}, 'target_id': None},
        {'type': 'add_traveler', 'value': {'id': 'new-traveler-1', 'age': None, 'type': 'adult', 'birth_date': None}, 'target_id': None},
        {'type': 'upsert_group', 'value': {'traveler_ids': ['new-traveler-1'], 'keep_separate': False}, 'target_id': 'new-group-1'},
        {'type': 'upsert_stay', 'value': {'city': 'Buenos Aires',
            'rooms': [{'id': 'new-room-1', 'traveler_ids': ['new-traveler-1']}],
            'check_in': '2026-11-10', 'hotel_id': None, 'check_out': '2026-11-17', 'requested_nights': 7},
            'target_id': 'new-stay-1'},
        {'type': 'set_budget', 'value': {'currency': 'RUB', 'amount_minor': 30000000}, 'target_id': None},
    ], 'speech_act': 'CHANGE', 'information_subject': None,
}


@pytest.mark.asyncio
async def test_cached_actual_compound_preserves_verified_world_city_and_all_independent_facts(monkeypatch):
    value = os.environ.get('TEST_DATABASE_URL')
    if not value:
        pytest.skip('Only isolated TEST_DATABASE_URL is used')
    if make_url(value).database != 'otravel_test':
        pytest.fail('Only otravel_test is authorized', pytrace=False)
    canonical = json.dumps(RAW_ACTUAL, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    # The recorded trace hash precedes JSONB's dictionary-key ordering. Bind
    # this exact exported value separately with canonical sorted JSON instead
    # of pretending it has the same original serialization bytes.
    assert hashlib.sha256(canonical).hexdigest() == RAW_CONTRACT_CANONICAL_SHA256
    model = StubLLM([deepcopy(RAW_ACTUAL)])
    async with world_app(monkeypatch, model) as (client, app, wires, travel):
        sid, token, initial = await _session(client)
        _, country = await _message(client, sid, token, 'хочу в аргентину наверное')
        assert country['trip'] == initial['trip'] and not model.calls
        _, cities = await _message(client, sid, token, 'Буэнос-Айрес')
        assert len(cities['pending_question']['context']['place_resolution']['candidates']) == 2
        _, selected = await _message(client, sid, token, 'первый')
        stay, = selected['trip']['stays']
        assert stay['id'] == 'new-stay-1' and stay['city'] == 'Buenos Aires'
        assert stay['place_id'] == 'geo:openmeteo:3435910'
        assert selected['trip']['origin'] is None and selected['trip']['travelers'] == []
        assert selected['trip']['budget'] is None and selected['state_version'] == 1
        before_refs = deepcopy(selected['trip']['location_refs'])
        before_wires, before_country_calls = len(wires), travel.calls

        run, after = await _message(client, sid, token, TEXT)
        assert run['status'] == 'completed' and not run.get('error')
        assert len(model.calls) == 1 and model.calls[0]['current_message'] == TEXT
        assert after['state_version'] == selected['state_version'] + 1
        state = after['trip']
        assert state['origin'] == 'Москва'
        person, = state['travelers']
        assert person['id'] == 'new-traveler-1' and person['type'] == 'adult'
        assert person['group_id'] == 'new-group-1'
        group, = state['groups']
        assert group['id'] == 'new-group-1' and group['traveler_ids'] == [person['id']]
        assert group['keep_separate'] is False
        stay, = state['stays']
        assert stay['id'] == 'new-stay-1' and stay['city'] == 'Buenos Aires'
        assert stay['place_id'] == 'geo:openmeteo:3435910' and stay['hotel_id'] is None
        assert state['location_refs'] == before_refs
        assert (stay['check_in'], stay['check_out']) == ('2026-11-10', '2026-11-17')
        assert (date.fromisoformat(stay['check_out']) - date.fromisoformat(stay['check_in'])).days == 7
        assert stay.get('requested_nights') is None, 'The source names dates, not a separate hard duration.'
        assert stay['rooms'] == [{'id': 'new-room-1', 'traveler_ids': [person['id']]}]
        assert state['budget'] == {'currency': 'RUB', 'amount_minor': 30000000, 'scope': 'whole_trip'}
        assert [(leg['origin'], leg['destination'], leg['departure_date']) for leg in state['legs']] == [
            ('Москва', 'Buenos Aires', '2026-11-10'), ('Buenos Aires', 'Москва', '2026-11-17')]
        reply = await _reply(client, sid, token, run)
        assert 'не распознан' not in reply and 'справочнике известных городов' not in reply
        # The actual model omitted nationality although it was in the input.
        # This regression qualifies city/fact retention, not a fabricated RU op.
        nationality = state['search_policy']['guest_nationality']
        assert nationality in {None, 'RU'}
        if nationality is None:
            assert after['waiting_reason'] == 'clarification'
            assert after['pending_question']['field'] == 'search.guest_nationality'
            assert 'Гражданство' in after['pending_question']['text']
            assert len(wires) == before_wires and travel.calls == before_country_calls
        events = await app.state.store.list_events(sid)
        checked = [e['payload']['structured_contract']['value'] for e in events
            if e['run_id'] == run['run_id'] and e['event_type'] == 'MODEL_OPERATIONS_CHECKED']
        assert len(checked) == 1
        assert any(op['type'] == 'upsert_stay' and op['value'].get('city') == 'Buenos Aires'
            for op in checked[0]['operations'])
        assert await app.state.store.list_orders(sid) == []
