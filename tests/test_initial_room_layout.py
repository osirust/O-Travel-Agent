"""Exact initial GLM45 room omission and bounded literal allocation rules."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from otravel.domain import Operation, TripState, apply_operations
from otravel.initial_room_layout import complete_initial_room_layouts

SOURCE = json.loads((Path(__file__).parents[1] / 'fixtures/regression/initial-rooms-glm45-20261027.json').read_text(encoding='utf-8'))


def captured():
    return TripState.model_validate(deepcopy(SOURCE['before_state'])), [Operation.model_validate(deepcopy(op))
        for op in SOURCE['validated']['value']['operations']]


def test_exact_actual_glm45_initial_room_omission_is_completed_from_literal_universal_and_group_partition():
    state, operations = captured()
    original = deepcopy(operations)
    completed, meta = complete_initial_room_layouts(state, operations, SOURCE['text'])
    assert meta['unknowns'] == []
    assert any(note['code'] == 'initial_literal_family_rooms_completed' for note in meta['notes'])
    trip, _ = apply_operations(state, completed)
    assert [stay.city for stay in trip.stays] == ['Стамбул', 'Анталья']
    for stay in trip.stays:
        assert [room.id for room in stay.rooms] == ['new-room-1', 'new-room-2']
        assert stay.rooms[0].traveler_ids == ['new-traveler-1', 'new-traveler-3']
        assert stay.rooms[1].traveler_ids == ['new-traveler-2']
    assert trip.groups[0].traveler_ids == ['new-traveler-1', 'new-traveler-3']
    assert trip.groups[1].traveler_ids == ['new-traveler-2']
    assert trip.budget.amount_minor == 40_000_000 and len(trip.travelers) == 3
    assert operations == original and not state.stays


@pytest.mark.parametrize('text', [
    'В каждом городе два номера. В первом номере первый взрослый с ребёнком, во втором второй взрослый.',
    'В обоих городах 2 номера. В первом номере первый взрослый с ребёнком, во втором номере второй взрослый отдельно.',
    'В каждом из городов два номера. В первом номере второй взрослый, во втором первый взрослый с ребёнком.',
])
def test_universal_literal_mapping_is_not_bound_to_cities_or_a_single_group_order(text):
    state, operations = captured()
    result, meta = complete_initial_room_layouts(state, operations, text)
    assert not meta['unknowns']
    trip, _ = apply_operations(state, result)
    expected = (['new-traveler-2'], ['new-traveler-1', 'new-traveler-3']) if 'первом номере второй' in text else (
        ['new-traveler-1', 'new-traveler-3'], ['new-traveler-2'])
    for stay in trip.stays:
        assert tuple(room.traveler_ids for room in stay.rooms) == expected


@pytest.mark.parametrize('text', [
    'В каждом городе два номера. В первом номере первый взрослый с ребёнком.',
    'В каждом городе два номера. В первом номере первый взрослый с ребёнком, во втором первый взрослый с ребёнком.',
    'В каждом городе два номера. В первом номере третий взрослый, во втором второй взрослый.',
    'В каждом городе три номера. В первом номере первый взрослый с ребёнком, во втором второй взрослый.',
    'В каждом городе два номера. В первом номере не первый взрослый с ребёнком, во втором второй взрослый.',
    'Если в каждом городе два номера. В первом номере первый взрослый с ребёнком, во втором второй взрослый.',
    'Не нужно в каждом городе два номера. В первом номере первый взрослый с ребёнком, во втором второй взрослый.',
    'В каждом городе два номера. «В первом номере первый взрослый с ребёнком, во втором второй взрослый».',
    'В каждом городе два номера? В первом номере первый взрослый с ребёнком, во втором второй взрослый?',
])
def test_unproven_scope_incomplete_mapping_contradiction_unknown_person_hypothesis_or_quoted_layout_asks_without_inference(text):
    state, operations = captured()
    result, meta = complete_initial_room_layouts(state, operations, text)
    assert result == operations
    assert meta['unknowns'] and all('stay.' not in question and 'new-' not in question for question in meta['unknowns'])
    assert all('rooms' not in op.value for op in result if op.type == 'upsert_stay')


def test_missing_or_incomplete_raw_groups_are_never_invented():
    state, operations = captured()
    for remove_all in (False, True):
        proposal = [deepcopy(op) for op in operations if op.type != 'upsert_group' or not remove_all]
        if not remove_all:
            first_group = next(op for op in proposal if op.type == 'upsert_group')
            first_group.value['traveler_ids'] = ['new-traveler-1']
        result, meta = complete_initial_room_layouts(state, proposal, SOURCE['text'])
        assert result == proposal and meta['unknowns']


def two_children():
    state, operations = captured()
    addition = Operation(type='add_traveler', value={'id': 'new-traveler-4', 'type': 'child', 'age': 8})
    first_group = next(index for index, op in enumerate(operations) if op.type == 'upsert_group')
    operations.insert(first_group, addition)
    operations[-1].value['traveler_ids'].append('new-traveler-4')
    return state, operations


def test_two_children_require_unique_addressing_even_when_model_groups_have_memberships():
    state, operations = two_children()
    result, meta = complete_initial_room_layouts(state, operations, SOURCE['text'])
    assert result == operations and meta['unknowns']
    assert meta['pending_context']['requested_room_count'] == 2


def test_explicit_unique_child_ages_can_prove_an_existing_complete_group_mapping():
    state, operations = two_children()
    text = 'В обоих городах два номера. В первом номере первый взрослый с ребёнком 7 лет, во втором второй взрослый с ребёнком 8 лет.'
    result, meta = complete_initial_room_layouts(state, operations, text)
    assert not meta['unknowns']
    trip, _ = apply_operations(state, result)
    assert all(stay.rooms[1].traveler_ids == ['new-traveler-2', 'new-traveler-4'] for stay in trip.stays)


def test_existing_or_already_proposed_layouts_and_constraints_are_not_rewritten():
    state, operations = captured()
    completed, _ = complete_initial_room_layouts(state, operations, SOURCE['text'])
    assert complete_initial_room_layouts(state, completed, SOURCE['text']) == (completed,
        {'notes': [], 'unknowns': [], 'pending_context': None})
    trip, _ = apply_operations(state, completed)
    before = deepcopy(trip)
    assert complete_initial_room_layouts(trip, [], SOURCE['text']) == ([],
        {'notes': [], 'unknowns': [], 'pending_context': None})
    assert trip == before
    raw, _ = apply_operations(state, operations)
    assert trip.constraints == raw.constraints and trip.groups == raw.groups and trip.travelers == raw.travelers


def add_third_stay(operations):
    proposal = deepcopy(operations)
    proposal.append(Operation(type='upsert_stay', target_id='new-stay-3', value={
        'city': 'Дубай', 'check_in': '2026-11-17', 'check_out': '2026-11-20'}))
    return proposal


def test_both_cities_cannot_apply_a_two_city_mapping_to_three_new_stays():
    state, operations = captured()
    operations = add_third_stay(operations)
    text = 'В обоих городах два номера. В первом номере первый взрослый с ребёнком, во втором второй взрослый.'
    result, meta = complete_initial_room_layouts(state, operations, text)
    assert result == operations
    assert meta['unknowns'] and 'двум' in meta['unknowns'][0]
    assert all('rooms' not in op.value for op in result if op.type == 'upsert_stay')


def test_each_city_can_apply_the_same_proved_mapping_to_three_new_stays():
    state, operations = captured()
    operations = add_third_stay(operations)
    text = 'В каждом городе два номера. В первом номере первый взрослый с ребёнком, во втором второй взрослый.'
    result, meta = complete_initial_room_layouts(state, operations, text)
    assert meta['unknowns'] == []
    trip, _ = apply_operations(state, result)
    assert len(trip.stays) == 3 and all(len(stay.rooms) == 2 for stay in trip.stays)


def test_without_universal_scope_the_helper_does_not_complete_or_hijack_normal_room_intake():
    state, operations = captured()
    text = 'Два номера. В первом номере первый взрослый с ребёнком, во втором второй взрослый.'
    assert complete_initial_room_layouts(state, operations, text) == (operations,
        {'notes': [], 'unknowns': [], 'pending_context': None})


def test_single_city_is_outside_the_multicity_helper_even_with_universal_wording():
    state, operations = captured()
    proposal = [op for op in operations if op.type != 'upsert_stay' or op.target_id == 'new-stay-1']
    assert complete_initial_room_layouts(state, proposal, SOURCE['text']) == (proposal,
        {'notes': [], 'unknowns': [], 'pending_context': None})
