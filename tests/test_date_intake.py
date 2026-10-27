"""Initial explicit date omissions; pure state/ops, no clock guessing or I/O."""
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_place_refs import place

from otravel.date_intake import ground_explicit_stay_dates
from otravel.domain import Operation, SearchPolicy, TripState, apply_operations
from otravel.intent_grounding import ground_intent_operations
from otravel.literal_intake import ground_explicit_initial_facts

NOW = datetime(2026, 10, 27, 17, tzinfo=UTC)
FIXTURE = Path(__file__).parents[1] / 'fixtures/regression/world-r4-date-omission-20261027.json'


def state():
    ref = place(3435910, 'Buenos Aires', 'AR')
    before = TripState(trip_id='date-intake-owned', state_version=1,
        search_policy=SearchPolicy(mode='HYBRID', guest_nationality='RU', flight_provider='duffel'))
    return apply_operations(before, [Operation(type='upsert_stay', target_id='new-stay-1',
        value={'city': ref.canonical_name, 'place_id': ref.place_id})], resolved_places={ref.place_id: ref})[0]


def pending(before, **values):
    return {'question_id': 'owned-current-date-question', 'state_version': before.state_version,
        'field': 'stay.check_in', 'target_id': 'new-stay-1', 'reason': 'clarification', **values}


def null_setter(**values):
    return [{'type': 'upsert_stay', 'target_id': 'new-stay-1',
        'value': {'check_in': None, 'check_out': None, 'requested_nights': 7, **values}}]


def test_exact_actual_R4_nulls_are_repaired_inside_the_existing_setter_without_a_new_wish_word():
    fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
    before = state()
    raw = deepcopy(fixture['raw']['operations'])
    assert len(raw) == 7 and raw[3]['value']['check_in'] is raw[3]['value']['check_out'] is None
    # Old initial-declaration helper's wish gate declines this exact factual
    # reply. The original actual checked contract also retained both NULLs.
    old, old_meta = ground_explicit_initial_facts(before, fixture['input'], raw, pending=pending(before), now=NOW)
    assert old == raw and not old_meta['recovered']
    grounded, _ = ground_intent_operations(before, raw, fixture['input'])
    original = deepcopy(grounded)
    result, meta = ground_explicit_stay_dates(before, fixture['input'], grounded, pending=pending(before), now=NOW)
    assert meta['recovered'] and not meta['conflicts'] and not meta['unknowns']
    assert grounded == original and raw == fixture['raw']['operations']
    assert len(result) == len(grounded)
    expected = deepcopy(grounded)
    expected[3]['value'].update(check_in='2026-11-10', check_out='2026-11-17')
    assert result == expected
    after, _ = apply_operations(before, result)
    assert after.stays[0].check_in.isoformat() == '2026-11-10'
    assert after.stays[0].check_out.isoformat() == '2026-11-17' and after.stays[0].requested_nights == 7
    assert after.location_refs == before.location_refs
    assert before.stays[0].check_in is before.stays[0].check_out is None


@pytest.mark.parametrize('text', ['Заезд 2026-11-10, выезд 2026-11-17.',
    'Заезд 10.11.2026. Выезд 17.11.2026.', 'Заезд: 10 ноября 2026, выезд: 17 ноября 2026.',
    'Заезд 10 ноября 2026 и выезд 17 ноября 2026.',
    'Отель «Выбранный». Заезд 10 ноября 2026; выезд 17 ноября 2026.'])
def test_absolute_complete_labelled_pairs_share_existing_calendar_parser(text):
    before = state()
    result, meta = ground_explicit_stay_dates(before, text, null_setter(), pending=pending(before), now=NOW)
    assert meta['recovered'] and not meta['conflicts'] and not meta['unknowns']
    assert result[0]['value']['check_in'] == '2026-11-10' and result[0]['value']['check_out'] == '2026-11-17'


@pytest.mark.parametrize('change', [
    {'state_version': 0}, {'state_version': 2}, {'state_version': True}, {'question_id': ''},
    {'target_id': 'foreign'}, {'target_id': None}, {'reason': 'user_pause'}, {'field': 'traveler.age'},
    {'context': {'trip_id': 'foreign-trip'}}, {'context': {'state_version': 99}},
    {'context': {'target_id': True}}, {'context': {'stay_id': 'foreign'}}])
def test_stale_foreign_or_unowned_pending_never_binds_missing_dates(change):
    before, ops = state(), null_setter()
    result, meta = ground_explicit_stay_dates(before, 'Заезд 10.11.2026, выезд 17.11.2026.', ops,
        pending=pending(before, **change), now=NOW)
    assert result == ops and not meta['recovered']


@pytest.mark.parametrize('scope', ['no_pending', 'stale_pending', 'multiple_stays'])
def test_invalid_dates_outside_owned_initial_question_do_not_inject_a_new_question(scope):
    before, ops = state(), null_setter()
    owned = pending(before)
    if scope == 'no_pending':
        owned = None
    elif scope == 'stale_pending':
        owned['state_version'] = before.state_version - 1
    else:
        before, _ = apply_operations(before, [Operation(type='upsert_stay', target_id='other',
            value={'city': 'Казань'})])
        owned = pending(before)
    result, meta = ground_explicit_stay_dates(before, 'Заезд 31.02.2027, выезд 07.03.2027.', ops,
        pending=owned, now=NOW)
    assert result == ops and not meta['recovered']
    assert meta['unknowns'] == [] and meta['conflicts'] == []


