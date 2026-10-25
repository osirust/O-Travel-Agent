from copy import deepcopy

import pytest

from otravel.domain import Constraint, DomainError, Leg, RoomAllocation, Stay, Traveler, TripState, apply_operations, sample_trip
from otravel.inventory import DemoInventory, reprice_plan, validate_plan


def test_complex_trip_has_complete_demo_components_and_exact_integer_totals():
    state = sample_trip()
    result = DemoInventory().search_trip(state)
    assert result["questions"] == []
    assert result["plans"]
    for plan in result["plans"]:
        assert validate_plan(state, plan) == []
        assert sum(offer["kind"] == "hotel" for offer in plan["offers"]) == 2
        assert sum(offer["kind"] == "flight" for offer in plan["offers"]) == 3
        assert sum(offer["kind"] == "transfer" for offer in plan["offers"]) == 2
        assert type(plan["total_minor"]) is int
        assert plan["total_minor"] == sum(offer["total_minor"] for offer in plan["offers"])
        assert plan["booking_created"] is False
        for offer in plan["offers"]:
            assert offer["total_minor"] == sum(line["unit_minor"] * line["quantity"] for line in offer["breakdown"])
            assert offer["source_type"] == "DEMO" and offer["is_demo"] is True
            assert offer["availability"] == "synthetic_not_live"
            assert "DEMO" in offer["name"]
    # Independently calculated: hotel 79,860 + 100,540; flights 145,800;
    # two group transfers 7,200 RUB. Breakfast is required by this sample.
    assert result["plans"][0]["total_minor"] == 33_340_000


def test_known_two_adult_price_has_a_fixed_independent_oracle():
    state = TripState(origin="Москва", travelers=[Traveler(id="a1", type="adult"), Traveler(id="a2", type="adult")], stays=[Stay(id="kazan", city="Казань", check_in="2026-11-06", check_out="2026-11-08", rooms=[RoomAllocation(id="r1", traveler_ids=["a1", "a2"])])])
    plans = DemoInventory().search_trip(state)["plans"]
    # 2*3150 room + 2*2*9000 air + 2*400 transfer, in RUB minor units.
    assert plans[0]["total_minor"] == 4_310_000


def test_low_budget_returns_no_matches_without_inventing_discount():
    state, _ = apply_operations(sample_trip(), [{"type": "set_budget", "value": {"amount_minor": 1}}])
    result = DemoInventory().search_trip(state)
    assert result["plans"] == []
    assert state.budget.amount_minor == 1
    assert any("не ослаблены" in warning for warning in result["warnings"])


def test_missing_origin_and_dates_ask_without_fabricating_input():
    state = TripState()
    before = state.model_dump(mode="json")
    result = DemoInventory().search_trip(state)
    assert result["plans"] == []
    assert result["questions"]
    assert any("города" in question for question in result["questions"])
    assert state.model_dump(mode="json") == before


def test_equal_dates_do_not_generate_offers():
    state = sample_trip()
    state.stays[0].check_out = state.stays[0].check_in
    result = DemoInventory().search_trip(state)
    assert not result["plans"]
    assert "invalid_dates" in {issue["code"] for issue in result["violations"]}


def test_auto_allocation_keeps_families_separate_and_children_with_adults():
    state = sample_trip()
    for stay in state.stays:
        stay.rooms = [RoomAllocation(id=room.id) for room in stay.rooms]
    result = DemoInventory().search_trip(state)
    assert result["plans"]
    people = {person.id: person for person in state.travelers}
    for offer in result["plans"][0]["offers"]:
        if offer["kind"] != "hotel":
            continue
        for room in offer["room_assignments"]:
            assert len(room["traveler_ids"]) <= 4
            assert len({people[identifier].group_id for identifier in room["traveler_ids"]}) == 1
            assert any(people[identifier].type == "adult" for identifier in room["traveler_ids"])


def test_too_few_rooms_is_not_fixed_by_inventing_capacity():
    state = sample_trip()
    for stay in state.stays:
        stay.rooms = [RoomAllocation(id="only-room")]
    assert not DemoInventory().search_trip(state)["plans"]


def test_breakfast_and_refundable_are_checked_per_rate():
    state = sample_trip()
    state.constraints.append(Constraint(key="refundable", value=True))
    plans = DemoInventory().search_trip(state)["plans"]
    assert plans
    assert all(offer["breakfast"] and offer["refundable"] and offer["rate_type"] == "flex" for plan in plans for offer in plan["offers"] if offer["kind"] == "hotel")


