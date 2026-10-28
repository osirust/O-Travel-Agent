"""Bounded RU month dates; original date-slot oracles stay unchanged."""
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from otravel.date_slots import resolve_date_answer
from otravel.domain import TripState, apply_operations, sample_trip

NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)
FIXTURE = json.loads((Path(__file__).parents[1] / "fixtures/regression/router8-date-label-002.json").read_text(encoding="utf-8"))


def single():
    return TripState(origin="Казань", travelers=[{"id": "a", "type": "adult"}],
        budget={"amount_minor": 13_000_000}, stays=[{"id": "s", "city": "Анталья"}])


def test_cached_router002_explicit_checkout_overrides_pending_checkin_not_people_or_budget():
    before = deepcopy(FIXTURE["before_trip"])
    pending = deepcopy(FIXTURE["pending_question"])
    frozen = deepcopy((before, pending))
    answer = resolve_date_answer(before, pending, FIXTURE["user"], NOW)
    assert answer == {"handled": True, "operations": FIXTURE["expected_operations"]}
    after, _ = apply_operations(TripState.model_validate(before), answer["operations"])
    expected = deepcopy(before)
    expected["stays"][0]["check_out"] = "2026-11-17"
    # No check-in, invented room, person, budget or route is supplied.
    assert after.model_dump(mode="json") == expected
    assert (before, pending) == frozen


@pytest.mark.parametrize("month,number", [("января", 1), ("февраля", 2), ("марта", 3),
    ("апреля", 4), ("мая", 5), ("июня", 6), ("июля", 7), ("августа", 8),
    ("сентября", 9), ("октября", 10), ("ноября", 11), ("декабря", 12)])
@pytest.mark.parametrize("year_suffix", ["", " года"])
def test_twelve_exact_russian_months_and_optional_goda(month, number, year_suffix):
    text = f"ВЫЕЗД: 7 {month} 2027{year_suffix}."
    answer = resolve_date_answer(single(), None, text, NOW)
    assert answer == {"handled": True, "operations": [{"type": "upsert_stay", "target_id": "s",
        "value": {"check_out": f"2027-{number:02d}-07"}}]}


def test_bound_bare_full_month_date_answers_only_fresh_pending_field():
    state = single().model_dump(mode="json")
    pending = {"field": "stay.check_in", "target_id": "s", "state_version": 0}
    answer = resolve_date_answer(state, pending, "10 ноября 2026 года", NOW)
    assert answer["operations"] == [{"type": "upsert_stay", "target_id": "s", "value": {"check_in": "2026-11-10"}}]
    for wrong in (None, {"field": "budget.amount_minor", "target_id": "s"},
                  {**pending, "state_version": 1}, {**pending, "state_version": True},
                  {**pending, "target_id": "missing"}, {"field": "stay.check_in"}):
        assert resolve_date_answer(state, wrong, "10 ноября 2026 года", NOW) == {"handled": False, "operations": []}


@pytest.mark.parametrize("text", ["10 ноября", "Заезд 10 ноября", "выезд 1 января"])
def test_yearless_month_never_guesses_year_even_when_another_stored_date_has_one(text):
    before = single()
    before.stays[0].check_out = "2026-11-20"
    frozen = before.model_dump(mode="json")
    answer = resolve_date_answer(before, {"field": "stay.check_in", "target_id": "s"}, text, NOW)
    assert answer["handled"] and answer["operations"] == []
    assert "год" in answer["question"]
    assert answer["pending_context"]["target_id"] == "s"
    assert before.model_dump(mode="json") == frozen
    assert resolve_date_answer(before, None, "10 ноября", NOW) == {"handled": False, "operations": []}


@pytest.mark.parametrize("text", ["Выезд 31 ноября 2026 года", "Заезд 29 февраля 2027", "выезд 0 ноября 2026"])
def test_bad_month_calendar_is_addressed_question_not_exception(text):
    answer = resolve_date_answer(single(), None, text, NOW)
    assert answer["handled"] and answer["operations"] == []
    assert "не существует" in answer["question"] and answer["pending_context"]["target_id"] == "s"


