"""Captured FINAL40 budget ambiguity and unpriced whole-trip costs."""
from copy import deepcopy

import pytest

from otravel.money import ground_budget_operations
from otravel.budget_requirements import guard_budget_requirements
from otravel.domain import Constraint, Operation, apply_operations, sample_trip
from otravel.inventory import DemoInventory


def op(amount):
    return {'type': 'set_budget', 'target_id': None, 'value': {
        'scope': 'whole_trip', 'currency': 'RUB', 'amount_minor': amount}}


def state(amount=None):
    return {'budget': None if amount is None else {
        'scope': 'whole_trip', 'currency': 'RUB', 'amount_minor': amount}}


def test_exact_final40_020_reserve_does_not_become_1200_rubles():
    text = 'Хочу уложиться в 100 тысяч с запасом на непредвиденные траты 20%.'
    original = [op(120000)]
    untouched = deepcopy(original)
    operations, notes = ground_budget_operations(state(), original, text)
    assert original == untouched
    # 100k plus an unspecified reserve is ambiguous, rather than an authority
    # to create an unrelated 1,200-ruble whole-trip limit.
    assert not any(item['type'] == 'set_budget' for item in operations)
    assert any(note.get('status') == 'clarification_required' for note in notes)


@pytest.mark.parametrize('text', [
    'Бюджет 100000 рублей с резервом 20% на непредвиденные расходы.',
    'Хочу уложиться в сто тысяч рублей, оставив запас двадцать процентов.',
])
def test_positive_reserve_declaration_requires_a_spendable_limit(text):
    operations, notes = ground_budget_operations(state(), [op(120000)], text)
    assert not any(item['type'] == 'set_budget' for item in operations)
    assert any(note.get('status') == 'clarification_required' for note in notes)


@pytest.mark.parametrize(('text', 'before', 'expected'), [
    ('Уменьши общий бюджет на20процентов.', 15000000, 12000000),
    ('Увеличь общий бюджет на 20 процентов.', 10000000, 12000000),
    ('Общий бюджет 100000 рублей.', None, 10000000),
])
def test_existing_unambiguous_arithmetic_and_literal_controls(text, before, expected):
    operations, _ = ground_budget_operations(state(before), [op(120000)], text)
    assert operations[0]['value']['amount_minor'] == expected


def test_exact_food_and_baggage_scope_is_not_a_silently_complete_priced_budget():
    text = '120 тысяч на всех, включая дорогу туда-обратно, отель, трансфер, еду и багаж.'
    operations, notes = ground_budget_operations(state(), [op(12000000)], text)
    # Safe implementations may require clarification, or retain a canonical
    # hard unpriced-cost condition which the existing assembler must block.
    clarified = any(note.get('status') == 'clarification_required' for note in notes)
    retained = any(item.get('type') == 'set_constraint' and item.get('value', {}).get('hard') is True
                   and item.get('value', {}).get('key') in {'unpriced_food_costs', 'unpriced_baggage_costs'}
                   for item in operations)
    assert clarified or retained


@pytest.mark.parametrize('text', [
    'Если бюджет 100 тысяч с резервом 20%, что изменится?',
    'Раньше бюджет был 100 тысяч с запасом 20%.',
    'Например, бюджет 100 тысяч с резервом 20%.',
    '«Бюджет 100 тысяч с резервом 20%» — это цитата.',
    '`Бюджет 100 тысяч с резервом 20%`',
    'Бюджет 100 тысяч с резервом 20%?',
    'Если бюджет 120 тысяч на всех, включая еду и багаж, получится?',
])
def test_nonasserted_cost_discussion_does_not_create_server_requirements(text):
    raw = [op(10_000_000)]
    checked, notes = guard_budget_requirements(state(), raw, text)
    assert checked == raw and notes == []


@pytest.mark.parametrize('mutation', [
    {'type': 'clear_constraint', 'value': {'key': 'unpriced_food_costs'}},
    {'type': 'set_constraint', 'value': {'key': 'unpriced_food_costs', 'value': False}},
    {'type': 'set_constraint', 'value': {'key': 'unpriced_food_costs', 'value': True, 'hard': False}},
])
def test_model_cannot_clear_unpriced_cost_without_current_waiver(mutation):
    trip = sample_trip()
    trip.constraints.append(Constraint(key='unpriced_food_costs', value=True, hard=True))
    checked, notes = guard_budget_requirements(trip, [mutation], 'Продолжим собирать поездку.')
    assert checked == []
    assert any(n['code'] == 'unpriced_cost_clear_not_authorized' for n in notes)


@pytest.mark.parametrize('text', [
    'Если исключим еду, пока подожди.',
    '«Еду оплачиваю отдельно» — это цитата.',
    'Раньше еду оплачивали отдельно.',
    'Без учета питания?',
    'Еду отдельно не считай пока, я ещё думаю.',
])
def test_hypothetical_quoted_or_question_cost_waiver_cannot_remove_saved_hard_condition(text):
    trip = sample_trip()
    trip.constraints.append(Constraint(key='unpriced_food_costs', value=True, hard=True))
    raw = [{'type': 'clear_constraint', 'value': {'key': 'unpriced_food_costs'}}]
    checked, notes = guard_budget_requirements(trip, raw, text)
    assert checked == []
    assert any(n['code'] == 'unpriced_cost_clear_not_authorized' for n in notes)


@pytest.mark.parametrize('text', ['Еду оплачиваю отдельно.', 'Без учета питания.', 'Не учитывай стоимость еды.'])
def test_current_explicit_waiver_clears_only_the_named_cost(text):
    trip = sample_trip()
    trip.constraints.extend([Constraint(key=key, value=True, hard=True)
                             for key in ('unpriced_food_costs', 'unpriced_baggage_costs')])
    checked, _ = guard_budget_requirements(trip, [], text)
    updated, _ = apply_operations(trip, [Operation.model_validate(x) for x in checked])
    keys = {c.key for c in updated.constraints}
    assert 'unpriced_food_costs' not in keys and 'unpriced_baggage_costs' in keys


def test_operation_models_are_preserved_and_unpriced_whole_cost_blocks_quote():
    trip = sample_trip()
    raw = [Operation.model_validate(op(12_000_000))]
    checked, notes = ground_budget_operations(trip, raw,
        'Общий бюджет 120 тысяч на всех, включая еду и багаж.')
    assert checked and all(isinstance(x, Operation) for x in checked)
    updated, _ = apply_operations(trip, checked)
    assert {'unpriced_food_costs', 'unpriced_baggage_costs'} <= {c.key for c in updated.constraints}
    result = DemoInventory().search_trip(updated)
    assert result['plans'] == []
    assert any(n['code'] == 'unpriced_cost_scope_retained' for n in notes)
