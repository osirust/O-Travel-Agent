"""Price priority is a soft preference, never a numeric budget or hard filter."""
from copy import deepcopy

import pytest

from otravel.domain import DomainError, Operation, Preference, TripState, apply_operations, sample_trip


def operation(**extra):
    return Operation(type="set_preference", value={"key": "price_priority", "value": "lowest_total", **extra})


def test_minimum_cost_on_empty_trip_does_not_create_budget_or_other_trip_data():
    original = TripState()
    before = original.model_dump(mode="json")
    changed, diff = apply_operations(original, [operation()])
    assert changed.preferences == [Preference(key="price_priority", value="lowest_total", weight=1)]
    assert changed.budget is None
    assert changed.model_dump(mode="json") | {"preferences": []} == before
    assert original.model_dump(mode="json") == before
    assert [entry["field"] for entry in diff] == ["preferences"]


def test_price_preference_preserves_budget_hard_constraints_route_and_membership():
    original = sample_trip()
    original.preferences = [Preference(key="quiet", value=True, weight=0.7)]
    original.legs[0].mode = "train"
    before = original.model_dump(mode="json")
    changed, diff = apply_operations(original, [operation(weight=0.5)])
    after = changed.model_dump(mode="json")
    assert {key: value for key, value in after.items() if key != "preferences"} == {key: value for key, value in before.items() if key != "preferences"}
    assert after["preferences"] == [before["preferences"][0], {"key": "price_priority", "value": "lowest_total", "weight": 0.5}]
    assert original.model_dump(mode="json") == before
    assert [entry["field"] for entry in diff] == ["preferences"]


def test_repeated_preference_is_noop_and_omitted_weight_preserves_explicit_weight():
    first, _ = apply_operations(sample_trip(), [operation(weight=0.4)])
    second, diff = apply_operations(first, [operation()])
    assert second.model_dump() == first.model_dump()
    assert diff == []
    third, diff = apply_operations(second, [operation(weight=0.8)])
    assert third.preferences[0].weight == 0.8
    assert len(third.preferences) == 1
    assert [entry["field"] for entry in diff] == ["preferences"]


def test_existing_duplicate_target_preferences_are_deduplicated_without_reordering_others():
    state = TripState(preferences=[Preference(key="quiet"), Preference(key="price_priority", value="lowest_total", weight=0.3),
                                  Preference(key="balcony"), Preference(key="price_priority", value="lowest_total", weight=0.9)])
    changed, _ = apply_operations(state, [operation()])
    assert [(item.key, item.weight) for item in changed.preferences] == [("quiet", 1), ("price_priority", 0.3), ("balcony", 1)]


@pytest.mark.parametrize("value", [
    {}, {"key": "price_priority"}, {"key": "other", "value": "lowest_total"},
    {"key": "price_priority", "value": "cheapest"}, {"key": "price_priority", "value": 0},
    {"key": "price_priority", "value": "lowest_total", "amount_minor": 0},
    {"key": "price_priority", "value": "lowest_total", "hard": True},
])
def test_only_the_whitelisted_preference_is_accepted_atomically(value):
    state = sample_trip()
    before = deepcopy(state.model_dump(mode="json"))
    with pytest.raises(DomainError):
        apply_operations(state, [Operation(type="set_preference", value=value)])
    assert state.model_dump(mode="json") == before


@pytest.mark.parametrize("weight", [True, False, "0.5", None, -0.1, 1.1, float("nan"), float("inf"), -float("inf")])
def test_invalid_weight_is_not_coerced_and_does_not_change_input(weight):
    state = sample_trip()
    before = state.model_dump(mode="json")
    with pytest.raises(DomainError):
        apply_operations(state, [operation(weight=weight)])
    assert state.model_dump(mode="json") == before


@pytest.mark.parametrize("weight", [0, 1, 0.25])
def test_valid_optional_weight_stays_a_preference(weight):
    state, _ = apply_operations(TripState(), [operation(weight=weight)])
    assert state.preferences[0].weight == weight
    assert state.budget is None


def test_preference_has_no_entity_target_and_failed_batch_retains_original_state():
    state = sample_trip()
    before = state.model_dump(mode="json")
    with pytest.raises(DomainError):
        apply_operations(state, [Operation(type="set_preference", target_id="stay-istanbul", value={"key": "price_priority", "value": "lowest_total"})])
    with pytest.raises(DomainError):
        apply_operations(state, [operation(), Operation(type="set_budget", value={"amount_minor": 0})])
    assert state.model_dump(mode="json") == before


def test_early_morning_preference_preserves_compound_trip_facts_without_invented_hours():
    state = sample_trip()
    state.preferences = [Preference(key="price_priority", value="lowest_total")]
    before = state.model_dump(mode="json")
    changed, diff = apply_operations(state, [Operation(type="set_preference", value={"key": "departure_time", "value": "early_morning"})])
    after = changed.model_dump(mode="json")
    assert {key: value for key, value in after.items() if key != "preferences"} == {key: value for key, value in before.items() if key != "preferences"}
    assert after["preferences"] == [before["preferences"][0], {"key": "departure_time", "value": "early_morning", "weight": 1.0}]
    assert [item["field"] for item in diff] == ["preferences"]
    assert all("departure_time" not in leg and "departure_hour" not in leg for leg in after["legs"])


@pytest.mark.parametrize("value", [
    {"key": "departure_time", "value": "05:00"},
    {"key": "departure_time", "value": 6},
    {"key": "departure_time", "value": "lowest_total"},
    {"key": "price_priority", "value": "early_morning"},
    {"key": "departure_time", "value": "early_morning", "hour": 6},
])
def test_preference_pairs_do_not_admit_invented_exact_time_or_mixed_meanings(value):
    original = sample_trip()
    before = original.model_dump(mode="json")
    with pytest.raises(DomainError):
        apply_operations(original, [Operation(type="set_preference", value=value)])
    assert original.model_dump(mode="json") == before
