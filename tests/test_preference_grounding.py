"""Pure authority checks for protected preferences, separate from domain support."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from otravel.domain import DomainError, TripState, apply_operations
from otravel.preference_grounding import guard_preference_operations


@pytest.mark.parametrize("text", [
    "Минимум стоимости",
    "Хочу номер до 10000 рублей за ночь, предпочтительно подешевле",
])
def test_existing_explicit_bare_or_preferably_cost_choice_remains_authorized(text):
    operation = {"type": "set_preference", "value": {"key": "price_priority", "value": "lowest_total"}}
    result, meta = guard_preference_operations({"preferences": []}, [operation], text)
    assert result == [operation] and not meta["conflicts"]

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "regression" / "world-r4-preferences-20261027.json"
UNRELATED = "Заезд 10 ноября, один взрослый, общий бюджет 300 тысяч рублей."
_ABSENT = object()


def _state(key=None, value="lowest_total", weight=1):
    preferences = [] if key is None else [{"key": key, "value": value, "weight": weight}]
    return TripState(trip_id="preference-authority-fixture", preferences=preferences).model_dump(mode="json")


def _set(key, value, *, weight=_ABSENT):
    fields = {"key": key, "value": deepcopy(value)}
    if weight is not _ABSENT:
        fields["weight"] = weight
    return {"type": "set_preference", "target_id": None, "value": fields}


def _clear(key):
    return {"type": "clear_preference", "target_id": None, "value": {"key": key}}


def _call(state, operations, text):
    before = deepcopy((state, operations))
    checked, meta = guard_preference_operations(state, operations, text)
    assert (state, operations) == before
    assert isinstance(checked, list)
    assert {"notes", "unknowns", "conflicts"} <= meta.keys()
    assert all(isinstance(meta[key], list) for key in ("notes", "unknowns", "conflicts"))
    return checked, meta


def test_actual_world_r4_suppresses_only_two_unrequested_preferences_and_keeps_other_raw_ops_exact():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture["finding_id"] == "HARNESS-004-UNREQUESTED-PREFERENCE-001"
    assert fixture["before_preferences"] == []
    raw = fixture["operations"]
    checked, meta = _call(_state(), raw, fixture["input"])
    expected = [op for op in raw if op["type"] != "set_preference"]
    assert len(expected) == 5 and checked == expected
    assert {op["value"]["key"] for op in raw if op["type"] == "set_preference"} == set(fixture["expected_suppressed_keys"])
    assert meta["notes"]


@pytest.mark.parametrize("key,value", [
    ("price_priority", "lowest_total"),
    ("price_priority", {"invented": "highest_total"}),
    ("departure_time", "early_morning"),
    ("departure_time", "late_night"),
    ("departure_window", {"after": "06:00"}),
])
def test_unrequested_new_protected_key_is_suppressed_for_any_model_value(key, value):
    checked, meta = _call(_state(), [_set(key, value)], UNRELATED)
    assert checked == [] and meta["notes"]


@pytest.mark.parametrize("key", ["price_priority", "departure_time", "departure_window"])
def test_unrequested_clear_is_suppressed_for_every_protected_key(key):
    value = "lowest_total" if key == "price_priority" else "early_morning"
    checked, meta = _call(_state(key, value), [_clear(key)], UNRELATED)
    assert checked == [] and meta["notes"]


@pytest.mark.parametrize("key,value,saved_weight,raw_weight", [
    ("price_priority", "lowest_total", 1, _ABSENT),
    ("price_priority", "lowest_total", 0.4, 0.4),
    ("departure_time", "early_morning", 0.4, _ABSENT),
])
def test_exact_saved_key_value_effective_weight_noop_is_retained_without_a_new_request(key, value, saved_weight, raw_weight):
    raw = [_set(key, value, weight=raw_weight)]
    checked, _ = _call(_state(key, value, saved_weight), raw, UNRELATED)
    assert checked == raw


@pytest.mark.parametrize("key,value", [("price_priority", "lowest_total"), ("departure_time", "early_morning")])
def test_unrequested_weight_change_is_suppressed_even_when_key_and_value_match(key, value):
    checked, meta = _call(_state(key, value, 0.4), [_set(key, value, weight=1)], UNRELATED)
    assert checked == [] and meta["notes"]


def test_unrequested_value_change_of_an_existing_protected_key_is_suppressed():
    checked, meta = _call(_state("departure_time", "early_morning"),
        [_set("departure_time", "late_night")], UNRELATED)
    assert checked == [] and meta["notes"]


@pytest.mark.parametrize("text,key,value", [
    ("Найди варианты с минимальной стоимостью.", "price_priority", "lowest_total"),
    ("Хочу ранний утренний вылет.", "departure_time", "early_morning"),
])
def test_genuine_current_cost_or_flight_time_request_retains_the_matching_preference(text, key, value):
    raw = [_set(key, value)]
    checked, _ = _call(_state(), raw, text)
    assert checked == raw


@pytest.mark.parametrize("text,key,value", [
    ("Найди варианты с минимальной стоимостью.", "price_priority", "lowest_total"),
    ("Хочу ранний утренний вылет.", "departure_time", "early_morning"),
])
def test_new_requested_preference_without_numeric_weight_uses_default_one(text, key, value):
    state = _state()
    checked, _ = _call(state, [_set(key, value, weight=0.2)], text)
    assert len(checked) == 1 and checked[0]["value"].get("weight", 1) == 1
    changed, _ = apply_operations(TripState.model_validate(state), checked)
    assert changed.preferences[0].weight == 1


@pytest.mark.parametrize("text,key,value", [
    ("Найди варианты с минимальной стоимостью.", "price_priority", "lowest_total"),
    ("Хочу ранний утренний вылет.", "departure_time", "early_morning"),
])
def test_current_request_without_numeric_weight_keeps_saved_weight_by_dropping_model_weight(text, key, value):
    state = _state(key, value, 0.4)
    checked, _ = _call(state, [_set(key, value, weight=1)], text)
    assert len(checked) == 1 and "weight" not in checked[0]["value"]
    changed, _ = apply_operations(TripState.model_validate(state), checked)
    assert changed.preferences[0].weight == 0.4


@pytest.mark.parametrize("number", ["0.5", "0,5"])
def test_matching_explicit_numeric_weight_is_allowed(number):
    state = _state("price_priority", "lowest_total", 0.2)
    raw = [_set("price_priority", "lowest_total", weight=0.5)]
    checked, _ = _call(state, raw, f"Найди варианты с минимальной стоимостью; вес пожелания {number}.")
    assert checked == raw
    changed, _ = apply_operations(TripState.model_validate(state), checked)
    assert changed.preferences[0].weight == 0.5


@pytest.mark.parametrize("text,key", [
    ("Убери пожелание минимальной цены.", "price_priority"),
    ("Время вылета любое.", "departure_time"),
    ("Время вылета любое.", "departure_window"),
])
def test_explicit_removal_or_free_flight_time_authorizes_only_the_requested_clear(text, key):
    value = "lowest_total" if key == "price_priority" else "early_morning"
    raw = [_clear(key)]
    checked, _ = _call(_state(key, value), raw, text)
    assert checked == raw


@pytest.mark.parametrize("text", [
    "Не удаляй пожелание раннего вылета.",
    "А если убрать пожелание раннего вылета?",
    "«Убери пожелание раннего вылета».",
    "Раньше просил убрать пожелание раннего вылета.",
], ids=["negation", "hypothetical", "quotation", "history"])
def test_negated_or_noncurrent_removal_does_not_authorize_clear(text):
    checked, meta = _call(_state("departure_time", "early_morning", 0.4),
        [_clear("departure_time")], text)
    assert checked == [] and meta["notes"]


def test_unknown_preference_key_is_left_for_the_normal_validator_with_a_scope_note():
    raw = [_set("seat_class", "business", weight=0.7)]
    checked, meta = _call(_state(), raw, UNRELATED)
    assert checked == raw and meta["notes"]
    with pytest.raises(DomainError):
        apply_operations(TripState.model_validate(_state()), checked)


def test_departure_alias_filter_does_not_rewrite_the_key_or_add_domain_support():
    raw = [_set("departure_window", "early_morning")]
    checked, _ = _call(_state(), raw, "Хочу ранний утренний вылет.")
    assert checked == raw
    with pytest.raises(DomainError):
        apply_operations(TripState.model_validate(_state()), checked)
