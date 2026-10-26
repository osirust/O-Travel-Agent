"""After80 monetary regressions from recorded before-states; no model/API.

These are scalar/domain oracles for an already proposed intent. They do not
claim to replay original NLU, and never manufacture a set_budget operation.
"""
from copy import deepcopy

import pytest

from otravel.domain import Operation
from otravel.money import ground_budget_operations


def state(amount, currency="RUB"):
    return {"budget": None if amount is None else {
        "amount_minor": amount, "currency": currency, "scope": "whole_trip"}}


def proposed(amount, currency="RUB"):
    return [{"type": "set_budget", "value": {
        "amount_minor": amount, "currency": currency, "scope": "whole_trip"}}]


@pytest.mark.parametrize("ref,text,before,wrong,expected", [
    ("003:T6", "Поставь общий бюджет 75000 рублей на двоих. Это сумма всей поездки, не за ночь и не на человека.", 9_000_000, 9_000_000, 7_500_000),
    ("014:T5", "One room for me. Total budget 95000 RUB for the whole trip, не на человека.", None, 123, 9_500_000),
    ("062:T5", "Добавь к общему бюджету 5000 рублей, остальные условия оставь как есть.", 13_000_000, 13_050_000, 13_500_000),
    ("063:T3", "Добавь к общему бюджету ещё 5000 рублей. Только не умножай на дни.", 3_000_000, 3_050_000, 3_500_000),
    ("067:T7", "Уменьши общий бюджет на 10 процентов, все люди и даты прежние.", 8_000_000, 7_999_000, 7_200_000),
    ("072:T2", "Уменьши общий бюджет на десять процентов, все люди и даты прежние.", 10_000_000, 9_999_000, 9_000_000),
    ("037:T6", "На всё 145 тыщ рублей. А Стамбул далеко? Пока только спрашиваю, второй отель не добавляй и Анталью не отменяй.", None, 145_000_000, 14_500_000),
    ("037:T7", "Нет, бюджет сто сорок пять тысяч рублей, 145000 ₽ на всех. Не миллион четыреста пятьдесят. И выезд сократи до 20 ноября, остальные условия те же.", 145_000_000, 145_000_000, 14_500_000),
    ("054:T6", "Общий лимит поставь точно 110000 рублей 50 копеек, не округляй.", 11_000_000, 11_000_000, 11_000_050),
    ("053:T7", "Уменьши общий бьюджет на десять тысяч рублей, даты и людей оставь.", 9_000_000, 8_900_000, 8_000_000),
])
def test_recorded_money_scalar_oracles(ref, text, before, wrong, expected):
    canonical, operations = state(before), proposed(wrong)
    untouched = deepcopy((canonical, operations))
    result, notes = ground_budget_operations(canonical, operations, text)
    assert result[0]["value"]["amount_minor"] == expected, ref
    assert type(result[0]["value"]["amount_minor"]) is int
    assert notes and notes[0]["after"] == expected
    assert (canonical, operations) == untouched


@pytest.mark.parametrize("text", [
    "Общий бюджет 110000.50 рублей", "Общий бюджет 110000,50 RUB",
    "Общий бюджет сто десять тысяч рублей пятьдесят копеек",
    "Общий бюджет 110000 рублей и 50 копеек",
])
def test_exact_minor_units_have_equivalent_decimal_and_separate_forms(text):
    result, _ = ground_budget_operations(state(11_000_000), proposed(11_000_000), text)
    assert result == proposed(11_000_050)


@pytest.mark.parametrize("text,expected", [
    ("Уменьши общий бюджет на 20%", 8_000_000),
    ("Увеличь общий бюджет на 12,5 процента", 11_250_000),
    ("Увеличь общий бюджет на десять процентов", 11_000_000),
    ("Прибавь к общему бюджету 5000 рублей", 10_500_000),
    ("Сократи общий бюджет на 5000 рублей", 9_500_000),
])
def test_explicit_relative_changes_use_exact_canonical_base(text, expected):
    result, notes = ground_budget_operations(state(10_000_000), proposed(123), text)
    assert result == proposed(expected)
    assert notes[0]["mode"].startswith("relative_")


def test_retained_previous_increment_is_not_applied_again_and_other_edit_survives():
    # 062:T8 before135000. The previous5000 is a retained historical delta.
    origin = Operation(type="set_origin", value={"city": "Сочи"})
    bad = Operation(type="set_budget", value={"amount_minor": 14_000_000, "currency": "RUB"})
    original = deepcopy((origin, bad))
    result, notes = ground_budget_operations(state(13_500_000), [origin, bad],
        "Верни город отправления как в начале, но прибавку 5000 к бюджету оставь. Остальное не меняй.")
    assert result == [origin]
    assert notes[0]["status"] == "no_op" and notes[0]["operation_suppressed"] is True
    assert (origin, bad) == original


def test_explicit_budget_restore_is_not_suppressed_by_a_separate_retention_phrase():
    result, _ = ground_budget_operations(state(14_000_000), proposed(123),
        "Верни прежний общий бюджет 135000 рублей, но прибавку 5000 оставь.")
    assert result == proposed(13_500_000)


@pytest.mark.parametrize("text", [
    "Теперь устанавливаю общий бюджет 2000 USD на всю поездку. Это новая сумма в долларах, рубли по неизвестному курсу не пересчитывай.",
    "Общий бюджет — две тысячи долларов США.",
])
def test_explicit_already_proposed_usd_is_scaled_without_conversion(text):
    # 066:T3/T4: old model proposed200000000 instead of200000 minor USD.
    result, notes = ground_budget_operations(state(10_000_000), proposed(200_000_000, "USD"), text)
    assert result == proposed(200_000, "USD")
    assert notes[0]["currency"] == "USD" and notes[0]["after"] == 200_000


@pytest.mark.parametrize("text", [
    "Если уменьшить общий бюджет на 10%, что изменится?",
    "Скидка на отель 10 процентов. Бюджет не меняй.",
    "Не уменьшай бюджет на 20 процентов.",
    "На ребёнка скидка 10%, общий бюджет 100000 рублей.",
])
def test_other_subject_hypothesis_or_negation_does_not_ground_percent(text):
    operations = proposed(123)
    assert ground_budget_operations(state(10_000_000), operations, text) == (operations, [])


@pytest.mark.parametrize("before,text", [
    (101, "Уменьши общий бюджет на 10 процентов"),
    (10_000_000, "Уменьши общий бюджет на 100 процентов"),
    (None, "Уменьши общий бюджет на 10 процентов"),
    (10_000_000, "Уменьши общий бюджет на -10 процентов"),
    (10_000_000, "Уменьши общий бюджет на 10%, новый бюджет 80000 рублей"),
])
def test_fractional_minor_units_zero_budget_or_missing_percent_base_ask_without_guessing(before, text):
    result, notes = ground_budget_operations(state(before), proposed(123), text)
    assert result == []
    assert notes[0]["status"] == "clarification_required"
    assert isinstance(notes[0]["question"], str) and notes[0]["question"].endswith("?")


def test_real_nightly_scope_is_still_suppressed_and_does_not_create_new_intents():
    text = "За гостинницу хочу не больше пяти тысяч за ночь. Это точно не замена общего бюджета; если такой лимит не умеешь, скажи прямо."
    operations = proposed(500_000)
    result, notes = ground_budget_operations(state(7_500_000), operations, text)
    assert result == [] and notes[0]["status"] == "clarification_required"
    assert ground_budget_operations(state(7_500_000), [], text) == ([], [])
