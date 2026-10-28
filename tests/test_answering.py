from copy import deepcopy

import pytest

from otravel.answering import (
    READONLY_SCHEMA,
    READONLY_SYSTEM,
    checked_reply,
    facts_projection,
    readonly_question,
    valid_readonly_contract,
)
from otravel.domain import sample_trip
from otravel.inventory import DemoInventory


def answer(reply="party,budget"):
    return {
        "operations": [],
        "control": {"action": "none"},
        "reply": reply,
        "unknowns": [],
        "conflicts": [],
        "speech_act": "ASK",
        "information_subject": None,
    }


@pytest.mark.parametrize(
    "text",
    [
        "Какой общий бюджет сейчас у нас записан? Ничего пока не меняй.",
        "Ты не ответил о детях: назови их возраста и текущий общий бюджет. Ничего не меняй.",
        "А если младшему уже пять на дату поездки, это что-то меняет? Пока возраст не исправляй.",
        "Анталья и Стамбул ведь в одной стране? Я спрашиваю, менять город не надо.",
        "Покажи уже созданный тестовый заказ. Новый не создавай, просто напомни состав и стоимость.",
        "Сколько ночей сейчас получается по сохранённым датам?",
        "Как ты нас сейчас распределил по комнатам?",
        "В какой стране Анталья?",
        "Назови текущий бюджет и состав",
        "Напомни маршрут и все изменения",
        "Можно перенести выезд на два дня? Пока только обсуждаем.",
    ],
)
def test_readonly_questions_include_compound_information_and_hypotheses(text):
    assert readonly_question(text)


@pytest.mark.parametrize(
    "text",
    [
        "Измени бюджет на 85000 и скажи состав",
        "Сними пожелание утра и напомни даты",
        "Добавь ребёнка 8 лет. Сколько нас теперь?",
        "Найди новые варианты",
        "Можно поискать ещё?",
        "Если можешь, забронируй первый план",
        "Подготовь предпросмотр",
        "Подожди и расскажи цены",
        "1",
        "да",
        "завтра",
        "Москва?",
        "Бюджет теперь 80000. Напомни маршрут",
        "Вылет лучше рано утром. На какое число у меня сейчас выезд?",
        "Бюджет 65к рублей общий. Питер и Санкт-Петербург у тебя ведь один город? Мне две остановки не нужны.",
        "Можно добавить ребёнка восьми лет?",
        "Ты можешь исправить возраст ребёнка на восемь?",
        "Ты можешь поменять отправление на Казань?",
    ],
)
def test_edits_search_controls_and_slot_answers_stay_outside_answer_role(text):
    assert not readonly_question(text)


def test_contract_forbids_operations_controls_unknowns_and_missing_fields():
    assert valid_readonly_contract(answer())
    for key, value in {
        "operations": [{"type": "set_budget", "value": {}}],
        "control": {"action": "continue"},
        "unknowns": ["Сколько взрослых?"],
        "speech_act": "CHANGE",
    }.items():
        changed = answer()
        changed[key] = value
        assert not valid_readonly_contract(changed)
    changed = answer()
    changed.pop("information_subject")
    assert not valid_readonly_contract(changed)
    assert len(str(READONLY_SCHEMA).encode()) + len(READONLY_SYSTEM.encode()) < 4000


def facts():
    state = sample_trip()
    plans = DemoInventory().search_trip(state)["plans"]
    return facts_projection(state.model_dump(mode="json"), plans, [], [], [])


def test_facts_projection_has_exact_totals_and_no_native_offer_identifiers():
    data = facts()
    assert "800 000,00 RUB" in data["budget"]
    assert data["options"][0]["saved_total"]
    assert "offer_id" not in repr(data) and "native_offer_id" not in repr(data)
    assert data["groups"][0]["keep_separate"] is True


@pytest.mark.parametrize(
    "reply",
    [
        "Я изменил поездку.",
        "Ваш бюджет 999999999 рублей.",
        "Реальная бронь подтверждена.",
        "Сохранённый бюджет: 8 RUB",
        "Сейчас едут 2 взрослых",
    ],
)
def test_unsupported_numbers_mutations_and_booking_assertions_are_replaced(reply):
    checked, reasons = checked_reply(reply, facts(), "Напомни текущие условия")
    assert reasons and checked != reply and "недостаточно" in checked


def test_new_user_number_does_not_become_a_current_fact_without_a_hypothesis():
    data = facts()
    data["recent_messages"] = [{"text": "Может бюджет 99999, ещё не решил"}]
    assert checked_reply("Ваш бюджет 99999 рублей.", data, "Если выбрать 99999, что меняется?")[1]
    text, rejected = checked_reply("hypothesis,party", data, "Если добавить ребёнка 8 лет? Пока не добавляй")
    assert not rejected and "Гипотетический вариант пока не применён" in text


def test_projection_does_not_mutate_original_inputs():
    state = sample_trip().model_dump(mode="json")
    original = deepcopy(state)
    facts_projection(state, [], [], [], [])
    assert state == original
