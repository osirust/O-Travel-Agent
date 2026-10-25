"""Money scalar grounding, not NLU/inference. Decimal and exact minor-unit oracles."""
from copy import deepcopy

import pytest

from otravel.domain import Operation, sample_trip
from otravel.money import ground_budget_operations


def proposed(amount=123, **fields):
    return [{"type": "set_budget", "value": {"amount_minor": amount, "currency": "RUB", **fields}}]


@pytest.mark.parametrize("text,expected", [
    ("Общий бюджет семьсот восемьдесят тысяч рублей", 78_000_000),
    ("Бюджет: семьсотвосемьдесяттысячрублей", 78_000_000),
    ("Бюджет двести пятьдесят тысяч рублей", 25_000_000),
    ("Предел один миллион двести тридцать четыре тысячи пятьсот рублей", 123_450_000),
    ("Лимит два миллиона рублей", 200_000_000),
    ("Потолок полмиллиона рублей", 50_000_000),
    ("Бюджет пол миллиона рублей", 50_000_000),
    ("Бюджет 20тыс рублей", 2_000_000),
    ("Бюджет 20 тыс. руб.", 2_000_000),
    ("Бюджет 20000 рублей", 2_000_000),
    ("Бюджет 20 000,50 рублей", 2_000_050),
    ("Бюджет 20\u00a0000,50 ₽", 2_000_050),
    ("Бюджет 20\u202f000.50 RUB", 2_000_050),
    ("Бюджет 1,5 миллиона рублей", 150_000_000),
    ("Бюджет 20к рублей", 2_000_000),
    ("780000 рублей — потолок бюджета", 78_000_000),
    ("Бюджет не больше 780000 рублей", 78_000_000),
    ("Сократи бюджет до 780000 рублей", 78_000_000),
    ("Уменьши бюджет на 20 тысяч рублей", 78_000_000),
    ("Сократи бюджет на двадцать тысяч", 78_000_000),
    ("Бюджет 20тыс", 2_000_000),
    ("Бюджет 20 000,50", 2_000_050),
    ("Добавь ребёнка 6 лет, бюджет семьсот восемьдесят тысяч рублей, вернись на 2 дня позже", 78_000_000),
    ("Бюджет 780000 рублей, даты 2026-11-10 и 20.11.2026", 78_000_000),
])
def test_exact_russian_and_digit_literals(text, expected):
    state = sample_trip()  # Existing whole-trip RUB budget: 800000 RUB.
    operations = proposed()
    before_state, before_ops = state.model_dump(mode="json"), deepcopy(operations)
    result, notes = ground_budget_operations(state, operations, text)
    assert result[0]["value"]["amount_minor"] == expected
    assert type(result[0]["value"]["amount_minor"]) is int
    assert notes[0]["after"] == expected
    assert state.model_dump(mode="json") == before_state
    assert operations == before_ops


@pytest.mark.parametrize("text", [
    "Бюджет был 780000 рублей", "Вчера бюджет 780000 рублей", "Раньше лимит 780000 рублей",
    "Бюджет от 700 до 800 тысяч рублей", "Бюджет 700 тысяч рублей или 800 тысяч рублей",
    "Бюджет 780000 рублей, отель стоит 20000 рублей", "Бюджет 780000 рублей, перелёт за 20000",
    "Бюджет 780000 рублей и 100 евро", "Бюджет 20000 USD", "Лимит 20000 $",
    "Бюджет 20000 рублей за ночь", "Бюджет 20000 рублей на человека",
    "Уменьши бюджет на 20%", "Если бюджет 780000 рублей, что предложишь?",
    "Не меняй бюджет 780000 рублей", "Например, потолок 780000 рублей",
    "Бюджет неизвестен, отель стоит 20000 рублей", "Отель стоит 780000 рублей",
    "Увеличь бюджет на 20000 рублей", "Бюджет на 20000 рублей меньше",
    "Бюджет 20 00 рублей", "Бюджет сто сто рублей", "Бюджет 1.234 рубля",
])
def test_ambiguous_historical_unit_foreign_or_nonbudget_mentions_skip(text):
    operations = proposed()
    result, notes = ground_budget_operations(sample_trip(), operations, text)
    assert result == operations
    assert notes == []


def test_no_budget_operation_is_created_and_other_operations_stay_unchanged():
    state = sample_trip()
    operations = [{"type": "add_traveler", "value": {"id": "child-new", "type": "child", "age": 6}}]
    assert ground_budget_operations(state, operations, "Бюджет 780000 рублей") == (operations, [])
    with_budget = operations + proposed()
    changed, notes = ground_budget_operations(state, with_budget, "Добавь ребёнка 6 лет; бюджет 780000 рублей")
    assert changed[0] == operations[0]
    assert changed[1]["value"]["amount_minor"] == 78_000_000
    assert len(notes) == 1


def test_multiple_proposed_budget_operations_skip_without_collapsing_them():
    operations = proposed(1) + proposed(2)
    assert ground_budget_operations(sample_trip(), operations, "Бюджет 780000 рублей") == (operations, [])


def test_domain_operations_remain_models_and_are_not_mutated():
    operation = Operation(type="set_budget", value={"amount_minor": 7_800_000, "currency": "RUB"})
    changed, notes = ground_budget_operations(sample_trip(), [operation], "Предел семьсот восемьдесят тысяч рублей")
    assert isinstance(changed[0], Operation)
    assert changed[0].value["amount_minor"] == 78_000_000
    assert operation.value["amount_minor"] == 7_800_000
    assert notes[0]["before"] == 7_800_000


def test_unitless_sum_requires_existing_rub_currency_and_relative_needs_base():
    empty = {"budget": None}
    assert ground_budget_operations(empty, proposed(), "Бюджет 20тыс")[1] == []
    assert ground_budget_operations(empty, proposed(), "Сократи бюджет на 20 тысяч рублей")[1] == []
    assert ground_budget_operations({"budget": {"amount_minor": 100000, "currency": "USD"}}, proposed(), "Бюджет 20000")[1] == []
    changed, _ = ground_budget_operations(empty, proposed(), "Бюджет 20тыс рублей")
    assert changed[0]["value"]["amount_minor"] == 2_000_000


def test_already_correct_scalar_needs_no_repair_and_impossible_relative_is_not_clamped():
    operations = proposed(78_000_000)
    assert ground_budget_operations(sample_trip(), operations, "Бюджет 780000 рублей") == (operations, [])
    changed, notes = ground_budget_operations({"budget": {"amount_minor": 10000, "currency": "RUB"}}, proposed(), "Сократи бюджет на 200 рублей")
    assert changed[0]["value"]["amount_minor"] == -10000
    assert notes[0]["mode"] == "relative_decrease"  # Domain gt=1 rejects it later.


def test_signed_invalid_amount_is_never_repaired_into_a_positive_budget():
    operations = proposed(-20000)
    assert ground_budget_operations(sample_trip(), operations, "Бюджет -200 рублей") == (operations, [])


def test_relative_budget_does_not_mix_whole_trip_and_per_person_bases():
    state = {"budget": {"amount_minor": 1000000, "currency": "RUB", "scope": "per_person"}}
    operations = proposed()
    assert ground_budget_operations(state, operations, "Сократи бюджет на 200 рублей") == (operations, [])
