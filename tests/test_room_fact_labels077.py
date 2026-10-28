"""Saved room conditions retain their exact canonical scope in factual replies."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from otravel.answering import checked_reply, facts_projection

FIXTURE = Path(__file__).parents[1] / 'fixtures/regression/room-fact-labels077-20261028.json'


def captured():
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


def preferences(state, text='Какие требования к тарифам у каждого номера?'):
    facts = facts_projection(state, [], [], [], [], question=text)
    return checked_reply('preferences', facts, text)[0]


def test_captured077_refundability_and_breakfast_label_the_two_distinct_rooms():
    record = captured()
    state = record['before_trip']
    before = deepcopy(state)
    result = preferences(state, record['input'])
    refundable, breakfast = result.split('. ')
    assert 'номера №1' in refundable and 'возвратность тарифа — да' in refundable
    assert 'номера №2' in breakfast and 'включённый завтрак — да' in breakfast
    assert 'Стамбул' in refundable and 'Стамбул' in breakfast
    assert 'номера №2' not in refundable and 'номера №1' not in breakfast
    assert 'указанного номера' not in result
    assert state == before


def test_scoped_room_id_reused_in_two_stays_uses_the_explicit_stay():
    state = captured()['before_trip']
    second = deepcopy(state['stays'][0])
    second.update(id='second-stay', city='Анталья')
    state['stays'].append(second)
    state['constraints'] = [{'key': 'breakfast', 'value': True, 'hard': True,
        'stay_id': 'second-stay', 'room_id': second['rooms'][1]['id']}]
    result = preferences(state)
    assert 'номера №2' in result and 'проживание №2' in result and 'Анталья' in result
    assert 'Стамбул' not in result


def test_room_id_without_stay_is_rejected_by_the_canonical_schema():
    state = captured()['before_trip']
    second = deepcopy(state['stays'][0])
    second.update(id='second-stay', city='Анталья')
    state['stays'].append(second)
    state['constraints'] = [{'key': 'breakfast', 'value': True, 'hard': True,
        'room_id': second['rooms'][1]['id']}]
    # This shape cannot enter canonical state: a room constraint requires stay_id.
    # Preserve that boundary rather than test an impossible public projection.
    with pytest.raises(ValueError, match='требует stay_id'):
        preferences(state)


def test_missing_room_reference_is_labelled_unresolved_without_raw_identifiers():
    state = captured()['before_trip']
    state['constraints'][0]['room_id'] = 'missing-private-room-identifier'
    state['constraints'] = state['constraints'][:1]
    result = preferences(state)
    assert 'нужно уточнить' in result and 'номера №' not in result
    assert 'missing-private-room-identifier' not in result


def test_stay_and_global_requirements_remain_distinguishable():
    state = captured()['before_trip']
    state['constraints'] = [{'key': 'breakfast', 'value': True, 'hard': True,
        'stay_id': 'new-stay-1'}, {'key': 'refundable', 'value': False, 'hard': False}]
    result = preferences(state)
    assert 'для проживания №1' in result and 'Стамбул' in result
    assert 'для всей поездки' in result and 'возвратность тарифа — нет (пожелание)' in result
