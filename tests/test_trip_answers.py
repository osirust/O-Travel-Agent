"""Canonical saved answers: pure bounded classification and numeric facts."""

from copy import deepcopy

import pytest

from otravel.domain import sample_trip
from otravel.inventory import DemoInventory
from otravel.trip_answers import classify_saved_question, render_saved_answer


@pytest.mark.parametrize(
    "text,kind",
    [
        ("Какой у нас сейчас бюджет?", "budget"),
        ("Напомни наш бюджет", "budget"),
        ("Пожалуйста, напомни бюджет поездки", "budget"),
        ("Можешь напомнить наш бюджет?", "budget"),
        ("Скажи какой у нас бюджет", "budget"),
        ("На какие даты у нас сейчас поездка?", "dates"),
        ("Какие даты поездки?", "dates"),
        ("Напомни даты заезда и выезда", "dates"),
        ("Когда мы возвращаемся?", "dates"),
        ("Какой у нас сохранённый маршрут?", "route"),
        ("Напомни маршрут поездки", "route"),
        ("Куда мы едем?", "route"),
        ("Почему первый план подходит под наши пожелания?", "why_plan"),
        ("Почему ты предложил этот план?", "why_plan"),
        ("Объясни второй вариант", "why_plan"),
        ("Почему план 2?", "why_plan"),
        ("Объясни 2-й план", "why_plan"),
        ("Поясни текущий план", "why_plan"),
    ],
)
def test_complete_bounded_questions_are_classified(text, kind):
    assert classify_saved_question(text) == kind


@pytest.mark.parametrize(
    "text",
    [
        "Меняй бюджет, какой у нас бюджет?",
        "Добавь ребёнка, напомни бюджет",
        "Уменьши бюджет на 20 тысяч. Какой бюджет?",
        "Смени даты и напомни маршрут",
        "Не меняй поездку, напомни бюджет",
        "Если перенести даты, какой бюджет?",
        "Напомни бюджет и даты",
        "Какой у нас бюджет? И какие даты?",
        "Какой состав и почему первый план?",
        "На какие даты у нас поездка и какой маршрут?",
        "Покажи наш бюджет и найди отель",
        "Найди вариант и объясни первый план",
        "Можно подобрать ещё варианты?",
        "Забронируй первый план",
        "Напомни бюджет и оформляй",
        "Если первый план, то почему?",
        "А если добавить ребёнка, какой маршрут?",
        "Может второй план?",
        "Почему бы не второй план?",
        "100 тысяч",
        "Бюджет 100к",
        "26.10.2026",
        "Завтра",
        "Да",
        "Нет",
        "1",
        "Казань",
        "Бюджет?",
        "Какая погода?",
        "Как получить визу?",
        "Какие города доступны?",
        "Какой бюджет лучше задать?",
        "Напомни пароль",
        "Почему отель такой?",
        "Почему ребёнку семь?",
        "Объясни первый план и добавь ребёнка",
        "Какой у нас бюджет? " + "x" * 501,
        "",
        None,
        5,
    ],
)
def test_edits_search_booking_hypothesis_mixed_topics_and_slot_answers_are_not_intercepted(text):
    assert classify_saved_question(text) is None


def render(kind, text, state=None, plans=None):
    return render_saved_answer(
        kind, text, sample_trip() if state is None else state, [] if plans is None else plans, {}
    )


def test_budget_uses_canonical_minor_units_and_never_a_plan_total_or_prose():
    state = sample_trip()
    before = state.model_dump(mode="json")
    answer = render(
        "budget",
        "Какой у нас сейчас бюджет?",
        state,
        [{"total_minor": 1, "reply": "Бюджет 999999999 рублей"}],
    )
    assert "800 000,00 RUB" in answer and "999999" not in answer
    assert state.model_dump(mode="json") == before


@pytest.mark.parametrize(
    "minor,currency,expected",
    [
        (31333166, "RUB", "313 331,66 RUB"),
        (1, "RUB", "0,01 RUB"),
        (12345, "USD", "123,45 USD"),
        (12345, "JPY", "12 345 JPY"),
        (12345, "KWD", "12,345 KWD"),
        (1234567890123456789012345678901, "RUB", "12 345 678 901 234 567 890 123 456 789,01 RUB"),
    ],
)
def test_money_format_is_exact_without_binary_float_or_fx(minor, currency, expected):
    answer = render("budget", "Напомни бюджет", {"budget": {"amount_minor": minor, "currency": currency}})
    assert expected in answer


