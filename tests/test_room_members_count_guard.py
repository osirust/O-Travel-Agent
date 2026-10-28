"""Count-only intake cannot bypass allocation preservation via room patches."""
from copy import deepcopy

import pytest
from test_api import StubLLM, _headers, _session, _snapshot, _wait
from test_api import api_factory as _shared_api_factory

from otravel.domain import DomainError, TripState, apply_operations
from otravel.room_intent import ground_room_count_operations

api_factory = _shared_api_factory


def state(two_stays=False):
    return TripState(origin='Москва', travelers=[
        {'id': 'a', 'type': 'adult', 'group_id': 'g1'}, {'id': 'b', 'type': 'adult', 'group_id': 'g2'}],
        groups=[{'id': 'g1', 'traveler_ids': ['a'], 'keep_separate': True},
            {'id': 'g2', 'traveler_ids': ['b'], 'keep_separate': True}],
        legs=[{'id': 'out', 'origin': 'Москва', 'destination': 'Стамбул', 'departure_date': '2026-11-10'}]
        + ([{'id': 'between', 'origin': 'Стамбул', 'destination': 'Анталья', 'departure_date': '2026-11-13'}]
            if two_stays else [])
        + [{'id': 'back', 'origin': 'Анталья' if two_stays else 'Стамбул', 'destination': 'Москва',
            'departure_date': '2026-11-17' if two_stays else '2026-11-13'}],
        stays=[{'id': 's1', 'city': 'Стамбул', 'check_in': '2026-11-10', 'check_out': '2026-11-13',
            'rooms': [{'id': 'r1', 'traveler_ids': ['a']}, {'id': 'r2', 'traveler_ids': ['b']}]}]
        + ([{'id': 's2', 'city': 'Анталья', 'check_in': '2026-11-13', 'check_out': '2026-11-17',
            'rooms': [{'id': 'r1', 'traveler_ids': ['a']}, {'id': 'r2', 'traveler_ids': ['b']}]}] if two_stays else []))


def swapped(stay_id='s1'):
    return [{'type': 'set_room_members', 'target_id': 'r1', 'value': {'stay_id': stay_id, 'traveler_ids': ['b']}},
        {'type': 'set_room_members', 'target_id': 'r2', 'value': {'stay_id': stay_id, 'traveler_ids': ['a']}}]


def test_count_only_request_preserves_existing_memberships_for_new_patch_primitive():
    original = state()
    raw = swapped()
    checked, meta = ground_room_count_operations(original, raw, 'В Стамбуле нужны два номера.')
    changed, changes = apply_operations(original, checked)
    assert changed == original and changes == []
    assert raw == swapped() and any(n['code'] == 'existing_room_allocation_preserved' for n in meta['notes'])


@pytest.mark.parametrize('count,text', [(1, 'В Стамбуле нужен один номер.'), (3, 'Нужны три номера.')])
def test_new_count_without_allocations_keeps_all_saved_members_and_asks(count, text):
    original = state(two_stays=count == 3)
    raw = swapped() + (swapped('s2') if count == 3 else [])
    checked, meta = ground_room_count_operations(original, raw, text)
    changed, _ = apply_operations(original, checked)
    assert changed == original
    assert meta['unknowns'] and meta['pending_context']['requested_room_count'] == count


def test_count_only_full_setter_and_patch_paths_have_the_same_preserving_semantics():
    original = state()
    full = [{'type': 'upsert_stay', 'target_id': 's1', 'value': {'rooms': [
        {'id': 'r1', 'traveler_ids': ['b']}, {'id': 'r2', 'traveler_ids': ['a']}]}}]
    for raw in [full, swapped()]:
        checked, _ = ground_room_count_operations(original, raw, 'В Стамбуле нужны два номера.')
        changed, changes = apply_operations(original, checked)
        assert changed == original and changes == []


def test_count_only_input_cannot_reorder_existing_rooms_or_guest_ids():
    original = state()
    raw_state = original.model_dump(mode='json')
    raw_state['travelers'].append({'id': 'a2', 'type': 'adult', 'group_id': 'g1'})
    raw_state['groups'][0]['traveler_ids'].append('a2')
    raw_state['stays'][0]['rooms'][0]['traveler_ids'].append('a2')
    original = TripState.model_validate(raw_state)
    rooms = [room.model_dump(mode='json') for room in original.stays[0].rooms]
    for raw in [[{'type': 'upsert_stay', 'target_id': 's1', 'value': {'rooms': list(reversed(rooms))}}],
            [{'type': 'set_room_members', 'target_id': 'r1', 'value': {'stay_id': 's1', 'traveler_ids': ['a2', 'a']}}]]:
        checked, _ = ground_room_count_operations(original, raw, 'В Стамбуле нужны два номера.')
        changed, changes = apply_operations(original, checked)
        assert changed == original and changes == []


