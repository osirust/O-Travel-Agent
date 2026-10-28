import json
from copy import deepcopy
from pathlib import Path

import pytest

from otravel.domain import Operation, TripState, apply_operations
from otravel.traveler_removal_grounding import FINDING_ID, guard_traveler_removal_operations


def state():
    return TripState(trip_id="remove-trip", travelers=[
        {"id": "adult-1", "type": "adult"}, {"id": "adult-2", "type": "adult"},
        {"id": "child-1", "type": "child", "age": 7}])


def proposals():
    return [{"type": "set_budget", "target_id": None,
        "value": {"amount_minor": 8500000, "currency": "RUB", "scope": "whole_trip"}},
        {"type": "remove_traveler", "target_id": "adult-2", "value": {}}]


@pytest.mark.parametrize("text", [
    "Обший бюджет теперь 85000 рублей. Кто в итоге едет?",
    "Кто в итоге едет? Ничего не меняй.",
    "Если сестра не поедет, сколько будет стоить поездка?",
    "А если убрать ребёнка? Пока не меняй состав.",
    "Раньше просил убрать второго взрослого. Сейчас поменяй бюджет.",
    "Думал убрать ребёнка. Теперь поменяй только бюджет.",
    "Не удаляй второго взрослого.",
    "Не нужно убирать ребёнка.",
    "Не убирай ребёнка, состав оставь прежним.",
    "Убери пожелание о детях.",
    "Убери гостиницу.",
    "Убери детали маршрута.",
    "Удали ребёнка из номера.",
    "Убери adult-2 из группы.",
    "Цитирую: «убери второго взрослого». Только вопрос.",
    "Убери второго взрослого?",
    "If my sister is not coming, what is the price?",
    "Do not remove the child.",
])
def test_absent_positive_current_removal_suppresses_only_remove(text):
    before, ops = state(), proposals()
    original = deepcopy(ops)
    checked, meta = guard_traveler_removal_operations(before, ops, text)
    assert checked == [original[0]]
    assert ops == original
    assert len(before.travelers) == 3
    assert meta["notes"][0]["finding_id"] == FINDING_ID
    assert not meta["unknowns"] and not meta["conflicts"]


@pytest.mark.parametrize("text", [
    "Убери второго взрослого из поездки.",
    "Удали adult-2.",
    "Исключи сестру из состава поездки.",
    "Ребёнок не едет.",
    "Сестра не поедет.",
    "Он больше не поедет с нами.",
    "Детей не будет.",
    "Убери его.",
    "Remove the second adult.",
    "My sister is not coming.",
    "Раньше хотела убрать ребёнка, но теперь исключи второго взрослого.",
])
def test_positive_current_removal_is_left_for_existing_identity_resolvers(text):
    ops = proposals()
    checked, meta = guard_traveler_removal_operations(state(), ops, text)
    assert checked == ops
    assert not meta["notes"]


def test_budget_and_domain_membership_preserved_when_remove_is_suppressed():
    before = state()
    checked, _ = guard_traveler_removal_operations(before, proposals(), "Общий бюджет85000 рублей. Кто едет?")
    after, _ = apply_operations(before, [Operation.model_validate(op) for op in checked])
    assert after.budget.amount_minor == 8500000
    assert after.travelers == before.travelers


def test_cached_actual_008_raw_and_checked_keep_all_other_operations_exact():
    path = Path(__file__).parents[1] / "fixtures/regression/traveler-removal-008-20261027.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    before = TripState.model_validate(fixture["before_trip"])
    for key in ("raw_contract", "checked_contract"):
        original = fixture[key]["value"]["operations"]
        checked, meta = guard_traveler_removal_operations(before, original, fixture["input"])
        assert checked == [op for op in original if op["type"] != "remove_traveler"]
        assert len(meta["notes"]) == 1
    checked, _ = guard_traveler_removal_operations(before, fixture["checked_contract"]["value"]["operations"], fixture["input"])
    after, _ = apply_operations(before, [Operation.model_validate(op) for op in checked])
    assert len(after.travelers) == 2
    assert after.stays[0].rooms == before.stays[0].rooms
    assert after.budget.amount_minor == 8500000
