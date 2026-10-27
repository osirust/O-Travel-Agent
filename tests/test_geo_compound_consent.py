"""Country conflicts and compound scoped consent; no NLU or external HTTP."""
from copy import deepcopy

import pytest

from otravel.clarifications import resolve_short_answer
from otravel.domain import TripState
from otravel.geography import parse_destination
from otravel.intent_grounding import ground_intent_operations


def pending(state=None):
    result = {"field": "destination.city", "target_id": "hotel", "context": {
        "requested_destination": parse_destination("Сербия")["requested_destination"],
        "proposed_destination": parse_destination("Стамбул")["requested_destination"],
        "confirmation_required": True, "proposal_field": "destination.city"}}
    if state is not None:
        result["state_version"] = state.state_version
    return result


def known():
    return TripState(origin="Москва", travelers=[{"id": "a", "type": "adult"}],
        stays=[{"id": "hotel", "city": "Белград"}],
        budget={"amount_minor": 8_000_000, "currency": "RUB"})


@pytest.mark.parametrize("text,city,country", [
    ("Стамбул в Сербии", "Стамбул", "Турция"),
    ("Белгород в Сербии", "Белгород", "Россия"),
    ("Анталья в России", "Анталья", "Турция"),
    ("Дубай в Турции", "Дубай", "ОАЭ"),
])
def test_first_qualified_place_assertion_cannot_lose_known_city_and_country_conflict(text, city, country):
    state = TripState()
    before = deepcopy(state.model_dump(mode="json"))
    operations, meta = ground_intent_operations(state,
        [{"type": "upsert_stay", "target_id": "new-stay-1", "value": {"city": city}}], text)
    assert operations == []
    assert any(city in question and country in question and "подтверждаете" in question for question in meta["unknowns"])
    assert meta["pending_context"]["proposed_destination"]["name"] == city
    assert meta["pending_context"]["confirmation_required"] is True
    assert state.model_dump(mode="json") == before


@pytest.mark.parametrize("text", ["Да, добавь ребёнка семи лет. Взрослый остаётся.",
    "да добавь ребёнка семи лет", "да и добавь ребёнка семи лет", "Да меняем, добавь ребёнка семи лет"])
def test_compound_yes_applies_only_owned_city_proposal_and_preserves_independent_child_edit(text):
    state = known()
    op = {"type": "add_traveler", "value": {"id": "child", "type": "child", "age": 7}}
    operations, meta = ground_intent_operations(state, [op], text, pending(state))
    assert op in operations
    assert {"type": "upsert_stay", "target_id": "hotel", "value": {"city": "Стамбул"}} in operations
    assert meta["pending_context"]["requested_destination"]["country_code"] == "TR"
    assert "proposed_destination" in meta["pending_context_clear"]


def test_compound_no_discards_proposal_while_independent_child_edit_remains():
    state = known()
    op = {"type": "add_traveler", "value": {"id": "child", "type": "child", "age": 7}}
    operations, meta = ground_intent_operations(state, [op], "Нет, добавь ребёнка семи лет. Взрослый остаётся.", pending(state))
    assert operations == [op]
    assert "proposed_destination" in meta["pending_context_clear"]
    assert meta["pending_context"]["requested_destination"]["country_code"] == "RS"
    assert any("Сербия" in question and "город" in question for question in meta["unknowns"])


@pytest.mark.parametrize("text", [
    "Да, Стамбул это Турция.", "Да, а Стамбул в Турции?", "Да, если добавим ребёнка семи лет.",
    "Да, добавь ребёнка семи лет в Стамбуле.", "Да, добавь ребёнка семи лет, но не меняй страну.",
    "Раньше говорили да, добавь ребёнка семи лет.", "«Да, добавь ребёнка семи лет» — это цитата.",
])
def test_compound_geography_fact_question_veto_or_history_never_accepts_proposal(text):
    state = known()
    operations, _meta = ground_intent_operations(state, [], text, pending(state))
    assert not any(op.get("type") == "upsert_stay" and op.get("value", {}).get("city") == "Стамбул" for op in operations)


def test_compound_yes_never_uses_stale_or_origin_bound_proposal():
    state = known()
    for change in ({"state_version": state.state_version + 1}, {"field": "origin.city"}):
        question = {**pending(state), **change}
        operations, _ = ground_intent_operations(state, [], "Да, добавь ребёнка семи лет.", question)
        assert not any(op.get("type") == "upsert_stay" and op.get("value", {}).get("city") == "Стамбул" for op in operations)


def test_first_conflict_proposal_can_be_accepted_with_an_independent_edit_without_inventing_target():
    state = TripState(origin="Москва", travelers=[{"id": "a", "type": "adult"}])
    _, first = ground_intent_operations(state, [], "Стамбул в Сербии")
    question = {**first["pending_context"], "state_version": state.state_version}
    op = {"type": "add_traveler", "value": {"id": "child", "type": "child", "age": 7}}
    operations, _ = ground_intent_operations(state, [op], "Да, добавь ребёнка семи лет.", question)
    assert {"type": "upsert_stay", "target_id": question["target_id"], "value": {"city": "Стамбул"}} in operations


@pytest.mark.parametrize("text", ["Белгород.", "белгород!", " БЕЛГОРОД "])
def test_scoped_serbian_typo_acknowledgement_survives_bare_answer_punctuation(text):
    state = known()
    question = pending(state)
    question["context"].pop("proposed_destination")
    question["context"].pop("confirmation_required")
    question["context"].pop("proposal_field")
    result = resolve_short_answer(state, question, text)
    assert result["operations"] == [] and result["pending_context"]["requested_destination"]["name"] == "Белград"
    assert result["notice"] == "Понял как «Белград», Сербия." and result["notice"] in result["question"]


def test_question_mark_is_not_scoped_typo_consent_or_an_implicit_country_change():
    state = known()
    result = resolve_short_answer(state, pending(state), "Белгород?")
    assert result["operations"] == [] and "notice" not in result
    assert result["pending_context"]["requested_destination"]["country_code"] == "RS"
    assert result["pending_context"]["proposed_destination"]["country_code"] == "RU"


def test_initial_country_conflict_retains_unrelated_explicit_party_and_budget_operations():
    state = TripState()
    unrelated = [{"type": "add_traveler", "value": {"id": "adult", "type": "adult"}},
                 {"type": "set_budget", "value": {"amount_minor": 9_000_000, "currency": "RUB"}}]
    operations, meta = ground_intent_operations(state, [*unrelated,
        {"type": "upsert_stay", "target_id": "new-stay-1", "value": {"city": "Стамбул"}}],
        "Стамбул в Сербии. Еду один взрослый, общий бюджет 90000 рублей.")
    assert operations == unrelated
    assert meta["pending_context"]["requested_destination"]["country_code"] == "RS"
    assert any("Стамбул" in text and "Турция" in text for text in meta["unknowns"])
