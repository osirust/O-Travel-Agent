"""Bound date-answer tests. No host clock, HTTP, DB, or model inference."""

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from otravel.domain import TripState, apply_operations, sample_trip
from otravel.date_slots import resolve_date_answer


NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


def trip(**stay):
    return TripState(origin="Москва", stays=[{"id": "stay-exact", "city": "Казань", **stay}])


def pending(field="stay.check_out", target="stay-exact"):
    return {"field": field, "target_id": target}


def test_owner_tomorrow_checkout_collision_names_actual_date_and_asks_later():
    before = trip(check_in="2026-10-26")
    original = before.model_dump(mode="json")
    result = resolve_date_answer(before, pending(), "завтра", NOW)
    assert result["handled"] and result["operations"] == []
    assert "26.10.2026" in result["question"]
    assert "позже" in result["question"].casefold()
    assert "завтра" in result["question"].casefold()
    assert result["pending_context"]["field"] == "stay.check_out"
    assert result["pending_context"]["target_id"] == "stay-exact"
    assert before.model_dump(mode="json") == original


@pytest.mark.parametrize("text,field,value", [
    ("26.10.2026", "stay.check_in", "2026-10-26"),
    ("2026-10-26", "stay.check_in", "2026-10-26"),
    ("сегодня", "stay.check_in", "2026-10-25"),
    ("ЗАВТРА.", "stay.check_in", "2026-10-26"),
    ("послезавтра", "stay.check_out", "2026-10-27"),
    ("27.10.2026", "stay.check_out", "2026-10-27"),
])
def test_exact_absolute_and_relative_date_produce_only_one_stay_operation(text, field, value):
    before = trip(check_in="2026-10-26") if field.endswith("check_out") else trip(check_out="2026-10-28")
    original = before.model_dump(mode="json")
    bound = pending(field)
    bound_before = deepcopy(bound)
    result = resolve_date_answer(before, bound, text, NOW)
    assert result == {"handled": True, "operations": [{"type": "upsert_stay", "target_id": "stay-exact", "value": {field.split(".")[1]: value}}]}
    after, _ = apply_operations(before, result["operations"])
    expected = deepcopy(original)
    expected["stays"][0][field.split(".")[1]] = value
    actual = after.model_dump(mode="json")
    expected.pop("legs")
    legs = actual.pop("legs")
    assert actual == expected
    stay = expected["stays"][0]
    assert [(leg["origin"], leg["destination"], leg["departure_date"]) for leg in legs] == [
        ("Москва", "Казань", stay["check_in"]), ("Казань", "Москва", stay["check_out"])]
    assert before.model_dump(mode="json") == original and bound == bound_before


def test_msk_midnight_uses_aware_reference_instead_of_host_date():
    now_utc = datetime(2026, 10, 25, 22, 30, tzinfo=timezone.utc)  # 26 October in Moscow.
    result = resolve_date_answer(trip(), pending("stay.check_in"), "завтра", now_utc)
    assert result["operations"][0]["value"] == {"check_in": "2026-10-27"}
    utc_result = resolve_date_answer(trip(), pending("stay.check_in"), "завтра", now_utc, timezone="UTC")
    assert utc_result["operations"][0]["value"] == {"check_in": "2026-10-26"}


@pytest.mark.parametrize("text", ["31.11.2026", "2026-02-29", "2026-13-01", "00.12.2026"])
def test_invalid_calendar_is_a_human_clarification_and_never_raises(text):
    before = trip()
    result = resolve_date_answer(before, pending(), text, NOW)
    assert result["handled"] and result["operations"] == []
    assert isinstance(result["question"], str) and result["question"].endswith("?")
    assert "check_" not in result["question"]
    assert before.stays[0].check_out is None


@pytest.mark.parametrize("text", ["2026-10-24", "24.10.2026"])
def test_past_checkin_is_rejected_without_autocorrection(text):
    result = resolve_date_answer(trip(), pending("stay.check_in"), text, NOW)
    assert result["handled"] and result["operations"] == []
    assert "24.10.2026" in result["question"]
    assert "прошл" in result["question"].casefold()


