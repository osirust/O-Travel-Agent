"""A valid room layout can violate saved constraints without a JSON error."""
from dataclasses import replace
from uuid import uuid4

import pytest
from test_api import StubLLM, _headers, _session, _snapshot, _wait
from test_api import api_factory as semantic_api_factory

from otravel.domain import sample_trip
from otravel.harness import operation_conflicts

api_factory = semantic_api_factory


def room_patch(stay, room, members):
    return {'type': 'set_room_members', 'target_id': room,
            'value': {'stay_id': stay, 'traveler_ids': members}}


@pytest.mark.parametrize('operations', [
    [room_patch('s', 'r', ['a']), room_patch('s', 'r', ['b'])],
    [{'type': 'upsert_stay', 'target_id': 's', 'value': {'rooms': [{'id': 'r', 'traveler_ids': ['a']}]}},
     room_patch('s', 'r', ['b'])],
    [{'type': 'remove_stay', 'target_id': 's', 'value': {}}, room_patch('s', 'r', ['a'])],
    [room_patch('s', 'r2', []), {'type': 'upsert_stay', 'target_id': 's',
        'value': {'rooms': [{'id': 'r1', 'traveler_ids': ['a', 'b']}]}}],
    [{'type': 'upsert_stay', 'target_id': 's', 'value': {'rooms': [{'id': 'r1', 'traveler_ids': ['a', 'b']}]}},
     room_patch('s', 'r2', [])],
])
def test_contradictory_room_patches_never_use_last_write_wins(operations):
    assert operation_conflicts(sample_trip(), operations)


def test_same_members_and_local_room_ids_do_not_create_false_conflicts():
    assert not operation_conflicts(sample_trip(), [room_patch('s', 'r', ['a', 'b']), room_patch('s', 'r', ['b', 'a'])])
    assert not operation_conflicts(sample_trip(), [room_patch('s1', 'r', ['a']), room_patch('s2', 'r', ['b'])])


@pytest.mark.asyncio
async def test_unapplied_semantic_room_change_keeps_trip_and_reports_domain_stage(api_factory):
    initial = {
        'origin': 'Москва',
        'travelers': [{'id': 'a', 'type': 'adult'}, {'id': 'b', 'type': 'adult'}],
        'stays': [{'id': 's', 'city': 'Казань', 'check_in': '2026-11-10', 'check_out': '2026-11-13',
            'rooms': [{'id': 'r1', 'traveler_ids': ['a']}, {'id': 'r2', 'traveler_ids': ['b']}]}],
        'legs': [{'id': 'out', 'origin': 'Москва', 'destination': 'Казань', 'departure_date': '2026-11-10'},
            {'id': 'back', 'origin': 'Казань', 'destination': 'Москва', 'departure_date': '2026-11-13'}],
        'budget': {'amount_minor': 100000000, 'currency': 'RUB'},
        'constraints': [{'key': 'breakfast', 'value': True, 'hard': True, 'stay_id': 's', 'room_id': 'r1'}],
    }
    model = StubLLM([{'operations': [{'type': 'upsert_stay', 'target_id': 's',
        'value': {'rooms': [{'id': 'r2', 'traveler_ids': ['a', 'b']}]}}],
        'control': {'action': 'continue'}, 'speech_act': 'CHANGE'}])
    async with api_factory(model) as (client, _):
        app = client._transport.app
        app.state.harness.settings = replace(app.state.harness.settings, max_llm_calls_per_run=1)
        sid, token, _ = await _session(client, initial)
        before = await _snapshot(client, sid, token)
        response = await client.post(f'/api/v1/sessions/{sid}/messages', headers=_headers(token), json={
            'client_message_id': str(uuid4()), 'expected_version': before['state_version'],
            'text': 'Объедини два номера в один: обоих взрослых посели во втором номере, первый убери. Завтрак пока не снимай.'})
        assert response.status_code == 202
        run = await _wait(client, response.json()['run_id'], token)
        after = await _snapshot(client, sid, token)
        assert run['status'] == 'failed'
        assert run['error']['code'] == 'INVALID_LLM_OPERATION'
        assert run['error']['details'] == {'validation_stage': 'domain', 'domain_error_code': 'unknown_constraint_room'}
        assert 'схем' not in run['error']['message'].casefold()
        assert after['trip'] == before['trip'] and after['state_version'] == before['state_version']
        events = await app.state.store.list_events(sid)
        failed = [event['payload'] for event in events if event['event_type'] == 'MODEL_CALL_FAILED']
        assert len(model.calls) == len(failed) == 1
        assert failed[0]['validation_stage'] == 'domain'
        assert failed[0]['domain_error_code'] == 'unknown_constraint_room'
        assert await app.state.store.list_orders(sid) == []
        assert not any(event['event_type'] in {'TOOL_CALL_STARTED', 'QUOTE_CREATED', 'DEMO_ORDER_CREATED'} for event in events)
