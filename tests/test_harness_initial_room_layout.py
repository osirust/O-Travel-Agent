"""Cached initial extraction through PG, then a labelled typed WOW continuation."""
from copy import deepcopy
from uuid import uuid4

import pytest
from test_api import StubLLM, _headers, _session
from test_api import api_factory as initial_api_factory
from test_initial_room_layout import SOURCE
from test_slot_continuation import _message, _reply

api_factory = initial_api_factory


@pytest.mark.asyncio
async def test_exact_cached_initial_builds_two_city_room_plans_then_typed_wow_preserves_first_family_and_revokes_quote(api_factory):
    model = StubLLM([deepcopy(SOURCE['validated']['value'])])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, SOURCE['before_state'])
        base = f'/api/v1/sessions/{sid}'
        store = client._transport.app.state.store
        run, initial = await _message(client, sid, token, SOURCE['text'])
        assert run['status'] == 'completed' and not run.get('error')
        assert initial['status'] == 'OPTIONS_READY' and initial['pending_question'] is None
        trip = initial['trip']
        assert [stay['city'] for stay in trip['stays']] == ['Стамбул', 'Анталья']
        assert trip['budget']['amount_minor'] == 40_000_000
        assert len(trip['travelers']) == 3 and len(trip['groups']) == 2
        all_ids = sorted(person['id'] for person in trip['travelers'])
        for stay in trip['stays']:
            assert len(stay['rooms']) == 2
            assert sorted(person for room in stay['rooms'] for person in room['traveler_ids']) == all_ids
            assert stay['rooms'][0]['traveler_ids'] == ['new-traveler-1', 'new-traveler-3']
            assert stay['rooms'][1]['traveler_ids'] == ['new-traveler-2']
        plans = run['result']['result']['plans']
        assert len(plans) >= 2 and len(model.calls) == 1
        selected = next(offer for offer in plans[1]['offers'] if offer.get('kind') == 'hotel' and offer.get('city') == 'Анталья')
        preview = await client.post(base + '/checkout/preview', headers=_headers(token), json={
            'plan_id': plans[0]['plan_id'], 'state_version': initial['state_version']})
        assert preview.status_code == 200
        quote = preview.json()
        # This continuation is controlled typed extraction, not a claimed
        # second actual GLM output. All room addresses come from the new state.
        model.answers.append({'control': {'action': 'continue'}, 'reply': 'Изменения внесены.', 'unknowns': [], 'conflicts': [],
            'operations': [
                {'type': 'add_traveler', 'value': {'id': 'new-traveler-4', 'type': 'child', 'age': 8, 'group_id': trip['groups'][1]['id']}},
                {'type': 'upsert_group', 'target_id': trip['groups'][1]['id'], 'value': {
                    'traveler_ids': ['new-traveler-2', 'new-traveler-4'], 'keep_separate': True}},
                *[{'type': 'set_room_members', 'target_id': stay['rooms'][1]['id'], 'value': {
                    'stay_id': stay['id'], 'traveler_ids': ['new-traveler-2', 'new-traveler-4']}} for stay in trip['stays']],
                {'type': 'replace_hotel', 'target_id': trip['stays'][1]['id'], 'value': {'hotel_id': selected['hotel_id']}},
                {'type': 'set_budget', 'value': {'amount_minor': 38_000_000, 'currency': 'RUB'}},
                {'type': 'shift_return', 'value': {'days': 2}},
            ]})
        wow, after = await _message(client, sid, token, 'Добавь ребёнка восьми лет во вторую семью: в обоих городах во второй номер. '
            f'Замени отель в Анталье на «{selected["name"]}» из второго плана. Уменьши общий бюджет на 20000 рублей '
            'и перенеси возвращение на два дня позже. Первый отель и завтрак в Стамбуле сохрани.')
        assert wow['status'] == 'completed' and not wow.get('error')
        changed = after['trip']
        assert changed['budget']['amount_minor'] == 38_000_000
        assert sorted(p['age'] for p in changed['travelers'] if p['type'] == 'child') == [7, 8]
        assert changed['constraints'] == trip['constraints'] and changed['groups'][0] == trip['groups'][0]
        for old, stay in zip(trip['stays'], changed['stays'], strict=True):
            assert stay['rooms'][0] == old['rooms'][0]
            assert stay['rooms'][1]['id'] == old['rooms'][1]['id']
            assert stay['rooms'][1]['traveler_ids'] == ['new-traveler-2', 'new-traveler-4']
        assert changed['stays'][1]['hotel_id'] == selected['hotel_id']
        assert changed['stays'][1]['check_out'] == changed['legs'][-1]['departure_date'] == '2026-11-19'
        assert len(model.calls) == 2 and not model.answers
        invalid = await client.post(base + '/checkout/confirm', headers=_headers(token), json={
            'quote_id': quote['quote_id'], 'quote_version': quote['quote_version'], 'state_version': quote['state_version'],
            'confirmed': True, 'idempotency_key': str(uuid4())})
        assert invalid.status_code == 409 and await store.list_orders(sid) == []


@pytest.mark.asyncio
async def test_missing_second_ordinal_assignment_keeps_known_count_and_asks_membership_without_fabricating_rooms(api_factory):
    model = StubLLM([deepcopy(SOURCE['validated']['value'])])
    text = SOURCE['text'].replace(', во втором второй взрослый.', '.')
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, SOURCE['before_state'])
        run, after = await _message(client, sid, token, text)
        assert run['status'] == 'completed' and not run.get('error')
        assert after['waiting_reason'] == 'clarification'
        assert all(stay['rooms'] == [] for stay in after['trip']['stays'])
        assert after['pending_question']['context']['requested_room_count'] == 2
        reply = await _reply(client, sid, token, run)
        assert 'состав' in reply.casefold() and 'кажд' in reply.casefold()
        assert len(model.calls) == 1