def test_explicit_membership_redistribution_remains_allowed_with_a_room_count():
    original = state()
    raw = swapped()
    checked, _ = ground_room_count_operations(original, raw,
        'Нужны два номера. Пересели первого взрослого во второй номер, второго в первый.')
    assert checked == raw
    changed, _ = apply_operations(original, checked)
    assert [room.traveler_ids for room in changed.stays[0].rooms] == [['b'], ['a']]


@pytest.mark.parametrize('shape', ['patch', 'full'])
def test_explicit_new_child_addition_retains_only_old_members_plus_proposed_new_person(shape):
    original = state()
    addition = {'type': 'add_traveler', 'value': {'id': 'c', 'type': 'child', 'age': 8, 'group_id': 'g2'}}
    room = {'type': 'set_room_members', 'target_id': 'r2', 'value': {'stay_id': 's1', 'traveler_ids': ['b', 'c']}}
    if shape == 'full':
        room = {'type': 'upsert_stay', 'target_id': 's1', 'value': {'rooms': [
            {'id': 'r1', 'traveler_ids': ['a']}, {'id': 'r2', 'traveler_ids': ['b', 'c']}]}}
    raw = [addition, room]
    checked, _ = ground_room_count_operations(original, raw,
        'Нужны два номера. Добавь ребёнка восьми лет в семью второго взрослого.')
    assert checked == raw
    changed, _ = apply_operations(original, checked)
    assert [room.traveler_ids for room in changed.stays[0].rooms] == [['a'], ['b', 'c']]


@pytest.mark.parametrize('text', ['Нужны два номера. Если добавить ребёнка восьми лет?',
    'Нужны два номера. «Добавь ребёнка восьми лет».',
    'Нужны два номера. Ранее просил добавить ребёнка восьми лет.',
    'Нужны два номера. Пока не добавляй ребёнка восьми лет.'])
def test_unasserted_child_addition_does_not_authorize_membership_changes(text):
    original = state()
    raw = [{'type': 'add_traveler', 'value': {'id': 'c', 'type': 'child', 'age': 8, 'group_id': 'g2'}},
        {'type': 'set_room_members', 'target_id': 'r2', 'value': {'stay_id': 's1', 'traveler_ids': ['b', 'c']}}]
    checked, _ = ground_room_count_operations(original, raw, text)
    changed, _ = apply_operations(original, checked)
    assert [room.traveler_ids for room in changed.stays[0].rooms] == [['a'], ['b']]


def test_new_child_intent_never_authorizes_moving_or_dropping_existing_members():
    original = state()
    raw = [{'type': 'add_traveler', 'value': {'id': 'c', 'type': 'child', 'age': 8, 'group_id': 'g2'}},
        {'type': 'set_room_members', 'target_id': 'r1', 'value': {'stay_id': 's1', 'traveler_ids': ['b', 'c']}},
        {'type': 'set_room_members', 'target_id': 'r2', 'value': {'stay_id': 's1', 'traveler_ids': []}}]
    checked, _ = ground_room_count_operations(original, raw, 'Нужны два номера. Добавь ребёнка восьми лет.')
    changed, _ = apply_operations(original, checked)
    assert [room.traveler_ids for room in changed.stays[0].rooms] == [['a'], ['b']]


def test_foreign_room_is_left_for_atomic_domain_rejection_and_other_edits_remain():
    original = state()
    raw = [{'type': 'set_room_members', 'target_id': 'foreign', 'value': {'stay_id': 's1', 'traveler_ids': ['a']}},
        {'type': 'set_budget', 'value': {'amount_minor': 7500000}}]
    checked, _ = ground_room_count_operations(original, raw, 'В Стамбуле нужны два номера. Бюджет75000рублей.')
    assert checked == raw
    with pytest.raises(DomainError, match='номер'):
        apply_operations(original, checked)


@pytest.mark.asyncio
async def test_model_count_only_room_patches_cannot_mutate_saved_allocation_in_real_pg(api_factory):
    original = state().model_dump(mode='json')
    raw = swapped()
    llm = StubLLM([{'operations': deepcopy(raw), 'speech_act': 'CHANGE',
        'control': {'action': 'none'}, 'reply': 'Номеров два.'}])
    async with api_factory(llm) as (client, _):
        sid, token, initial = await _session(client, original)
        accepted = await client.post('/api/v1/sessions/' + sid + '/messages', headers=_headers(token),
            json={'client_message_id': 'count-only-' + sid, 'text': 'В Стамбуле нужны два номера.'})
        run = await _wait(client, accepted.json()['run_id'], token)
        snapshot = await _snapshot(client, sid, token)
        assert run['status'] == 'completed'
        assert snapshot['trip'] == initial['trip'] and snapshot['state_version'] == initial['state_version']
        assert len(llm.calls) == 1
        events = (await client.get('/api/v1/sessions/' + sid + '/events', headers=_headers(token))).json()['events']
        assert not any(event['event_type'] == 'TOOL_CALL_STARTED' for event in events)
        assert (await client.get('/api/v1/sessions/' + sid + '/orders', headers=_headers(token))).json()['orders'] == []
