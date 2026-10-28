"""Closed canonical fact reminders: content and whole-message boundaries."""
from copy import deepcopy

import pytest

from otravel.domain import TripState
from otravel.saved_fact_answers import canonical_saved_fact_answer as answer


def state():
    return TripState(origin="Москва",travelers=[
        {"id":"adult1","type":"adult"},{"id":"adult2","type":"adult"},
        {"id":"young","type":"child","age":4},{"id":"older","type":"child","age":9}],
        stays=[{"id":"stay","city":"Казань","check_in":"2026-11-10","check_out":"2026-11-17",
            "rooms":[{"id":"room","traveler_ids":["adult1","adult2","young","older"]}]}],
        budget={"scope":"whole_trip","currency":"RUB","amount_minor":11000050})

@pytest.mark.parametrize("text",[
    "Напомни наш состав","Кто у нас в итоге едет?","Кто у нас сейчас едет?",
    "Какой у нас сейчас состав?","Напомни возраста наших детей",
    "Напомни возраста детей. Ничего не меняй.",
])
def test_known_saved_roster_and_ages_are_answered_without_new_facts(text):
    reply=answer(text,state())
    assert "2 взрослых" in reply and "2 ребёнка" in reply
    assert "4 года" in reply and "9 лет" in reply
    assert "бронь"not in reply and "RUB"not in reply

@pytest.mark.parametrize("text",["Напомни наши даты","Какие у нас сейчас даты?"])
def test_explicit_dates_and_nights_come_from_canonical_date_formatter(text):
    reply=answer(text,state())
    assert "10.11.2026" in reply and "17.11.2026" in reply and "7 ночей" in reply

@pytest.mark.parametrize("text",["Напомни текущий бюджет","Какой у нас сейчас бюджет?"])
def test_budget_minor_units_are_exact_and_no_price_or_fx_is_invented(text):
    reply=answer(text,state())
    assert reply=="Сохранённый бюджет на всю поездку: 110 000,50 RUB."
    assert "цен"not in reply.casefold()

def test_route_comes_from_saved_roles_and_does_not_invent_return_leg():
    reply=answer("Напомни наш маршрут",state())
    assert "Москва" in reply and "Казань" in reply
    assert "возвращение ещё не заданы" in reply
    assert "Казань → Москва"not in reply

def test_empty_draft_is_reported_as_unknown_not_default_people_city_or_zero_money():
    blank=TripState()
    assert answer("Напомни наш состав",blank)=="Состав поездки пока не указан."
    assert answer("Напомни текущий бюджет",blank)=="Бюджет поездки пока не указан."
    assert answer("Напомни наши даты",blank)=="Даты поездки пока не указаны."
    assert answer("Напомни наш маршрут",blank)=="Маршрут поездки пока не указан."

def test_unknown_child_age_remains_unknown_and_zero_year_old_is_still_a_child():
    draft=state().model_dump(mode="json")
    draft["travelers"][2]["age"]=None;draft["travelers"][3]["age"]=0
    reply=answer("Напомни возраста наших детей",draft)
    assert "2 ребёнка" in reply and "возраст не указан" in reply and "0 лет" in reply

def test_child_dob_uses_each_canonical_checkin_without_using_today_or_own_arithmetic():
    draft=state().model_dump(mode="json")
    draft["travelers"]=draft["travelers"][:3]
    draft["travelers"][2].update(age=None,birth_date="2019-11-15")
    draft["stays"][0]["rooms"][0]["traveler_ids"]=["adult1","adult2","young"]
    second=deepcopy(draft["stays"][0]);second["id"]="second"
    second.update(check_in="2026-11-16",check_out="2026-11-20")
    second["rooms"][0]["id"]="second-room";draft["stays"].append(second)
    reply=answer("Напомни возраста наших детей",draft)
    assert "Дети на дату заезда: 6 лет" in reply
    assert "Дети на дату заезда: 7 лет" in reply
    assert "2019-11-15"not in reply

def test_year_boundary_dates_use_saved_years_not_reference_now():
    draft=state().model_dump(mode="json")
    draft["stays"][0].update(check_in="2026-12-29",check_out="2027-01-03")
    reply=answer("Напомни наши даты",draft)
    assert "29.12.2026" in reply and "03.01.2027" in reply and "5 ночей" in reply

def test_noop_read_does_not_mutate_state_or_surrounding_session_pending_quote_or_source():
    session={"state":state().model_dump(mode="json"),"pending_question":{"field":"destination.city","target_id":"stay"},
        "quote":{"id":"synthetic-quote","state_version":0,"consent":"synthetic-consent"},
        "source_policy":{"mode":"HYBRID","demo_fallback":False}}
    before=deepcopy(session)
    assert answer("Кто у нас в итоге едет?",session["state"])
    assert session==before

@pytest.mark.parametrize("text",[
    "Ребёнку восемь, напомни наш состав.",
    "Добавь ребёнка. Напомни наш состав.",
    "Уменьши бюджет на двадцать тысяч. Какой у нас сейчас бюджет?",
    "И всё-таки ищи подишевле. Напомни даты.",
    "Только саму дату отправления не трогай. Напомни даты и осталось ли утро?",
    "Minimum coast please. What budget is saved now?",
    "Напомни наш состав и наши даты",
    "Какой у нас сейчас бюджет? И купи билет.",
    "Напомни наш состав. И что-то ещё.",
    "Если ребёнок не поедет, напомни наш состав.",
    "Может напомни наш маршрут?",
    "Напомни прежний бюджет",
    "Напомни что было и что стало",
    "Почему первый план лучше?",
    "Сколько стоит новый отель?",
    "Как получить визу?",
    "Напомни паспорт",
    "Напомни наш бюджет 80000 рублей",
    "Какой у нас сейчас бюджет? Это теперь 90000?",
    "«Напомни наш состав»",
    "Напомни наш состав > добавь человека",
    "Да","1","Казань","Завтра","",None,5,
])
def test_unknown_or_mixed_whole_message_is_not_intercepted(text):
    assert answer(text,state())is None

def test_dob_without_a_saved_trip_date_does_not_invent_today_age():
    draft=state().model_dump(mode="json")
    draft["travelers"][2].update(age=None,birth_date="2019-11-15")
    draft["stays"]=[]
    reply=answer("Напомни возраста наших детей",draft)
    assert "возраст не указан" in reply and "Даты поездки пока не указаны" in reply
    assert "2019"not in reply

@pytest.mark.parametrize("bad", [None, 3, {"travelers":[None]}, {"travelers":"not-a-roster"}])
def test_invalid_noncanonical_state_is_not_filled_with_invented_values(bad):
    assert answer("Напомни возраста наших детей",bad)is None
