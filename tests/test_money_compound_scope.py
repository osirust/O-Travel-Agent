"""CF10-10: a prohibition for other fields does not cancel a budget command."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from otravel.money import ground_budget_operations

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/regression/after80-cf-native-6009644.json"


def proposed(amount=60_000_000):
    return [{"type": "set_budget", "value": {"amount_minor": amount, "currency": "RUB", "scope": "whole_trip"}}]


def test_actual_cf10_compound_budget_is_78m_and_every_other_recorded_operation_is_preserved():
    source = next(case for case in json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"] if case["id"] == "CF10-10")
    state, operations = source["before_state"], source["raw"]["operations"]
    original = deepcopy((state, operations))
    result, notes = ground_budget_operations(state, operations, source["user"])
    assert next(op for op in result if op["type"] == "set_budget")["value"]["amount_minor"] == 78_000_000
    assert [op for op in result if op["type"] != "set_budget"] == [op for op in operations if op["type"] != "set_budget"]
    assert notes[0]["mode"] == "relative_decrease" and notes[0]["after"] == 78_000_000
    assert (state, operations) == original


@pytest.mark.parametrize("text,expected", [
    ("Сократи общий бюджет на 20000 рублей. Отели и всех участников не меняй.", 78_000_000),
    ("Сократи общий бюджет на 20000 рублей, первое проживание не меняй.", 78_000_000),
    ("Первое проживание не меняй, сократи общий бюджет на 20000 рублей.", 78_000_000),
    ("Установи общий бюджет 780000 рублей. Остальные данные не меняй.", 78_000_000),
    ("Сократи общий бюджет на 20000 рублей. В прошлом году мы жили в другом отеле.", 78_000_000),
    ("Сократи общий бюджет на 20000 рублей, проживание от 6 до 9 ноября не меняй.", 78_000_000),
])
def test_unrelated_negation_history_and_date_range_do_not_cancel_current_budget_intent(text, expected):
    result, _ = ground_budget_operations({"budget": {"amount_minor": 80_000_000, "currency": "RUB", "scope": "whole_trip"}}, proposed(), text)
    assert result == proposed(expected)


@pytest.mark.parametrize("text", [
    "Бюджет не меняй, добавь ребёнка пяти лет.",
    "Не уменьшай общий бюджет на 20000 рублей. Отели поменять можно.",
    "В прошлом году бюджет был 780000 рублей. В этом году отель другой.",
    "Общий бюджет от 700000 до 800000 рублей. Других требований пока нет.",
    "Если общий бюджет 780000 рублей, что изменится?",
])
def test_actual_budget_negation_history_hypothesis_or_range_still_skip_scalar_grounding(text):
    operations = proposed(80_000_000)
    result, notes = ground_budget_operations({"budget": {"amount_minor": 80_000_000, "currency": "RUB", "scope": "whole_trip"}}, operations, text)
    assert result == operations and notes == []
