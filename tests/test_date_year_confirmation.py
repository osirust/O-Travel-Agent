"""Exact addressed checkout, explicit New Year candidate; no inferred consent."""
from datetime import UTC, datetime

import pytest

from otravel.date_slots import resolve_date_answer
from otravel.domain import TripState, apply_operations

NOW = datetime(2026, 10, 27, 18, tzinfo=UTC)


def state():
    return TripState(trip_id="owned-december", state_version=2, origin="Москва",
        travelers=[{"id": "one-adult", "type": "adult"}],
        stays=[{"id": "owned-stay", "city": "Стамбул", "check_in": "2026-12-28"}])


def pending(before, context=None):
    return {"question_id": "server-owned-checkout", "state_version": before.state_version,
        "field": "stay.check_out", "target_id": "owned-stay", "reason": "clarification",
        "context": context or {}}


def proposal(before=None):
    before = before or state()
    answer = resolve_date_answer(before, pending(before), "5 января", NOW)
    assert answer["handled"] and answer["operations"] == []
    return answer, pending(before, answer["pending_context"])


@pytest.mark.parametrize("text", ["на 27 декабря", "27 декабря", "на27 декабря"])
def test_checkout_before_arrival_is_explained_without_rolling_a_year_or_mutating(text):
    before = state()
    snapshot = before.model_dump(mode="json")
    answer = resolve_date_answer(before, pending(before), text, NOW)
    assert answer["handled"] and answer["operations"] == []
    assert "27.12.2026" in answer["question"] and "28.12.2026" in answer["question"]
    assert "позже" in answer["question"] and "2027" not in answer["question"]
    assert "date_proposal" not in answer["pending_context"]
    assert before.model_dump(mode="json") == snapshot


@pytest.mark.parametrize("text", ["на 05.01.2027", "на05.01.2027", "на 2027-01-05"])
def test_whole_addressed_preposition_accepts_an_explicit_absolute_date(text):
    before = state()
    answer = resolve_date_answer(before, pending(before), text, NOW)
    assert answer["operations"] == [{"type": "upsert_stay", "target_id": "owned-stay",
        "value": {"check_out": "2027-01-05"}}]


@pytest.mark.parametrize("change", [{"state_version": 1}, {"state_version": True}, {"question_id": ""},
    {"target_id": "foreign"}, {"reason": "user_pause"}, {"field": "origin.city"},
    {"context": {"based_state_version": 1}}, {"context": {"trip_id": "foreign"}}])
def test_prefixed_date_requires_fresh_owned_pending_and_never_extracts_a_new_target(change):
    before = state()
    bound = {**pending(before), **change}
    answer = resolve_date_answer(before, bound, "на 05.01.2027", NOW)
    assert not answer["handled"] and answer["operations"] == []


@pytest.mark.parametrize("text", ["5 января", "на 5 января", "Выезд 5 января"])
def test_january_after_known_december_is_only_a_candidate_until_explicit_confirmation(text):
    before = state()
    original = before.model_dump(mode="json")
    answer = resolve_date_answer(before, pending(before), text, NOW)
    assert answer["handled"] and answer["operations"] == []
    packet = answer["pending_context"]["date_proposal"]
    assert packet["owner"] == "date_slots.year.v1" and packet["kind"] == "checkout_year_confirmation"
    assert packet["candidate_date"] == "2027-01-05" and packet["before"] == {
        "check_in": "2026-12-28", "check_out": None, "requested_nights": None}
    assert packet["source_text"] == text and packet["based_state_version"] == 2
    assert "05.01.2027" in answer["question"] and "да или нет" in answer["question"]
    confirmed = resolve_date_answer(before, pending(before, answer["pending_context"]), "да", NOW)
    assert confirmed["operations"] == [{"type": "upsert_stay", "target_id": "owned-stay",
        "value": {"check_out": "2027-01-05"}}]
    assert confirmed["pending_context_clear"] == ["date_proposal"]
    after, _ = apply_operations(before, confirmed["operations"])
    assert after.stays[0].check_out.isoformat() == "2027-01-05"
    assert before.model_dump(mode="json") == original


def test_refusal_clears_only_year_candidate_and_does_not_choose_any_date():
    before = state()
    _, bound = proposal(before)
    answer = resolve_date_answer(before, bound, "нет", NOW)
    assert answer["handled"] and not answer["operations"]
    assert answer["pending_context_clear"] == ["date_proposal"]
    assert "date_proposal" not in answer["pending_context"] and before.stays[0].check_out is None


@pytest.mark.parametrize("mutation", ["state_version", "trip_id", "target", "removed_stay", "baseline", "bool_version",
    "candidate", "source", "explicit_source_year", "source_december", "timezone", "pending_field", "context_target", "question_id", "future_clock"])
