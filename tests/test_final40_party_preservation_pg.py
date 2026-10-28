"""Private RED before repairs: captured actual contracts, no live NLU/HTTP.

The actual current before-trip/pending packets are explicit fixture setup.
Only a fresh isolated PostgreSQL container is used; no app/runtime DB access.
"""
import asyncio
import json
import re
import time
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from test_api import StubLLM, _headers, _session, _snapshot
from test_api import api_factory as private_api_factory

from otravel.domain import TripState, apply_operations
from otravel.room_intent import resolve_room_count_answer
from otravel.store import quotes, sessions

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / ".local/evaluation/final40-party-controlled-20261028"
DATA = {r["id"]: r for r in json.loads((ROOT / "fixtures/evaluation/final40-party-20261028.json").read_text(encoding="utf-8"))["rows"]}
api_factory = private_api_factory


async def seed(client, row):
    sid, token, created = await _session(client, deepcopy(row['before_trip']))
    p = deepcopy(row.get('before_pending'))
    if p:
        p['state_version'] = created['state_version']
        p['question_id'] = 'owned-party-fixture-' + uuid4().hex
        for source in (p, p.get('context') or {}):
            for key in ('state_version', 'based_state_version'):
                if key in source:
                    source[key] = created['state_version']
            if 'trip_id' in source:
                source['trip_id'] = created['trip_id']
        repository = client._transport.app.state.store
        async with repository.engine.begin() as conn:
            await conn.execute(update(sessions).where(sessions.c.session_id == sid).values(
                status='WAITING_USER', waiting_reason='clarification', pending_question=p))
    return sid, token, await _snapshot(client, sid, token)


async def send(client, sid, token, text, *, allow_safe_rejection=False):
    before = await _snapshot(client, sid, token)
    response = await client.post(f'/api/v1/sessions/{sid}/messages', headers=_headers(token), json={
        'client_message_id': str(uuid4()), 'expected_version': before['state_version'], 'text': text})
    assert response.status_code == 202
    runid = response.json()['run_id']
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = await client.get(f'/api/v1/runs/{runid}', headers=_headers(token))
        assert result.status_code == 200
        run = result.json()
        if run['status'] not in {'pending', 'running'}:
            if allow_safe_rejection:
                assert run['status'] == 'completed' or run['status'] == 'failed' and (run.get('error') or {}).get('code') in {'INVALID_LLM_OPERATION', 'INVALID_LLM_RESPONSE'}
            else:
                assert run['status'] == 'completed', (run['status'], (run.get('error') or {}).get('code'))
            return run, await _snapshot(client, sid, token)
        await asyncio.sleep(0.02)
    pytest.fail('Private controlled run did not finish; request was not resent')


async def evidence(client, sid, row, run, after, model):
    repository = client._transport.app.state.store
    events = await repository.list_events(sid)
    checked = [e['payload'] for e in events if e['event_type'] == 'MODEL_OPERATIONS_CHECKED']
    assert (await repository.get_session(sid))['state'] == after['trip']
    assert await repository.list_orders(sid) == []
    async with repository.engine.connect() as conn:
        assert await conn.scalar(select(func.count()).select_from(quotes).where(quotes.c.session_id == sid)) == 0
    record = {'captured_id': row['id'], 'user': row['user'], 'source_file_sha256': row['source_file_sha256'],
        'after_trip': after['trip'], 'pending': after.get('pending_question'), 'reply': run.get('result', {}).get('reply'),
        'checked_contracts': checked, 'controlled_model_calls': len(model.calls), 'vendor_http': 0, 'orders': 0, 'quotes': 0}
    BASE.mkdir(parents=True, exist_ok=True)
    (BASE / ('observation-' + row['id'].replace(':', '-') + '.json')).write_text(json.dumps(record, ensure_ascii=False, indent=2))


@pytest.mark.asyncio
async def test_captured_024_only_child_not_going_is_removed_not_age_cleared(api_factory):
    row = DATA['FINAL40-024:T4']
    model = StubLLM([deepcopy(row['raw_contract'])])
    async with api_factory(model) as (client, _):
        sid, token, before = await seed(client, row)
        run, after = await send(client, sid, token, row['user'])
        await evidence(client, sid, row, run, after, model)
        expected = [p for p in before['trip']['travelers'] if p['type'] == 'adult']
        assert after['trip']['travelers'] == expected, 'Sole child non-participation must not become an unknown child age'
        for key in ('origin', 'stays', 'budget', 'constraints', 'preferences', 'route_policy', 'search_policy'):
            assert after['trip'][key] == before['trip'][key], key
        assert (after.get('pending_question') or {}).get('field') != 'traveler.age'
        assert (after.get('pending_question') or {}).get('context', {}).get('children_known') is True
        assert (after.get('pending_question') or {}).get('context', {}).get('children_count') == 0


