"""Bound-question rendering controls; no model, storage, or network."""
from copy import deepcopy

import pytest

from otravel.clarifications import derive_pending_field, human_question, question_field
from otravel.question_rendering import render_untrusted_question

EXACT_UNKNOWN = ("Количество ночей (6 дней — уточните, сколько ночей) и возраст взрослого, "
                 "если Known: от 18 до 120.")


def _adult_state():
    return {"origin": "Москва", "travelers": [{"id": "adult", "type": "adult", "age": None}],
            "stays": [], "legs": []}


@pytest.mark.parametrize("raw", [EXACT_UNKNOWN, "Сколько взрослых поедет в поездку?", "adult_count"])
def test_known_adult_question_renders_the_derived_children_slot_without_inventing_count(raw):
    state = _adult_state()
    before = deepcopy(state)
    bound = derive_pending_field([raw], state)
    assert bound["field"] == "party.children"
    text = render_untrusted_question(raw, state, binding=bound)
    assert text == human_question({"field": "party.children"}, state)
    assert question_field(text) == bound["field"]
    assert state == before and state["travelers"][0]["age"] is None


def test_no_known_adult_does_not_skip_the_adult_question():
    state = {"travelers": [], "stays": [], "legs": []}
    text = render_untrusted_question("Сколько взрослых поедет?", state)
    assert question_field(text) == "party.adults"


def test_children_known_is_an_exhausted_slot_and_remains_for_the_callers_skip_guard():
    packet = {"text": "Сколько взрослых поедет?", "children_known": True}
    state = _adult_state()
    before = deepcopy(packet)
    assert derive_pending_field([packet], state)["field"] is None
    # No renderer may manufacture a fresh child-count binding from this state.
    assert packet == before


def test_impossible_flight_date_renders_the_single_owned_hotel_checkin():
    state = _adult_state()
    state["stays"] = [{"id": "hotel", "city": "Дубай", "check_in": None}]
    packet = {"text": "На какую дату планируете вылет?"}
    bound = derive_pending_field([packet], state)
    assert bound["field"] == "stay.check_in" and bound["target_id"] == "hotel"
    text = render_untrusted_question(packet, state, binding=bound)
    assert question_field(text) == "stay.check_in" and "Дубай" in text


@pytest.mark.parametrize("field,text", [
    ("origin.city", "Из какого города вы отправляетесь?"),
    ("search.guest_nationality", "Гражданство какой страны использовать для поиска отеля?"),
    ("party.addition", "Нужно добавить нового участника или сохранить текущий состав? Укажите, кого добавляем, либо напишите, что состав остаётся прежним."),
])
def test_unrelated_canonical_questions_keep_their_address_and_wording(field, text):
    state = {"travelers": [], "stays": [], "legs": []}
    assert render_untrusted_question({"field": field, "text": text}, state) == text


def test_unrecognized_natural_question_is_not_replaced_with_an_invented_slot():
    text = "Какой сайт вы предпочитаете для справочной информации?"
    state = _adult_state()
    assert question_field(text) is None
    assert render_untrusted_question(text, state) == text


def test_explicit_owned_hotel_target_and_selection_context_are_not_replaced():
    state = _adult_state()
    state["stays"] = [{"id": "first", "city": "Дубай"}, {"id": "second", "city": "Анталья"}]
    raw = {"text": "На какую дату планируете выезд?", "room_target_selection_required": True}
    bound = {"field": "stay.check_out", "target_id": "second", "context": {"stay_id": "second"}}
    before = deepcopy((raw, bound, state))
    text = render_untrusted_question(raw, state, binding=bound)
    assert "Анталья" in text and "Дубай" not in text
    assert (raw, bound, state) == before


def test_server_proposal_binding_is_not_rederived_from_misleading_wording():
    state = _adult_state()
    state["stays"] = [{"id": "owned", "city": "Дубай"}]
    bound = {"field": "stay.check_out", "target_id": "owned"}
    text = render_untrusted_question("Сколько взрослых поедет?", state, binding=bound)
    assert question_field(text) == "stay.check_out" and "Дубай" in text