def test_pets_use_explicit_property_rules_and_fees():
    state = sample_trip()
    state.constraints.append(Constraint(key="pets", value={"kind": "dog", "weight_kg": 8}))
    plans = DemoInventory().search_trip(state)["plans"]
    assert plans
    for plan in plans:
        for offer in plan["offers"]:
            if offer["kind"] == "hotel":
                assert offer["pets_allowed"] and offer["max_pet_kg"] >= 8
                assert any(line["kind"] == "pet_stay_fee" for line in offer["breakdown"])
    state.constraints[-1].value = {"kind": "dog", "weight_kg": 40}
    assert not DemoInventory().search_trip(state)["plans"]


def test_pet_details_are_not_guessed():
    state = sample_trip()
    state.constraints.append(Constraint(key="pets", value=True))
    result = DemoInventory().search_trip(state)
    assert result["questions"] and not result["plans"]


def test_unsupported_hard_requirement_is_explicit():
    state = sample_trip()
    state.constraints.append(Constraint(key="guaranteed_sea_view", value=True))
    result = DemoInventory().search_trip(state)
    assert not result["plans"]
    assert result["violations"][0]["code"] == "unsupported_constraint"


def test_search_and_reprice_are_deterministic_and_never_live():
    state = sample_trip()
    first = DemoInventory().search_trip(state)
    assert first == DemoInventory().search_trip(state)
    plan = first["plans"][0]
    snapshot = deepcopy(plan)
    repriced = reprice_plan(plan)
    assert plan == snapshot
    assert repriced["total_minor"] == plan["total_minor"]
    assert repriced["reprice_kind"] == "deterministic_demo_not_live"
    assert validate_plan(state, repriced) == []
    with pytest.raises(DomainError):
        reprice_plan({"source_type": "LIVE"})


def test_validation_rejects_changed_total_and_lost_passenger():
    state = sample_trip()
    plan = DemoInventory().search_trip(state)["plans"][0]
    changed = deepcopy(plan)
    changed["total_minor"] -= 1
    changed["offers"][-1]["traveler_ids"].pop()
    codes = {item["code"] for item in validate_plan(state, changed)}
    assert {"invalid_total", "transport_occupancy"} <= codes


def test_validation_uses_catalog_capacity_and_requires_transfers():
    state = sample_trip()
    plan = deepcopy(DemoInventory().search_trip(state)["plans"][0])
    next(offer for offer in plan["offers"] if offer["kind"] == "hotel")["room_assignments"][0]["capacity"] = 100
    plan["offers"] = [offer for offer in plan["offers"] if offer["kind"] != "transfer"]
    plan["total_minor"] = sum(offer["total_minor"] for offer in plan["offers"])
    codes = {issue["code"] for issue in validate_plan(state, plan)}
    assert {"room_capacity", "transfer_mismatch"} <= codes


def test_no_offer_can_be_relabeled_as_live_confirmed_availability():
    state = sample_trip()
    plan = deepcopy(DemoInventory().search_trip(state)["plans"][0])
    plan["offers"][0]["availability"] = "confirmed"
    assert "invalid_source" in {issue["code"] for issue in validate_plan(state, plan)}


def test_changed_rate_flags_cannot_create_missing_catalog_facts():
    state = sample_trip()
    plan = deepcopy(DemoInventory().search_trip(state)["plans"][0])
    plan["offers"][0]["refundable"] = True
    assert "property_facts_mismatch" in {issue["code"] for issue in validate_plan(state, plan)}


def test_unsupported_train_route_does_not_turn_into_a_flight():
    state = sample_trip()
    state.legs[0].mode = "train"
    result = DemoInventory().search_trip(state)
    assert not result["plans"]
    assert result["violations"][0]["code"] == "unsupported_transport"


def test_birthday_changes_air_fare_on_the_relevant_leg_date():
    state, _ = apply_operations(sample_trip(), [{"type": "update_traveler", "target_id": "child-1", "value": {"age": None, "birth_date": "2024-11-10"}}])
    plan = DemoInventory().search_trip(state)["plans"][0]
    units = [next(line["unit_minor"] for line in offer["breakdown"] if line.get("traveler_id") == "child-1") for offer in plan["offers"] if offer["kind"] == "flight"]
    assert units == [100_000, 100_000, 525_000]


def test_plan_from_another_trip_is_rejected_even_when_other_fields_match():
    state = sample_trip()
    plan = deepcopy(DemoInventory().search_trip(state)["plans"][0])
    plan["trip_id"] = "someone-elses-trip"
    assert "wrong_trip" in {issue["code"] for issue in validate_plan(state, plan)}


def test_unpriced_gap_between_stays_requires_clarification():
    state, _ = apply_operations(sample_trip(), [{"type": "upsert_stay", "target_id": "stay-antalya", "value": {"check_in": "2026-11-11"}}])
    result = DemoInventory().search_trip(state)
    assert not result["plans"]
    assert any("Между остановками" in question for question in result["questions"])
