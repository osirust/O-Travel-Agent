"""Actual ASK-reply loss replay; no new model/supplier requests."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from otravel.answering import canonical_geography_answer
from otravel.clarifications import resolve_short_answer

CAPTURE = json.loads((Path(__file__).parents[1] / 'fixtures/regression/geography-openrouter-r3-20261027.json').read_text(encoding='utf-8'))


def test_actual_openrouter_correct_geography_reply_was_overwritten_by_harness_country_faq():
    row = CAPTURE['observation']
    assert row['event_types'].count('MODEL_CALL_STARTED') == 1
    assert row['raw_validated']['speech_act'] == 'ASK' and row['raw_validated']['operations'] == []
    assert 'Стамбул' in row['raw_validated']['reply'] and 'Турци' in row['raw_validated']['reply']
    assert 'Стамбул' not in row['actual_reply'] and 'Турци' not in row['actual_reply']
    assert row['actual_pending'] == row['before_pending']


def test_exact_actual_qualified_city_answer_creates_bound_server_consent_before_model():
    row = deepcopy(CAPTURE['observation'])
    result = resolve_short_answer(row['before_state'], row['before_pending'], row['text'])
    assert result['handled'] is True
    assert result['operations'] == []
    assert 'Стамбул' in result['question'] and 'Турци' in result['question']
    assert result['pending_context']['requested_destination'] == {'kind': 'city', 'name': 'Белград', 'country_code': 'RS'}
    assert result['pending_context']['proposed_destination'] == {'kind': 'city', 'name': 'Стамбул', 'country_code': 'TR'}
    assert result['pending_context']['confirmation_required'] is True


@pytest.mark.parametrize('state', [{}, CAPTURE['observation']['before_state']])
def test_question_variant_reports_actual_country_and_explicitly_refutes_wrong_country(state):
    reply = canonical_geography_answer('Стамбул в Сербии?', state)
    assert reply and 'Стамбул' in reply and 'Турция' in reply and 'Сербия' in reply and 'не находится' in reply


@pytest.mark.parametrize('text', ['Стамбул в Сербии?', 'Стамбул в Сербии? Добавь ребёнка семи лет.',
    'Если Стамбул в Сербии', 'Раньше Стамбул в Сербии', 'Стамбул в Сербии или в Турции'])
def test_uncertain_questions_and_compound_text_are_not_exact_contextual_city_assertions(text):
    row = CAPTURE['observation']
    result = resolve_short_answer(row['before_state'], row['before_pending'], text)
    assert not result['handled'] or not result.get('pending_context', {}).get('confirmation_required')


@pytest.mark.parametrize('question_patch', [{'state_version': 2}, {'state_version': True}, {'state_version': None}])
def test_qualified_city_assertion_cannot_use_stale_or_unversioned_pending(question_patch):
    row = CAPTURE['observation']
    question = {**deepcopy(row['before_pending']), **question_patch}
    result = resolve_short_answer(row['before_state'], question, row['text'])
    assert result == {'handled': False, 'operations': []}


@pytest.mark.parametrize('text,requested_country', [('Стамбул в России', 'RU'), ('Дубай в Турции', 'TR')])
def test_wrong_city_with_explicit_new_country_does_not_restore_the_old_country(text, requested_country):
    row = CAPTURE['observation']
    result = resolve_short_answer(row['before_state'], row['before_pending'], text)
    assert result['handled'] and result['operations'] == []
    assert result['pending_context']['requested_destination']['kind'] == 'country'
    assert result['pending_context']['requested_destination']['country_code'] == requested_country
    assert result['pending_context']['confirmation_required'] is True


def test_explicit_known_city_and_correct_country_still_require_confirmation_to_change_old_country():
    row = CAPTURE['observation']
    result = resolve_short_answer(row['before_state'], row['before_pending'], 'Стамбул в Турции')
    assert result['operations'] == [] and result['pending_context']['confirmation_required'] is True
    assert result['pending_context']['requested_destination']['name'] == 'Белград'
    assert result['pending_context']['proposed_destination']['name'] == 'Стамбул'
