"""Replay actual CF operations locally; no new model/supplier HTTP."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from otravel.clarifications import resolve_short_answer
from otravel.intent_grounding import ground_intent_operations

SOURCE = json.loads((Path(__file__).parents[1] / 'fixtures/regression/geography-cf-r2-20261027.json').read_text(encoding='utf-8'))
BELGRADE = {"kind": "city", "name": "Белград", "country_code": "RS"}


def observed(case, step):
    return deepcopy(next(item for item in SOURCE['cases'] if item['case_id'] == case and item['step_id'] == step))


def merged_pending(before, meta, version):
    context = deepcopy((before or {}).get('context') or {})
    for key in meta['pending_context_clear']:
        context.pop(key, None)
    context.update(meta['pending_context'] or {})
    field = context.pop('field', 'destination.city')
    target = context.pop('target_id', None)
    return {'field': field, 'target_id': target, 'context': context, 'state_version': version}


def test_cached_cf_late_origin_field_typo_requests_consent_preserving_destination_budget_and_state():
    record = observed('PF-GEO-02', '04')
    before = deepcopy(record['before_state'])
    operations, meta = ground_intent_operations(before, record['raw_validated'][0]['operations'],
        record['text'], record['before_pending'])
    assert operations == []
    context = meta['pending_context'] or {}
    assert context.get('field') == context.get('proposal_field') == 'origin.city'
    assert context['confirmation_required'] is True
    assert context['proposed_destination'] == {'kind': 'city', 'name': 'Санкт-Петербург', 'country_code': 'RU'}
    assert any('Санкт-Петербург' in text and 'опечатка' in text for text in meta['unknowns'])
    assert record['before_state'] == before


@pytest.mark.parametrize('text,field,candidate', [
    ('Сейчас отправление Сант-Петербург. Направление в Анталью оставь прежним.', 'origin.city', 'Санкт-Петербург'),
    ('Город отправления: теперь Мосвка. Остальное прежнее.', 'origin.city', 'Москва'),
    ('Теперь город вылета Дубй.', 'origin.city', 'Дубай'),
    ('Направление теперь Стамбулл, бюджет прежний.', 'destination.city', 'Стамбул'),
    ('Назначение Санкт-Петербрг. Отправление прежнее.', 'destination.city', 'Санкт-Петербург'),
])
def test_explicit_late_field_typo_does_not_need_current_city_slot(text, field, candidate):
    record = observed('PF-GEO-02', '04')
    operations, meta = ground_intent_operations(record['before_state'], [], text, record['before_pending'])
    assert not any(op['type'] == 'set_origin' for op in operations)
    context = meta['pending_context'] or {}
    assert context.get('field') == context.get('proposal_field') == field
    assert context['confirmation_required'] is True
    assert context['proposed_destination']['name'] == candidate


@pytest.mark.parametrize('text', [
    'Если отправление Санкт-Петербрг?', 'Раньше отправление было Мосвка.',
    'Не меняй отправление Сант-Петербург.', 'Отправление Санкт-Петербрг?',
    '«Отправление Санкт-Петербрг» — цитата, ничего не меняй.',
    'Какой город отправления Санкт-Петербрг?', 'Отправление Москва. Направление Анталья.',
    'Сейчас отправление Мосвка или Казан. Ничего не выбирай.',
])
def test_field_typo_questions_history_quotes_veto_or_alternatives_do_not_create_confirmation(text):
    record = observed('PF-GEO-02', '04')
    _, meta = ground_intent_operations(record['before_state'], [], text, record['before_pending'])
    assert not (meta['pending_context'] or {}).get('confirmation_required')


@pytest.mark.parametrize('reply', ['нет', 'Нет, добавь ребёнка семи лет. Существующий взрослый остаётся.'])
def test_cached_cf_conflicting_alternative_rejected_retains_exact_previous_unsupported_city(reply):
    conflict = observed('PF-GEO-01', '06')
    before = deepcopy(conflict['before_state'])
    operations, meta = ground_intent_operations(before, conflict['raw_validated'][0]['operations'],
        conflict['text'], conflict['before_pending'])
    assert not any(op['type'] == 'upsert_stay' for op in operations)
    question = merged_pending(conflict['before_pending'], meta, before['state_version'])
    assert question['context']['requested_destination'] == BELGRADE
    assert question['context']['proposed_destination']['name'] == 'Стамбул'
    if reply == 'нет':
        result = resolve_short_answer(before, question, reply)
        context, questions = result['pending_context'], [result['question']]
        assert result['operations'] == []
        clear = result['pending_context_clear']
    else:
        addition = observed('PF-GEO-01', '07')['raw_validated'][0]['operations']
        result_ops, result_meta = ground_intent_operations(before, addition, reply, question)
        assert result_ops == addition
        context, questions = result_meta['pending_context'], result_meta['unknowns']
        clear = result_meta['pending_context_clear']
    assert context['requested_destination'] == BELGRADE
    assert any('Белград' in text and 'поиск' in text for text in questions)
    assert 'proposed_destination' in clear
    assert record_state_without_version(before) == record_state_without_version(conflict['before_state'])


def record_state_without_version(state):
    return {key: value for key, value in state.items() if key != 'state_version'}


@pytest.mark.parametrize('new_country,city,code', [('России', 'Россия', 'RU'), ('ОАЭ', 'ОАЭ', 'AE')])
def test_explicit_different_country_supersedes_old_city_and_is_retained_after_refusal(new_country, city, code):
    record = observed('PF-GEO-01', '06')
    before = record['before_state']
    _, meta = ground_intent_operations(before, [], f'Стамбул в {new_country}', record['before_pending'])
    question = merged_pending(record['before_pending'], meta, before['state_version'])
    assert question['context']['requested_destination']['kind'] == 'country'
    assert question['context']['requested_destination']['name'] == city
    assert question['context']['requested_destination']['country_code'] == code
    rejected = resolve_short_answer(before, question, 'нет')
    assert rejected['pending_context']['requested_destination']['country_code'] == code
    assert rejected['pending_context']['requested_destination']['kind'] == 'country'
    assert 'Белград' not in rejected['question']


def test_canonical_new_city_inside_same_country_is_a_real_change_not_previous_choice_restore():
    record = observed('PF-GEO-02', '04')
    operations, meta = ground_intent_operations(record['before_state'], [],
        'Теперь направление Стамбул. Отправление и бюджет прежние.', record['before_pending'])
    assert {'type': 'upsert_stay', 'target_id': 'new-stay-1', 'value': {'city': 'Стамбул'}} in operations
    assert meta['pending_context']['requested_destination']['name'] == 'Стамбул'
    assert not meta['pending_context'].get('confirmation_required')