def test_confirmation_revalidates_saved_source_baseline_version_and_owned_slot(mutation):
    before = state()
    _, bound = proposal(before)
    data = before.model_dump(mode="json")
    packet = bound["context"]["date_proposal"]
    if mutation == "state_version":
        data["state_version"] += 1
    elif mutation == "trip_id":
        data["trip_id"] = "foreign"
    elif mutation == "target":
        packet["target_id"] = "foreign"
    elif mutation == "removed_stay":
        data["stays"] = []
    elif mutation == "baseline":
        data["stays"][0]["check_in"] = "2026-12-29"
    elif mutation == "bool_version":
        packet["based_state_version"] = True
    elif mutation == "candidate":
        packet["candidate_date"] = "2028-01-05"
    elif mutation == "source":
        packet["source_text"] = "Если 5 января"
    elif mutation == "explicit_source_year":
        packet["source_text"] = "5 января 2028"
    elif mutation == "source_december":
        packet["source_text"] = "27 декабря"
    elif mutation == "timezone":
        packet["timezone"] = "Europe/Berlin"
    elif mutation == "pending_field":
        bound["field"] = "origin.city"
    elif mutation == "context_target":
        bound["context"]["target_id"] = "foreign"
    elif mutation == "question_id":
        bound["question_id"] = ""
    else:
        packet["reference_clock"] = "2026-10-28T18:00:00+00:00"
        packet["reference_date"] = "2026-10-28"
    answer = resolve_date_answer(data, bound, "да", NOW)
    assert answer["handled"] and answer["operations"] == []
    assert answer["pending_context_clear"] == ["date_proposal"]


@pytest.mark.parametrize("text", ['«5 января»', 'Если 5 января', '5 января?', 'на 5 января; бюджет 300000',
    'Объясни на 5 января', 'Напомни 5 января', 'Не на 5 января'])
def test_question_quotes_hypothesis_or_compound_is_not_a_yearless_date_command(text):
    answer = resolve_date_answer(state(), pending(state()), text, NOW)
    assert not answer["handled"] and not answer["operations"]


@pytest.mark.parametrize("text", ["пауза", "стоп", "отмена", "продолжай", "подожди", "да, но добавь ребёнка"])
def test_year_packet_does_not_swallow_execution_controls_or_compound_edits(text):
    _, bound = proposal()
    answer = resolve_date_answer(state(), bound, text, NOW)
    assert not answer["handled"] and not answer["operations"]


def test_explicit_new_year_is_not_silently_replaced_by_the_suggested_year():
    _, bound = proposal()
    answer = resolve_date_answer(state(), bound, "05.01.2028", NOW)
    assert answer["operations"][0]["value"] == {"check_out": "2028-01-05"}
    assert answer["pending_context_clear"] == ["date_proposal"]


def test_saved_nights_conflict_remains_a_second_explicit_choice_after_year_confirmation():
    data = state().model_dump(mode="json")
    data["stays"][0]["requested_nights"] = 7  # Labelled legacy dict, no automatic derived checkout.
    proposed = resolve_date_answer(data, pending(state()), "5 января", NOW)
    bound = pending(state(), proposed["pending_context"])
    answer = resolve_date_answer(data, bound, "да", NOW)
    assert answer["handled"] and not answer["operations"]
    assert answer["pending_context"]["duration_proposal"]["value"]["requested_nights"] == 8
    assert "8 ночей вместо 7" in answer["question"]
    assert answer["pending_context_clear"] == ["date_proposal"]


def test_candidate_cannot_bypass_a_neighbouring_stop():
    before = state()
    _, bound = proposal(before)
    data = before.model_dump(mode="json")
    data["stays"].append({"id": "second", "city": "Анталья", "check_in": "2027-01-04", "check_out": "2027-01-07", "rooms": [], "hotel_id": None})
    answer = resolve_date_answer(data, bound, "да", NOW)
    assert answer["handled"] and not answer["operations"] and "маршруту" in answer["question"]


def test_existing_checkout_or_other_month_without_year_keeps_the_no_guess_policy():
    before = state()
    data = before.model_dump(mode="json")
    data["stays"][0]["check_out"] = "2027-01-06"
    for value, text in ((data, "5 января"), (before, "5 февраля")):
        answer = resolve_date_answer(value, pending(before), text, NOW)
        assert answer["handled"] and not answer["operations"]
        assert "год" in answer["question"] and "date_proposal" not in answer["pending_context"]


def test_confirmation_without_an_owned_candidate_does_not_set_a_date():
    answer = resolve_date_answer(state(), pending(state()), "да", NOW)
    assert not answer["handled"] and not answer["operations"]