@pytest.mark.asyncio
async def test_captured_029_city_only_answer_cannot_add_an_unrequested_adult(api_factory):
    row = DATA['FINAL40-029:T4']
    model = StubLLM([deepcopy(row['raw_contract'])])
    async with api_factory(model) as (client, _):
        sid, token, before = await seed(client, row)
        run, after = await send(client, sid, token, row['user'])
        await evidence(client, sid, row, run, after, model)
        assert before['trip']['travelers'] == []
        assert after['trip']['travelers'] == [], 'A current city/country choice is not authority to invent one adult'
        assert after['trip']['origin'] == before['trip']['origin']
        for key in ('groups', 'budget', 'constraints', 'preferences'):
            assert after['trip'][key] == before['trip'][key], key


@pytest.mark.asyncio
async def test_captured_026_including_child_census_requires_consistency_or_targeted_question(api_factory):
    row = DATA['FINAL40-026:T3']
    model = StubLLM([deepcopy(row['raw_contract'])])
    async with api_factory(model) as (client, _):
        sid, token, before = await seed(client, row)
        run, after = await send(client, sid, token, row['user'])
        await evidence(client, sid, row, run, after, model)
        people = after['trip']['travelers']
        consistent = len(people) == 4 and any(p['type'] == 'child' for p in people)
        if not consistent:
            assert people == before['trip']['travelers'], 'Do not guess which existing adult identity is a child'
            pending = after.get('pending_question') or {}
            assert re.search(r'реб[её]н|дет', pending.get('text', ''), re.I), 'The next question must address the explicitly included child'
            assert (pending.get('field') or '').startswith(('party.', 'traveler.', 'group.'))
        for key in ('origin', 'stays', 'groups', 'budget', 'constraints', 'preferences'):
            assert after['trip'][key] == before['trip'][key], key


def test_captured_001_short_one_room_allocates_the_only_known_adult_without_guessing_ids():
    row = DATA['FINAL40-001:T12']
    before = deepcopy(row['before_trip'])
    pending = deepcopy(row['before_pending'])
    result = resolve_room_count_answer(before, pending, row['user'])
    assert result['handled']
    after, _ = apply_operations(TripState.model_validate(before), result['operations'])
    assert len(after.travelers) == 1 and after.travelers[0].type == 'adult'
    assert len(after.stays) == len(after.stays[0].rooms) == 1
    assert after.stays[0].rooms[0].traveler_ids == [after.travelers[0].id], 'A sole known adult and explicit sole room have a unique allocation'
    assert before == row['before_trip']


@pytest.mark.asyncio
@pytest.mark.parametrize('text', [
    '«Ребёнок не едет» — пример фразы.',
    'Если ребёнок не едет, можно ли выбрать другой номер?',
    'Ребёнок не едет?',
    'Раньше ребёнок не ездил, сейчас никого не удаляй.',
    'Взрослый не едет.',
    'Ребёнок не едет в этот отель.',
    'Ребёнок не едет поездом.',
    'Ребёнка не удаляй.',
])
async def test_noncurrent_question_preserve_adult_or_local_scope_cannot_remove_child(api_factory, text):
    row = DATA['FINAL40-024:T4']
    model = StubLLM([{'operations': [], 'speech_act': 'CHANGE', 'control': {'action': 'continue'}, 'reply': 'Controlled no-op.'}])
    async with api_factory(model) as (client, _):
        sid, token, before = await seed(client, row)
        # A deliberately contradictory controlled CHANGE response to a user
        # question may be rejected. This guard checks no removal authority,
        # not a fresh natural-language or completed-answer success claim.
        _, after = await send(client, sid, token, text, allow_safe_rejection=True)
        assert after['trip']['travelers'] == before['trip']['travelers']
        assert await client._transport.app.state.store.list_orders(sid) == []


