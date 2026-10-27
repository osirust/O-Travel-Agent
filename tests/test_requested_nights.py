"""Durable per-stay nights; pure state/schema/slot checks, no model or HTTP."""
from copy import deepcopy
from datetime import UTC, date, datetime

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from otravel.date_slots import resolve_date_answer, resolve_duration_answer
from otravel.domain import (
    DomainError,
    Stay,
    TripState,
    apply_operations,
    sample_trip,
    validate_trip,
)
from otravel.inventory import DemoInventory
from otravel.llm_contract import build_operation_schema
from otravel.planning import assemble_plan, validate_quote

NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)


def trip(**stay):
    return TripState(trip_id="duration-owned-trip", origin="Москва",
        travelers=[{"id": "adult", "type": "adult"}],
        stays=[{"id": "s", "city": "Казань", "rooms": [{"id": "r", "traveler_ids": ["adult"]}], **stay}],
        budget={"amount_minor": 10_000_000, "currency": "RUB", "scope": "whole_trip"})


def pending(state, field="stay.requested_nights", target="s", context=None):
    return {"field": field, "target_id": target, "state_version": state.state_version,
            "context": context or {}}


def followup(state, answer):
    context = deepcopy(answer["pending_context"])
    return pending(state, context["field"], context.get("target_id"), context)


def applied(state, answer):
    assert answer["handled"] and answer["operations"]
    return apply_operations(state, answer["operations"])[0]


def test_legacy_stay_json_and_python_dump_have_no_new_none_key():
    old = {"id": "s", "city": "Казань", "check_in": None, "check_out": None, "rooms": [], "hotel_id": None}
    assert Stay.model_validate(old).model_dump(mode="json") == old
    assert Stay.model_validate({**old, "requested_nights": None}).model_dump(mode="json") == old
    assert "requested_nights" not in Stay.model_validate(old).model_dump()


def test_legacy_noop_has_no_changes_and_keeps_an_old_priced_plan_hash_valid():
    old = sample_trip().model_dump(mode="json")
    assert not any("requested_nights" in stay for stay in old["stays"])
    state = TripState.model_validate(old)
    unchanged, changes = apply_operations(state, [{"type": "upsert_stay", "target_id": state.stays[0].id,
        "value": {"requested_nights": None}}])
    assert changes == [] and unchanged.model_dump(mode="json") == old
    original = DemoInventory().search_trip(state)["plans"][0]
    plan = assemble_plan(state, original["offers"], now=NOW)
    assert plan["ready_for_simulated_checkout"]
    assert validate_quote(plan, unchanged, now=NOW) == []
    assert assemble_plan(unchanged, original["offers"], now=NOW)["snapshot_hash"] == plan["snapshot_hash"]


@pytest.mark.parametrize("invalid", [True, False, 0, -1, 366, 1.0, "5", [], {}])
def test_requested_nights_is_strict_bounded_optional_integer(invalid):
    with pytest.raises(ValidationError):
        Stay(id="s", requested_nights=invalid)


def test_five_nights_remain_durable_without_dates_then_exact_checkin_derives_checkout():
    original = trip()
    state = applied(original, resolve_duration_answer(original, pending(original, "stay.check_in"), "на 5 ночей", NOW))
    assert state.stays[0].requested_nights == 5
    assert state.stays[0].check_in is state.stays[0].check_out is None
    for _ in range(15):
        state = TripState.model_validate(state.model_dump(mode="json"))
    answer = resolve_date_answer(state, pending(state, "stay.check_in"), "2026-11-10", NOW)
    completed = applied(state, answer)
    assert completed.stays[0].check_in == date(2026, 11, 10)
    assert completed.stays[0].check_out == date(2026, 11, 15)
    assert completed.stays[0].requested_nights == 5
    assert completed.travelers == original.travelers and completed.budget == original.budget
    assert [leg.departure_date for leg in completed.legs] == [date(2026, 11, 10), date(2026, 11, 15)]


@pytest.mark.parametrize("start,nights,end", [
    ("2028-02-27", 3, "2028-03-01"), ("2026-12-30", 3, "2027-01-02"),
    ("2026-10-24", 3, "2026-10-27"), ("2027-03-27", 2, "2027-03-29"),
])
def test_calendar_nights_cross_leap_year_midnight_and_DST_without_hour_arithmetic(start, nights, end):
    stay = Stay(id="s", check_in=start, requested_nights=nights)
    assert stay.check_out.isoformat() == end
    assert (stay.check_out - stay.check_in).days == nights


def test_date_overflow_is_rejected_without_wrapping_calendar():
    with pytest.raises(ValidationError):
        Stay(id="s", check_in="9999-12-31", requested_nights=1)


def test_requested_nights_does_not_invent_checkin_from_known_checkout():
    stay = Stay(id="s", check_out="2026-11-15", requested_nights=5)
    assert stay.check_in is None and stay.check_out == date(2026, 11, 15)