def test_month_date_in_past_is_not_rolled_forward():
    answer = resolve_date_answer(single(), None, "Заезд 10 ноября 2025 года", NOW)
    assert answer["operations"] == [] and "прошл" in answer["question"]


@pytest.mark.parametrize("text", ["Выезд 17 ноября 2026 года и бюджет 100000", "Если выезд 17 ноября 2026 года",
    "Раньше был выезд 17 ноября 2026 года", '«Выезд 17 ноября 2026 года»', "Выезд 17 ноября 2026 года?",
    "Не выезд 17 ноября 2026 года", "Выезд 17 ноября 2026 года; добавь ребёнка", "Вылет 17 ноября 2026 года"])
def test_compound_history_quotes_hypotheses_and_flight_dates_are_not_consumed(text):
    assert resolve_date_answer(single(), {"field": "stay.check_in", "target_id": "s"}, text, NOW) == {"handled": False, "operations": []}


def test_multistay_month_date_asks_target_and_reuses_owned_absolute_proposal():
    state = sample_trip()
    frozen = state.model_dump(mode="json")
    proposed = resolve_date_answer(state, None, "Выезд 14 ноября 2026 года", NOW)
    assert proposed["handled"] and not proposed["operations"]
    assert "Для какой остановки" in proposed["question"]
    assert proposed["pending_context"]["target_id"] is None
    assert proposed["pending_context"]["date_proposal"]["source_text"] == "Выезд 14 ноября 2026 года"
    bound = {"field": "stay.check_out", "context": proposed["pending_context"]}
    selected = resolve_date_answer(state, bound, "Анталья", NOW)
    assert selected["operations"] == [{"type": "upsert_stay", "target_id": "stay-antalya", "value": {"check_out": "2026-11-14"}}]
    assert selected["pending_context_clear"] == ["date_proposal"]
    assert state.model_dump(mode="json") == frozen


def test_multistay_new_bare_month_date_replaces_old_proposal_and_keeps_same_address_question():
    state = sample_trip()
    proposed = resolve_date_answer(state, None, "Выезд 14 ноября 2026 года", NOW)
    replacement = resolve_date_answer(state, {"field": "stay.check_out", "context": proposed["pending_context"]},
                                      "17 ноября 2026 года", NOW)
    assert not replacement["operations"]
    proposal = replacement["pending_context"]["date_proposal"]
    assert proposal["source_kind"] == "date_answer" and proposal["resolved_date"] == "2026-11-17"
    selected = resolve_date_answer(state, {"field": "stay.check_out", "context": replacement["pending_context"]}, "Анталья", NOW)
    assert selected["operations"][0]["value"] == {"check_out": "2026-11-17"}


def test_month_proposal_tampering_and_newer_question_keep_semantic_binding():
    state = sample_trip().model_dump(mode="json")
    proposed = resolve_date_answer(state, None, "Выезд 14 ноября 2026 года", NOW)
    bad = deepcopy(proposed["pending_context"])
    bad["date_proposal"]["source_text"] = "Заезд 14 ноября 2026 года"
    refused = resolve_date_answer(state, {"field": "stay.check_out", "context": bad}, "Анталья", NOW)
    assert not refused["operations"] and refused["pending_context_clear"] == ["date_proposal"]
    newer = {"field": "stay.check_in", "target_id": "stay-antalya", "state_version": state["state_version"],
             "context": proposed["pending_context"]}
    fresh = resolve_date_answer(state, newer, "12 ноября 2026 года", NOW)
    assert fresh["operations"][0]["value"] == {"check_in": "2026-11-12"}
    assert fresh["pending_context_clear"] == ["date_proposal"]


def test_yearless_replacement_clears_old_absolute_proposal_without_reusing_its_year():
    state = sample_trip()
    proposed = resolve_date_answer(state, None, "Выезд 14 ноября 2026 года", NOW)
    answer = resolve_date_answer(state, {"field": "stay.check_out", "context": proposed["pending_context"]},
                                 "17 ноября", NOW)
    assert answer["handled"] and not answer["operations"]
    assert "год" in answer["question"]
    assert "date_proposal" not in answer["pending_context"]
    assert answer["pending_context_clear"] == ["date_proposal"]
