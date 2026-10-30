"""Pure capability8 gate, separate from saved DEMO7 regression evidence."""
from copy import deepcopy

import pytest
from test_geo_dialogue import legacy, pending

from otravel.country_intake import literal_country_wish
from otravel.domain import Operation, TripState, apply_operations
from otravel.geo_dialogue import resolve_geo_destination
from otravel.intent_grounding import ground_intent_operations, resolve_country_destination_answer


def policy(state, *, mode='HYBRID', hotel='liteapi', flight='duffel'):
    state = deepcopy(state)
    state['search_policy'].update(mode=mode, hotel_provider=hotel, flight_provider=flight)
    return state


@pytest.mark.parametrize('text', ['Хочу в Белград', 'Выбираю Белград'])
def test_qualified_hybrid_belgrade_is_not_an_outside_catalogue_question(text):
    state = policy(TripState().model_dump(mode='json'))
    operations = [{'type': 'upsert_stay', 'target_id': 'new-stay-1', 'value': {'city': 'Белград'}}]
    after, meta = ground_intent_operations(state, operations, text)
    assert after == operations and not meta['unknowns']
    assert not any(note['code'] == 'known_destination_outside_search_catalogue' for note in meta['notes'])


@pytest.mark.parametrize(('mode', 'hotel', 'flight'), [
    ('DEMO', 'liteapi', 'duffel'), ('HYBRID', 'liteapi', 'serpapi'), ('HYBRID', 'hotelbeds', 'duffel'),
])
def test_unqualified_policy_retains_old_seven_city_capability(mode, hotel, flight):
    state = policy(TripState().model_dump(mode='json'), mode=mode, hotel=hotel, flight=flight)
    _, meta = ground_intent_operations(state,
        [{'type': 'upsert_stay', 'target_id': 'new-stay-1', 'value': {'city': 'Белград'}}], 'Хочу в Белград')
    assert meta['unknowns']


def test_country_suggestions_use_capability_gate_without_guessing_a_city():
    hybrid = policy(TripState().model_dump(mode='json'))
    demo = policy(hybrid, mode='DEMO')
    for state, expected in [(hybrid, True), (demo, False)]:
        answer = literal_country_wish(state, 'хочу в Сербию наверное')
        suggested = {row['name']: row['available'] for row in answer['requested_destination']['suggested_cities']}
        assert suggested == {'Белград': expected, 'Нови-Сад': False}
        assert answer['requested_destination']['kind'] == 'country' and state['stays'] == []
        country = resolve_country_destination_answer(state, {'field': 'destination.city', 'state_version': 0}, 'страна Сербия')
        suggested2 = {row['name']: row['available'] for row in country['pending_context']['requested_destination']['suggested_cities']}
        assert suggested2 == suggested


@pytest.mark.parametrize('text', ['сохраняем', 'сохряняем'])
def test_legacy_keep_for_now_covered_belgrade_uses_normal_selection_not_discussion_only(text):
    state = policy(legacy())
    answer = resolve_geo_destination(state, pending(state), text)
    assert answer['action'] == 'select_destination'
    assert answer['pending_context']['requested_destination']['name'] == 'Белград'
    assert 'geo_destination' in answer['pending_context_clear']
    assert 'не запускается' not in answer['notice']
    after, _ = apply_operations(TripState.model_validate(state), [Operation.model_validate(op) for op in answer['operations']])
    after = after.model_dump(mode='json')
    assert after['stays'][0]['city'] == 'Белград' and after['stays'][0]['hotel_id'] is None
    for name in ('origin', 'travelers', 'groups', 'budget', 'preferences', 'constraints', 'search_policy'):
        assert after[name] == state[name]


def test_accepted_contextual_city_proposal_uses_capability8_normal_path():
    state = policy(legacy())
    answer = resolve_geo_destination(state, pending(state, requested='Белгород', proposal='Белград'), 'да')
    assert answer['action'] == 'select_destination'
    assert answer['operations'][0]['value']['city'] == 'Белград'


def test_legacy_numeric_wrong_city_room_binding_requires_fresh_city_confirmation():
    state = policy(legacy())
    answer = resolve_geo_destination(state, pending(state, field='stay.rooms'), '1')
    assert answer['operations'] == [] and answer['action'] == 'ask_destination'
    assert 'без поиска' not in answer['question']
    ctx = {'state_version': state['state_version'], 'field': 'destination.city',
        'target_id': 'owned-stay', 'context': answer['pending_context'], 'text': answer['question']}
    selected = resolve_geo_destination(state, ctx, 'да')
    assert selected['action'] == 'select_destination' and selected['operations'][0]['value']['city'] == 'Белград'


def test_other_serbian_city_does_not_inherit_belgrade_qualification():
    state = policy(legacy())
    answer = resolve_geo_destination(state, pending(state, requested='Нови-Сад'), 'сохраняем')
    assert answer['action'] == 'retain_unsupported_destination'