def test_conflicting_explicit_dates_and_nights_are_human_domain_error_and_atomic():
    state = trip(check_in="2026-11-10", check_out="2026-11-15", requested_nights=5)
    before = state.model_dump(mode="json")
    with pytest.raises(DomainError) as error:
        apply_operations(state, [{"type": "upsert_stay", "target_id": "s", "value": {"check_out": "2026-11-17"}}])
    assert error.value.code == "duration_mismatch"
    assert "ноч" in error.value.message and "requested_nights" not in error.value.message
    assert state.model_dump(mode="json") == before
    accepted, _ = apply_operations(state, [{"type": "upsert_stay", "target_id": "s",
        "value": {"check_out": "2026-11-17", "requested_nights": 7}}])
    assert accepted.stays[0].requested_nights == 7
    cleared, _ = apply_operations(state, [{"type": "upsert_stay", "target_id": "s",
        "value": {"check_out": "2026-11-17", "requested_nights": None}}])
    assert "requested_nights" not in cleared.model_dump(mode="json")["stays"][0]


def test_explicit_shift_return_updates_nights_and_return_leg_without_dropping_people():
    state = trip(check_in="2026-11-10", check_out="2026-11-15", requested_nights=5)
    result, _ = apply_operations(state, [{"type": "shift_return", "value": {"days": 2}}])
    assert result.stays[0].requested_nights == 7 and result.stays[0].check_out == date(2026, 11, 17)
    assert result.travelers == state.travelers and result.budget == state.budget
    assert result.legs[-1].departure_date == date(2026, 11, 17)


@pytest.mark.parametrize("text", ["5 дней", "на пять дней", "5 days"])
def test_days_are_not_guessed_as_four_or_five_nights(text):
    state = trip()
    answer = resolve_duration_answer(state, pending(state, "stay.check_in"), text, NOW)
    assert answer["handled"] and answer["operations"] == []
    assert "ноч" in answer["question"]
    assert answer["pending_context"]["duration_days"] == 5
    assert state.stays[0].requested_nights is None
    after = applied(state, resolve_duration_answer(state, followup(state, answer), "4", NOW))
    assert after.stays[0].requested_nights == 4 and after.stays[0].check_in is None


@pytest.mark.parametrize("field,version", [("travelers.child_age", 0), ("stay.requested_nights", 1), (None, 0)])
def test_bare_number_is_not_nights_without_fresh_owned_slot(field, version):
    state = trip()
    p = {**pending(state, field), "state_version": version}
    assert resolve_duration_answer(state, p, "5", NOW) == {"handled": False, "operations": []}


@pytest.mark.parametrize("text", ["5 ночей и бюджет 60000", "может 5 ночей?", "не надо 5 ночей", "5.5 ночей", "1e2 ночей", "завтра"])
def test_compound_hypothetical_negated_or_other_input_is_not_captured_by_count_parser(text):
    state = trip()
    assert resolve_duration_answer(state, pending(state), text, NOW) == {"handled": False, "operations": []}


def two_stays():
    return TripState(trip_id="duration-owned-trip", stays=[{"id": "a", "city": "Стамбул"}, {"id": "b", "city": "Анталья"}])


@pytest.mark.parametrize("answer_text,target", [("Анталья", "b"), ("вторая", "b"), ("a", "a"), ("2", "b")])
def test_multicity_unknown_duration_target_is_persisted_then_selected_without_first_stay_guess(answer_text, target):
    state = two_stays()
    question = resolve_duration_answer(state, None, "5 ночей", NOW)
    assert question["operations"] == [] and question["pending_context"]["target_id"] is None
    p = followup(state, question)
    p = deepcopy(p)  # Represents persistence/reload, not an in-memory closure.
    result = applied(state, resolve_duration_answer(state.model_dump(mode="json"), p, answer_text, NOW))
    assert next(stay for stay in result.stays if stay.id == target).requested_nights == 5
    assert next(stay for stay in result.stays if stay.id != target).requested_nights is None


@pytest.mark.parametrize("change", ["version", "trip", "before_bool", "value_bool", "foreign_target", "extra"])
def test_duration_date_confirmation_rejects_tampered_or_stale_packets(change):
    state = trip(check_in="2026-11-10", check_out="2026-11-11", requested_nights=1)
    question = resolve_date_answer(state, pending(state, "stay.check_in"), "2026-11-09", NOW)
    p = followup(state, question)
    packet = p["context"]["duration_proposal"]
    if change == "version": packet["based_state_version"] += 1
    if change == "trip": packet["trip_id"] = "foreign"
    if change == "before_bool": packet["before"]["requested_nights"] = True
    if change == "value_bool": packet["value"]["requested_nights"] = True
    if change == "foreign_target": packet["target_id"] = "foreign"
    if change == "extra": packet["value"]["city"] = "Дубай"
    answer = resolve_duration_answer(state, p, "да", NOW)
    assert answer["handled"] and answer["operations"] == [] and "duration_proposal" in answer["pending_context_clear"]