@pytest.mark.parametrize('text', ['Если поедем: заезд 10.11.2026, выезд 17.11.2026.',
    'Раньше: заезд 10.11.2026, выезд 17.11.2026.',
    'Объясни даты. Заезд 10.11.2026, выезд 17.11.2026.',
    'Заезд 10.11.2026, выезд 17.11.2026?',
    '«Заезд 10.11.2026, выезд 17.11.2026».',
    'Не меняй даты. Заезд 10.11.2026, выезд 17.11.2026.',
    'Заезд 10.11.2026 или выезд 17.11.2026.'])
def test_hypothesis_history_information_negation_or_quoted_dates_do_not_initiate_a_patch(text):
    before, ops = state(), null_setter()
    result, meta = ground_explicit_stay_dates(before, text, ops, pending=pending(before), now=NOW)
    assert result == ops and not meta['recovered']


@pytest.mark.parametrize('text', ['Заезд 10 ноября, выезд 17 ноября.',
    'Заезд 31.02.2027, выезд 07.03.2027.', 'Заезд 10.11.2025, выезд 17.11.2025.'])
def test_missing_year_invalid_calendar_and_past_dates_ask_without_guessing(text):
    before, ops = state(), null_setter()
    result, meta = ground_explicit_stay_dates(before, text, ops, pending=pending(before), now=NOW)
    assert result == ops and not meta['recovered'] and meta['unknowns']


@pytest.mark.parametrize('text,nights', [('Заезд 17.11.2026, выезд 10.11.2026.', 7),
    ('Заезд 10.11.2026, выезд 17.11.2026.', 8),
    ('Заезд 10.11.2026, заезд 11.11.2026, выезд 17.11.2026.', 7)])
def test_inconsistent_positive_dates_or_model_duration_are_human_conflicts(text, nights):
    before, ops = state(), null_setter(requested_nights=nights)
    result, meta = ground_explicit_stay_dates(before, text, ops, pending=pending(before), now=NOW)
    assert result == ops and not meta['recovered'] and meta['conflicts']
    assert all('check_in' not in question and 'requested_nights' not in question for question in meta['conflicts'])


def test_wrong_concrete_model_date_is_not_overwritten_by_an_omission_fallback():
    before, ops = state(), null_setter(check_in='2026-11-11')
    result, meta = ground_explicit_stay_dates(before, 'Заезд 10.11.2026, выезд 17.11.2026.', ops,
        pending=pending(before), now=NOW)
    assert result == ops and meta['conflicts'] and not meta['recovered']


def test_known_state_date_cannot_be_cleared_or_replaced_by_this_fallback():
    before = state()
    before, _ = apply_operations(before, [Operation(type='upsert_stay', target_id='new-stay-1',
        value={'check_in': '2026-11-10', 'check_out': '2026-11-17'})])
    ops = null_setter()
    result, meta = ground_explicit_stay_dates(before, 'Заезд 10.11.2026, выезд 17.11.2026.', ops,
        pending=pending(before), now=NOW)
    assert result == ops and meta['conflicts'] and not meta['recovered']
    assert before.stays[0].check_in.isoformat() == '2026-11-10'


def test_concrete_correct_model_dates_are_noop_without_extra_setter_or_fake_revision():
    before = state()
    ops = null_setter(check_in='2026-11-10', check_out='2026-11-17')
    result, meta = ground_explicit_stay_dates(before, 'Заезд 10.11.2026, выезд 17.11.2026.', ops,
        pending=pending(before), now=NOW)
    assert result == ops and not meta['recovered'] and not meta['conflicts']


def test_multiple_stays_or_foreign_model_target_do_not_silently_choose_the_first():
    before = state()
    other, _ = apply_operations(before, [Operation(type='upsert_stay', target_id='other', value={'city': 'Казань'})])
    text = 'Заезд 10.11.2026, выезд 17.11.2026.'
    result, meta = ground_explicit_stay_dates(other, text, null_setter(), pending=pending(other), now=NOW)
    assert result == null_setter() and not meta['recovered']
    ops = [{'type': 'upsert_stay', 'target_id': 'foreign', 'value': {'check_in': None}}]
    result, meta = ground_explicit_stay_dates(before, text, ops, pending=pending(before), now=NOW)
    assert result == ops and not meta['recovered']


def test_new_explicit_pause_does_not_disallow_current_positive_facts_for_the_owned_pending_target():
    before = state()
    result, meta = ground_explicit_stay_dates(before, 'Заезд 10.11.2026, выезд 17.11.2026. Подожди, я ещё дополню.',
        null_setter(), pending=pending(before), now=NOW)
    assert meta['recovered'] and result[0]['value']['check_in'] == '2026-11-10'
    # The helper has no control/search effect. Harness must keep the explicit
    # pause and defer sources after saving the validated positive facts.


@pytest.mark.parametrize('now,zone', [(datetime(2026, 10, 27), 'Europe/Moscow'), (NOW, 'Invalid/Zone')])
def test_a_missing_aware_clock_or_unknown_timezone_does_not_guess_calendar_context(now, zone):
    before, ops = state(), null_setter()
    result, meta = ground_explicit_stay_dates(before, 'Заезд 10.11.2026, выезд 17.11.2026.', ops,
        pending=pending(before), now=now, timezone=zone)
    assert result == ops and not meta['recovered']
def test_unrelated_input_does_not_emit_date_normalization_notes():
    from datetime import UTC, datetime
    from otravel.date_intake import ground_explicit_stay_dates
    from otravel.domain import TripState
    operations,meta=ground_explicit_stay_dates(TripState(),
        'Один взрослый из Москвы в Казань, детей пока не будет.',[],pending=None,now=datetime(2026,10,27,tzinfo=UTC))
    assert operations==[] and meta=={'recovered':False,'notes':[],'conflicts':[],'unknowns':[]}