@pytest.mark.asyncio
async def test_an_already_paused_session_queues_child_statement_without_model_or_party_edit(api_factory):
    row = DATA['FINAL40-024:T4'];model = StubLLM([])
    async with api_factory(model) as (client, _):
        sid, token, before = await seed(client, row)
        paused = await client.post(f'/api/v1/sessions/{sid}/control', headers=_headers(token), json={'action': 'pause'})
        assert paused.status_code == 200
        snapshot = await _snapshot(client, sid, token)
        response = await client.post(f'/api/v1/sessions/{sid}/messages', headers=_headers(token), json={
            'client_message_id': str(uuid4()), 'expected_version': snapshot['state_version'], 'text': row['user']})
        assert response.status_code == 202
        after = await _snapshot(client, sid, token)
        assert after['waiting_reason'] == 'user_pause'
        assert after['trip']['travelers'] == before['trip']['travelers']
        assert model.calls == []


def two_children():
    row = deepcopy(DATA['FINAL40-024:T4'])
    row['before_trip']['travelers'].append({'id': 'second-child', 'type': 'child', 'age': 9, 'birth_date': None, 'group_id': None})
    return row


@pytest.mark.asyncio
async def test_ambiguous_singular_nonparticipation_cannot_pick_one_of_two_children(api_factory):
    model = StubLLM([{'operations': [], 'speech_act': 'CHANGE', 'control': {'action': 'continue'}, 'reply': 'Controlled no-op.'}])
    async with api_factory(model) as (client, _):
        sid, token, before = await seed(client, two_children())
        _, after = await send(client, sid, token, 'Ребёнок не едет.')
        assert after['trip']['travelers'] == before['trip']['travelers']


@pytest.mark.asyncio
async def test_explicit_current_known_child_id_is_still_a_valid_removal(api_factory):
    model = StubLLM([{'operations': [{'type': 'remove_traveler', 'target_id': 'second-child', 'value': {}}], 'speech_act': 'CHANGE', 'control': {'action': 'continue'}, 'reply': 'Controlled explicit removal.'}])
    async with api_factory(model) as (client, _):
        sid, token, before = await seed(client, two_children())
        _, after = await send(client, sid, token, 'Исключи участника second-child из всей поездки.')
        assert after['trip']['travelers'] == [p for p in before['trip']['travelers'] if p['id'] != 'second-child']


@pytest.mark.asyncio
async def test_explicit_all_children_literal_still_permits_the_recorded_removal_batch(api_factory):
    row = two_children()
    children = [p['id'] for p in row['before_trip']['travelers'] if p['type'] == 'child']
    model = StubLLM([{'operations': [{'type': 'remove_traveler', 'target_id': child, 'value': {}} for child in children], 'speech_act': 'CHANGE', 'control': {'action': 'continue'}, 'reply': 'Controlled plural removal.'}])
    async with api_factory(model) as (client, _):
        sid, token, before = await seed(client, row)
        _, after = await send(client, sid, token, 'Дети не едут.')
        assert after['trip']['travelers'] == [p for p in before['trip']['travelers'] if p['type'] == 'adult']


@pytest.mark.asyncio
@pytest.mark.parametrize('text', ['Едут 1 взрослых, детей нет.', 'Едут 1 взрослых. Детей нет.',
    'Едут 1 взрослых. Детей нет. Нужно 1 номера.'])
async def test_closed_compound_fresh_party_slot_skips_even_a_poisoned_model_answer(api_factory, text):
    row = deepcopy(DATA['FINAL40-029:T4'])
    row['before_pending'] = {'field': 'party.adults', 'target_id': None,
        'state_version': row['before_trip']['state_version'], 'text': 'Сколько взрослых едет?',
        'questions': ['Сколько взрослых едет?'], 'context': {}}
    # This invalid stale preference remains invalid; the closed owned answer
    # never asks the model for it and does not relax the operation schema.
    model = StubLLM([{'operations': [{'type': 'set_preference', 'value': {'evening': True}}]}])
    async with api_factory(model) as (client, _):
        sid, token, before = await seed(client, row)
        run, after = await send(client, sid, token, text)
        assert len(after['trip']['travelers']) == 1
        assert after['trip']['travelers'][0]['type'] == 'adult'
        assert model.calls == []
        assert after['trip']['preferences'] == before['trip']['preferences']
        assert (after.get('pending_question') or {}).get('field') not in {'party.adults', 'party.children'}
        assert (after.get('pending_question') or {}).get('context', {}).get('children_known') is True
        if 'номера' in text:
            assert len(after['trip']['stays'][0]['rooms']) == 1
            assert after['trip']['stays'][0]['rooms'][0]['traveler_ids'] == [after['trip']['travelers'][0]['id']]
        assert await client._transport.app.state.store.list_orders(sid) == []