@pytest.mark.parametrize("field,text,start,end,nights", [
    ("stay.check_in", "2026-11-11", "2026-11-11", "2026-11-16", 5),
    ("stay.check_out", "2026-11-17", "2026-11-10", "2026-11-17", 7),
])
def test_conflicting_date_requires_explicit_current_yes_and_preserves_original_on_no(field, text, start, end, nights):
    state = trip(check_in="2026-11-10", check_out="2026-11-15", requested_nights=5)
    before = state.model_dump(mode="json")
    question = resolve_date_answer(state, pending(state, field), text, NOW)
    assert question["handled"] and not question["operations"] and "ноч" in question["question"]
    p = followup(state, question)
    declined = resolve_duration_answer(state, p, "нет", NOW)
    assert declined["operations"] == [] and state.model_dump(mode="json") == before
    result = applied(state, resolve_duration_answer(state, p, "да", NOW))
    assert (result.stays[0].check_in.isoformat(), result.stays[0].check_out.isoformat(), result.stays[0].requested_nights) == (start, end, nights)
    assert result.travelers == state.travelers and result.budget == state.budget


def test_duration_count_change_does_not_silently_override_a_known_checkout():
    state = trip(check_in="2026-11-10", check_out="2026-11-14")
    question = resolve_duration_answer(state, None, "5 ночей", NOW)
    assert question["operations"] == [] and "изменить выезд" in question["question"]
    result = applied(state, resolve_duration_answer(state, followup(state, question), "да", NOW))
    assert result.stays[0].check_out == date(2026, 11, 15) and result.stays[0].requested_nights == 5
    assert result.legs[-1].departure_date == date(2026, 11, 15)


@pytest.mark.parametrize("clock", [None, datetime(2026, 10, 27), datetime(2026, 11, 20, tzinfo=UTC)])
def test_duration_date_confirmation_has_no_implicit_clock_and_rejects_past_start(clock):
    state = trip(check_in="2026-11-10", check_out="2026-11-15", requested_nights=5)
    question = resolve_date_answer(state, pending(state, "stay.check_in"), "2026-11-11", NOW)
    answer = resolve_duration_answer(state, followup(state, question), "да", clock)
    assert answer["handled"] and answer["operations"] == []


@pytest.mark.parametrize("value,valid", [(5, True), (None, True), (True, False), (0, False), (366, False), (1.2, False), ("5", False)])
def test_model_operation_schema_has_same_nights_shape_as_domain(value, valid):
    state = trip()
    reply = {"operations": [{"type": "upsert_stay", "target_id": "s", "value": {"requested_nights": value}}],
             "control": {"action": "none"}, "reply": "Уточним даты.", "unknowns": [], "conflicts": []}
    assert Draft202012Validator(build_operation_schema(state)).is_valid(reply) is valid


def test_duration_derived_checkout_cannot_overlap_next_stop():
    state = two_stays()
    state.stays[0].check_in = date(2026, 11, 10)
    state.stays[1].check_in, state.stays[1].check_out = date(2026, 11, 13), date(2026, 11, 17)
    answer = resolve_duration_answer(state, pending(state, target="a"), "5 ночей", NOW)
    assert answer["operations"] == [] and "не подходит" in answer["question"]
    assert state.stays[0].check_out is None and not validate_trip(trip(), final=False)


def test_saved_ambiguous_date_target_can_transition_to_addressed_duration_confirmation():
    state = TripState(trip_id="duration-date-target", stays=[
        {"id": "a", "city": "Стамбул", "check_in": "2026-11-10", "check_out": "2026-11-13", "requested_nights": 3},
        {"id": "b", "city": "Анталья", "check_in": "2026-11-13", "check_out": "2026-11-17", "requested_nights": 4}])
    target_question = resolve_date_answer(state, None, "Выезд 19.11.2026", NOW)
    assert "date_proposal" in target_question["pending_context"]
    duration_question = resolve_date_answer(state, followup(state, target_question), "Анталья", NOW)
    assert duration_question["operations"] == []
    assert "duration_proposal" in duration_question["pending_context"]
    assert "date_proposal" in duration_question["pending_context_clear"]
    result = applied(state, resolve_duration_answer(state, followup(state, duration_question), "да", NOW))
    assert result.stays[0] == state.stays[0]
    assert result.stays[1].requested_nights == 6 and result.stays[1].check_out == date(2026, 11, 19)


def test_duplicated_city_is_not_a_duration_target_but_owned_ordinal_still_works():
    state = TripState(trip_id="duration-duplicate-city", stays=[{"id": "a", "city": "Стамбул"}, {"id": "b", "city": "Стамбул"}])
    question = resolve_duration_answer(state, None, "5 ночей", NOW)
    p = followup(state, question)
    assert resolve_duration_answer(state, p, "Стамбул", NOW)["operations"] == []
    after = applied(state, resolve_duration_answer(state, p, "вторая", NOW))
    assert after.stays[0].requested_nights is None and after.stays[1].requested_nights == 5


def test_new_unrelated_edit_is_not_swallowed_by_duration_confirmation():
    state = trip(check_in="2026-11-10", check_out="2026-11-15", requested_nights=5)
    question = resolve_date_answer(state, pending(state, "stay.check_out"), "2026-11-17", NOW)
    assert resolve_duration_answer(state, followup(state, question), "Добавь ребёнка 7 лет", NOW) == {"handled": False, "operations": []}
