"""Room count and clarification continuations; controlled model, real PG."""
import pytest
from test_api import StubLLM, _session
from test_api import api_factory as room_api_factory
from test_harness_conversational import known_trip
from test_room_intent import CF08
from test_slot_continuation import _message

api_factory = room_api_factory


@pytest.mark.asyncio
async def test_cf08_omitted_rooms_are_saved_without_guessing_family_membership(api_factory):
    raw = {'operations': [
        {'type': 'set_origin', 'value': {'city': 'Москва'}},
        {'type': 'set_budget', 'value': {'amount_minor': 28_000_000}},
        *[{'type': 'add_traveler', 'value': {'id': f'new-traveler-{i}', 'type': 'adult'}} for i in range(1, 5)],
        {'type': 'add_traveler', 'value': {'id': 'new-traveler-5', 'type': 'child', 'age': 6}},
        {'type': 'add_traveler', 'value': {'id': 'new-traveler-6', 'type': 'child', 'age': 10}}],
        'speech_act': 'CHANGE', 'control': {'action': 'continue'}}
    model = StubLLM([raw])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client)
        _, after = await _message(client, sid, token, CF08)
        state = after['trip']
        assert state['origin'] == 'Москва' and state['stays'][0]['city'] == 'Казань'
        assert len(state['stays'][0]['rooms']) == 2
        assert all(not room['traveler_ids'] for room in state['stays'][0]['rooms'])
        assert len(state['groups']) == 2 and all(not group['traveler_ids'] and group['keep_separate'] for group in state['groups'])
        assert len(state['travelers']) == 6 and all(person['group_id'] is None for person in state['travelers'])
        assert after['pending_question']['field'] == 'group.membership'
        assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_bare_count_cannot_erase_existing_allocation_after_composition_question(api_factory):
    model = StubLLM([{'operations': [], 'speech_act': 'CHANGE'}])
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client, known_trip())
        _, proposed = await _message(client, sid, token, 'Нужны два номера.')
        assert proposed['trip'] == before['trip']
        assert proposed['pending_question']['context']['room_reallocation_required'] is True
        _, after = await _message(client, sid, token, '2')
        assert after['trip'] == before['trip'] and len(model.calls) == 1
        assert after['pending_question']['field'] == 'stay.rooms'
        assert after['pending_question']['target_id'] == 'kazan-stay'
        assert 'кто должен жить' in after['pending_question']['text']