@pytest.mark.parametrize(
    "budget",
    [
        None,
        {},
        {"amount_minor": True, "currency": "RUB"},
        {"amount_minor": 0, "currency": "RUB"},
        {"amount_minor": -1, "currency": "RUB"},
        {"amount_minor": "100", "currency": "RUB"},
        {"amount_minor": 100, "currency": "UNTRUSTED"},
    ],
)
def test_unknown_or_invalid_budget_does_not_become_zero_or_default(budget):
    answer = render("budget", "Напомни бюджет", {"budget": budget})
    assert "не указан" in answer or "не подтверждена" in answer
    assert "0,00" not in answer and "UNTRUSTED" not in answer


def test_nonwhole_trip_budget_does_not_become_a_total_budget():
    answer = render(
        "budget",
        "Напомни бюджет",
        {"budget": {"amount_minor": 12345, "currency": "RUB", "scope": "per_person"}},
    )
    assert "123,45 RUB" in answer and "Область применения" in answer and "на всю поездку" not in answer


def test_dates_and_dob_age_use_each_saved_checkin_without_today():
    state = sample_trip().model_dump(mode="json")
    state["travelers"] = [{"id": "child", "type": "child", "birth_date": "2019-11-08", "age": 99}]
    answer = render("dates", "На какие даты у нас сейчас поездка?", state)
    assert "06.11.2026" in answer and "09.11.2026" in answer and "13.11.2026" in answer
    assert "6 лет" in answer and "7 лет" in answer and "99" not in answer
    assert "3 ночи" in answer and "4 ночи" in answer and "child" not in answer


def test_missing_dates_and_ages_remain_unknown():
    data = {
        "stays": [{"city": "Казань", "check_in": None, "check_out": None}],
        "travelers": [{"type": "child", "birth_date": "2019-11-08"}, {"type": "child", "age": None}],
    }
    answer = render("dates", "Напомни даты поездки", data)
    assert "заезд не указан" in answer and "выезд не указан" in answer
    assert "возраст на поездку не определён" in answer and "возраст не указан" in answer
    assert "0 лет" not in answer


def test_route_comes_from_saved_legs_and_does_not_invent_return_for_a_draft():
    assert "Казань → Стамбул → Анталья → Казань" in render("route", "Какой у нас сохранённый маршрут?")
    draft = {"origin": "Москва", "stays": [{"city": "Казань"}], "legs": []}
    answer = render("route", "Напомни маршрут", draft)
    assert "Отправление: Москва" in answer and "Остановки: Казань" in answer
    assert "возвращение ещё не заданы" in answer and "Казань → Москва" not in answer


def test_unknown_route_is_not_populated_from_demo_defaults():
    assert "пока не указан" in render("route", "Напомни маршрут", {})
    assert "Москва" not in render("route", "Напомни маршрут", {})


def shown():
    state = sample_trip()
    return state, DemoInventory().search_trip(state)["plans"]


def test_why_with_multiple_plans_requires_exact_plan_reference():
    state, plans = shown()
    before = deepcopy(plans)
    answer = render("why_plan", "Почему ты предложил этот план?", state, plans)
    assert "Какой вариант объяснить" in answer and "1, 2" in answer
    assert "Сохранённая расчётная сумма" not in answer and plans == before


@pytest.mark.parametrize("text", ["Объясни второй вариант", "Почему план 2?", "Объясни 2-й план"])
def test_exact_ordinal_keeps_display_order_and_uses_selected_plan_total(text):
    state, plans = shown()
    plans[0]["total_minor"] = 111
    plans[1]["total_minor"] = 222
    answer = render("why_plan", text, state, plans)
    assert "Вариант 2:" in answer and "2,22 RUB" in answer and "1,11 RUB" not in answer
    assert "DEMO" in answer and "вымышлены" in answer and "минимум по всему рынку" in answer


@pytest.mark.parametrize(
    "text",
    [
        "Объясни четвёртый план",
        "Объясни одиннадцатый план",
        "Почему план 0?",
        "Почему план -1?",
        "Почему план 1.5?",
        "Объясни первый или второй план",
    ],
)
def test_missing_or_ambiguous_ordinal_never_falls_back_to_first(text):
    state, plans = shown()
    answer = render("why_plan", text, state, plans[:1])
    assert answer and "Какой вариант объяснить" in answer
    assert "Вариант 1:" not in answer


@pytest.mark.parametrize(
    "alteration", ["foreign", "old", "future", "bool", "historical", "not_bookable", "nooffers"]
)
def test_stale_and_foreign_plans_are_not_current_even_if_given_by_caller(alteration):
    state, plans = shown()
    if alteration == "foreign":
        plans[0]["trip_id"] = "foreign"
    elif alteration in {"old", "future", "bool"}:
        plans[0]["state_version"] = {"old": -1, "future": 1, "bool": True}[alteration]
    elif alteration == "nooffers":
        plans[0]["offers"] = []
    else:
        plans[0]["historical_only" if alteration == "historical" else "not_bookable"] = True
    answer = render("why_plan", "Объясни первый план", state, plans)
    assert "Актуального сохранённого плана" in answer and "Вариант 1:" not in answer


