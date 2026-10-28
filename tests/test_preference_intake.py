from copy import deepcopy

import pytest

from otravel.preference_intake import explicit_lowest_cost, ground_lowest_cost


@pytest.mark.parametrize('text', [
    'Ищи минимум стоимсти. Напомни, какой город сейчас выбран и сколько нас едет.',
    'Ищи минимум стоимости.', 'Найди самый дешёвый вариант.', 'Подбери варианты с минимальной ценой.',
    'Пожалуйста, поищи подешевле.',
])
def test_only_explicit_current_price_command_can_fill_omitted_soft_preference(text):
    operations = []
    grounded, notes = ground_lowest_cost(operations, text)
    assert grounded[0]['value'] == {'key': 'price_priority', 'value': 'lowest_total'}
    assert notes[0]['finding_id'] == 'AFTER80-PRICE-INTAKE-001' and operations == []


@pytest.mark.parametrize('text', [
    'Не ищи минимум стоимости.', 'Если бы искал минимум стоимости, что было бы?',
    'Пример: «Ищи минимум стоимости». Ничего не меняй.', 'Раньше я говорил: ищи минимум стоимости.',
    'Если будет акция. Ищи минимум стоимости.', 'А можешь искать дешевле? Пока только обсуждаем.',
    'Как ты ищешь минимум стоимости?', 'Уменьши бюджет на 20000.',
    'Ищи не минимум стоимости, а лучший завтрак.', 'Минимальная стоимость уже не нужна.',
])
def test_hypotheses_quotes_history_negation_and_ordinary_questions_do_not_create_a_preference(text):
    assert not explicit_lowest_cost(text)
    assert ground_lowest_cost([], text) == ([], [])


def test_model_proposal_is_not_duplicated_or_mutated():
    ops = [{'type': 'set_preference', 'value': {'key': 'price_priority', 'value': 'lowest_total', 'weight': 0.5}}]
    before = deepcopy(ops)
    grounded, notes = ground_lowest_cost(ops, 'Ищи минимум стоимости.')
    assert grounded == before and ops == before and notes
