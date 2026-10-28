"""Owner T10→T11: a new citizenship question must not inherit the room slot.

Pure controls identify the binding defect, not a missing country parser. The PG
primary case drives the real message chain; it never patches the answered state.
"""
from copy import deepcopy

import pytest
from sqlalchemy import func, select
from test_api import StubLLM, _session
from test_api import api_factory as rebinding_api_factory
from test_slot_continuation import _message

from otravel.clarifications import derive_pending_field, resolve_short_answer
from otravel.domain import TripState, apply_operations
from otravel.store import quotes

api_factory = rebinding_api_factory
NATIONALITY_QUESTION = 'Гражданство какой страны использовать для поиска отеля?'


def owner_like_state(city='Белгород', *, rooms=True):
    return TripState.model_validate({'trip_id': 'owner-nationality-control', 'state_version': 2,
        'origin': 'Москва', 'travelers': [{'id': 'new-traveler-1', 'type': 'adult'}],
        'stays': [{'id': 'new-stay-1', 'city': city, 'check_in': '2026-10-28',
            'check_out': '2026-11-04', 'requested_nights': 7,
            'rooms': [{'id': 'new-room-1', 'traveler_ids': []}] if rooms else []}],
        'search_policy': {'mode': 'HYBRID', 'hotel_provider': 'liteapi', 'flight_provider': 'duffel',
            'demo_fallback': True, 'guest_nationality': None}}).model_dump(mode='json')


def wrong_owner_pending():
    return {'text': NATIONALITY_QUESTION, 'questions': [NATIONALITY_QUESTION], 'field': 'stay.rooms',
        'target_id': 'new-stay-1', 'state_version': 2, 'reason': 'clarification',
        'context': {'requested_destination': {'kind': 'city', 'name': 'Белград', 'country_code': 'RS'}}}


@pytest.mark.parametrize('answer', ['Россия', 'россии', 'RU', 'Russian Federation'])
def test_existing_nationality_resolver_handles_exact_country_once_question_is_bound(answer):
    state, pending = owner_like_state(), wrong_owner_pending()
    original_state, original_pending = deepcopy(state), deepcopy(pending)
    assert resolve_short_answer(state, pending, answer) == {'handled': False, 'operations': []}
    binding = derive_pending_field(pending['questions'], state)
    assert binding == {'field': 'search.guest_nationality', 'target_id': None, 'context': {}}
    result = resolve_short_answer(state, {**pending, **binding}, answer)
    assert result['handled'] is True
    assert result['operations'] == [{'type': 'set_search_policy', 'value': {'guest_nationality': 'RU'}}]
    changed, _ = apply_operations(TripState.model_validate(state), result['operations'])
    data = changed.model_dump(mode='json')
    assert data['search_policy'] == {**state['search_policy'], 'guest_nationality': 'RU'}
    for key in state:
        if key not in {'search_policy', 'state_version'}:
            assert data[key] == state[key]
    assert state == original_state and pending == original_pending


@pytest.mark.asyncio
async def test_rooms_answer_then_source_nationality_question_rebinds_and_russia_continues_without_model(api_factory):
    # Available Istanbul isolates question rebinding from the separate actual
    # unsupported-Belgorod/country mismatch. No geography is silently repaired.
    model = StubLLM([{'operations': [], 'control': {'action': 'none'}, 'speech_act': 'CHANGE',
        'unknowns': ['Сколько гостиничных номеров вам нужно в городе «Стамбул»?']}])
    initial = owner_like_state('Стамбул', rooms=False)
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, initial)
        _, seed = await _message(client, sid, token, 'Начни подбор поездки, количество номеров ещё не сообщил.')
        assert seed['pending_question']['field'] == 'stay.rooms'
        assert seed['pending_question']['target_id'] == 'new-stay-1'
        seed_calls = len(model.calls)
        assert seed_calls == 1

        room_run, after_room = await _message(client, sid, token, '1')
        assert len(model.calls) == seed_calls, 'A bare answer to an owned room slot is not another model request'
        pending = after_room['pending_question']
        assert pending['text'] == NATIONALITY_QUESTION
        assert pending['questions'] == [NATIONALITY_QUESTION]
        assert pending['field'] == 'search.guest_nationality', (
            'A newly generated source question inherited the consumed stay.rooms binding: ' + str(pending))
        assert pending['target_id'] is None
        assert pending['state_version'] == after_room['state_version']
        assert after_room['trip']['search_policy']['guest_nationality'] is None
        assert len(after_room['trip']['stays'][0]['rooms']) == 1
        assert room_run['result']['result']['tool_calls_used'] == 0

        _, after_country = await _message(client, sid, token, 'россии')
        assert len(model.calls) == seed_calls, 'Citizenship is already a canonical slot answer, not a new NLU question'
        assert after_country['trip']['search_policy'] == {
            **after_room['trip']['search_policy'], 'guest_nationality': 'RU'}
        for key in ('origin', 'travelers', 'groups', 'stays', 'legs', 'budget', 'constraints', 'preferences', 'route_policy'):
            assert after_country['trip'][key] == after_room['trip'][key]
        store = client._transport.app.state.store
        assert await store.list_orders(sid) == []
        async with store.engine.connect() as connection:
            assert await connection.scalar(select(func.count()).select_from(quotes).where(quotes.c.session_id == sid)) == 0
