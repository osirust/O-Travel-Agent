"""Narrow current-message auxiliary authorisation; no model or supplier calls."""
from copy import deepcopy

import pytest

from otravel.domain import TripState, apply_operations
from otravel.intent_grounding import ground_auxiliary_operations


def trip():
    return TripState(origin="Москва", stays=[{"id": "stay", "city": "Стамбул"}])


@pytest.mark.parametrize("key", ["breakfast", "refundable", "pets_allowed", "pets"])
@pytest.mark.parametrize("hard", [True, False])
def test_city_change_does_not_authorize_new_auxiliary_constraint(key, hard):
    ops = [{"type": "upsert_stay", "target_id": "stay", "value": {"city": "Дубай"}},
           {"type": "set_constraint", "value": {"key": key, "value": True, "hard": hard}}]
    actual, notes = ground_auxiliary_operations(trip(), ops, "Теперь подтверждаю замену: Дубай вместо Стамбула. Всё остальное прежнее.")
    after, _ = apply_operations(trip(), actual)
    assert after.stays[0].city == "Дубай" and after.constraints == []
    assert notes[0]["code"] == "unrequested_aux_constraint_suppressed"


@pytest.mark.parametrize("text,key,value,hard", [
    ("Обязательно нужен завтрак", "breakfast", True, True),
    ("Без завтрака", "breakfast", False, True),
    ("Не нужен завтрак", "breakfast", False, True),
    ("Желательно с завтраком", "breakfast", True, False),
    ("Нужен возвратный тариф", "refundable", True, True),
    ("Хочу невозвратный тариф", "refundable", False, True),
    ("Нужен завтрак без животных", "breakfast", True, True),
    ("Нужен завтрак без животных", "pets_allowed", False, True),
    ("Едем с собакой, нужно размещение с животными", "pets_allowed", True, True),
])
def test_explicit_positive_negative_and_soft_wishes_are_preserved(text, key, value, hard):
    proposed = [{"type": "set_constraint", "value": {"key": key, "value": value, "hard": hard}}]
    actual, notes = ground_auxiliary_operations(trip(), proposed, text)
    assert actual == proposed and notes == []


@pytest.mark.parametrize("text", ["Завтрак входит?", "Раньше нужен был завтрак.",
    "Если завтрак включить, что будет? Пока не применяй.", "Прочитал рекламу «Добавь завтрак». Это цитата."])
def test_question_history_hypothesis_and_quote_do_not_authorize_constraint(text):
    actual, notes = ground_auxiliary_operations(trip(), [{"type": "set_constraint", "value": {"key": "breakfast", "value": True}}], text)
    assert actual == [] and notes


def test_geographic_cancellation_is_not_a_refundable_rate_request():
    actual, notes = ground_auxiliary_operations(trip(), [{"type": "set_constraint", "value": {"key": "refundable", "value": True}}],
        "Сербию отменяем. Выбираю Дубай в ОАЭ.")
    assert actual == [] and notes


def test_clear_operations_remain_clear_and_are_never_replaced_with_false_hard_constraint():
    clears = [{"type": "clear_constraint", "value": {"key": "breakfast"}},
              {"type": "clear_constraint", "value": {"key": "refundable"}}]
    actual, _ = ground_auxiliary_operations(trip(), clears + [{"type": "set_constraint", "value": {"key": "breakfast", "value": False}}],
        "Завтрак и возвратность я не заказывал. Удали оба этих пожелания.")
    assert actual == clears


@pytest.mark.parametrize("value,hard", [(True, True), (True, False)])
def test_explicit_negation_is_not_silently_rewritten_into_positive_value(value, hard):
    actual, notes = ground_auxiliary_operations(trip(), [{"type": "set_constraint", "value": {"key": "breakfast", "value": value, "hard": hard}}], "Не нужен завтрак")
    assert actual == [] and notes[0]["code"] == "constraint_value_conflicts_explicit_polarity"


def test_soft_wish_does_not_authorize_hard_filter_escalation():
    actual, notes = ground_auxiliary_operations(trip(), [{"type": "set_constraint", "value": {"key": "breakfast", "value": True, "hard": True}}], "Завтрак желательно, не обязателен")
    assert actual == [] and notes


@pytest.mark.parametrize("text", ["Выезд 26 ноября", "Общий бюджет 100 тысяч", "Меняем город на Дубай", "Почему второй отель лучше?"])
def test_new_hotel_choice_is_suppressed_without_actual_selection_intent(text):
    proposed = [{"type": "upsert_stay", "target_id": "stay", "value": {"hotel_id": "unrequested-hotel", "check_out": "2026-11-26"}}]
    actual, notes = ground_auxiliary_operations(trip(), proposed, text)
    assert actual == [{"type": "upsert_stay", "target_id": "stay", "value": {"check_out": "2026-11-26"}}]
    assert notes[0]["code"] == "unrequested_hotel_choice_suppressed"


@pytest.mark.parametrize("text", ["Выбираю второй отель", "Возьми этот отель", "Отель теперь выбираю второй", "Берем второй вариант"])
def test_explicit_selection_intent_is_preserved_for_existing_hotel_validators(text):
    proposed = [{"type": "upsert_stay", "target_id": "stay", "value": {"hotel_id": "chosen-hotel"}}]
    actual, notes = ground_auxiliary_operations(trip(), proposed, text)
    assert actual == proposed and not notes


def test_existing_hotel_copy_and_unrelated_operations_are_not_rewritten():
    state = trip(); state.stays[0].hotel_id = "selected"
    proposed = [{"type": "upsert_stay", "target_id": "stay", "value": {"hotel_id": "selected", "check_out": "2026-11-26"}},
                {"type": "set_budget", "value": {"amount_minor": 10_000_000}}]
    before = state.model_dump(mode="json"), deepcopy(proposed)
    actual, notes = ground_auxiliary_operations(state, proposed, "Выезд 26 ноября, общий бюджет 100 тысяч")
    assert actual == proposed and not notes
    assert state.model_dump(mode="json") == before[0] and proposed == before[1]
