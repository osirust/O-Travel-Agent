"""Explicit current zero-child facts; no model, geography or domain writes."""
from copy import deepcopy

import pytest

from otravel.clarifications import resolve_short_answer
from otravel.domain import TripState
from otravel.party_literals import explicit_no_children


@pytest.mark.parametrize("text", ["Дети не едут", "детей пока не будет", "детей пока нет"])
def test_current_zero_child_phrases_answer_the_owned_slot_exactly_like_zero(text):
    state = TripState(trip_id="no-child-regression", travelers=[{"id": "adult", "type": "adult"}])
    pending = {"field": "party.children", "state_version": 0, "context": {"children_known": False}}
    before = deepcopy(state.model_dump(mode="json"))
    result = resolve_short_answer(state, pending, text)
    expected = resolve_short_answer(state, pending, "0")
    assert result == expected and result["handled"] is True
    assert result["operations"] == [] and result["pending_context"] == {"children_known": True, "children_count": 0}
    assert state.model_dump(mode="json") == before


@pytest.mark.parametrize("text", ["Дети не едут", "Детей нет.", "Нет детей", "Без детей",
    "Детей пока не будет", "Детей пока нет", "Детей не будет"])
def test_shared_current_statement_and_bound_answer_agree(text):
    assert explicit_no_children(text) and explicit_no_children(text, answer=True)


@pytest.mark.parametrize("text", ['«Дети не едут»', 'Если дети не едут', 'Раньше дети не едут',
    'Дети не едут?', 'Например, дети не едут', 'Я не говорил: «детей нет»',
    'Дети не едут, возможно возьмём ребёнка', 'Не дети не едут'])
def test_quotes_history_hypotheses_questions_and_nonliteral_negations_are_not_zero(text):
    assert not explicit_no_children(text)
    assert not explicit_no_children(text, answer=True)


@pytest.mark.parametrize("text", ["нет", "не надо", "не будет", "0"])
def test_short_no_is_only_a_bound_child_answer_and_not_a_tripwide_fact(text):
    assert not explicit_no_children(text) and explicit_no_children(text, answer=True)


def test_zero_child_reply_never_deletes_an_existing_child_or_consumes_a_compound_edit():
    state = TripState(travelers=[{"id": "adult", "type": "adult"}, {"id": "child", "type": "child", "age": 7}])
    pending = {"field": "party.children"}
    response = resolve_short_answer(state, pending, "Дети не едут")
    assert response["operations"] == [] and response.get("question") and not response.get("pending_context", {}).get("children_known")
    assert resolve_short_answer(state, pending, "Детей нет, бюджет 100000 рублей")["handled"] is False
