"""Scoped room-membership patches never replace unrelated room layouts."""
import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from otravel.domain import DomainError, Operation, TripState, apply_operations
from otravel.group_semantics import protect_group_separation
from otravel.llm_contract import build_operation_schema
from otravel.room_additions import normalize_sparse_room_additions

SOURCE = json.loads((Path(__file__).parents[1] / 'fixtures/regression/compound-openrouter-r4-20261027.json').read_text(encoding='utf-8'))


def observed():
    row = SOURCE['steps'][0]
    return row, TripState.model_validate(row['before_state']), [Operation.model_validate(op) for op in row['validated'][0]['value']['operations']]


def test_actual_partial_room_snapshots_are_schema_valid_but_fail_semantic_room_reference_validation():
    row, state, _ = observed()
    assert not list(Draft202012Validator(build_operation_schema(state)).iter_errors(row['validated'][0]['value']))
    assert row['run_error']['code'] == 'INVALID_LLM_OPERATION'
    assert row['checked'] == [] and row['actual_after_state'] == row['before_state']


def test_new_typed_room_membership_updates_only_the_owned_stay_and_room():
    _, state, _ = observed()
    original = deepcopy(state)
    first = state.stays[0]
    # A no-op membership setter isolates the new primitive from party changes.
    operation = Operation(type='set_room_members', target_id=first.rooms[1].id,
        value={'stay_id': first.id, 'traveler_ids': list(first.rooms[1].traveler_ids)})
    changed, changes = apply_operations(state, [operation])
    assert changed == original and changes == []


def test_exact_actual_sparse_new_child_updates_are_converted_without_deleting_other_rooms():
    row, state, operations = observed()
    compatible, notes = normalize_sparse_room_additions(state, operations, row['text'])
    assert sum(op.type == 'set_room_members' for op in compatible) == 2
    assert any(note['code'] == 'sparse_new_traveler_room_patch_converted' for note in notes)
    compatible = protect_group_separation(state, compatible, row['text'])['operations']
    changed, _ = apply_operations(state, compatible)
    assert len(changed.travelers) == 4 and changed.budget.amount_minor == 38_000_000
    assert changed.constraints == state.constraints
    assert changed.stays[0].rooms[0] == state.stays[0].rooms[0]
    assert changed.stays[1].rooms[0] == state.stays[1].rooms[0]
    for before, stay in zip(state.stays, changed.stays, strict=True):
        assert [room.id for room in stay.rooms] == [room.id for room in before.rooms]
        assert stay.rooms[1].traveler_ids == ['new-traveler-2', 'new-traveler-4']
    assert changed.stays[-1].check_out.isoformat() == changed.legs[-1].departure_date.isoformat() == '2026-11-19'


def scoped_state():
    return TripState(travelers=[{'id': 'a', 'type': 'adult'}, {'id': 'b', 'type': 'adult'}], stays=[
        {'id': 's1', 'city': 'Стамбул', 'rooms': [{'id': 'r1', 'traveler_ids': ['a']}, {'id': 'r2', 'traveler_ids': ['b']}]},
        {'id': 's2', 'city': 'Анталья', 'rooms': [{'id': 'r1', 'traveler_ids': ['a']}, {'id': 'r2', 'traveler_ids': ['b']}]},
    ])


def test_typed_room_targets_are_correlated_by_stay_even_when_room_ids_repeat_across_cities():
    state = scoped_state()
    before = deepcopy(state)
    changed, _ = apply_operations(state, [
        {'type': 'set_room_members', 'target_id': 'r1', 'value': {'stay_id': 's1', 'traveler_ids': ['b']}},
        {'type': 'set_room_members', 'target_id': 'r2', 'value': {'stay_id': 's1', 'traveler_ids': ['a']}},
    ])
    assert changed.stays[1] == state.stays[1]
    assert [room.id for room in changed.stays[0].rooms] == ['r1', 'r2']
    assert [room.traveler_ids for room in changed.stays[0].rooms] == [['b'], ['a']]
    assert changed.travelers == state.travelers and state == before


@pytest.mark.parametrize('target,value', [
    ('r1', {'traveler_ids': ['a']}), ('r1', {'stay_id': 'unknown', 'traveler_ids': ['a']}),
    ('new-room', {'stay_id': 's1', 'traveler_ids': ['a']}), ('r1', {'stay_id': 's1'}),
    ('r1', {'stay_id': 's1', 'traveler_ids': ['unknown']}),
    ('r1', {'stay_id': 's1', 'traveler_ids': ['a', 'a']}),
    ('r1', {'stay_id': 's1', 'traveler_ids': ['a'], 'hotel_id': 'not-authorized'}),
    ('r1', {'stay_id': 's1', 'traveler_ids': True}),
])
def test_typed_room_membership_rejects_missing_or_foreign_scope_bad_guests_and_unrelated_fields_atomically(target, value):
    state = scoped_state()
    before = deepcopy(state)
    with pytest.raises(DomainError):
        apply_operations(state, [{'type': 'set_room_members', 'target_id': target, 'value': value}])
    assert state == before


