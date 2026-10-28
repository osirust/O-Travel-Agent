"""Current person-NoAdd authority; no provider or database calls."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from otravel.traveler_addition_grounding import guard_traveler_addition_operations as guard

SOURCE = json.loads((Path(__file__).parents[1] / 'fixtures/regression/nemo-explicit-noadd-20261028.json').read_text(encoding='utf-8'))['recorded_case']


def add():
    return {'type': 'add_traveler', 'value': {'id': 'new-traveler-5', 'type': 'child', 'age': 4}}


def budget():
    return {'type': 'set_budget', 'value': {'amount_minor': 118000000, 'currency': 'RUB'}}


def test_exact_nemo_prohibition_with_dependent_ids_requires_repair_without_guessing():
    before, raw = deepcopy(SOURCE['before_trip']), deepcopy(SOURCE['recorded_final_contract']['operations'])
    frozen = deepcopy(raw)
    kept, meta = guard(before, raw, SOURCE['input'])
    assert kept == raw == frozen
    assert meta['repair_required'] is True and not meta['conflicts']
    assert meta['feedback']['existing_traveler_ids'] == [person['id'] for person in before['travelers']]
    assert meta['feedback']['forbidden_new_traveler_ids'] == ['new-traveler-5']
    assert len(meta['feedback']['dependent_operations']) == 5
    assert all(op['value'].get('id') != 'new-traveler-4' for op in kept if op['type'] == 'add_traveler')


@pytest.mark.parametrize('text', [
    'Повторно никого не добавляй.', 'Никого больше не добавляй.',
    'Не добавляй новых участников.', 'Пожалуйста, не добавляй никого.',
    'Уменьши бюджет, а никого не добавляй.',
])
def test_global_person_prohibition_suppresses_only_independent_new_add(text):
    ops = [add(), budget()]
    frozen = deepcopy(ops)
    kept, meta = guard(SOURCE['before_trip'], ops, text)
    assert kept == [budget()] and not meta['repair_required']
    assert meta['notes'] and not meta['conflicts']
    assert ops == frozen


@pytest.mark.parametrize('dependent,path', [
    ({'type': 'upsert_group', 'target_id': 'new-group-1', 'value': {'traveler_ids': ['new-traveler-5']}}, 'value.traveler_ids'),
    ({'type': 'set_room_members', 'target_id': 'new-room-1', 'value': {'stay_id': 'new-stay-1', 'traveler_ids': ['new-traveler-5']}}, 'value.traveler_ids'),
    ({'type': 'upsert_stay', 'target_id': 'new-stay-1', 'value': {'city': 'Istanbul', 'rooms': [{'id': 'new-room-1', 'traveler_ids': ['new-traveler-5']}]}}, 'value.rooms[0].traveler_ids'),
    ({'type': 'update_traveler', 'target_id': 'new-traveler-5', 'value': {'group_id': 'new-group-1'}}, 'target_id'),
    ({'type': 'remove_traveler', 'target_id': 'new-traveler-5', 'value': {}}, 'target_id'),
])
def test_dependence_requires_atomic_repair_not_partial_dangling_commit(dependent, path):
    ops = [add(), dependent, budget()]
    kept, meta = guard(SOURCE['before_trip'], ops, 'Никого не добавляй.')
    assert kept == ops and meta['repair_required']
    assert meta['feedback']['dependent_operations'] == [{'index': 1, 'type': dependent['type'], 'fields': [path]}]


@pytest.mark.parametrize('text', [
    'Добавь ребёнка 4 лет.', 'Не добавляй завтрак.',
    '«Повторно никого не добавляй».', 'Если никого не добавляй, что получится?',
    'Раньше просил: никого не добавляй.', 'Никого не добавляй?',
    'Добавь ребёнка 4 лет, а никого другого не добавляй.',
    'Добавь сына, а никого другого кроме него не добавляй.',
    'Не добавляй новых участников, кроме сестры.',
    'Друг советует: никого не добавляй.',
    'Раньше никого не добавляй. Теперь добавь ребёнка 4 лет.',
])
def test_non_global_or_noncurrent_clause_is_not_a_prohibition(text):
    ops = [add(), budget()]
    kept, meta = guard(SOURCE['before_trip'], ops, text)
    assert kept == ops and not meta['repair_required'] and not meta['conflicts'] and not meta['notes']


@pytest.mark.parametrize('text', [
    'Никого не добавляй. Добавь ребёнка четырёх лет.',
    'Добавь одного взрослого, но никого не добавляй.',
])
def test_explicit_current_person_instructions_conflict_instead_of_model_repair(text):
    ops = [add(), budget()]
    kept, meta = guard(SOURCE['before_trip'], ops, text)
    assert kept == ops and meta['conflicts'] and not meta['repair_required']
    assert meta['unknowns'] == meta['conflicts']
    assert any('добав' in question and 'состав' in question for question in meta['conflicts'])
    assert meta['pending_context'] == {'field': 'party.addition', 'target_id': None, 'force_clarification': True}


def test_no_person_add_or_existing_id_echo_is_not_removed():
    echo = {'type': 'add_traveler', 'value': {'id': 'new-traveler-4', 'type': 'child', 'age': 4}}
    for ops in ([budget()], [echo, budget()]):
        assert guard(SOURCE['before_trip'], ops, 'Никого не добавляй.')[0] == ops


def test_do_not_remap_duplicate_child_to_an_existing_same_age_identity():
    ops = [add(), {'type': 'upsert_group', 'target_id': 'new-group-1', 'value': {'traveler_ids': ['new-traveler-5']}}]
    kept, meta = guard(SOURCE['before_trip'], ops, 'Повторно никого не добавляй.')
    assert meta['repair_required'] and kept == ops
    assert 'new-traveler-5' in meta['feedback']['forbidden_new_traveler_ids']


def test_global_person_noadd_does_not_conflict_with_requested_breakfast():
    ops = [add(), {'type': 'set_constraint', 'value': {'key': 'breakfast', 'value': True}}]
    kept, meta = guard(SOURCE['before_trip'], ops, 'Никого не добавляй. Добавь завтрак.')
    assert kept == ops[1:] and not meta['conflicts'] and not meta['repair_required']


def test_quoted_two_sentence_noadd_is_not_current_authority():
    ops = [add()]
    kept, meta = guard(SOURCE['before_trip'], ops,
        '«Никого не добавляй. Повторно никого не добавляй». Сейчас добавь ребёнка четырёх лет.')
    assert kept == ops and not meta['notes'] and not meta['conflicts']


def test_unnamed_new_add_is_also_forbidden_without_inventing_a_feedback_id():
    unnamed = {'type': 'add_traveler', 'value': {'type': 'child', 'age': 4}}
    kept, meta = guard(SOURCE['before_trip'], [unnamed, budget()], 'Никого не добавляй.')
    assert kept == [budget()] and not meta['repair_required']
    assert meta['notes'][0]['forbidden_new_traveler_ids'] == []
