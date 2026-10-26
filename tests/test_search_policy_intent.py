"""Small observed permission surface; no model or supplier requests."""
from copy import deepcopy

import pytest

from otravel.domain import Operation
from otravel.search_policy_intent import guard_search_policy_operations

STATE = {"search_policy": {"mode": "HYBRID", "hotel_provider": "liteapi", "flight_provider": "duffel", "demo_fallback": True, "guest_nationality": "RU"}}


def policy(**value):
    return {"type": "set_search_policy", "value": value}


@pytest.mark.parametrize("text", [
    "Откуда цены в первом плане: настоящие или демонстрационные? Как перейти только к тестовому оформлению?",
    "Как включить DEMO?",
    "Если включи DEMO, что изменится?",
    'Он написал «включи DEMO». Покажи состав.',
    "Вчера просил: включи DEMO.",
    "Не включай DEMO.",
])
def test_capability_questions_history_quotes_and_negation_do_not_change_sources(text):
    original = [policy(mode="DEMO", hotel_provider="liteapi", flight_provider="serpapi", demo_fallback=False)]
    saved = deepcopy(original)
    actual, meta = guard_search_policy_operations(STATE, original, text)
    assert actual == [] and original == saved
    assert meta["notes"][0]["fields"] == ["mode", "flight_provider", "demo_fallback"]


@pytest.mark.parametrize("text,prior,proposed", [
    ("Включи DEMO.", STATE, {"mode": "DEMO"}),
    ("Переключи режим на HYBRID.", {"search_policy": {"mode": "DEMO"}}, {"mode": "HYBRID"}),
    ("Хочу режим DEMO.", STATE, {"mode": "DEMO"}),
    ("Ищи без демо.", STATE, {"demo_fallback": False}),
    ("Для отелей используй SerpApi.", STATE, {"hotel_provider": "serpapi"}),
    ("Для перелётов используй SerpApi.", STATE, {"flight_provider": "serpapi"}),
    ("Отключи fallback.", STATE, {"demo_fallback": False}),
])
def test_explicit_current_policy_fields_are_allowed(text, prior, proposed):
    result, meta = guard_search_policy_operations(prior, [policy(**proposed)], text)
    assert result == [policy(**proposed)] and not meta["notes"]


def test_mode_permission_does_not_authorize_changed_provider_or_fallback():
    result, meta = guard_search_policy_operations(STATE, [policy(mode="DEMO", flight_provider="serpapi", demo_fallback=False)], "Включи DEMO.")
    assert result == [policy(mode="DEMO")]
    assert meta["notes"][0]["fields"] == ["flight_provider", "demo_fallback"]


def test_unrelated_changes_and_separately_owned_nationality_slot_are_preserved():
    adult = {"type": "add_traveler", "value": {"type": "adult"}}
    result, _ = guard_search_policy_operations(STATE, [adult, policy(mode="DEMO", guest_nationality="TR")], "Гражданство TR. Добавь взрослого.")
    assert result == [adult, policy(guest_nationality="TR")]


def test_identical_policy_does_not_trigger_work_and_changed_model_object_stays_model():
    assert guard_search_policy_operations(STATE, [policy(mode="HYBRID")], "Ничего не меняй.")[0] == []
    proposed = Operation.model_validate(policy(mode="DEMO"))
    result, _ = guard_search_policy_operations(STATE, [proposed], "Включи DEMO.")
    assert isinstance(result[0], Operation) and result[0] == proposed and result[0] is not proposed


def test_demo_fallback_permission_is_not_a_mode_switch():
    prior = deepcopy(STATE)
    prior["search_policy"]["demo_fallback"] = False
    result, meta = guard_search_policy_operations(prior, [policy(mode="DEMO", demo_fallback=True)], "Включи DEMO fallback.")
    assert result == [policy(demo_fallback=True)]
    assert meta["notes"][0]["fields"] == ["mode"]
