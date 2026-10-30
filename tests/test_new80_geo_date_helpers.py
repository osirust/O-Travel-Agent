"""Captured actual013/015/017 boundaries; no vendor HTTP or model inference."""
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_place_run import Harness as PlaceHarness
from test_place_run import context

from otravel.date_intake import ground_explicit_stay_dates
from otravel.date_slots import resolve_date_answer
from otravel.domain import TripState, apply_operations
from otravel.intent_grounding import ground_intent_operations
from otravel.place_run import finish_place_run

NOW = datetime(2026, 10, 28, 12, tzinfo=UTC)
ROWS = {row['id']: row for row in json.loads((Path(__file__).parents[1]
    / 'fixtures/regression/new80-geo-date-corrections-20261028.json').read_text(encoding='utf-8'))['cases']}


@pytest.mark.asyncio
async def test_actual013_current_explicit_russia_filters_the_exact_source_request():
    row = ROWS['ZAI100-013:T3']
    state = TripState.model_validate(row['before_trip'])
    state, session, run = context(state, deepcopy(row['before_pending']), 'clarification')
    run['input_text'] = row['user']
    raw = row['model_contracts']['MODEL_EXTRACTION_VALIDATED']['operations']
    checked = row['model_contracts']['MODEL_OPERATIONS_CHECKED']['operations']
    request = next(op['value'] for op in raw if op['type'] == 'resolve_place')
    original = deepcopy((state.model_dump(mode='json'), request, session))
    harness = PlaceHarness([{'action': 'unavailable', 'question': 'Controlled source; no HTTP.', 'pending_context': None}])
    await finish_place_run(harness, session, run, state, request=request,
        operations=checked, model_meta={'provider': 'controlled', 'model': 'captured-actual'})
    actual = harness.place_resolution.calls[0]['value']
    assert actual['query'] == 'Санкт-Петербург' and actual['country_iso2'] == 'RU'
    assert actual['role'] == 'destination' and actual['stay_id'] == 'new-stay-1'
    assert actual['country_ref']['source_provider'] == 'unicode-cldr'
    assert actual['country_ref']['country_iso2'] == 'RU'
    assert actual['country_ref']['state_version'] == harness.place_resolution.calls[0]['state']['state_version']
    assert (state.model_dump(mode='json'), request, session) == original


def test_actual015_named_city_country_confirmation_does_not_ask_the_same_city_again():
    row = ROWS['ZAI100-015:T7']
    original = deepcopy(row['before_trip'])
    operations, meta = ground_intent_operations(original,
        row['model_contracts']['MODEL_EXTRACTION_VALIDATED']['operations'], row['user'], row['before_pending'])
    assert not meta['unknowns'] and meta['replace_geo_unknowns'] is True
    assert not any('Какой город в стране' in question for question in meta['unknowns'])
    after, _ = apply_operations(TripState.model_validate(original), operations)
    assert after.stays[0].city == 'Санкт-Петербург'
    assert after.stays[0].check_in.isoformat() == '2026-12-05'
    assert after.stays[0].check_out.isoformat() == '2026-12-12'
    assert after.travelers == TripState.model_validate(original).travelers
    assert original == row['before_trip']


def test_actual017_impossible_literal_proposes_an_owned_target_even_when_checkout_is_filled():
    row = ROWS['ZAI100-017:T8']
    before = deepcopy(row['before_trip'])
    ops, meta = ground_explicit_stay_dates(before, row['user'],
        row['model_contracts']['MODEL_EXTRACTION_VALIDATED']['operations'],
        pending=row['before_pending'], now=NOW)
    assert meta['conflicts'] and not meta['recovered']
    assert meta['pending_context']['field'] == 'stay.check_out'
    assert meta['pending_context']['target_id'] == before['stays'][0]['id']
    assert before == row['before_trip']
    assert ops == row['model_contracts']['MODEL_EXTRACTION_VALIDATED']['operations']


def owned_checkout(before):
    return {'question_id': 'owned-calendar-correction', 'state_version': before['state_version'],
        'reason': 'clarification', 'field': 'stay.check_out', 'target_id': before['stays'][0]['id'],
        'context': {'trip_id': before['trip_id']}}


