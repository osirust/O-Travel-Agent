"""Existing ordered city references are distinct from route-choice intents."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from otravel.answering import canonical_geography_answer
from otravel.domain import TripState
from otravel.intent_grounding import ground_intent_operations

SOURCE = json.loads((Path(__file__).parents[1] / 'fixtures/regression/actual-wow-router-r5-20261027.json').read_text(encoding='utf-8'))


def test_actual_raw_used_scoped_membership_primitive_without_alias_or_sparse_repair():
    assert SOURCE['step_status'] == 'PASS'
    assert all(check['status'] == 'PASS' for check in SOURCE['checks'])
    raw = SOURCE['raw_validated'][0]['value']['operations']
    assert raw == SOURCE['checked'][0]['value']['operations']
    assert sum(op['type'] == 'set_room_members' for op in raw) == 2
    assert SOURCE['normalizations'] == [[]]
    assert SOURCE['after_status'] == 'WAITING_USER' and not SOURCE['plans']
    assert SOURCE['after_pending']['field'] == 'destination.city'


def test_captured_hotel_and_breakfast_city_references_do_not_create_a_route_choice_question():
    raw = deepcopy(SOURCE['raw_validated'][0]['value']['operations'])
    result, meta = ground_intent_operations(SOURCE['before_trip'], raw, SOURCE['text'])
    assert result == raw
    assert meta['unknowns'] == []
    assert meta['pending_context'] is None


@pytest.mark.parametrize('text', [
    'Замени отель в Анталье. Завтрак в Стамбуле сохрани.',
    'В Стамбуле сохрани номера. В Анталье измени проживание.',
    'Тариф отеля в Анталье поменяй. Условия отмены в Стамбуле сохрани.',
    'Первый отель в Стамбуле сохрани.',
    'Сохрани отель в Стамбуле.',
])
def test_existing_hotel_room_and_constraint_scopes_do_not_reopen_destination_intake(text):
    _, meta = ground_intent_operations(SOURCE['before_trip'], [], text)
    assert meta['unknowns'] == [] and meta['pending_context'] is None
    assert any(note['code'] == 'existing_stay_city_scopes_preserved' for note in meta['notes'])


@pytest.mark.parametrize('extra', [
    {'type': 'upsert_stay', 'target_id': 'new-stay-3', 'value': {'city': 'Дубай'}},
    {'type': 'upsert_stay', 'target_id': 'new-stay-1', 'value': {'city': 'Казань'}},
    {'type': 'remove_stay', 'target_id': 'new-stay-1', 'value': {}},
    {'type': 'set_origin', 'value': {'city': 'Москва'}},
    {'type': 'set_route_policy', 'value': {'kind': 'one_way'}},
])
def test_actual_city_origin_or_route_edits_are_not_hidden_as_hotel_scope_references(extra):
    _, meta = ground_intent_operations(SOURCE['before_trip'], [extra], SOURCE['text'])
    assert meta['unknowns']
    assert not any(note['code'] == 'existing_stay_city_scopes_preserved' for note in meta['notes'])


@pytest.mark.parametrize('text', [
    'Поменяй маршрут: отель в Стамбуле и отель в Анталье.',
    'Хочу в Стамбул и в Анталью, отели подбери.',
    'Выбери Стамбул или Анталью.',
    'Замени отель в Белграде, завтрак в Стамбуле сохрани, отель в Анталье тоже сохрани.',
])
def test_new_route_wishes_alternatives_and_an_unowned_city_keep_the_normal_clarification(text):
    _, meta = ground_intent_operations(SOURCE['before_trip'], [], text)
    assert meta['unknowns']
    assert not any(note['code'] == 'existing_stay_city_scopes_preserved' for note in meta['notes'])


def test_a_fresh_explicit_multicity_route_still_keeps_both_requested_new_stays():
    proposal = [{'type': 'upsert_stay', 'target_id': 'new-stay-1', 'value': {'city': 'Стамбул'}},
                {'type': 'upsert_stay', 'target_id': 'new-stay-2', 'value': {'city': 'Анталья'}}]
    result, meta = ground_intent_operations(TripState(), proposal, 'Из Казани в Стамбул, затем в Анталью.')
    assert [op['value']['city'] for op in result if op['type'] == 'upsert_stay'] == ['Стамбул', 'Анталья']
    assert not any(note['code'] == 'existing_stay_city_scopes_preserved' for note in meta['notes'])


def test_explicit_route_replacement_is_still_grounded_to_the_owned_stay():
    result, meta = ground_intent_operations(SOURCE['before_trip'], [], 'Замени Стамбул на Анталью.')
    assert {'type': 'upsert_stay', 'target_id': 'new-stay-1', 'value': {'city': 'Анталья'}} in result
    assert not any(note['code'] == 'existing_stay_city_scopes_preserved' for note in meta['notes'])


@pytest.mark.parametrize('field', ['destination.city', 'origin.city'])
def test_existing_pending_geography_question_is_not_silenced_by_a_hotel_reference(field):
    pending = {'field': field, 'state_version': SOURCE['before_trip']['state_version']}
    _, meta = ground_intent_operations(SOURCE['before_trip'], [], SOURCE['text'], pending)
    assert meta['unknowns']
    assert not any(note['code'] == 'existing_stay_city_scopes_preserved' for note in meta['notes'])


def test_geography_question_keeps_the_canonical_readonly_path():
    text = 'В какой стране Стамбул и Анталья?'
    reply = canonical_geography_answer(text, SOURCE['before_trip'])
    assert reply and 'Турция' in reply
    result, meta = ground_intent_operations(SOURCE['before_trip'], [], text)
    assert result == [] and meta['unknowns'] == []


def test_repeated_city_stays_are_not_assumed_to_be_a_unique_component_scope():
    state = deepcopy(SOURCE['before_trip'])
    repeated = deepcopy(state['stays'][0])
    repeated['id'] = 'repeated-istanbul'
    state['stays'].append(repeated)
    _, meta = ground_intent_operations(state, [], 'Сохрани отель в Стамбуле.')
    assert meta['unknowns']
    assert not any(note['code'] == 'existing_stay_city_scopes_preserved' for note in meta['notes'])


def test_single_saved_stay_city_change_remains_a_real_edit():
    state = deepcopy(SOURCE['before_trip'])
    state['stays'] = state['stays'][:1]
    result, meta = ground_intent_operations(state, [], 'Замени Стамбул на Казань.')
    assert {'type': 'upsert_stay', 'target_id': state['stays'][0]['id'], 'value': {'city': 'Казань'}} in result
    assert not any(note['code'] == 'existing_stay_city_scopes_preserved' for note in meta['notes'])
