"""Exact059 closed current reminder; finite calendar-footer safety only."""
from copy import deepcopy

import pytest

from otravel import saved_fact_answers as candidate
from otravel.domain import TripState

TEXT="Сверь пожалуста обе даты и число взрослых. Февраль был только вопросом."
MONTHS=("январь","февраль","март","апрель","май","июнь","июль","август","сентябрь","октябрь","ноябрь","декабрь")

def trip():
    return TripState(origin="Казань",travelers=[{"id":"a1","type":"adult"},{"id":"a2","type":"adult"}],
        stays=[{"id":"stay","city":"Дубай","check_in":"2026-11-05","check_out":"2026-11-12"}],
        budget={"scope":"whole_trip","currency":"RUB","amount_minor":15000000})

@pytest.mark.parametrize("month",MONTHS)
def test_neutral_month_footer_never_changes_canonical_november_or_people(month):
    before=trip().model_dump(mode="json");copy=deepcopy(before)
    text=f"Сверь пожалуста обе даты и число взрослых. {month.capitalize()} был только вопросом."
    reply=candidate.canonical_saved_fact_answer(text,before)
    assert candidate.classify_closed_saved_fact_question(text)=="dates,party"
    assert "05.11.2026" in reply and "12.11.2026" in reply and "2 взрослых" in reply
    assert "RUB"not in reply and "руб"not in reply.casefold()
    assert before==copy and all(p["age"]is None for p in before["travelers"])

@pytest.mark.parametrize("text",[
    "Сверь пожалуйста обе даты и число взрослых.",
    "Сверь пожалуста даты и число взрослых",
    "Сверь обе даты и число взрослых?",
])
def test_closed_two_topics_use_canonical_sections(text):
    reply=candidate.canonical_saved_fact_answer(text,trip())
    assert reply and "05.11.2026" in reply and "2 взрослых" in reply

@pytest.mark.parametrize("tail",[
    " Добавь одного взрослого.",
    " Уменьши бюджет.",
    " Сдвинь выезд.",
    " И еще неизвестный вопрос.",
    " Сверь цены с поставщиком.",
    " Теперь нас трое.",
])
def test_any_following_footer_tail_cannot_be_stripped_away(tail):
    assert candidate.canonical_saved_fact_answer(TEXT+tail,trip())is None

@pytest.mark.parametrize("text",[
    "Сверь цены с поставщиком.",
    "Сверь обе даты и число взрослых. Февраль был только вопросом?",
    "Сверь обе даты и число взрослых. Февраль и март были только вопросами.",
    "Сверь обе даты и число взрослых. Февраль не был только вопросом.",
    "Сверь обе даты и число взрослых. Февраль 2027 был только вопросом.",
    "Сверь обе даты и число взрослых. Мы были в феврале.",
    "Сверь обе даты и число взрослых. Не меняй февраль.",
    "Добавь человека. Сверь пожалуста обе даты и число взрослых. Март был только вопросом.",
    "Если выезд раньше, сверь обе даты и число взрослых. Март был только вопросом.",
    "Сверь обе даты и число взрослых, общий бюджет и визу.",
    "Сверь пожалуста обе даты и число взрослых. Ноябрь был только примером.",
])
def test_unknown_or_edit_calendar_and_external_forms_remain_normal_pipeline(text):
    assert candidate.canonical_saved_fact_answer(text,trip())is None
