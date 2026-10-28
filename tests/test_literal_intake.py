"""Pure missing-fact recovery contracts; no model, directory, SQL or HTTP calls."""

from copy import deepcopy
from datetime import UTC, date, datetime

import pytest

from otravel.domain import TripState, apply_operations
from otravel.literal_intake import ground_explicit_initial_facts

NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)
OWNER_TEXT = "из москвы, я один взрослый и хочу в белгород в отель на неделю с 28 октября"


def _state(**overrides):
    return TripState.model_validate({"trip_id": "literal-intake-fixture", **overrides}).model_dump(mode="json")


def _call(text, operations=None, *, state=None, pending=None, now=NOW):
    state = _state() if state is None else state
    operations = [] if operations is None else operations
    before = deepcopy((state, operations, pending))
    result, meta = ground_explicit_initial_facts(state, text, operations, pending=pending, now=now)
    assert (state, operations, pending) == before
    assert isinstance(result, list)
    assert {"recovered", "notes", "conflicts", "unknowns", "speech_act"} <= meta.keys()
    assert type(meta["recovered"]) is bool
    assert all(isinstance(meta[key], list) for key in ("notes", "conflicts", "unknowns"))
    return result, meta


def _of_type(operations, kind):
    return [operation for operation in operations if operation["type"] == kind]


def _partial_state():
    return _state(origin="Москва", travelers=[{"id": "owned-adult", "type": "adult"}],
        stays=[{"id": "own-stay", "city": "Казань", "rooms": [{"id": "owned-room", "traveler_ids": ["owned-adult"]}]}])


def test_owner_initial_declaration_recovers_only_literal_facts_and_domain_derives_checkout():
    operations, meta = _call(OWNER_TEXT)
    assert len(operations) == 3
    assert _of_type(operations, "set_origin") == [{"type": "set_origin", "value": {"city": "Москва"}}]
    assert _of_type(operations, "add_traveler") == [{"type": "add_traveler",
        "value": {"id": "new-traveler-1", "type": "adult"}}]
    stay = _of_type(operations, "upsert_stay")
    assert stay == [{"type": "upsert_stay", "target_id": "new-stay-1",
        "value": {"check_in": "2026-10-28", "requested_nights": 7}}]
    assert "city" not in stay[0]["value"] and "check_out" not in stay[0]["value"]
    assert meta["recovered"] is True and meta["speech_act"] == "CHANGE" and not meta["conflicts"]
    changed, _ = apply_operations(TripState.model_validate(_state()), operations)
    assert changed.origin == "Москва" and len(changed.travelers) == 1
    assert changed.stays[0].city is None and changed.stays[0].requested_nights == 7
    assert changed.stays[0].check_in == date(2026, 10, 28)
    assert changed.stays[0].check_out == date(2026, 11, 4)


def test_other_initial_declaration_recovers_two_adults_and_explicit_total_ruble_budget():
    operations, meta = _call("Едем из Казани, двое взрослых, общий бюджет 40 тысяч рублей.")
    assert _of_type(operations, "set_origin")[0]["value"] == {"city": "Казань"}
    people = _of_type(operations, "add_traveler")
    assert [person["value"]["id"] for person in people] == ["new-traveler-1", "new-traveler-2"]
    assert all(person["value"] == {"id": person["value"]["id"], "type": "adult"} for person in people)
    assert _of_type(operations, "set_budget")[0]["value"] == {
        "amount_minor": 4_000_000, "currency": "RUB", "scope": "whole_trip"}
    assert not _of_type(operations, "upsert_stay")
    assert meta["recovered"] is True and not meta["conflicts"]


def test_full_iso_checkin_and_explicit_numeric_nights_are_not_owner_phrase_specific():
    operations, meta = _call("Едем из Казани в отель, 2 взрослых, на 3 ночи с 2026-11-03.")
    stay = _of_type(operations, "upsert_stay")[0]
    assert stay["value"] == {"check_in": "2026-11-03", "requested_nights": 3}
    changed, _ = apply_operations(TripState.model_validate(_state()), operations)
    assert changed.stays[0].check_out == date(2026, 11, 6)
    assert meta["recovered"] is True


@pytest.mark.parametrize("start", ["28 сентября", "26 октября"])
def test_omitted_year_does_not_roll_a_past_date_into_next_year(start):
    operations, meta = _call(f"Хочу в отель на 7 ночей с {start}.")
    assert all("check_in" not in op["value"] for op in _of_type(operations, "upsert_stay"))
    assert meta["unknowns"]


def test_missing_reference_clock_does_not_invent_a_year():
    operations, meta = _call("Хочу в отель на неделю с 28 октября.", now=None)
    assert all("check_in" not in op["value"] for op in _of_type(operations, "upsert_stay"))
    assert meta["unknowns"]


def test_child_presence_and_first_person_do_not_invent_adult_count_child_count_or_age():
    operations, meta = _call("Я еду с ребёнком.")
    assert not _of_type(operations, "add_traveler")
    assert meta["recovered"] is False


def test_already_saved_origin_budget_and_people_are_not_replaced_by_fallback():
    state = _state(origin="Москва", budget={"amount_minor": 5_000_000, "currency": "RUB"},
        travelers=[{"id": "adult-a", "type": "adult"}, {"id": "adult-b", "type": "adult"}])
    operations, meta = _call("Едем из Казани, один взрослый, общий бюджет 40 тысяч рублей.", state=state)
    assert operations == [] and meta["recovered"] is False


