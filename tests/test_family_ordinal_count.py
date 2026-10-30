"""Ordinal family declarations only guard admission; never infer membership."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from otravel.domain import Operation, TripState, apply_operations
from otravel.group_semantics import guard_group_separation

FIXTURE = Path(__file__).parents[1] / 'fixtures/regression/family-ordinal-count-076-20261028.json'
CODE = 'explicit_ordinal_family_count_mismatch'


def source():
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


def corrected_ops(fixture):
    ops = deepcopy(fixture['raw_contract']['operations'][:3])
    return [*ops,
        {'type': 'upsert_group', 'target_id': 'new-group-1', 'value': {
            'traveler_ids': ['new-traveler-1', 'new-traveler-2', 'new-traveler-6', 'new-traveler-7'], 'keep_separate': True}},
        {'type': 'upsert_group', 'target_id': 'new-group-2', 'value': {
            'traveler_ids': ['new-traveler-3', 'new-traveler-4', 'new-traveler-5', 'new-traveler-8', 'new-traveler-9'], 'keep_separate': True}}]


@pytest.mark.parametrize('candidate_count', [0, 1, 3])
def test_exact_current_family_pair_mismatch_blocks_without_inventing_members_or_modifying_raw(candidate_count):
    fixture = source()
    state = TripState.model_validate(fixture['before_trip'])
    ops = deepcopy(fixture['raw_contract']['operations'][:3])
    ops.extend({'type': 'upsert_group', 'target_id': f'g{i}', 'value': {
        'traveler_ids': [] if i else [p['id'] for p in fixture['before_trip']['travelers'] if p['type'] == 'adult'],
        'keep_separate': True}} for i in range(candidate_count))
    before, before_ops = state.model_dump(mode='json'), deepcopy(ops)
    result = guard_group_separation(state, ops, fixture['input'])
    assert result['handled'] and result['block_batch'] is True
    assert result['field'] == 'group.membership' and result['target_id'] is None
    assert result['context']['force_clarification'] is True
    assert result['context']['family_separation_required'] is True
    assert result['notes'][-1]['code'] == CODE
    assert result['notes'][-1]['declared_count'] == 2
    assert result['notes'][-1]['candidate_count'] == candidate_count
    assert result['operations'] == [Operation.model_validate(op) for op in ops]
    assert state.model_dump(mode='json') == before and ops == before_ops


@pytest.mark.parametrize('pair', [
    'Первая семья живёт в первом номере, вторая — во втором и третьем.',
    'Первая семья — в первом номере. Вторая семья живёт во втором и третьем.',
    'Первая семья размещается в первом номере; вторая семья — во втором и третьем.',
])
def test_narrow_asserted_ordinal_pair_with_no_mix_requires_exactly_two(pair):
    fixture = source()
    result = guard_group_separation(fixture['before_trip'], fixture['raw_contract']['operations'], pair + ' Семьи не смешивай.')
    assert result['handled'] and result['block_batch']
    assert result['notes'][-1]['code'] == CODE


def test_correct_explicit_two_groups_and_all_three_room_setters_survive_exactly():
    fixture = source()
    ops = corrected_ops(fixture)
    before_ops = deepcopy(ops)
    result = guard_group_separation(fixture['before_trip'], ops, fixture['input'])
    assert result['handled'] and not result['block_batch'] and 'question' not in result
    assert result['operations'] == [Operation.model_validate(op) for op in ops]
    saved, _ = apply_operations(TripState.model_validate(fixture['before_trip']), result['operations'])
    assert len(saved.groups) == 2 and all(group.keep_separate for group in saved.groups)
    assert [group.traveler_ids for group in saved.groups] == [op['value']['traveler_ids'] for op in ops[3:]]
    assert [room.traveler_ids for room in saved.stays[0].rooms] == [op['value']['traveler_ids'] for op in ops[:3]]
    assert ops == before_ops


@pytest.mark.parametrize('text', [
    'Если первая семья живёт в первом номере, вторая — во втором. Семьи не смешивай.',
    'Первая семья живёт в первом номере, вторая — во втором. Семьи не смешивай?',
    'Раньше первая семья жила в первом номере, вторая — во втором. Семьи не смешивай.',
    'Вчера говорил: первая семья живёт в первом номере, вторая — во втором. Семьи не смешивай.',
    '«Первая семья живёт в первом номере, вторая — во втором». Семьи не смешивай.',
    '"Первая семья живёт в первом номере, вторая — во втором". Семьи не смешивай.',
    'Первая семья живёт в первом номере, вторая — во втором. Семьи можно смешивать.',
    'Первая семья живёт в первом номере, вторая — во втором. Семьи не смешивай, но можно смешивать.',
    'Первая семья живёт в первом или втором номере, вторая — во втором. Семьи не смешивай.',
    'Первая семья живёт в первом номере, вторая либо третья — во втором. Семьи не смешивай.',
    'Первая семья живёт в первом номере, вторая — во втором, третья семья в третьем. Семьи не смешивай.',
    'Первый номер — два взрослых, второй номер — взрослый. Семьи не смешивай.',
    'Первая семья живёт в первом номере, вторая — во втором.',
    'Первая семья не живёт в первом номере, вторая — во втором. Семьи не смешивай.',
    'Не первая семья живёт в первом номере, вторая — во втором. Семьи не смешивай.',
    'Первая семья живёт в первом номере, второй номер — другая семья. Семьи не смешивай.',
    'Возможно первая семья живёт в первом номере, вторая — во втором. Семьи не смешивай.',
    'Первая семья живёт в первом номере, вторая — во втором. Подожди, пока не меняй состав. Семьи не смешивай.',
    'Две или три семьи. Первая семья живёт в первом номере, вторая — во втором. Семьи не смешивай.',
])
def test_unasserted_or_unsupported_ordinal_text_does_not_activate_new_count_block(text):
    fixture = source()
    result = guard_group_separation(fixture['before_trip'], fixture['raw_contract']['operations'], text)
    assert not any(note.get('code') == CODE for note in result.get('notes', []))


def test_existing_cardinal_count_path_still_adds_only_empty_unknown_groups():
    fixture = source()
    result = guard_group_separation(fixture['before_trip'], fixture['raw_contract']['operations'], 'Две семьи, семьи не смешивай.')
    assert result['handled'] and not result['block_batch']
    assert not any(note.get('code') == CODE for note in result.get('notes', []))
    saved, _ = apply_operations(TripState.model_validate(fixture['before_trip']), result['operations'])
    assert len(saved.groups) == 2 and saved.groups[1].traveler_ids == []
