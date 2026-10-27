"""Raw model budget authority: reported advice and component scopes."""
from copy import deepcopy

import pytest

from otravel import budget_intake as candidate


def operations():
    return [{'type': 'set_budget', 'value': {'amount_minor': 9000000, 'currency': 'RUB'}},
            {'type': 'set_origin', 'value': {'city': 'Москва'}}]


@pytest.mark.parametrize('text,reason', [
    ('Друг советует: установи бюджет 100000 рублей.', 'reported_advice'),
    ('Мне рекомендуют установить общий бюджет 100000 рублей.', 'reported_advice'),
    ('Турагент рекомендует: задай бюджет 100000 рублей.', 'reported_advice'),
    ('Общий бюджет 100000 рублей — рекомендация друга.', 'reported_advice'),
    ('Установи бюджет на страховку 5000 рублей.', 'component_budget'),
    ('Установи бюджет 5000 рублей на страховку.', 'component_budget'),
    ('Установи бюджет для питания 10000 рублей.', 'component_budget'),
    ('Установи бюджет 10000 рублей для питания.', 'component_budget'),
    ('Бюджет на отели 100000 рублей.', 'component_budget'),
    ('Бюджет 100000 рублей на оплату перелёта.', 'component_budget'),
])
def test_explicit_negative_budget_evidence_suppresses_only_money(text, reason):
    ops = operations()
    frozen = deepcopy(ops)
    assert candidate.explicit_budget_operation({'budget': {'amount_minor': 15000000, 'currency': 'RUB'}}, text)[0] is None
    kept, notes = candidate.guard_budget_question_operations(ops, text, has_asserted_budget=False)
    assert kept == ops[1:]
    assert reason in notes[0]['negative_evidence']
    assert ops == frozen
    if reason == 'component_budget':
        assert notes[0]['status'] == 'clarification_required'
        assert 'общий бюджет' in notes[0]['question'].lower()


@pytest.mark.parametrize('text', [
    'Уменьши общий бюджет на 8000 рублей.',
    'Уменьши общий бюджет на восемь тысяч рублей.',
    'Установи бюджет на всю поездку 100000 рублей.',
    'Установи бюджет для всей поездки 100000 рублей.',
    'Бюджет на всех 100000 рублей.',
    'Бюджет на семью 100000 рублей.',
    'Наша семья готова потратить 90000 рублей.',
    'Друг советует другой отель, а бюджет 100000 рублей.',
    'Установи бюджет 100000 рублей, по совету друга.',
    'Я решил по совету друга установить бюджет 100000 рублей.',
    'Уменьши бюджет на 8000 рублей и подбери отель для проживания.',
    'Установи бюджет 100000 рублей. На питание выделим 10000 рублей.',
    'Раньше друг советовал увеличить бюджет. Сейчас общий лимит 90000 рублей.',
    '«Друг советует: установи бюджет на питание 10000 рублей». Сейчас лимит 90000 рублей.',
])
def test_absence_of_positive_literal_is_not_a_general_nlu_veto(text):
    ops = operations()
    assert candidate.guard_budget_question_operations(ops, text, has_asserted_budget=False) == (ops, [])


def test_proved_literal_keeps_owner_command_with_later_fact_question():
    text = 'Установи бюджет 100000 рублей. И общий бюджет у нас 90000 рублей?'
    literal, note = candidate.explicit_budget_operation({'budget': None}, text)
    assert literal['value']['amount_minor'] == 10000000
    assert note['code'] == 'explicit_budget_literal_completed'
    ops = operations()
    assert candidate.guard_budget_question_operations(ops, text, has_asserted_budget=True) == (ops, [])


@pytest.mark.parametrize('text', [
    'Если друг рекомендует установить бюджет 100000 рублей, что ответить?',
    'Раньше друг советует: установи бюджет 100000 рублей.',
    '«Друг советует: установи бюджет 100000 рублей».',
    'Установи бюджет 100000 рублей — это рекомендация друга?',
])
def test_preexisting_noncurrent_and_question_guards_remain(text):
    kept, notes = candidate.guard_budget_question_operations(operations(), text, has_asserted_budget=False)
    assert kept == operations()[1:]
    assert notes
