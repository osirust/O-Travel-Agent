"""Existing-adult scalar grounding only; no universal age/person attribution."""
from copy import deepcopy

import pytest

from otravel.adult_age_grounding import FINDING_ID, guard_adult_age_operations
from otravel.domain import Operation, TripState, apply_operations


def before(age=None):
    return TripState(trip_id='known-adult-age', travelers=[{'id': 'existing-adult', 'type': 'adult', 'age': age},
        {'id': 'existing-child', 'type': 'child', 'age': 7}])


def update(age=18, **fields):
    return {'type': 'update_traveler', 'target_id': 'existing-adult', 'value': {'age': age, **fields}}


@pytest.mark.parametrize('text', [
    'Сверь пожалуста обе даты и число взрослых. Февраль был только вопросом.',
    'Напомни состав и даты поездки.', 'Ребёнку семь лет, взрослых двое.',
    '«Мне 46 лет» — это цитата.', 'Если мне 46 лет, получится дешевле?',
    'Раньше писал: мне 46 лет.', 'Мне не 46 лет.', 'Мне 46 лет?',
])
@pytest.mark.parametrize('previous,new', [(None, 18), (45, 46)])
def test_unasked_existing_adult_age_is_removed_but_other_fields_and_ops_survive(text, previous, new):
    state = before(previous)
    operations = [update(new, type='adult', group_id=None),
        {'type': 'update_traveler', 'target_id': 'existing-child', 'value': {'age': 8}}]
    frozen = deepcopy(operations)
    checked, meta = guard_adult_age_operations(state, operations, text)
    assert checked == [{**operations[0], 'value': {'type': 'adult', 'group_id': None}}, operations[1]]
    assert operations == frozen and state.travelers[0].age == previous
    assert meta['notes'][0]['code'] == 'unasked_existing_adult_age_removed'
    assert meta['notes'][0]['finding_id'] == FINDING_ID
    assert meta['notes'][0]['traveler_id'] == 'existing-adult'
    after, _ = apply_operations(state, [Operation.model_validate(operation) for operation in checked])
    assert after.travelers[0].age == previous and after.travelers[1].age == 8


def test_age_only_update_is_dropped_without_a_fabricated_replacement():
    checked, meta = guard_adult_age_operations(before(), [update()], 'Сверь даты и взрослых.')
    assert checked == [] and len(meta['notes']) == 1


def test_identical_existing_age_is_a_harmless_echo():
    operations = [update(45)]
    assert guard_adult_age_operations(before(45), operations, 'Сверь даты.') == (
        operations, {'notes': [], 'unknowns': [], 'conflicts': []})


@pytest.mark.parametrize('text', ['Исправь мой возраст: мне 46 лет.', 'Взрослому 46 лет.',
    'My wife is 46 years old.', 'Муж родился 1980-01-01.'])
def test_current_explicit_adult_age_or_dob_preserves_existing_update(text):
    operations = [update(46)]
    checked, meta = guard_adult_age_operations(before(45), operations, text)
    assert checked == operations and not meta['notes']


@pytest.mark.parametrize('age', [None, 17, '46', 46.0, True])
def test_clear_invalid_or_other_age_shapes_remain_outside_this_narrow_guard(age):
    operations = [update(age)]
    checked, meta = guard_adult_age_operations(before(45), operations, 'Сверь даты.')
    assert checked == operations and not meta['notes']


def test_domain_typed_update_is_unchanged_by_model_only_guard():
    state = before()
    after, _ = apply_operations(state, [Operation.model_validate(update())])
    assert after.travelers[0].age == 18