@pytest.mark.parametrize("text", ["сегодня", "2026-10-25"])
def test_checkout_requires_a_date_after_today_even_without_checkin(text):
    result = resolve_date_answer(trip(), pending(), text, NOW)
    assert result["handled"] and result["operations"] == []
    assert "25.10.2026" in result["question"] and "позже" in result["question"]


def test_checkin_cannot_reach_existing_checkout_and_no_automatic_day_shift():
    result = resolve_date_answer(trip(check_out="2026-10-27"), pending("stay.check_in"), "послезавтра", NOW)
    assert result["handled"] and result["operations"] == []
    assert "27.10.2026" in result["question"] and "раньше" in result["question"]


@pytest.mark.parametrize("bound", [None, {}, {"field": "stay.check_out"}, pending(target="missing"),
    pending("check_out"), pending("traveler.age"), {"field": "stay.check_out", "target_id": 1}])
def test_missing_or_nonexact_slot_and_unknown_target_are_unhandled(bound):
    assert resolve_date_answer(trip(), bound, "завтра", NOW) == {"handled": False, "operations": []}


@pytest.mark.parametrize("text", ["завтра или послезавтра", "завтра и бюджет 100000", "отмена",
    "подожди", "завтра; добавь взрослого", "завтра\nубери ребёнка", "не завтра", "если завтра",
    "завтра?", "26.10", "26.10.26", "2026-10-26T10:00:00", "2026-10-26 и 2026-10-28",
    "через два дня", "выезд завтра", "перенеси на завтра"])
def test_compound_control_ambiguous_and_unimplemented_date_language_is_unhandled(text):
    before = trip()
    assert resolve_date_answer(before, pending(), text, NOW) == {"handled": False, "operations": []}
    assert before.stays[0].check_out is None


@pytest.mark.parametrize("now,tz", [(datetime(2026, 10, 25), "Europe/Moscow"), (None, "Europe/Moscow"),
    (NOW, "Unknown/Timezone"), (NOW, None)])
def test_missing_aware_clock_or_timezone_never_uses_host_fallback(now, tz):
    assert resolve_date_answer(trip(), pending(), "завтра", now, timezone=tz) == {"handled": False, "operations": []}


def test_nested_explicit_target_and_dict_state_preserve_unrelated_context():
    bound = {"field": "stay.check_out", "context": {"stay_id": "stay-exact", "children_known": True}}
    before = trip(check_in="2026-10-26").model_dump(mode="json")
    snapshot = deepcopy((before, bound))
    result = resolve_date_answer(before, bound, "завтра", NOW)
    assert result["pending_context"]["target_id"] == "stay-exact"
    assert result["pending_context"]["children_known"] is True
    assert (before, bound) == snapshot


def test_only_targeted_stay_changes_group_allocation_budget_and_other_stay_preserved():
    before = sample_trip()
    original = before.model_dump(mode="json")
    result = resolve_date_answer(before, {"field": "stay.check_out", "target_id": "stay-antalya"}, "14.11.2026", NOW)
    assert result["operations"] == [{"type": "upsert_stay", "target_id": "stay-antalya", "value": {"check_out": "2026-11-14"}}]
    after, _ = apply_operations(before, result["operations"])
    expected = deepcopy(original)
    expected["stays"][-1]["check_out"] = "2026-11-14"
    expected["legs"][-1]["departure_date"] = "2026-11-14"  # Domain-derived return leg.
    assert after.model_dump(mode="json") == expected
    assert before.model_dump(mode="json") == original


def test_leg_departure_is_explicitly_unsupported_without_a_public_leg_setter():
    before = sample_trip()
    snapshot = before.model_dump(mode="json")
    result = resolve_date_answer(before, {"field": "leg.departure_date", "target_id": "leg-out"}, "26.10.2026", NOW)
    assert result == {"handled": False, "operations": []}
    assert before.model_dump(mode="json") == snapshot
