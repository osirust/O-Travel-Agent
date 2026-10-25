from copy import deepcopy
from datetime import date

import pytest

from otravel.domain import DomainError, Operation, TripState, apply_operations, canonical_city, sample_trip, validate_trip


def test_sample_has_multi_city_separate_families_and_three_rooms():
    state = sample_trip()
    assert validate_trip(state) == []
    assert len(state.travelers) == 9
    assert [person.type for person in state.travelers].count("adult") == 6
    assert len(state.groups) == 2
    assert [(stay.check_out - stay.check_in).days for stay in state.stays] == [3, 4]
    assert [leg.destination for leg in state.legs] == ["Стамбул", "Анталья", "Казань"]
    for stay in state.stays:
        assert len(stay.rooms) == 3
        assert sorted(identifier for room in stay.rooms for identifier in room.traveler_ids) == sorted(person.id for person in state.travelers)


def test_compound_edit_preserves_unmentioned_fields_and_version():
    original = sample_trip()
    snapshot = original.model_dump(mode="json")
    state, changes = apply_operations(original, [
        Operation(type="add_traveler", value={"id": "new-child", "type": "child", "age": 6, "group_id": "family-1"}),
        Operation(type="replace_hotel", target_id="stay-antalya", value={"hotel_id": "demo-hotel-antalya-garden"}),
        Operation(type="set_budget", value={"amount_minor": original.budget.amount_minor - 2_000_000}),
    ])
    assert original.model_dump(mode="json") == snapshot
    assert state.state_version == original.state_version
    assert state.origin == original.origin
    assert state.legs == original.legs
    assert state.constraints == original.constraints
    assert state.stays[0] == original.stays[0]
    assert state.stays[1].check_out == original.stays[1].check_out
    assert len(state.travelers) == 10
    assert "new-child" in state.groups[0].traveler_ids
    assert changes and all("before" in change and "after" in change for change in changes)


def test_shift_return_extends_final_stay_only():
    original = sample_trip()
    state, _ = apply_operations(original, [Operation(type="shift_return", value={"days": 2})])
    assert state.stays[0] == original.stays[0]
    assert state.stays[-1].check_in == original.stays[-1].check_in
    assert state.stays[-1].check_out == date(2026, 11, 15)
    assert state.legs[-1].departure_date == date(2026, 11, 15)
    assert state.travelers == original.travelers


def test_batch_allows_intermediate_date_overlap_but_final_dates_must_match():
    state, _ = apply_operations(sample_trip(), [
        Operation(type="upsert_stay", target_id="stay-istanbul", value={"check_out": "2026-11-11"}),
        Operation(type="upsert_stay", target_id="stay-antalya", value={"check_in": "2026-11-11"}),
    ])
    assert validate_trip(state) == []
    assert state.legs[1].departure_date == date(2026, 11, 11)


def test_remove_traveler_removes_all_allocations_and_group_references():
    state, _ = apply_operations(sample_trip(), [Operation(type="remove_traveler", target_id="child-2")])
    assert len(state.travelers) == 8
    assert all("child-2" not in group.traveler_ids for group in state.groups)
    assert all("child-2" not in room.traveler_ids for stay in state.stays for room in stay.rooms)
    assert validate_trip(state) == []


def test_update_age_retains_stable_identity_and_other_members():
    original = sample_trip()
    state, _ = apply_operations(original, [Operation(type="update_traveler", target_id="child-1", value={"age": 4})])
    assert state.travelers[-3].id == original.travelers[-3].id
    assert state.travelers[-3].age == 4
    assert state.travelers[:6] == original.travelers[:6]
    assert state.stays == original.stays


@pytest.mark.parametrize("operation,code", [
    ({"type": "execute_sql", "value": {}}, "unsupported_operation"),
    ({"type": "set_budget", "value": {"amount_minor": 10, "state_version": 99}}, "unknown_fields"),
    ({"type": "remove_traveler", "target_id": "nobody"}, "unknown_target"),
    ({"type": "shift_return", "value": {"days": True}}, "invalid_days"),
    ({"type": "shift_return", "value": {"days": -20}}, "invalid_dates"),
])
def test_rejected_operation_never_mutates_input(operation, code):
    original = sample_trip()
    snapshot = deepcopy(original.model_dump(mode="json"))
    with pytest.raises(DomainError) as caught:
        apply_operations(original, [operation])
    assert caught.value.code == code
    assert original.model_dump(mode="json") == snapshot


def test_proposed_guard_rejects_eleventh_traveler_atomically():
    original = sample_trip()
    with pytest.raises(DomainError, match="guard") as caught:
        apply_operations(original, [Operation(type="add_traveler", value={"type": "adult"}), Operation(type="add_traveler", value={"type": "adult"})])
    assert caught.value.code == "unsupported_limit"
    assert len(original.travelers) == 9


def test_draft_unknown_fields_remain_unknown_until_final_validation():
    state = TripState()
    assert state.origin is None and state.budget is None
    assert validate_trip(state, final=False) == []
    assert {issue["code"] for issue in validate_trip(state)} >= {"missing_origin", "missing_travelers", "missing_stays"}


@pytest.mark.parametrize("alias,expected", [("Питер", "Санкт-Петербург"), ("СПб", "Санкт-Петербург"), ("St. Petersburg", "Санкт-Петербург"), ("Istanbul", "Стамбул"), ("Kazan", "Казань")])
def test_city_aliases_do_not_expand_supported_geography(alias, expected):
    assert canonical_city(alias) == expected


def test_unknown_city_is_explicitly_unsupported():
    with pytest.raises(DomainError) as caught:
        canonical_city("Лондон")
    assert caught.value.code == "unsupported_city"


def test_invalid_date_in_operation_is_a_domain_error():
    with pytest.raises(DomainError) as caught:
        apply_operations(sample_trip(), [Operation(type="upsert_stay", target_id="stay-istanbul", value={"check_in": "2026-11-31"})])
    assert caught.value.code == "invalid_operation"
