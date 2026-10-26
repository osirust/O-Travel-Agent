"""Bounded explicit prohibitions, not a claim about actual model NLU."""
from copy import deepcopy

import pytest

from otravel.budget_intent import guard_budget_prohibitions
from otravel.domain import Operation


def operations():
    return [{"type": "set_budget", "value": {"amount_minor": 60_000_000, "currency": "RUB"}},
        {"type": "add_traveler", "value": {"id": "new-child", "type": "child", "age": 5}},
        {"type": "set_origin", "value": {"city": "Москва"}}]


@pytest.mark.parametrize("text", [
    "Бюджет не меняй, добавь ребёнка пяти лет.",
    "Не трогай общий бюджет. Добавь ребёнка пяти лет.",
    "Оставь прежний бюджет и поменяй город отправления на Москву.",
    "Общий бюджет оставь прежним, ребёнку пять лет.",
    "Сохрани бюджет без изменений.",
    "Не уменьшай мой бюджет на 20000 рублей.",
    "Бюджет не изменяйте, пожалуйста.",
    "Не трогай сейчас общий бюджет.",
])
def test_only_proposed_budget_ops_are_suppressed_with_no_amount_permission(text):
    original = operations()
    saved = deepcopy(original)
    actual, meta = guard_budget_prohibitions(original, text)
    assert actual == original[1:] and original == saved
    assert meta["conflicts"] == []
    assert meta["notes"] == [{"code": "budget_change_prohibited", "status": "suppressed",
        "operation": "set_budget", "suppressed_count": 1}]


@pytest.mark.parametrize("text", [
    "Сократи общий бюджет на 20000 рублей. Первое проживание и участников не меняй.",
    "Оставь прежний отель, а бюджет сократи на 20000 рублей.",
    'Он написал: «бюджет не меняй». Добавь ребёнка пяти лет.',
    'Команда "не трогай бюджет" приведена как пример.',
    "Если бюджет не менять, какие будут варианты?",
    "Если я скажу бюджет не меняй, сохранишь отели?",
    "Допустим, я попрошу: бюджет не меняй.",
    "Вчера просил: бюджет не меняй.",
    "Раньше бюджет не меняй говорил я. Сейчас сократи бюджет на 20000 рублей.",
    "Можно сказать бюджет не меняй?",
    "> Бюджет не меняй.\nДобавь ребёнка пяти лет.",
])
def test_unrelated_quotes_history_hypotheses_or_questions_are_not_authoritative(text):
    original = operations()
    actual, meta = guard_budget_prohibitions(original, text)
    assert actual == original and meta == {"notes": [], "conflicts": []}


@pytest.mark.parametrize("text", [
    "Бюджет не меняй и сократи общий бюджет на 20000 рублей. Добавь ребёнка.",
    "Установи бюджет 780000 рублей, но не трогай бюджет. Добавь ребёнка пяти лет.",
    "Не меняй бюджет. Бюджет увеличь на 5000 рублей.",
    "Подтверждаю новый бюджет 780000 рублей. Оставь бюджет прежним.",
])
def test_current_opposing_budget_commands_block_the_whole_batch(text):
    original = operations()
    actual, meta = guard_budget_prohibitions(original, text)
    assert actual == [] and len(meta["conflicts"]) == 1
    assert "изменить" in meta["conflicts"][0] and "сохранить" in meta["conflicts"][0]
    assert meta["notes"][0]["code"] == "budget_instruction_conflict"


def test_prohibition_does_not_require_or_invent_a_model_budget_operation():
    other = operations()[1:]
    actual, meta = guard_budget_prohibitions(other, "Бюджет не меняй, добавь ребёнка пяти лет.")
    assert actual == other and meta == {"notes": [], "conflicts": []}
    actual, meta = guard_budget_prohibitions(other, "Бюджет не меняй и установи бюджет 800000 рублей.")
    assert actual == [] and meta["conflicts"]


def test_model_operation_objects_duplicates_and_inputs_remain_unchanged():
    original = [Operation.model_validate(op) for op in operations()]
    original.insert(1, original[0].model_copy(deep=True))
    saved = deepcopy(original)
    actual, meta = guard_budget_prohibitions(original, "Бюджет не трогай, добавь ребёнка пяти лет.")
    assert actual == original[2:] and all(isinstance(op, Operation) for op in actual)
    assert original == saved and actual[0] is not original[2]
    assert meta["notes"][0]["suppressed_count"] == 2
