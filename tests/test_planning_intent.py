import pytest

from otravel.domain import TripState
from otravel.planning_intent import explicit_planning_wish, initial_planning_question


@pytest.mark.parametrize("text", ["Хочу в тепло осенью, где недорого.", "Хочу отдохнуть. Поможешь?",
    "Хотим поехать в отпуск", "Планирую поездку", "Планируем путешествие", "Нам нужна поездка"])
def test_fresh_explicit_wish_can_ground_a_missing_fact_question(text):
    state = TripState().model_dump(mode="json")
    question = initial_planning_question(state, None, text)
    assert explicit_planning_wish(text)
    assert question["context"]["field"] == "origin.city" and "город" in question["question"].casefold()
    assert state["origin"] is None and state["travelers"] == [] and state["stays"] == []


@pytest.mark.parametrize("text", ["Хочу узнать, где тепло", "Хочу отменить поездку", "Не хочу поездку",
    "А если я захочу в тепло?", "Хочу отдохнуть, если получится", "Хочу отдохнуть без вопросов",
    "Хочу отдохнуть, не задавай вопросов", "Пользователь сказал: хочу в тепло", '«Хочу в тепло»',
    "Хочу туристический справочник", "Где тепло осенью?", "Хочу оплатить поездку"])
def test_information_hypothesis_quotes_and_nonplanning_actions_are_not_intake(text):
    assert not explicit_planning_wish(text)


@pytest.mark.parametrize("kind", ["pending", "origin", "people", "stays"])
def test_established_context_is_not_replaced_by_initial_planning_question(kind):
    state = TripState().model_dump(mode="json")
    pending = {"field": "destination.city"} if kind == "pending" else None
    if kind == "origin": state["origin"] = "Москва"
    if kind == "people": state["travelers"] = [{"id": "adult", "type": "adult"}]
    if kind == "stays": state["stays"] = [{"id": "stay", "city": "Стамбул"}]
    assert initial_planning_question(state, pending, "Хочу отдохнуть") is None
