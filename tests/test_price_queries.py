from copy import deepcopy
from datetime import UTC, datetime

import pytest

from otravel.conversation_followup import followup_kind
from otravel.conversational import explicit_search_request
from otravel.price_queries import saved_price_answer, whole_trip_price_question


@pytest.mark.parametrize("text", ["сколько будет стоить", "Сколько стоит?", "А сколько это будет стоить?",
    "Какая итоговая стоимость поездки?", "во сколько обойдется поездка"])
def test_whole_price_questions_are_explicit_estimate_requests(text):
    assert whole_trip_price_question(text) and explicit_search_request(text)


@pytest.mark.parametrize("text", ["сколько стоит отель", "сколько будет стоить если добавить ребёнка",
    "Добавь ребёнка, сколько стоит?", "не ищи, сколько стоит?", "сколько стоит перелёт бизнесом",
    "сколько стоит в Сербию", "сколько стоит? Подожди", "'сколько стоит'", "какой бюджет"])
def test_price_question_cannot_swallow_edits_hypotheses_scope_or_pause(text):
    assert not whole_trip_price_question(text)


def stored():
    state = {"trip_id": "trip", "state_version": 3}
    plan = {"trip_id": "trip", "state_version": 3, "plan_id": "p", "total_minor": 2808681,
        "currency": "RUB", "source_type": "MIXED", "offers": [
            {"source_type": "SANDBOX", "expires_at": "2027-01-01T00:00:00Z"},
            {"source_type": "DEMO"}]}
    return state, [plan]


def test_saved_price_uses_minor_units_and_source_without_claiming_actual_price():
    state, plans = stored()
    before = deepcopy(plans)
    answer = saved_price_answer(state, plans, now=datetime(2026, 10, 27, tzinfo=UTC))
    assert "28 086,81 RUB" in answer and "смешанный" in answer and "сохранённая оценка" in answer
    assert "бронь не создана" in answer and plans == before


@pytest.mark.parametrize("mutation", ["foreign", "stale", "expired", "naive", "missing_expiry", "unknown_total", "unknown_source"])
def test_invalid_price_snapshot_is_not_reported_as_whole_total(mutation):
    state, plans = stored()
    plan = plans[0]
    if mutation == "foreign": plan["trip_id"] = "other"
    if mutation == "stale": plan["state_version"] = 2
    if mutation == "expired": plan["offers"][0]["expires_at"] = "2026-01-01T00:00:00Z"
    if mutation == "naive": plan["offers"][0]["expires_at"] = "2027-01-01"
    if mutation == "missing_expiry": plan["offers"][0].pop("expires_at")
    if mutation == "unknown_total": plan["total_minor"] = None
    if mutation == "unknown_source": plan["source_type"] = "UNVERIFIED"
    assert saved_price_answer(state, plans, now=datetime(2026, 10, 27, tzinfo=UTC)) is None


@pytest.mark.parametrize("text", ["так что?", "Ну что?", "Что дальше?", "что теперь"])
def test_nudges_retain_intake_context(text):
    assert followup_kind(text) == "help"