def test_actual017_full_corrective_utterance_sets_only_owned_checkout_and_return_date():
    row = ROWS['ZAI100-017:T9']
    before = deepcopy(row['before_trip'])
    answer = resolve_date_answer(before, owned_checkout(before), row['user'], NOW)
    assert answer['handled'] and not answer.get('question')
    assert answer['operations'] == [{'type': 'upsert_stay', 'target_id': before['stays'][0]['id'],
        'value': {'check_out': '2026-12-01'}}]
    after, _ = apply_operations(TripState.model_validate(before), answer['operations'])
    assert after.stays[0].check_in.isoformat() == '2026-11-25'
    assert after.stays[0].check_out.isoformat() == '2026-12-01'
    assert after.legs[-1].departure_date.isoformat() == '2026-12-01'
    assert after.travelers == TripState.model_validate(before).travelers
    assert after.budget == TripState.model_validate(before).budget
    assert after.stays[0].rooms == TripState.model_validate(before).stays[0].rooms
    assert before == row['before_trip']


@pytest.mark.parametrize('change', [
    {'state_version': 0}, {'state_version': True}, {'question_id': ''}, {'reason': 'user_pause'},
    {'target_id': None}, {'target_id': 'foreign'}, {'context': {'trip_id': 'foreign'}},
    {'context': {'target_id': 'foreign'}}, {'field': 'party.children'},
])
def test_corrective_date_utterance_cannot_use_a_stale_foreign_or_different_slot(change):
    row = ROWS['ZAI100-017:T9']
    question = {**owned_checkout(row['before_trip']), **change}
    result = resolve_date_answer(row['before_trip'], question, row['user'], NOW)
    assert not result['handled'] and not result['operations']


@pytest.mark.parametrize('text', [
    'Если имел в виду первое декабря 2026. Это окончательный выезд; заезд не меняем.',
    'Имел в виду первое декабря 2026?',
    '«Имел в виду первое декабря 2026». Это окончательный выезд; заезд не меняем.',
    'Имел в виду первое декабря. Это окончательный выезд; заезд не меняем.',
    'Имел в виду первое декабря 2026. Увеличь бюджет на 5000.',
    'Имел в виду первое декабря 2026. Подожди, я дополню.',
    'Раньше имел в виду первое декабря 2026.',
])
def test_corrective_date_closed_grammar_does_not_swallow_hypotheses_quotes_or_extra_edits(text):
    before = ROWS['ZAI100-017:T9']['before_trip']
    result = resolve_date_answer(before, owned_checkout(before), text, NOW)
    assert not result['handled'] and not result['operations']


@pytest.mark.parametrize('kind', ['no_stay', 'two_stays', 'conflicting_target'])
def test_impossible_literal_does_not_guess_a_target_without_a_unique_owned_scope(kind):
    row = ROWS['ZAI100-017:T8']
    before = deepcopy(row['before_trip'])
    ops = []
    if kind == 'no_stay':
        before['stays'] = []
    elif kind == 'two_stays':
        before['stays'].append({**deepcopy(before['stays'][0]), 'id': 'other-stay'})
    else:
        ops = [{'type': 'upsert_stay', 'target_id': 'foreign', 'value': {'check_out': '2026-12-01'}}]
    _, meta = ground_explicit_stay_dates(before, row['user'], ops, pending=None, now=NOW)
    assert meta['conflicts']
    assert not (meta.get('pending_context') or {}).get('target_id')


@pytest.mark.asyncio
@pytest.mark.parametrize('text', [
    'Выбираю Санкт-Петербург.',
    'Раньше выбирал Санкт-Петербург в России.',
    'Если выбрать Санкт-Петербург в России?',
    '«Выбираю Санкт-Петербург в России».',
    'Не выбираю Санкт-Петербург в России.',
    'Из Санкт-Петербурга в России хочу в Стамбул.',
    'Выбираю Стамбул в Турции.',
    'Выбираю Санкт-Петербург в России или Казань.',
])
async def test_source_country_filter_is_not_guessed_from_another_city_role_history_or_hypothesis(text):
    row = ROWS['ZAI100-013:T3']
    state, session, run = context(TripState.model_validate(row['before_trip']), None)
    run['input_text'] = text
    request = {'kind': 'city', 'role': 'destination', 'query': 'Санкт-Петербург', 'stay_id': 'new-stay-1'}
    h = PlaceHarness([{'action': 'unavailable', 'question': 'Controlled source.', 'pending_context': None}])
    await finish_place_run(h, session, run, state, request=request)
    assert h.place_resolution.calls[0]['value'].get('country_iso2') is None