@pytest.mark.parametrize("text", ["Едем, один взрослый.", "Едем, двое взрослых."])
def test_existing_adult_count_at_least_declared_never_creates_or_removes_people(text):
    state = _state(travelers=[{"id": "adult-a", "type": "adult"}, {"id": "adult-b", "type": "adult"}])
    operations, meta = _call(text, state=state)
    assert operations == [] and meta["recovered"] is False


@pytest.mark.parametrize("text,raw,kind,field,expected", [
    (OWNER_TEXT, {"type": "set_origin", "value": {"city": "Казань"}}, "set_origin", "city", "Казань"),
    ("Едем, общий бюджет 40 тысяч рублей.", {"type": "set_budget", "value": {"amount_minor": 5_000_000}},
     "set_budget", "amount_minor", 5_000_000),
    ("Хочу в отель на неделю с 28 октября.", {"type": "upsert_stay", "target_id": "new-stay-1",
        "value": {"check_in": "2026-11-02"}}, "upsert_stay", "check_in", "2026-11-02"),
], ids=["origin", "budget", "date"])
def test_conflicting_raw_fact_is_preserved_and_reported_without_field_override(text, raw, kind, field, expected):
    operations, meta = _call(text, [raw])
    assert operations[0] == raw
    values = [op["value"][field] for op in _of_type(operations, kind) if field in op["value"]]
    assert values == [expected]
    assert meta["conflicts"]


def test_fully_covered_raw_initial_facts_are_idempotent_without_new_notes():
    raw = [
        {"type": "set_origin", "value": {"city": "Москва"}},
        {"type": "add_traveler", "value": {"id": "new-traveler-1", "type": "adult"}},
        {"type": "upsert_stay", "target_id": "new-stay-1",
            "value": {"check_in": "2026-10-28", "requested_nights": 7}},
    ]
    operations, meta = _call(OWNER_TEXT, raw)
    assert operations == raw
    assert meta["recovered"] is False and meta["notes"] == [] and meta["speech_act"] is None
    assert not meta["conflicts"] and not meta["unknowns"]


def test_unique_partial_stay_missing_dates_preserves_its_city_rooms_and_owned_ids():
    state = _partial_state()
    operations, meta = _call("Нужно проживание на 7 ночей с 28 октября.", state=state)
    assert operations == [{"type": "upsert_stay", "target_id": "own-stay",
        "value": {"check_in": "2026-10-28", "requested_nights": 7}}]
    changed, _ = apply_operations(TripState.model_validate(state), operations)
    assert changed.stays[0].id == "own-stay" and changed.stays[0].city == "Казань"
    assert changed.stays[0].rooms[0].model_dump(mode="json") == state["stays"][0]["rooms"][0]
    assert changed.stays[0].check_out == date(2026, 11, 4)
    assert meta["recovered"] is True


def test_completed_or_multicity_state_does_not_enter_initial_recovery():
    complete = _state(origin="Москва", travelers=[{"id": "adult", "type": "adult"}],
        stays=[{"id": "owned", "city": "Казань", "check_in": "2026-11-10", "check_out": "2026-11-13",
            "rooms": [{"id": "room", "traveler_ids": ["adult"]}]}])
    multicity = _state(stays=[{"id": "first", "city": "Казань"}, {"id": "second", "city": "Стамбул"}])
    for state in (complete, multicity):
        operations, meta = _call(OWNER_TEXT, state=state)
        assert operations == [] and meta["recovered"] is False


@pytest.mark.parametrize("text", [
    "Едем из Москвы, один взрослый, бюджет 40 тысяч рублей?",
    "Если едем из Москвы, один взрослый, бюджет 40 тысяч рублей.",
    "«Едем из Москвы, один взрослый, бюджет 40 тысяч рублей.»",
    "Раньше едем из Москвы, один взрослый, бюджет 40 тысяч рублей.",
    "Едем из Москвы или Казани, один взрослый, бюджет 40 тысяч рублей.",
    "Не едем из Москвы, один взрослый, бюджет 40 тысяч рублей.",
], ids=["question", "hypothetical", "quotation", "history", "alternative", "negation"])
def test_noncurrent_or_ambiguous_text_does_not_authorize_recovery(text):
    operations, meta = _call(text)
    assert operations == [] and meta["recovered"] is False


@pytest.mark.parametrize("change", ["foreign-target", "stale-version", "foreign-trip"])
def test_pending_target_must_not_redirect_dates_to_foreign_or_stale_stay(change):
    state = _partial_state()
    pending = {"field": "stay.check_in", "state_version": state["state_version"], "target_id": "own-stay",
        "context": {"field": "stay.check_in", "target_id": "own-stay", "trip_id": state["trip_id"],
            "based_state_version": state["state_version"]}}
    if change == "foreign-target":
        pending["target_id"] = "foreign-stay"
        pending["context"]["target_id"] = "foreign-stay"
    elif change == "stale-version":
        pending["context"]["based_state_version"] += 1
    else:
        pending["context"]["trip_id"] = "other-trip"
    operations, meta = _call("Нужно проживание на 7 ночей с 28 октября.", state=state, pending=pending)
    assert not _of_type(operations, "upsert_stay")
    assert meta["recovered"] is False