def test_schema_only_accepts_correlated_owned_stay_room_pairs():
    state = scoped_state()
    state.stays[1].rooms[1].id = 'only-s2'
    schema = build_operation_schema(state)
    def response(stay_id, room_id):
        return {'operations': [{'type': 'set_room_members', 'target_id': room_id,
            'value': {'stay_id': stay_id, 'traveler_ids': ['b']}}],
            'control': {'action': 'continue'}, 'reply': '', 'unknowns': [], 'conflicts': []}
    assert not list(Draft202012Validator(schema).iter_errors(response('s1', 'r2')))
    assert not list(Draft202012Validator(schema).iter_errors(response('s2', 'only-s2')))
    assert list(Draft202012Validator(schema).iter_errors(response('s1', 'only-s2')))
    assert list(Draft202012Validator(schema).iter_errors(response('unknown', 'r2')))


@pytest.mark.parametrize('text', [
    'Если добавить ребёнка восьми лет во второй номер?', 'Добавь ребёнка восьми лет во второй номер?',
    'Ранее просил добавить ребёнка во второй номер.', '«Добавь ребёнка восьми лет во второй номер».',
    'Добавь ребёнка восьми лет во второй номер, но пока не применяй.',
    'Добавь ребёнка восьми лет во второй номер. Удали первый номер.',
    'Добавь ребёнка восьми лет. Объедини номера.',
    'Добавь ребёнка восьми лет во второй номер. Теперь нужен один номер.',
])
def test_sparse_compat_is_not_authorized_by_question_history_quote_veto_or_layout_change(text):
    _, state, operations = observed()
    assert normalize_sparse_room_additions(state, operations, text) == (operations, [])


@pytest.mark.parametrize('text', [
    'Добавь отель для ребёнка. Он живёт во втором номере.',
    'Добавь ребёнка восьми лет. Второй номер стоит пять тысяч рублей.',
])
def test_sparse_compat_requires_a_person_addition_and_an_actual_room_membership_instruction(text):
    _, state, operations = observed()
    assert normalize_sparse_room_additions(state, operations, text) == (operations, [])


@pytest.mark.parametrize('mutation', ['move_existing', 'drop_old', 'unknown_room', 'new_member_not_created', 'duplicate_new', 'extra_room_field'])
def test_sparse_compat_does_not_guess_changed_old_memberships_new_ids_or_unknown_people(mutation):
    row, state, operations = observed()
    # Restrict to a single setter to assert exact failure without independent
    # conversion on the other stay obscuring this negative.
    additions = [deepcopy(op) for op in operations if op.type == 'add_traveler']
    setter = deepcopy(next(op for op in operations if op.type == 'upsert_stay'))
    room = setter.value['rooms'][0]
    if mutation == 'move_existing':
        room['traveler_ids'].append('new-traveler-1')
    elif mutation == 'drop_old':
        room['traveler_ids'] = ['new-traveler-4']
    elif mutation == 'unknown_room':
        room['id'] = 'new-alias'
    elif mutation == 'new_member_not_created':
        room['traveler_ids'].append('uncreated-child')
    elif mutation == 'duplicate_new':
        room['traveler_ids'].append('new-traveler-4')
    else:
        room['price'] = 1
    candidate = [*additions, setter]
    assert normalize_sparse_room_additions(state, candidate, row['text']) == (candidate, [])


def test_sparse_compat_requires_unique_new_traveler_creation_and_does_not_touch_a_full_layout():
    row, state, operations = observed()
    assert normalize_sparse_room_additions(state, [op for op in operations if op.type != 'add_traveler'], row['text']) == (
        [op for op in operations if op.type != 'add_traveler'], [])
    addition = deepcopy(next(op for op in operations if op.type == 'add_traveler'))
    assert normalize_sparse_room_additions(state, [*operations, addition], row['text']) == ([*operations, addition], [])
    full = deepcopy(next(op for op in operations if op.type == 'upsert_stay'))
    full.value['rooms'].insert(0, state.stays[0].rooms[0].model_dump(mode='json'))
    candidate = [addition, full]
    assert normalize_sparse_room_additions(state, candidate, row['text']) == (candidate, [])
