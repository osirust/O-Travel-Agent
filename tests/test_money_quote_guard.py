"""AFTER80-MONEY-QUOTE-001: transaction/offer price is not a budget command.

Fact: pilot-f52b622/002:T10 changed130000RUB to the accepted quote's100750RUB.
Action: suppress only unrequested budget intents; keep explicit budget clauses.
Close: exact raw controlled replay preserves state; real API replay is separate.
"""
from copy import deepcopy

import pytest

from otravel.domain import Operation
from otravel.money import ground_budget_operations

PILOT_TEXT = "Предварительную DEMO-корзину проверил. Подтверждаю только тестовое оформление первого варианта за 100750 рублей, настоящую бронь не создавай."


def budget(amount=13_000_000):
    return {"budget": {"amount_minor": amount, "currency": "RUB", "scope": "whole_trip"}}


def proposed(amount=10_075_000, currency="RUB"):
    return [{"type": "set_budget", "value": {"amount_minor": amount, "currency": currency, "scope": "whole_trip"}}]


@pytest.mark.parametrize("text", [
    PILOT_TEXT,
    "Подтверждаю оплату первого варианта за 100750 рублей.",
    "Цена первого варианта 100750 рублей. Бюджет не меняй.",
    "Текущая стоимость выбранного предложения 100750 рублей.",
    "Счёт к оплате 100750 рублей, подтверждаю.",
    "Внесу предоплату 10000 рублей за отель, остаток позже.",
    "Депозит 3000 рублей внесён.",
    "Вернуть залог 5000 рублей, бюджет 130000 оставь.",
    "Цена выбранного тарифа 100к рублей.",
    "Оплату варианта за 100750 рублей подтверждаю, в рамках прежнего бюджета 130000 рублей.",
])
def test_quote_payment_or_deposit_amount_cannot_replace_budget(text):
    state, operations = budget(), proposed()
    untouched = deepcopy((state, operations))
    result, notes = ground_budget_operations(state, operations, text)
    assert result == []
    assert notes[0]["operation_suppressed"] is True and notes[0]["status"] == "no_op"
    assert notes[0]["finding_id"] == "AFTER80-MONEY-QUOTE-001"
    assert (state, operations) == untouched


def test_nonbudget_payment_is_suppressed_for_foreign_currency_too_without_conversion():
    result, _ = ground_budget_operations(budget(), proposed(100_000, "EUR"), "Оплачиваю первый вариант за 1000 EUR.")
    assert result == []


@pytest.mark.parametrize("text,expected", [
    ("Подтверждаю общий бюджет 100750 рублей.", 10_075_000),
    ("Подтверждаю бюджет 150000 рублей, оформление первого варианта ещё не подтверждаю.", 15_000_000),
    ("Стоимость первого варианта 100750 рублей. Установи общий бюджет 150000 рублей.", 15_000_000),
    ("Увеличь общий бюджет на 5000 рублей. Оформление варианта за 100750 рублей не подтверждаю.", 13_500_000),
    ("Новый общий лимит 150000 рублей. Предоплату 5000 рублей не вноси.", 15_000_000),
    ("На всё 150к рублей. Первый вариант стоит 100750 рублей.", 15_000_000),
])
def test_explicit_budget_intent_is_not_blocked_by_confirmation_or_other_transaction(text, expected):
    result, _ = ground_budget_operations(budget(), proposed(123), text)
    assert result[0]["value"]["amount_minor"] == expected


def test_price_guard_keeps_other_operation_models_and_suppresses_duplicate_bad_budgets():
    origin = Operation(type="set_origin", value={"city": "Сочи"})
    bad = Operation(type="set_budget", value={"amount_minor": 10_075_000, "currency": "RUB"})
    operations = [origin, bad, deepcopy(bad)]
    original = deepcopy(operations)
    result, notes = ground_budget_operations(budget(), operations, PILOT_TEXT)
    assert result == [origin] and isinstance(result[0], Operation)
    assert notes[0]["operation_suppressed"] is True and operations == original


def test_price_guard_does_not_create_budget_or_alter_other_intents_without_one():
    origin = Operation(type="set_origin", value={"city": "Сочи"})
    assert ground_budget_operations(budget(), [origin], PILOT_TEXT) == ([origin], [])
