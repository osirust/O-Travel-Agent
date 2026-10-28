"""Actual new80 contracts through fresh isolated PG; controlled model/source.

Canonical captured starting states are explicit test setup, not fresh natural
language intake. No vendor HTTP, live model, or supplier booking is performed.
"""
from copy import deepcopy
from datetime import UTC, datetime

import pytest
from test_api import StubLLM, _session
from test_api import api_factory as controlled_api_fixture
from test_new80_geo_date_helpers import ROWS
from test_place_run_pg import PGHarness, claimed
from test_place_run_pg import pg as controlled_place_fixture
from test_slot_continuation import _message, _reply

from otravel.domain import TripState
from otravel.place_run import finish_place_run

api_factory = controlled_api_fixture
place_pg = controlled_place_fixture


@pytest.mark.asyncio
async def test_pg_actual017_calendar_rejection_binds_current_stay_then_ordinal_correction_commits_once(api_factory):
    invalid = ROWS['ZAI100-017:T8']
    correction = ROWS['ZAI100-017:T9']
    model = StubLLM([deepcopy(invalid['model_contracts']['MODEL_EXTRACTION_VALIDATED'])])
    async with api_factory(model) as (client, _):
        sid, token, original = await _session(client, deepcopy(invalid['before_trip']))
        first, rejected = await _message(client, sid, token, invalid['user'])
        assert rejected['trip'] == original['trip'] and rejected['state_version'] == original['state_version']
        question = rejected['pending_question']
        assert question['field'] == 'stay.check_out'
        assert question['target_id'] == original['trip']['stays'][0]['id']
        assert 'не существует' in await _reply(client, sid, token, first)
        repository = client._transport.app.state.store
        persisted = await repository.get_session(sid)
        assert persisted['pending_question'] == question
        changed, after = await _message(client, sid, token, correction['user'])
        expected = deepcopy(original['trip'])
        expected['stays'][0]['check_out'] = '2026-12-01'
        expected['legs'][-1]['departure_date'] = '2026-12-01'
        expected['state_version'] += 1
        assert after['trip'] == expected and after['state_version'] == original['state_version'] + 1
        assert len(model.calls) == 1, 'An owned exact correction must not need another model call'
        assert (await repository.get_session(sid))['state'] == expected
        assert '31 ноября' not in (after.get('pending_question') or {}).get('text', '')
        assert await repository.list_orders(sid) == []
        assert changed['status'] == 'completed'


@pytest.mark.asyncio
async def test_pg_actual015_named_city_country_and_checkout_shift_do_not_reopen_destination(api_factory):
    row = ROWS['ZAI100-015:T7']
    model = StubLLM([deepcopy(row['model_contracts']['MODEL_EXTRACTION_VALIDATED'])])
    async with api_factory(model) as (client, _):
        sid, token, initial = await _session(client, deepcopy(row['before_trip']))
        run, after = await _message(client, sid, token, row['user'])
        expected = deepcopy(initial['trip'])
        expected['stays'][0]['check_out'] = '2026-12-12'
        expected['legs'][-1]['departure_date'] = '2026-12-12'
        expected['state_version'] += 1
        assert after['trip'] == expected and run['status'] == 'completed'
        assert (after.get('pending_question') or {}).get('field') != 'destination.city'
        assert 'Какой город в стране' not in await _reply(client, sid, token, run)
        assert len(model.calls) == 1
        assert await client._transport.app.state.store.list_orders(sid) == []


@pytest.mark.asyncio
async def test_pg_actual013_current_qualified_country_reaches_source_and_persists_independent_facts(place_pg):
    repository, catalog = place_pg
    row = ROWS['ZAI100-013:T3']
    made = await repository.create_session(deepcopy(row['before_trip']), 'a' * 64)
    session, run, _ = await claimed(repository, made['session_id'], text=row['user'])
    raw = row['model_contracts']['MODEL_EXTRACTION_VALIDATED']['operations']
    checked = row['model_contracts']['MODEL_OPERATIONS_CHECKED']['operations']
    request = next(op['value'] for op in raw if op['type'] == 'resolve_place')
    h = PGHarness(repository, catalog,
        [{'action': 'unavailable', 'question': 'Controlled unavailable source.', 'pending_context': None}], datetime.now(UTC))
    await finish_place_run(h, session, run, TripState.model_validate(session['state']), request=request,
        operations=checked, model_meta={'provider': 'controlled', 'model': 'captured-actual'})
    actual = h.place_resolution.calls[0]['value']
    assert actual['query'] == 'Санкт-Петербург' and actual['country_iso2'] == 'RU'
    assert not actual.get('country_ref')
    after = await repository.get_session(made['session_id'])
    expected = TripState.model_validate(session['state'])
    assert after['state_version'] == session['state_version'] + 1
    assert after['state']['stays'][0]['city'] == 'Санкт-Петербург'
    assert after['state']['travelers'] == expected.model_dump(mode='json')['travelers']
    assert after['state']['origin'] == 'Москва'
    assert await repository.list_orders(made['session_id']) == []
    assert (await repository.get_run(run['run_id']))['status'] == 'completed'
