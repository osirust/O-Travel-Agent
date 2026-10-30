"""Room identity repair keeps explicit memberships and complete bijections."""
from copy import deepcopy

import pytest

from otravel.domain import Operation, TripState
from otravel.room_identity import normalize_room_identities


def state():
    return TripState(travelers=[{'id': 'a', 'type': 'adult'}, {'id': 'b', 'type': 'adult'},
        {'id': 'c', 'type': 'child', 'age': 7}], stays=[{'id': 's', 'city': 'Стамбул',
        'rooms': [{'id': 'r1', 'traveler_ids': ['a', 'c']}, {'id': 'r2', 'traveler_ids': ['b']}]}],
        constraints=[{'key': 'breakfast', 'value': True, 'hard': True, 'stay_id': 's', 'room_id': 'r1'}])


def setter(rooms):
    return {'type': 'upsert_stay', 'target_id': 's', 'value': {'rooms': rooms}}


@pytest.mark.parametrize('typed', [False, True])
def test_complete_unknown_alias_repair_preserves_existing_ids_guests_order_constraints_and_input(typed):
    before = state()
    operations = [setter([{'id': 'r2', 'traveler_ids': ['b', 'new-child']},
                          {'id': 'fresh-model-alias', 'traveler_ids': ['a', 'c']}])]
    if typed:
        operations = [Operation.model_validate(op) for op in operations]
    original = deepcopy(operations)
    expected_state = before.model_dump(mode='json')
    result, notes = normalize_room_identities(before, operations)
    value = result[0].value if typed else result[0]['value']
    assert value['rooms'] == [{'id': 'r1', 'traveler_ids': ['a', 'c']},
                              {'id': 'r2', 'traveler_ids': ['b', 'new-child']}]
    assert notes[0]['aliases'] == [{'proposed_id': 'fresh-model-alias', 'canonical_id': 'r1',
                                   'existing_traveler_ids': ['a', 'c']}]
    assert notes[0]['guest_membership_inferred'] is False
    assert operations == original and before.model_dump(mode='json') == expected_state
    assert normalize_room_identities(before, result) == (result, [])


@pytest.mark.parametrize('rooms', [
    [{'id': 'x', 'traveler_ids': ['a', 'c']}, {'id': 'r2', 'traveler_ids': ['b']}, {'id': 'new', 'traveler_ids': []}],
    [{'id': 'x', 'traveler_ids': ['a']}, {'id': 'r2', 'traveler_ids': ['b']}],
    [{'id': 'x', 'traveler_ids': ['new-person']}, {'id': 'y', 'traveler_ids': ['a', 'b', 'c']}],
    [{'id': 'x', 'traveler_ids': ['a', 'b']}, {'id': 'y', 'traveler_ids': ['c']}],
    [{'id': 'x', 'traveler_ids': ['a', 'c']}, {'id': 'x', 'traveler_ids': ['b']}],
    [{'id': 'x', 'traveler_ids': ['a', 'c', 'b']}, {'id': 'y', 'traveler_ids': ['b']}],
    [{'id': 'r1', 'traveler_ids': ['b']}, {'id': 'x', 'traveler_ids': ['a', 'c']}],
])
def test_new_count_membership_drop_new_people_only_nonmatching_anchors_duplicates_and_valid_id_conflict_are_not_repaired(rooms):
    operations = [setter(rooms)]
    assert normalize_room_identities(state(), operations) == (operations, [])


def test_existing_valid_id_swap_is_never_replaced_by_prior_membership():
    operations = [setter([{'id': 'r1', 'traveler_ids': ['b']}, {'id': 'r2', 'traveler_ids': ['a', 'c']}])]
    assert normalize_room_identities(state(), operations) == (operations, [])


@pytest.mark.parametrize('swap_guests', [False, True])
def test_fully_valid_ids_reverse_array_or_explicit_guest_swap_is_not_an_alias_repair(swap_guests):
    rooms = ([{'id': 'r2', 'traveler_ids': ['a', 'c']}, {'id': 'r1', 'traveler_ids': ['b']}]
        if swap_guests else [{'id': 'r2', 'traveler_ids': ['b']}, {'id': 'r1', 'traveler_ids': ['a', 'c']}])
    operations = [setter(rooms)]
    assert normalize_room_identities(state(), operations) == (operations, [])


def test_ambiguous_old_partition_is_not_proof_of_a_unique_room_identity():
    before = state()
    before.stays[0].rooms[1].traveler_ids = ['a', 'c']
    operations = [setter([{'id': 'x', 'traveler_ids': ['a', 'c']}, {'id': 'y', 'traveler_ids': ['a', 'c']}])]
    assert normalize_room_identities(before, operations) == (operations, [])


def test_no_old_guest_anchors_or_partial_bijection_leaves_entire_setter_unchanged():
    before = TripState(travelers=[{'id': 'a', 'type': 'adult'}], stays=[{'id': 's', 'city': 'Казань', 'rooms': [
        {'id': 'r1', 'traveler_ids': ['a']}, {'id': 'r2', 'traveler_ids': []}, {'id': 'r3', 'traveler_ids': []}]}])
    operations = [setter([{'id': 'x', 'traveler_ids': ['a']}, {'id': 'y', 'traveler_ids': ['new1']},
                          {'id': 'z', 'traveler_ids': ['new2']}])]
    assert normalize_room_identities(before, operations) == (operations, [])


def test_multiple_setters_and_changed_city_are_left_to_the_conflict_or_domain_gate():
    operation = setter([{'id': 'x', 'traveler_ids': ['a', 'c']}, {'id': 'r2', 'traveler_ids': ['b']}])
    assert normalize_room_identities(state(), [operation, deepcopy(operation)]) == ([operation, operation], [])
    operation['value']['city'] = 'Анталья'
    assert normalize_room_identities(state(), [operation]) == ([operation], [])
