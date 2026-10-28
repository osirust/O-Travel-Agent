"""Captured party regressions and current-message authority boundaries."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from otravel.domain import TripState, apply_operations
from otravel.party_preservation import guard_current_party_operations, resolve_owned_party_census_answer
from otravel.room_intent import resolve_room_count_answer

DATA = {row['id']: row for row in json.loads((Path(__file__).parents[1] /
    'fixtures/evaluation/final40-party-20261028.json').read_text(encoding='utf-8'))['rows']}


def project(identifier):
    row = DATA[identifier]
    state = deepcopy(row['before_trip'])
    operations, meta = guard_current_party_operations(state, row['raw_contract']['operations'],
        row['user'], row['before_pending'])
    after, _ = apply_operations(TripState.model_validate(state), operations)
    assert state == row['before_trip']
    return after.model_dump(mode='json'), meta


def test_captured024_current_only_child_is_removed_not_age_cleared():
    after, meta = project('FINAL40-024:T4')
    before = DATA['FINAL40-024:T4']['before_trip']
    assert after['travelers'] == [p for p in before['travelers'] if p['type'] == 'adult']
    for field in ('stays', 'origin', 'budget', 'constraints', 'preferences', 'search_policy'):
        assert after[field] == before[field]
    assert meta['notes'][0]['target_ids'] == ['new-traveler-3']
    assert meta['pending_context'] == {'children_known': True, 'children_count': 0}


def test_captured029_city_choice_is_not_an_adult_fact():
    after, meta = project('FINAL40-029:T4')
    assert after['travelers'] == []
    assert any(n['code'] == 'unrequested_party_addition_suppressed' for n in meta['notes'])


def test_captured026_inclusive_census_asks_about_child_without_identity_guess():
    after, meta = project('FINAL40-026:T3')
    assert after['travelers'] == DATA['FINAL40-026:T3']['before_trip']['travelers']
    assert meta['block_batch'] and meta['pending_context']['field'] == 'party.composition'
    assert 'ребён' in meta['unknowns'][0]


def test_captured001_one_known_adult_and_requested_one_room_have_unique_allocation():
    row = DATA['FINAL40-001:T12']
    result = resolve_room_count_answer(deepcopy(row['before_trip']), deepcopy(row['before_pending']), row['user'])
    after, _ = apply_operations(TripState.model_validate(row['before_trip']), result['operations'])
    assert result['handled']
    assert after.stays[0].rooms[0].traveler_ids == [after.travelers[0].id]


def test_one_existing_empty_room_reuses_its_id_for_solo_allocation():
    row = DATA['FINAL40-001:T12']; state = deepcopy(row['before_trip'])
    state['stays'][0]['rooms'] = [{'id': 'existing-room', 'traveler_ids': []}]
    result = resolve_room_count_answer(state, row['before_pending'], '1')
    after, _ = apply_operations(TripState.model_validate(state), result['operations'])
    assert after.stays[0].rooms[0].id == 'existing-room'
    assert after.stays[0].rooms[0].traveler_ids == [state['travelers'][0]['id']]


@pytest.mark.parametrize('text', [
    '«Ребёнок не едет» — пример фразы.', 'Если ребёнок не едет, можно выбрать другой номер.',
    'Ребёнок не едет?', 'Раньше ребёнок не ездил, сейчас никого не удаляй.',
    'Взрослый не едет.', 'Ребёнок не едет в этот отель.', 'Ребёнок не едет поездом.',
    'Ребёнка не удаляй.', 'Ребёнок не едет, но не удаляй его.',
])
def test_nonparticipation_requires_current_whole_trip_assertion(text):
    row = DATA['FINAL40-024:T4']
    operations, meta = guard_current_party_operations(row['before_trip'], [], text, row['before_pending'])
    assert operations == [] and not meta['notes']


@pytest.mark.parametrize('text', [
    '«Ребёнок не едет» — пример фразы.', 'Если ребёнок не едет, можно выбрать другой номер.',
    'Ребёнок не едет?', 'Ребёнок не едет в этот отель.', 'Ребёнок не едет поездом.',
])
def test_adversarial_model_delete_or_age_clear_has_no_local_or_hypothetical_authority(text):
    state = DATA['FINAL40-024:T4']['before_trip']; child = state['travelers'][-1]['id']
    operations = [{'type': 'remove_traveler', 'target_id': child, 'value': {}},
        {'type': 'update_traveler', 'target_id': child, 'value': {'age': None}}]
    result, _ = guard_current_party_operations(state, operations, text)
    assert result == []


def test_two_children_do_not_resolve_singular_by_order_and_drop_wrong_model_removal():
    state = deepcopy(DATA['FINAL40-024:T4']['before_trip'])
    state['travelers'].append({'id': 'child2', 'type': 'child', 'age': 9})
    operations, meta = guard_current_party_operations(state,
        [{'type': 'remove_traveler', 'target_id': 'child2', 'value': {}}], 'Ребёнок не едет.')
    assert operations == [] and meta['block_batch']
    assert meta['pending_context']['field'] == 'party.removal'


@pytest.mark.parametrize('binding', ['child2', '9 лет'])
def test_current_explicit_owned_child_binding_is_unique(binding):
    state = deepcopy(DATA['FINAL40-024:T4']['before_trip'])
    state['travelers'].append({'id': 'child2', 'type': 'child', 'age': 9})
    operations, meta = guard_current_party_operations(state, [], f'Ребёнок {binding} не едет.')
    assert operations == [{'type': 'remove_traveler', 'target_id': 'child2', 'value': {}}]
    assert not meta['block_batch']


def test_foreign_child_identifier_cannot_fall_back_to_sole_current_child():
    state = DATA['FINAL40-024:T4']['before_trip']
    operations, meta = guard_current_party_operations(state, [], 'Ребёнок foreign-child не едет.')
    assert operations == [] and meta['block_batch']


def test_plural_all_children_is_canonical_removal_and_cleans_room_group_references():
    state = deepcopy(DATA['FINAL40-024:T4']['before_trip'])
    child = state['travelers'][-1]['id']
    adult = state['travelers'][0]['id']
    state['groups'] = [{'id': 'g1', 'traveler_ids': [adult, child]}]
    for person in state['travelers']:
        if person['id'] in {adult, child}:
            person['group_id'] = 'g1'
    state['stays'][0]['rooms'] = [{'id': 'r1', 'traveler_ids': [adult, child]}]
    operations, _ = guard_current_party_operations(state, [], 'Дети не едут.')
    after, _ = apply_operations(TripState.model_validate(state), operations)
    assert child not in [p.id for p in after.travelers]
    assert after.groups[0].traveler_ids == after.stays[0].rooms[0].traveler_ids == [adult]


@pytest.mark.parametrize('text,kind,allowed', [
    ('Едут 1 взрослых, детей нет.', 'adult', True),
    ('Из Москвы в Казань, один взрослый и ребёнок 6 лет.', 'adult', True),
    ('Добавь ребёнка 6 лет.', 'child', True),
    ('Добавь ребёнка 6 лет.', 'adult', False),
    ('Если добавим одного взрослого, сколько будет стоить?', 'adult', False),
    ('«Один взрослый» — пример заполнения.', 'adult', False),
    ('We are two adults.', 'adult', True),
])
def test_addition_authority_is_current_and_does_not_cross_party_category(text, kind, allowed):
    state = DATA['FINAL40-029:T4']['before_trip']
    operations = [{'type': 'add_traveler', 'value': {'id': 'p', 'type': kind, 'age': 6 if kind == 'child' else None}}]
    result, _ = guard_current_party_operations(state, operations, text)
    assert bool(result) is allowed


@pytest.mark.parametrize('text,kind', [
    ('Один взрослый из Москвы в Казань, детей пока не будет. Хочу сначала собрать простой вариант поездки.', 'adult'),
    ('Добавь ребёнка, возраст пока не назвал', 'child'),
])
def test_current_positive_party_fact_survives_unknown_age_or_no_children_yet(text, kind):
    state = DATA['FINAL40-029:T4']['before_trip']
    operations = [{'type': 'add_traveler', 'value': {'id': 'new-person', 'type': kind}}]
    result, meta = guard_current_party_operations(state, operations, text)
    assert result == operations
    assert not any(n.get('code') == 'unrequested_party_addition_suppressed' for n in meta['notes'])


@pytest.mark.parametrize('text', [
    'Добавь ребёнка, если поедет сестра.',
    'Добавь ребёнка, пока не применяй.',
    'Ребёнка пока не добавляй, возраст пока не назвал.',
    'Пока не добавляй ребёнка.',
    'Пока не надо добавлять ребёнка.',
    'Не надо добавлять ребёнка.',
    'Если добавим ребёнка, возраст пока не назвал.',
    'Добавь ребёнка? Возраст пока не назвал.',
])
def test_unknown_age_does_not_override_an_actual_hypothesis_question_or_addition_veto(text):
    state = DATA['FINAL40-029:T4']['before_trip']
    operations = [{'type': 'add_traveler', 'value': {'id': 'new-child', 'type': 'child'}}]
    result, _ = guard_current_party_operations(state, operations, text)
    assert result == []


def test_unasked_person_used_by_membership_setter_blocks_instead_of_guessing():
    row = DATA['FINAL40-029:T4']
    operations = deepcopy(row['raw_contract']['operations'])
    operations.append({'type': 'upsert_group', 'target_id': 'g', 'value': {'traveler_ids': ['new-traveler-1']}})
    result, meta = guard_current_party_operations(row['before_trip'], operations, row['user'])
    assert result == [] and meta['block_batch']
    assert meta['pending_context']['field'] == 'party.composition'


@pytest.mark.parametrize('field,answer,version_delta,permitted', [
    ('party.adults', '1', 0, True), ('party.adults', '1', -1, False),
    ('destination.city', 'Москва', 0, False), ('party.adults', 'Москва', 0, False),
])
def test_owned_party_short_answer_is_fresh_and_matches_slot(field, answer, version_delta, permitted):
    row = DATA['FINAL40-029:T4']
    added = [row['raw_contract']['operations'][1]]
    pending = {'field': field, 'state_version': row['before_trip']['state_version'] + version_delta}
    result, _ = guard_current_party_operations(row['before_trip'], added, answer, pending)
    assert bool(result) is permitted


def test_owned_child_count_does_not_authorize_a_model_created_adult():
    row = DATA['FINAL40-029:T4']
    result, _ = guard_current_party_operations(row['before_trip'], [row['raw_contract']['operations'][1]], '1',
        {'field': 'party.children', 'state_version': row['before_trip']['state_version']})
    assert result == []


@pytest.mark.parametrize('mutation', ['two_people', 'two_stays', 'stale', 'foreign_target', 'two_rooms'])
def test_solo_allocator_has_no_ambiguous_or_stale_authority(mutation):
    row = DATA['FINAL40-001:T12']; state = deepcopy(row['before_trip']); pending = deepcopy(row['before_pending'])
    text = '1'
    if mutation == 'two_people':
        state['travelers'].append({'id': 'extra', 'type': 'adult'})
    elif mutation == 'two_stays':
        state['stays'].append({'id': 'other', 'city': 'Казань', 'rooms': []})
    elif mutation == 'stale':
        pending['state_version'] -= 1
    elif mutation == 'foreign_target':
        pending['target_id'] = 'foreign'
    else:
        text = '2'
    result = resolve_room_count_answer(state, pending, text)
    for operation in result['operations']:
        for room in operation.get('value', {}).get('rooms', []):
            assert room['traveler_ids'] == []


@pytest.mark.parametrize('text', ['Едут 1 взрослых, детей нет.', 'Один взрослый, без детей.',
    'Едем один взрослый, дети не едут.', 'Едут 1 взрослых. Детей нет.'])
def test_closed_compound_owned_party_answer_does_not_need_model(text):
    state = DATA['FINAL40-029:T4']['before_trip']
    pending = {'field': 'party.adults', 'state_version': state['state_version']}
    result = resolve_owned_party_census_answer(state, pending, text)
    assert result['handled'] and result['pending_context']['children_known']
    after, _ = apply_operations(TripState.model_validate(state), result['operations'])
    assert len(after.travelers) == 1 and after.travelers[0].type == 'adult'


@pytest.mark.parametrize('text', ['Едут 1 взрослых, детей нет. Бюджет 70000 рублей.',
    'Едут 1 взрослых, детей нет?', 'Если едут 1 взрослых, детей нет.',
    '«Едут 1 взрослых, детей нет».', 'Едут 1 взрослых, детей нет; перенеси возвращение на день.'])
def test_closed_party_slot_cannot_swallow_extra_edit_question_hypothesis_or_quote(text):
    state = DATA['FINAL40-029:T4']['before_trip']
    result = resolve_owned_party_census_answer(state, {'field': 'party.adults', 'state_version': state['state_version']}, text)
    assert not result['handled']


@pytest.mark.parametrize('field,version_delta', [('destination.city', 0), ('party.adults', -1)])
def test_closed_party_slot_must_be_owned_and_current(field, version_delta):
    state = DATA['FINAL40-029:T4']['before_trip']
    result = resolve_owned_party_census_answer(state, {'field': field, 'state_version': state['state_version'] + version_delta},
        'Едут 1 взрослых, детей нет.')
    assert not result['handled']


def test_closed_party_count_reduction_asks_identity_instead_of_deleting_known_adult():
    state = DATA['FINAL40-026:T3']['before_trip']
    result = resolve_owned_party_census_answer(state, {'field': 'party.adults', 'state_version': state['state_version']},
        'Едут 1 взрослых, детей нет.')
    assert result['handled'] and result['operations'] == []
    assert result['pending_context']['force_clarification']


def test_actual_closed_three_clause_roster_and_solo_room_are_one_canonical_batch():
    state = DATA['FINAL40-029:T4']['before_trip']
    result = resolve_owned_party_census_answer(state, {'field': 'party.adults', 'state_version': state['state_version']},
        'Едут 1 взрослых. Детей нет. Нужно 1 номера.')
    assert result['handled']
    after, _ = apply_operations(TripState.model_validate(state), result['operations'])
    assert len(after.travelers) == len(after.stays) == len(after.stays[0].rooms) == 1
    assert after.stays[0].rooms[0].traveler_ids == [after.travelers[0].id]
    assert after.stays[0].city == state['stays'][0]['city']


@pytest.mark.parametrize('suffix', ['Бюджет 70000 рублей.', 'Отель только в центре.',
    'Заезд 2026-11-10.', 'Перенеси возвращение на день.', 'Не применяй.',
    'Ребёнок поедет.', 'Два номера.', 'Гражданство Россия.'])
def test_three_closed_clauses_cannot_consume_fourth_edit(suffix):
    state = DATA['FINAL40-029:T4']['before_trip']
    result = resolve_owned_party_census_answer(state, {'field': 'party.adults', 'state_version': state['state_version']},
        'Едут 1 взрослых. Детей нет. Нужно 1 номера. ' + suffix)
    assert not result['handled']


@pytest.mark.parametrize('mutation', ['two_adults', 'two_rooms', 'two_stays', 'many_existing_rooms', 'stale'])
def test_closed_compound_room_requires_unique_solo_layout(mutation):
    state = deepcopy(DATA['FINAL40-029:T4']['before_trip'])
    text = 'Едут 1 взрослых. Детей нет. Нужно 1 номера.'
    pending = {'field': 'party.adults', 'state_version': state['state_version']}
    if mutation == 'two_adults':
        text = text.replace('Едут 1', 'Едут 2')
    elif mutation == 'two_rooms':
        text = text.replace('Нужно 1', 'Нужно 2')
    elif mutation == 'two_stays':
        state['stays'].append({'id': 'second', 'city': 'Казань', 'rooms': []})
    elif mutation == 'many_existing_rooms':
        state['stays'][0]['rooms'] = [{'id': 'r1', 'traveler_ids': []}, {'id': 'r2', 'traveler_ids': []}]
    else:
        pending['state_version'] -= 1
    result = resolve_owned_party_census_answer(state, pending, text)
    assert not result['handled']