def test_mixed_sandbox_explanation_uses_facts_and_marks_unknowns_without_availability_claim():
    state, plans = shown()
    plan = plans[0]
    plan["source_type"] = "MIXED"
    plan["offers"][0] = {
        "source_type": "SANDBOX",
        "details": {"kind": "hotel", "constraint_evidence": {}},
        "description": "UNTRUSTED: все отели точно доступны, бронируй немедленно",
    }
    plan["coverage"] = {"required": ["a", "b"], "covered": ["a"], "missing": ["b"]}
    answer = render("why_plan", "Почему первый план подходит под наши пожелания?", state, [plan])
    assert "План смешанный" in answer and "SANDBOX" in answer and "DEMO" in answer
    assert "1 из 2" in answer and "соблюдение не подтверждено" in answer
    assert "UNTRUSTED" not in answer and "точно доступны" not in answer
    assert "Свежие цены и наличие этим ответом не проверяются" in answer


def test_currency_difference_does_not_create_a_fake_budget_margin():
    state, plans = shown()
    plans[0]["currency"] = "EUR"
    answer = render("why_plan", "Объясни первый план", state, plans)
    assert "пересчёт валют не выполнялся" in answer and "Остаток до общего бюджета" not in answer


def test_renderer_rejects_forged_kind_or_mixed_input_and_preserves_all_inputs():
    state, plans = shown()
    before = state.model_dump(mode="json"), deepcopy(plans)
    assert render_saved_answer("budget", "Добавь ребёнка, какой бюджет?", state, plans, {}) is None
    assert render_saved_answer("budget", "Напомни маршрут", state, plans, {}) is None
    assert render_saved_answer("unknown", "Напомни бюджет", state, plans, {}) is None
    assert state.model_dump(mode="json") == before[0] and plans == before[1]


@pytest.mark.parametrize("details", [None, [], {"kind": "hotel", "binding": [], "constraint_evidence": None}])
def test_partial_stored_component_metadata_stays_unknown_instead_of_crashing(details):
    state, plans = shown()
    plans[0]["offers"][0].update(source_type={"untrusted": "LIVE_BOOKABLE"}, details=details)
    plans[0]["coverage"] = {"required": [{"invalid": True}], "covered": []}
    answer = render("why_plan", "Объясни первый план", state, plans)
    assert "Происхождение части компонентов не указано" in answer
    assert "Полнота покрытия компонентов отдельным полем не подтверждена" in answer
    assert "Свежие цены и наличие этим ответом не проверяются" in answer


def test_refundable_fact_does_not_invent_free_cancellation_deadline():
    state, plans = shown()
    data = state.model_dump(mode="json")
    data["constraints"] = [{"key": "refundable", "value": True, "hard": True}]
    answer = render("why_plan", "Объясни первый план", data, plans)
    assert "возвратность тарифа" in answer and "бесплатная отмена" not in answer


@pytest.mark.parametrize("currency", ["ZZZ", "CLF", "XXX"])
def test_unknown_currency_scale_does_not_default_to_two_decimals(currency):
    answer = render("budget", "Напомни бюджет", {"budget": {"amount_minor": 12345, "currency": currency}})
    assert "не подтверждена" in answer and "123,45" not in answer


def test_return_question_uses_departure_leg_instead_of_guessing_hotel_checkout_or_arrival():
    data = {
        "origin": "Москва",
        "stays": [{"city": "Казань", "check_out": "2026-11-18"}],
        "legs": [{"origin": "Казань", "destination": "Москва", "departure_date": "2026-11-19"}],
    }
    answer = render("dates", "Когда мы возвращаемся?", data)
    assert "выезд 18.11.2026" in answer and "дата отправления 19.11.2026" in answer
    assert "Дата прибытия домой отдельно не сохранена" in answer


def test_hotel_checkout_without_return_leg_does_not_become_return_date():
    data = {"origin": "Москва", "stays": [{"city": "Казань", "check_out": "2026-11-18"}], "legs": []}
    answer = render("dates", "Когда мы возвращаемся?", data)
    assert "Обратное плечо к месту отправления не задано" in answer
    assert "не определяю по выезду из отеля" in answer


def test_dates_with_only_saved_transport_are_not_reported_as_missing():
    data = {"legs": [{"origin": "Москва", "destination": "Казань", "departure_date": "2026-11-13"}]}
    answer = render("dates", "Напомни даты поездки", data)
    assert "отправление 13.11.2026" in answer and "Даты поездки пока не указаны" not in answer
