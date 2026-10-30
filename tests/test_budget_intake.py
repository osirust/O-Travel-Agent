from copy import deepcopy

import pytest

from otravel.budget_intake import explicit_budget_operation, guard_budget_question_operations
from otravel.money import ground_budget_operations


def state(amount=15000000):
    return {'budget': None if amount is None else {'amount_minor': amount, 'currency': 'RUB', 'scope': 'whole_trip'}}


@pytest.mark.parametrize('text,base,expected', [
    ('Урежь обший бюджет на 20 тысяч рублей и напомни теперь возраста обоих детей.', 15000000, 13000000),
    ('Обший бюджет теперь85000 рублей. Кто в итоге едет?', None, 8500000),
    ('Общий бюджет уменьши на восемь тысяч рублей. Весь отдых остаётся там же.', 9800000, 9000000),
    ('Общий бюджет увеличь на восемь тысяч рублей.', 9800000, 10600000),
    ('Установи общий бюджет 75000 рублей.', None, 7500000),
    ('Общий бюджет 75 тысяч рублей.', None, 7500000),
    ('Общий бюджет 75000.50 рублей. Кто едет?', None, 7500050),
    ('Общий бюджет 75000,50 рублей.', None, 7500050),
    ('Установи общий бюджет100000 рублей, хорошо?', None, 10000000),
    ('Бюджет установи100000 рублей, хорошо?', None, 10000000),
    ('Уменьши бюджет на 20 процентов.', 15000000, 12000000),
    ('Бюджет уменьши на 20 процентов.', 15000000, 12000000),
    ('Раньше просил увеличить бюджет. Теперь уменьши бюджет на 20 тысяч рублей.', 15000000, 13000000),
    ('Сократи бюджет на 20 тысяч рублей. Остальные условия не меняй.', 15000000, 13000000),
])
def test_explicit_current_budget_is_completed(text, base, expected):
    before = state(base)
    frozen = deepcopy(before)
    operation, note = explicit_budget_operation(before, text)
    assert operation == {'type': 'set_budget', 'value': {'amount_minor': expected, 'currency': 'RUB'}}
    assert note['source'] == 'current_asserted_financial_clause'
    assert before == frozen


@pytest.mark.parametrize('text', [
    'Общий бюджет у нас сейчас 90000 рублей?',
    'Стамбул в Турции, верно? И общий бюджет у нас сейчас90000 рублей? Ничего больше не меняй.',
    'Если уменьшить бюджет на восемь тысяч рублей, что получится?',
    'Раньше просил: уменьши бюджет на 20 тысяч рублей.',
    '«Уменьши бюджет на 20 тысяч рублей» — это пример.',
    '«Уменьши бюджет на 20 тысяч рублей. Срочно!» — это пример.',
    '> Уменьши бюджет на 20 тысяч рублей.',
    'Уменьшили бюджет на 20 тысяч рублей.',
    'Не уменьшай бюджет на 20 тысяч рублей.',
    'Не уменьши бюджет на 20 тысяч рублей?',
    'Уменьши бюджет на 20 тысяч рублей. Бюджет не меняй.',
    'Пока ничего не меняй. Уменьши бюджет на 20 тысяч рублей.',
    'Уменьши бюджет на 20 тысяч рублей. Увеличь бюджет на 10 тысяч рублей.',
    'Уменьши возраст ребёнка на 2 года. Общий бюджет не меняй.',
    'Отель стоит 90000 рублей. Сколько стоит поездка?',
    'Бюджет отеля 90000 рублей.',
    'Уменьши бюджет на 8000 рублей за ночь.',
    'Уменьши бюджет на восемь или десять тысяч рублей.',
    'Уменьши бюджет на -8000 рублей.',
    'Установи бюджет 100000 EUR.',
    'Общий бюджет 75000.',
    'Бюджет увеличили на 20 тысяч рублей.',
    'Бюджет100000 рублей — только моё предложение, не применяй его пока.',
    'Установи бюджет100000 рублей, но пока не применяй.',
    'Друг советует: установи бюджет100000 рублей. Я ещё не решил.',
    'Установи бюджет на страховку5000 рублей.',
    'Установи бюджет5000 рублей на страховку.',
    'Установи бюджет на питание10000 рублей.',
    'Установи бюджет10000 рублей на питание.',
    '"Установи бюджет100000 рублей',
    'Бюджет100000 рублей — моё предложение.',
    'Общий бюджет100000 рублей — гипотеза.',
    'Бюджет100000 рублей — возможный вариант, ещё подумаю.',
    'Установи бюджет100000 рублей — это твой совет?',
])
def test_noncurrent_questions_ambiguous_or_unit_scope_do_not_create_budget(text):
    before = state(None)
    assert explicit_budget_operation(before, text) == (None, None)


@pytest.mark.parametrize('text', [
    'Общий бюджет уменьши на восемь тысяч рублей. Весь отдых остаётся там же.',
    'Уменьши общий бюджет на восемь тысяч рублей.',
])
def test_existing_model_word_scale_is_grounded_in_either_word_order(text):
    operations = [{'type': 'set_budget', 'value': {'amount_minor': 9720000, 'currency': 'RUB'}}]
    fixed, _ = ground_budget_operations(state(9800000), operations, text)
    assert fixed[0]['value']['amount_minor'] == 9000000
    assert operations[0]['value']['amount_minor'] == 9720000


@pytest.mark.parametrize('text', [
    'Общий бюджет у нас сейчас90000 рублей?',
    'Стамбул в Турции, верно? И общий бюджет у нас сейчас90000 рублей? Ничего больше не меняй.',
    'Перенеси выезд. И общий бюджет у нас90000 рублей?',
    'Если увеличить бюджет до90000 рублей, что изменится?',
])
def test_budget_fact_question_does_not_reassign_amount(text):
    ops = [{'type': 'set_budget', 'value': {'amount_minor': 9000000, 'currency': 'RUB'}},
           {'type': 'set_preference', 'value': {'key': 'departure_time_of_day', 'value': 'morning'}}]
    kept, notes = guard_budget_question_operations(ops, text, has_asserted_budget=False)
    assert kept == ops[1:] and notes[0]['code'] == 'budget_question_not_assignment'
    assert len(ops) == 2


@pytest.mark.parametrize('text', ['Уменьши бюджет до90000 рублей, хорошо?',
                                'Бюджет уменьши до90000 рублей, хорошо?'])
def test_polite_direct_budget_command_is_explicitly_grounded(text):
    ops = [{'type': 'set_budget', 'value': {'amount_minor': 9000000, 'currency': 'RUB'}}]
    literal, _ = explicit_budget_operation(state(), text)
    assert literal['value']['amount_minor'] == 9000000
    assert guard_budget_question_operations(ops, text, has_asserted_budget=True) == (ops, [])


@pytest.mark.parametrize('text', ['Как понять «Поставь общий бюджет100000 рублей»?',
    'Бюджет100000 рублей — только моё предложение, не применяй его пока.',
    'Установи бюджет100000 рублей, но пока не применяй.',
    'Друг советует: установи бюджет100000 рублей. Я ещё не решил.',
    '«Поставь общий бюджет100000 рублей» — это только пример.',
    '"Установи бюджет100000 рублей'])
def test_quoted_reported_nonapply_budget_is_not_model_authority(text):
    ops = [{'type': 'set_budget', 'value': {'amount_minor': 10000000, 'currency': 'RUB'}}]
    kept, notes = guard_budget_question_operations(ops, text, has_asserted_budget=False)
    assert not kept and notes
