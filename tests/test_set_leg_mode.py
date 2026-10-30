"""Owned leg mode edits preserve identity, dates and the rest of the trip."""
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from otravel.domain import DomainError, TripState, apply_operations
from otravel.llm_contract import OPERATION_TYPES, build_operation_schema


def trip():
    return TripState(trip_id="owned-leg-mode", origin="Москва", travelers=[{"id": "a", "type": "adult"}],
        stays=[{"id": "s", "city": "Казань", "check_in": "2026-11-10", "check_out": "2026-11-15",
                "rooms": [{"id": "r", "traveler_ids": ["a"]}]}],
        legs=[{"id": "out", "origin": "Москва", "destination": "Казань", "departure_date": "2026-11-10"},
              {"id": "back", "origin": "Казань", "destination": "Москва", "departure_date": "2026-11-15"}],
        budget={"amount_minor": 10_000_000, "currency": "RUB"}, preferences=[{"key": "price_priority", "value": "lowest_total"}])


def operation(target="out", mode="train", **extra):
    return {"type": "set_leg_mode", "target_id": target, "value": {"mode": mode, **extra}}


def valid_schema(state, ops):
    response = {"operations": ops, "control": {"action": "none"}, "reply": "Режим транспорта изменён.",
                "unknowns": [], "conflicts": []}
    return Draft202012Validator(build_operation_schema(state)).is_valid(response)


def test_only_selected_owned_leg_mode_changes_without_new_identity_or_date():
    state = trip()
    before = state.model_dump(mode="json")
    updated, changes = apply_operations(state, [operation()])
    expected = deepcopy(before)
    expected["legs"][0]["mode"] = "train"
    assert updated.model_dump(mode="json") == expected
    assert state.model_dump(mode="json") == before
    assert changes and all(change["field"] == "legs" for change in changes)
    assert valid_schema(state, [operation()]) and "set_leg_mode" in OPERATION_TYPES


@pytest.mark.parametrize("mode", [None, True, 1, "bus", "", ["train"]])
def test_unknown_or_nonstring_modes_fail_closed(mode):
    state = trip()
    with pytest.raises(DomainError) as error:
        apply_operations(state, [operation(mode=mode)])
    assert error.value.code == "invalid_leg_mode"
    assert not valid_schema(state, [operation(mode=mode)])


@pytest.mark.parametrize("target", [None, "foreign", "new-leg-1"])
def test_mode_cannot_create_a_leg_or_edit_an_unknown_target(target):
    state = trip()
    before = state.model_dump(mode="json")
    with pytest.raises(DomainError) as error:
        apply_operations(state, [operation(target=target)])
    assert error.value.code == "unknown_target"
    assert state.model_dump(mode="json") == before and not valid_schema(state, [operation(target=target)])


@pytest.mark.parametrize("extra", [{"origin": "Сочи"}, {"id": "other"}, {"departure_date": "2026-11-20"}, {"hotel_id": "h"}])
def test_mode_setter_is_not_a_general_leg_editor(extra):
    state = trip()
    with pytest.raises(DomainError) as error:
        apply_operations(state, [operation(**extra)])
    assert error.value.code == "unknown_fields"
    assert not valid_schema(state, [operation(**extra)])


def test_mode_setter_is_unavailable_in_model_schema_without_owned_legs():
    state = TripState()
    assert not valid_schema(state, [operation()])


def test_same_mode_is_a_real_noop_and_does_not_rebuild_route():
    state = trip()
    after, changes = apply_operations(state, [operation(mode="flight")])
    assert after.model_dump(mode="json") == state.model_dump(mode="json") and changes == []


def test_mode_and_later_addressed_date_edit_keep_the_same_leg_mode_and_ID():
    state = trip()
    after, _ = apply_operations(state, [operation(),
        {"type": "upsert_stay", "target_id": "s", "value": {"check_out": "2026-11-17"}},
        {"type": "set_budget", "value": {"amount_minor": 9_000_000, "currency": "RUB"}}])
    assert [(leg.id, leg.mode) for leg in after.legs] == [("out", "train"), ("back", "flight")]
    assert after.legs[1].departure_date.isoformat() == "2026-11-17"
    assert after.stays[0].check_in == state.stays[0].check_in
    assert after.travelers == state.travelers and after.preferences == state.preferences
    assert after.budget.amount_minor == 9_000_000


def test_invalid_second_leg_in_batch_leaves_original_state_untouched():
    state = trip()
    before = state.model_dump(mode="json")
    with pytest.raises(DomainError):
        apply_operations(state, [operation(), operation(target="foreign")])
    assert state.model_dump(mode="json") == before
