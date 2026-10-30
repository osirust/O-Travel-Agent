import json
from copy import deepcopy
from pathlib import Path

import pytest

from otravel.adult_age_grounding import FINDING_ID, guard_adult_age_operations
from otravel.domain import Operation, TripState, apply_operations


def adult(identifier="new-traveler-1", age=30):
    return {"type": "add_traveler", "target_id": None, "value": {"id": identifier, "type": "adult", "age": age}}


@pytest.mark.parametrize("text", [
    "Два взрослых и ребёнок 7 лет из Москвы в Стамбул.",
    "Один взрослый, без детей. Хочу отель на неделю.",
    "Два взрослых и ребёнок 18 лет.",
    'Цитирую: «мне 30 лет». Сейчас едет один взрослый.',
    "Если мне будет 30 лет, сможешь добавить взрослого?",
    "Раньше писал: мне 30 лет. Сейчас добавь взрослого.",
    "Мне не 30 лет. Добавь взрослого.",
    "Мне 30 лет?",
    "The hotel is 30 years old. Add two adults.",
    "If I am 30 years old, add an adult.",
    "Когда мне было 30 лет, я ездил в Стамбул. Сейчас добавь взрослого.",
    "Мне было 30 лет, когда я ездил в Стамбул. Добавь взрослого.",
    "I was 30 years old when I visited Istanbul. Add an adult.",
])
def test_unasked_adult_age_is_removed_without_losing_the_adult_or_other_ops(text):
    state = TripState(trip_id="trip-age")
    operations = [adult(), {"type": "add_traveler", "target_id": None,
        "value": {"id": "new-traveler-2", "type": "child", "age": 7}}]
    original = deepcopy(operations)
    checked, meta = guard_adult_age_operations(state, operations, text)
    assert "age" not in checked[0]["value"]
    assert checked[1] == original[1]
    assert operations == original
    assert state.travelers == []
    assert meta["notes"][0]["finding_id"] == FINDING_ID
    after, _ = apply_operations(state, [Operation.model_validate(row) for row in checked])
    assert after.travelers[0].type == "adult" and after.travelers[0].age is None
    assert after.travelers[1].age == 7


@pytest.mark.parametrize("text", [
    "Мне 30 лет, жене 32 года. Едем с ребёнком 7 лет.",
    "Мне 30. Один взрослый.",
    "Взрослому 30 лет.",
    "My wife is 32 years old.",
    "I am 30 years old.",
    "Я родился 1996-03-02, еду один.",
    "Мне: дата рождения 02.03.1996.",
    "Ребёнок родился 2019-01-01. Муж родился 1990-04-20.",
    "Дата рождения взрослого 1990-04-20.",
    "I was born 1990-04-20.",
    "My birth date is 1990-04-20.",
])
def test_explicit_current_adult_age_or_dob_is_retained_without_person_attribution_claim(text):
    operations = [adult()]
    checked, meta = guard_adult_age_operations(TripState(trip_id="trip-age"), operations, text)
    assert checked == operations
    assert meta == {"notes": [], "unknowns": [], "conflicts": []}


def test_existing_adult_update_and_child_are_untouched():
    state = TripState(trip_id="trip-age", travelers=[{"id": "existing-adult", "type": "adult", "age": 45}])
    operations = [adult("existing-adult", 45),
        {"type": "update_traveler", "target_id": "existing-adult", "value": {"age": 46}},
        {"type": "add_traveler", "target_id": None, "value": {"id": "child", "type": "child", "age": 7}}]
    checked, meta = guard_adult_age_operations(state, operations, "Добавь ребёнка семи лет.")
    assert checked == operations
    assert state.travelers[0].age == 45
    assert not meta["notes"]


@pytest.mark.parametrize("age", [None, 17, "30", 30.0, True])
def test_no_cast_or_repair_of_other_age_shapes(age):
    operations = [adult(age=age)]
    checked, meta = guard_adult_age_operations(TripState(trip_id="trip-age"), operations, "Два взрослых.")
    assert checked == operations
    assert not meta["notes"]


def test_cached_actual_complex_raw_batch_removes_only_the_two_invented_adult_ages():
    fixture = json.loads((Path(__file__).parents[1] / "fixtures/regression/adult-age-complex-r1-20261027.json")
        .read_text(encoding="utf-8"))
    raw_ops = fixture["raw_contract"]["value"]["operations"]
    assert [op["value"]["age"] for op in raw_ops if op["type"] == "add_traveler"
        and op["value"]["type"] == "adult"] == [30, 30]
    state = TripState.model_validate(fixture["before_trip"])
    checked, meta = guard_adult_age_operations(state, raw_ops, fixture["input"])
    expected = deepcopy(raw_ops)
    for op in expected:
        if op["type"] == "add_traveler" and op["value"]["type"] == "adult":
            del op["value"]["age"]
    assert checked == expected
    assert len(meta["notes"]) == 2
    after, _ = apply_operations(state, [Operation.model_validate(row) for row in checked])
    assert [person.age for person in after.travelers] == [None, None, 7]
    assert [stay.city for stay in after.stays] == ["Стамбул", "Анталья"]
    assert sum(len(stay.rooms) for stay in after.stays) == 2
    assert after.budget.amount_minor == 120000000
