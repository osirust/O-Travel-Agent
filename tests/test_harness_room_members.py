"""Typed and cached sparse membership edits through API/isolated PostgreSQL."""
from copy import deepcopy
from uuid import uuid4

import pytest
from test_api import StubLLM, _headers, _session, _wait
from test_api import api_factory as room_api_factory
from test_room_members import SOURCE
from test_slot_continuation import _message, _reply

api_factory = room_api_factory


def typed_answer(row):
    return {'control': {'action': 'continue'}, 'reply': 'Состав и бюджет изменены.', 'unknowns': [], 'conflicts': [],
        'speech_act': 'CHANGE', 'operations': [
            {'type': 'add_traveler', 'value': {'id': 'new-traveler-4', 'type': 'child', 'age': 8, 'group_id': 'new-group-2'}},
            {'type': 'upsert_group', 'target_id': 'new-group-2', 'value': {
                'traveler_ids': ['new-traveler-2', 'new-traveler-4'], 'keep_separate': True}},
            {'type': 'set_room_members', 'target_id': 'new-room-2', 'value': {
                'stay_id': 'new-stay-1', 'traveler_ids': ['new-traveler-2', 'new-traveler-4']}},
            {'type': 'set_room_members', 'target_id': 'new-room-4', 'value': {
                'stay_id': 'new-stay-2', 'traveler_ids': ['new-traveler-2', 'new-traveler-4']}},
            {'type': 'replace_hotel', 'target_id': 'new-stay-2', 'value': {'hotel_id': 'demo-hotel-antalya-garden'}},
            {'type': 'set_budget', 'value': {'amount_minor': 38_000_000, 'currency': 'RUB', 'scope': 'whole_trip'}},
            {'type': 'shift_return', 'value': {'days': 2}},
        ]}


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['cached_sparse', 'typed_primitive'])
async def test_compound_new_child_room_patches_preserve_untouched_rooms_breakfast_and_revoke_old_quote(api_factory, mode):
    row = SOURCE['steps'][0]
    answer = row['validated'][0]['value'] if mode == 'cached_sparse' else typed_answer(row)
    model = StubLLM([answer])
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, row['before_state'])
        store = client._transport.app.state.store
        base = f'/api/v1/sessions/{sid}'
        accepted = await client.post(base + '/search', headers=_headers(token), json={})
        assert accepted.status_code == 202
        searched = await _wait(client, accepted.json()['run_id'], token)
        assert searched['status'] == 'completed'
        plans = searched['result']['result']['plans']
        assert len(plans) >= 2
        preview = await client.post(base + '/checkout/preview', headers=_headers(token), json={
            'plan_id': plans[0]['plan_id'], 'state_version': created['state_version']})
        assert preview.status_code == 200
        quote = preview.json()
        selection = await store.current_selected_plan(sid, created['state_version'])
        assert selection and selection['trip_id'] == created['trip_id']
        preserved_hotel = next(hotel['hotel_id'] for hotel in selection['hotels']
            if hotel['stay_id'] == created['trip']['stays'][0]['id'])
        before = deepcopy(created['trip'])
        run, after = await _message(client, sid, token, row['text'])
        assert run['status'] == 'completed' and not run.get('error')
        trip = after['trip']
        assert trip['state_version'] == before['state_version'] + 1
        assert trip['budget']['amount_minor'] == 38_000_000 and trip['origin'] == before['origin']
        assert trip['travelers'][:3] == before['travelers']
        assert sorted(p['age'] for p in trip['travelers'] if p['type'] == 'child') == [7, 8]
        assert trip['groups'][0] == before['groups'][0]
        assert trip['groups'][1]['keep_separate'] is True
        assert trip['groups'][1]['traveler_ids'] == ['new-traveler-2', 'new-traveler-4']
        assert trip['constraints'] == before['constraints']
        for prior, stay in zip(before['stays'], trip['stays'], strict=True):
            assert len(stay['rooms']) == 2
            assert [room['id'] for room in stay['rooms']] == [room['id'] for room in prior['rooms']]
            assert stay['rooms'][0] == prior['rooms'][0]
            assert stay['rooms'][1]['traveler_ids'] == ['new-traveler-2', 'new-traveler-4']
        assert trip['stays'][0]['hotel_id'] == preserved_hotel
        assert trip['stays'][0]['check_out'] == '2026-11-13'
        assert trip['stays'][1]['hotel_id'] == 'demo-hotel-antalya-garden'
        assert trip['stays'][1]['check_out'] == trip['legs'][-1]['departure_date'] == '2026-11-19'
        assert len(model.calls) == 1 and not model.answers
        reply = await _reply(client, sid, token, run)
        assert '380' in reply and 'бюджет' in reply.casefold()
        invalid = await client.post(base + '/checkout/confirm', headers=_headers(token), json={
            'quote_id': quote['quote_id'], 'quote_version': quote['quote_version'], 'state_version': quote['state_version'],
            'confirmed': True, 'idempotency_key': str(uuid4())})
        assert invalid.status_code == 409 and await store.list_orders(sid) == []
        later, snapshot = await _message(client, sid, token, 'Напомни общий бюджет поездки.')
        assert snapshot['trip'] == trip and len(model.calls) == 1
        assert '380' in await _reply(client, sid, token, later)
        notes = run['result']['result']['operation_normalizations']
        assert sum(note['code'] == 'sparse_new_traveler_room_patch_converted' for note in notes) == (2 if mode == 'cached_sparse' else 0)
