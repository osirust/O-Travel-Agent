"""Impossible current hotel dates cannot be silently rolled into valid dates."""
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from otravel.date_intake import ground_explicit_stay_dates

FIXTURE = Path(__file__).parents[1] / 'fixtures/regression/bulk017-invalid-calendar-20261027.json'
ROW = json.loads(FIXTURE.read_text(encoding='utf-8'))
NOW = datetime(2026, 10, 28, tzinfo=UTC)


def test_exact_actual_current_edit_rejects_invalid_literal_before_pending_ownership():
    original = deepcopy(ROW)
    ops, meta = ground_explicit_stay_dates(ROW['before'], ROW['input'], ROW['raw']['operations'], pending=None, now=NOW)
    assert ops == ROW['raw']['operations'] and ROW == original
    assert meta['conflicts'] and '31 ноября 2026' in meta['conflicts'][0]
    assert 'не существует' in meta['conflicts'][0]
    assert meta['unknowns'] == meta['conflicts']
    assert meta['pending_context_clear'] == ['date_proposal', 'duration_proposal', 'duration_days']
    assert any(note.get('finding_id') == 'HARNESS-004-INVALID-CALENDAR-ROLLOVER-001' for note in meta['notes'])


@pytest.mark.parametrize('pending', [None, {'question_id': 'stale', 'state_version': -1, 'field': 'stay.check_out', 'target_id': 'foreign'},
    {'question_id': 'another', 'state_version': 4, 'field': 'origin.city'}, 'multiple_stays'])
def test_invalid_asserted_date_is_independent_of_a_missing_stale_or_other_pending(pending):
    before = deepcopy(ROW['before'])
    if pending == 'multiple_stays':
        before['stays'].append({'id': 'other-stay', 'city': 'Сочи', 'check_in': '2026-12-04', 'check_out': '2026-12-05'})
        pending = None
    _, meta = ground_explicit_stay_dates(before, ROW['input'], ROW['raw']['operations'], pending=pending, now=NOW)
    assert meta['conflicts'] and not meta['recovered']


@pytest.mark.parametrize('text', ['Заезд31.11.2026.', 'Выезд: 2026-11-31.',
    'Перенеси дату выезда на31 ноября2026года.', 'Поставь заезд на29 февраля2027 года.',
    'Выезд 2026-13-05.', 'Заезд00.11.2026.', 'Заезд31 апреля.', 'Выезд30 февраля.'])
def test_explicit_impossible_calendar_or_universal_month_bound_has_no_nearest_date(text):
    _, meta = ground_explicit_stay_dates(ROW['before'], text, ROW['raw']['operations'], pending=None, now=NOW)
    assert meta['conflicts'] and not meta['recovered']


@pytest.mark.parametrize('text', ['Выезд29 февраля.', 'Выезд29 февраля2028 года.',
    'Перенеси только выезд на30 ноября2026 года.', 'Выезд2026-12-01.'])
def test_valid_dates_or_yearless_possible_leap_day_do_not_gain_a_calendar_conflict(text):
    _, meta = ground_explicit_stay_dates(ROW['before'], text, ROW['raw']['operations'], pending=None, now=NOW)
    assert not meta['conflicts'] and not meta['recovered']


@pytest.mark.parametrize('text', ['Если перенести выезд на31 ноября2026 года, что будет?',
    'Перенеси выезд на31 ноября2026 года?', '«Перенеси выезд на31 ноября2026 года».',
    'Раньше был выезд31 ноября2026 года.', 'Не переноси выезд на31 ноября2026 года.',
    'Объясни, почему выезд31 ноября2026 года невозможен.',
    'Напомни дату: выезд31 ноября2026 года.', 'Скажи, существует ли выезд31 ноября2026 года.',
    'Скажи, выезд31 ноября2026 года.', 'Покажи, заезд31 ноября2026 года.',
    'Вылет31 ноября2026 года.', 'Бюджет31 тысяча рублей.',
    'Отель «Выезд31 ноября2026 года» понравился.'])
def test_readonly_hypothesis_history_negation_quotes_and_other_roles_do_not_assert_a_new_booking_date(text):
    _, meta = ground_explicit_stay_dates(ROW['before'], text, ROW['raw']['operations'], pending=None, now=NOW)
    assert not meta['conflicts'] and not meta['recovered']


def test_invalid_date_keeps_independent_budget_in_audited_raw_batch_but_reports_whole_batch_conflict():
    raw = deepcopy(ROW['raw']['operations']) + [{'type': 'set_budget', 'value': {'amount_minor': 7500000, 'currency': 'RUB'}}]
    text = ROW['input'] + ' И установи общий бюджет75000 рублей.'
    ops, meta = ground_explicit_stay_dates(ROW['before'], text, raw, pending=None, now=NOW)
    assert ops == raw and meta['conflicts'], 'Caller blocks the entire batch, not only the impossible date'
