"""Three exact recorded BULK32 failures, no model/network/DB in pure checks."""
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from otravel.date_slots import resolve_date_answer
from otravel.money import ground_budget_operations
from otravel.preference_grounding import guard_preference_operations

FIXTURE = Path(__file__).parents[1] / 'fixtures/regression/bulk32-money-date-preference-20261027.json'
RECORDS = {row['kind']: row for row in json.loads(FIXTURE.read_text(encoding='utf-8'))['records']}
NOW = datetime(2026, 10, 28, 0, tzinfo=UTC)


def test_exact_budget_typo_delta_is_twenty_thousand_not_two_thousand():
    row = RECORDS['money']
    assert row['before']['budget']['amount_minor'] == 15000000
    assert row['raw']['operations'][0]['value']['amount_minor'] == 14800000
    checked, notes = ground_budget_operations(row['before'], row['raw']['operations'], row['source_user_text'])
    assert checked[0]['value']['amount_minor'] == 13000000
    assert checked[1:] == row['raw']['operations'][1:]
    assert notes[0]['mode'] == 'relative_decrease'


def test_exact_suffix_date_is_an_answer_to_the_fresh_owned_checkout():
    row = RECORDS['date']
    assert row['pending']['field'] == 'stay.check_out'
    answer = resolve_date_answer(row['before'], row['pending'], row['source_user_text'], NOW)
    assert answer['handled'] and answer['operations'] == [{'type': 'upsert_stay',
        'target_id': 'new-stay-1', 'value': {'check_out': '2026-11-10'}}]


def test_exact_positive_flight_morning_typo_does_not_erase_the_valid_model_preference():
    row = RECORDS['preference']
    checked, meta = guard_preference_operations(row['before'], row['raw']['operations'], row['source_user_text'])
    assert checked == row['raw']['operations'] and not meta['conflicts']


@pytest.mark.parametrize('text', ['Урежь обший бюджет на20 тысяч рублей.',
    'Урежь общий бюджет на20 тысяч рублей.', 'Сократи обший бюджет на 20 тысяч рублей.'])
def test_budget_modifier_typo_is_bound_to_the_same_explicit_current_budget(text):
    row = RECORDS['money']
    original = deepcopy(row)
    checked, notes = ground_budget_operations(row['before'], row['raw']['operations'], text)
    assert checked[0]['value']['amount_minor'] == 13000000 and notes
    assert row == original


@pytest.mark.parametrize('text', ['Урежь чужой бюджет на 20 тысяч рублей.',
    'Уменьши и увеличь бюджет на 20 тысяч рублей.'])
def test_unresolved_relative_budget_direction_never_accepts_model_arithmetic(text):
    row = RECORDS['money']
    checked, notes = ground_budget_operations(row['before'], row['raw']['operations'], text)
    assert not any(op['type'] == 'set_budget' for op in checked)
    assert checked == row['raw']['operations'][1:]
    assert notes[0]['status'] == 'clarification_required' and notes[0]['question']


@pytest.mark.parametrize('text', ['Не урежь обший бюджет на 20 тысяч рублей.',
    'Если урежь обший бюджет на 20 тысяч рублей, что изменится?',
    'Раньше урежь обший бюджет на 20 тысяч рублей.'])
def test_typo_does_not_grant_scalar_grounding_to_prohibition_hypothesis_or_history(text):
    row = RECORDS['money']
    checked, notes = ground_budget_operations(row['before'], row['raw']['operations'], text)
    assert checked == row['raw']['operations'] and notes == []


@pytest.mark.parametrize('text', ['10 ноября2026 года выезжаем.',
    '10 ноября2026года выезжаем.', '10 ноября 2026 выезжаю.',
    '2026-11-10 выезжаем.', '10.11.2026 выезжаю.', '10 ноября2026года'])
def test_controlled_complete_calendar_variants_use_only_the_fresh_owned_target(text):
    row = RECORDS['date']
    answer = resolve_date_answer(row['before'], row['pending'], text, NOW)
    assert answer['handled'] and answer['operations'][0]['value'] == {'check_out': '2026-11-10'}
    assert answer['operations'][0]['target_id'] == 'new-stay-1'


@pytest.mark.parametrize('patch', [{'field': 'stay.check_in'}, {'field': 'origin.city'}, {'state_version': 1},
    {'state_version': True}, {'question_id': ''}, {'target_id': 'foreign'}, {'reason': 'user_pause'}])
def test_suffix_never_redirects_a_stale_or_different_kind_of_question(patch):
    row = RECORDS['date']
    answer = resolve_date_answer(row['before'], {**row['pending'], **patch}, row['source_user_text'], NOW)
    assert not answer['handled'] and not answer['operations']


@pytest.mark.parametrize('text', ['Если 10 ноября2026 года выезжаем.', '10 ноября2026 года не выезжаем.',
    '«10 ноября2026 года выезжаем».', '10 ноября2026 года выезжаем?',
    'Напомни: 10 ноября2026 года выезжаем.', '10 ноября2026 года вылетаем.',
    '10 ноября2026 года выезжаем из Москвы.', 'Подожди, 10 ноября2026 года выезжаем.',
    'Бюджет100000 рублей, 10 ноября2026 года выезжаем.'])
def test_suffix_parser_does_not_drop_context_before_or_after_a_date(text):
    row = RECORDS['date']
    answer = resolve_date_answer(row['before'], row['pending'], text, NOW)
    assert not answer['handled'] and not answer['operations']


def test_suffix_invalid_calendar_asks_without_committing_a_fake_date():
    row = RECORDS['date']
    answer = resolve_date_answer(row['before'], row['pending'], '31 ноября2026 года выезжаем.', NOW)
    assert answer['handled'] and not answer['operations'] and 'не существует' in answer['question']


@pytest.mark.parametrize('text', ['Заезд лучше рано утрам.', 'Хочу встать рано утрам.',
    'Если вылет лучше рано утрам?', 'Не хочу вылет рано утрам.',
    '«Вылет лучше рано утрам».', 'Раньше вылет лучше рано утрам.'])
def test_morning_typo_is_not_a_generic_time_grammar_or_noncurrent_authority(text):
    row = RECORDS['preference']
    checked, _ = guard_preference_operations(row['before'], row['raw']['operations'], text)
    assert checked == []
