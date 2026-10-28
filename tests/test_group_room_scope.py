"""Pure family membership and room-rate contracts; no HTTP or credentials."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from otravel.clarifications import derive_pending_field, human_question
from otravel.domain import Constraint, DomainError, Group, TripState, apply_operations, validate_trip
from otravel.inventory import DemoInventory, validate_plan
from otravel.llm_contract import build_operation_schema
from otravel.planning import assemble_plan, validate_quote


def fixture():
    return TripState(origin="Москва", travelers=[{"id": f"a{i}", "type": "adult"} for i in range(1, 5)],
        stays=[{"id": "s1", "city": "Казань", "check_in": "2026-11-06", "check_out": "2026-11-08",
                "rooms": [{"id": "r1", "traveler_ids": ["a1", "a2"]}, {"id": "r2", "traveler_ids": ["a3", "a4"]}]}])


def group_op(target="g1", ids=None, separate=True):
    return {"type": "upsert_group", "target_id": target, "value": {"traveler_ids": ids or [], "keep_separate": separate}}


def room_condition(key="refundable", value=True, room="r1", stay="s1"):
    return {"type": "set_constraint", "value": {"key": key, "value": value, "stay_id": stay, "room_id": room}}


def test_membership_setter_moves_people_atomically_and_is_idempotent():
    state = fixture()
    state, _ = apply_operations(state, [group_op("g1", ["a1", "a2"]), group_op("g2", ["a3", "a4"])])
    before = state.model_dump(mode="json")
    result, changes = apply_operations(state, [group_op("g2", ["a2", "a3"])])
    assert state.model_dump(mode="json") == before
    assert {g.id: g.traveler_ids for g in result.groups} == {"g1": ["a1"], "g2": ["a2", "a3"]}
    assert {p.id: p.group_id for p in result.travelers} == {"a1": "g1", "a2": "g2", "a3": "g2", "a4": None}
    again, unchanged = apply_operations(result, [group_op("g2", ["a2", "a3"])])
    assert changes and again == result and unchanged == [] and validate_trip(result, final=False) == []


def test_empty_separate_membership_is_draft_but_final_search_and_quote_are_blocked():
    state, _ = apply_operations(fixture(), [group_op("new-group-1")])
    assert validate_trip(state, final=False) == []
    issue = next(i for i in validate_trip(state) if i["code"] == "missing_group_membership")
    assert issue["details"]["group_id"] == "new-group-1"
    assert derive_pending_field([issue], state)["target_id"] == "new-group-1"
    assert "групп" in human_question(issue, state).lower()
    assert not DemoInventory().search_trip(state)["plans"]
    plan = assemble_plan(state, [], demo_fallback=True)
    assert not plan["ready_for_simulated_checkout"] and validate_quote(plan, state)


def test_room_scopes_are_independent_and_clear_removes_only_exact_identity():
    state, _ = apply_operations(fixture(), [room_condition(), room_condition("breakfast", False, "r2")])
    assert len(state.constraints) == 2
    plans = DemoInventory().search_trip(state)["plans"]
    assert plans
    offer = next(o for o in plans[0]["offers"] if o["kind"] == "hotel")
    rates = {r["room_id"]: r for r in offer["room_rates"]}
    assert rates["r1"]["refundable"] is True and rates["r2"]["breakfast"] is False
    assert offer["rate_type"] == "mixed" and offer["refundable"] is None
    assert validate_plan(state, plans[0]) == []
    updated, _ = apply_operations(state, [{"type": "clear_constraint", "value": {"key": "refundable", "stay_id": "s1", "room_id": "r1"}}])
    assert [(c.key, c.stay_id, c.room_id) for c in updated.constraints] == [("breakfast", "s1", "r2")]


def test_native_schema_accepts_group_setter_and_exact_room_constraint():
    state = fixture()
    payload = {"operations": [group_op("new-group-1", ["a1", "a2"]), room_condition()],
               "control": {"action": "none"}, "reply": "Сохранил.", "unknowns": [], "conflicts": []}
    assert Draft202012Validator(build_operation_schema(state)).is_valid(payload)


def test_provider_aggregate_facts_do_not_prove_a_room_rate():
    state, _ = apply_operations(fixture(), [room_condition()])
    demo = DemoInventory().search_trip(state)["plans"][0]
    hotel = next(o for o in demo["offers"] if o["kind"] == "hotel")
    now = datetime(2026, 10, 26, tzinfo=UTC)
    external = {"offer_id": "synthetic-provider-fixture", "provider": "liteapi", "source_type": "SANDBOX",
        "price_kind": "COMPONENT_TOTAL", "currency": "RUB", "amount_minor": hotel["total_minor"],
        "retrieved_at": now.isoformat(), "expires_at": (now + timedelta(minutes=10)).isoformat(),
        "details": {"kind": "hotel", "allocation_verified": True, "fees_included": True,
            "constraint_evidence": {"refundable": True},
            "binding": {"trip_id": state.trip_id, "state_version": state.state_version, "stay_id": "s1",
                "room_ids": ["r1", "r2"], "room_assignments": deepcopy(hotel["room_assignments"]),
                "city": hotel["city"], "checkin": hotel["check_in"], "checkout": hotel["check_out"], "hotel_id": hotel["hotel_id"]}}}
    plan = assemble_plan(state, [external], demo_fallback=True, now=now)
    assert any(i["code"] == "hard_constraint_unverified" and i["details"].get("room_id") == "r1" for i in plan["blockers"])
    assert not plan["ready_for_simulated_checkout"]
    for bad_evidence in (None, [], {"r2": {"refundable": True}}, {"r1": []}, {"r1": {"refundable": 1}}, {"r1": {"refundable": False}}):
        external["details"]["room_constraint_evidence"] = bad_evidence
        rejected = assemble_plan(state, [external], demo_fallback=True, now=now)
        assert not rejected["ready_for_simulated_checkout"]
        assert any(i["code"] == "hard_constraint_unverified" for i in rejected["blockers"])
    external["details"]["room_constraint_evidence"] = {"r1": {"refundable": True}}
    proven_fixture = assemble_plan(state, [external], demo_fallback=True, now=now)
    assert proven_fixture["ready_for_simulated_checkout"] is True
    assert proven_fixture["source_type"] == "MIXED" and proven_fixture["ready_for_real_booking"] is False
    assert validate_quote(proven_fixture, state, now=now) == []
    external["details"]["room_constraint_evidence"]["r1"]["refundable"] = False
    assert validate_quote(proven_fixture, state, current_offers=[external], now=now)


@pytest.mark.parametrize("ids", [["a1", "a1"], ["missing"], [1]])
def test_bad_membership_rejects_atomically(ids):
    state = fixture()
    before = state.model_dump(mode="json")
    with pytest.raises(DomainError):
        apply_operations(state, [group_op("g1", ["a2"]), group_op("g2", ids)])
    assert state.model_dump(mode="json") == before


@pytest.mark.parametrize("value", [{"traveler_ids": []}, {"keep_separate": True},
    {"traveler_ids": [], "keep_separate": "true"}, {"traveler_ids": [], "keep_separate": True, "api_key": "fixture"}])
def test_group_contract_requires_full_typed_setter(value):
    with pytest.raises(DomainError):
        apply_operations(fixture(), [{"type": "upsert_group", "target_id": "g1", "value": value}])


def test_legacy_empty_default_group_is_not_missing_membership():
    state = fixture()
    state.groups = [Group(id="old-default-empty")]
    assert validate_trip(state) == [] and DemoInventory().search_trip(state)["plans"]


def test_two_unresolved_groups_ask_first_exact_group_then_next_without_guessing_members():
    state, _ = apply_operations(fixture(), [group_op("g1"), group_op("g2")])
    assert derive_pending_field(["missing_group_membership"], state)["target_id"] == "g1"
    assert "№1" in human_question("missing_group_membership", state)
    state, _ = apply_operations(state, [group_op("g1", ["a1", "a2"])])
    assert derive_pending_field(["missing_group_membership"], state)["target_id"] == "g2"


def test_separation_setter_blocks_mixing_and_allocator_does_not_guess_families():
    state, _ = apply_operations(fixture(), [group_op("g1", ["a1", "a2"]), group_op("g2", ["a3", "a4"])])
    state.stays[0].rooms[0].traveler_ids = ["a1", "a3"]
    state.stays[0].rooms[1].traveler_ids = ["a2", "a4"]
    assert not DemoInventory().search_trip(state)["plans"]
    for room in state.stays[0].rooms:
        room.traveler_ids = []
    plans = DemoInventory().search_trip(state)["plans"]
    assert plans
    for room in plans[0]["room_assignments"]["s1"]:
        assert set(room["traveler_ids"]) in ({"a1", "a2"}, {"a3", "a4"})


def test_move_last_member_leaves_unresolved_policy_until_explicitly_cleared():
    state, _ = apply_operations(fixture(), [group_op("g1", ["a1"]), group_op("g2", ["a1", "a2"])])
    assert any(i["code"] == "missing_group_membership" for i in validate_trip(state))
    state, _ = apply_operations(state, [group_op("g1", separate=False)])
    assert not any(i["code"] == "missing_group_membership" for i in validate_trip(state))


@pytest.mark.parametrize("value", [
    {"key": "breakfast", "value": True, "room_id": "r1"},
    {"key": "pets_allowed", "value": True, "stay_id": "s1", "room_id": "r1"},
    {"key": "refundable", "value": True, "stay_id": "missing", "room_id": "r1"},
    {"key": "refundable", "value": True, "stay_id": "s1", "room_id": "missing"},
])
def test_invalid_room_scope_is_not_accepted_by_domain(value):
    state = fixture()
    before = state.model_dump(mode="json")
    with pytest.raises(DomainError):
        apply_operations(state, [{"type": "set_constraint", "value": value}])
    assert state.model_dump(mode="json") == before


def test_room_reference_is_local_to_its_stay():
    state = fixture()
    raw = state.model_dump(mode="json")
    raw["stays"].append({"id": "s2", "city": "Стамбул", "check_in": "2026-11-08", "check_out": "2026-11-10", "rooms": [{"id": "only-in-s2"}]})
    with pytest.raises(DomainError, match="номер"):
        apply_operations(TripState.model_validate(raw), [room_condition(room="only-in-s2")])


def test_constraint_set_clear_uses_trip_stay_room_triple_without_flattening():
    state, _ = apply_operations(fixture(), [
        {"type": "set_constraint", "value": {"key": "refundable", "value": True}},
        {"type": "set_constraint", "value": {"key": "refundable", "value": True, "stay_id": "s1"}},
        room_condition(), room_condition(room="r2")])
    assert len(state.constraints) == 4
    updated, _ = apply_operations(state, [{"type": "clear_constraint", "value": {"key": "refundable", "stay_id": "s1"}}])
    assert {(c.stay_id, c.room_id) for c in updated.constraints} == {(None, None), ("s1", "r1"), ("s1", "r2")}
    again, changes = apply_operations(updated, [{"type": "clear_constraint", "value": {"key": "refundable", "stay_id": "s1"}}])
    assert again == updated and not changes


def test_conflicting_whole_stay_and_room_requirements_yield_no_offer_without_weakening():
    state, _ = apply_operations(fixture(), [
        {"type": "set_constraint", "value": {"key": "refundable", "value": False}}, room_condition()])
    assert not DemoInventory().search_trip(state)["plans"]
    assert state.constraints[0].value is False and state.constraints[1].value is True


def test_room_specific_demo_price_is_calculated_per_room_and_quote_validates():
    state, _ = apply_operations(fixture(), [room_condition(), room_condition("breakfast", False, "r2")])
    plan = DemoInventory().search_trip(state)["plans"][0]
    hotel = next(o for o in plan["offers"] if o["kind"] == "hotel")
    # Central hotel: 2 rooms * 2 nights *3150 + breakfast2adults*2days*700
    # + flexonlyr1:3150*.15*2; no breakfast/flex charge for r2.
    assert hotel["total_minor"] == 1_634_500
    assert sum(line["amount_minor"] for line in hotel["breakdown"]) == 1_634_500
    assembled = assemble_plan(state, [], demo_fallback=True)
    assert assembled["ready_for_simulated_checkout"] and validate_quote(assembled, state) == []


@pytest.mark.parametrize("mutation", ["remove", "wrong_room", "wrong_fact", "unhashable"])
def test_demo_room_rate_evidence_cannot_be_faked(mutation):
    state, _ = apply_operations(fixture(), [room_condition()])
    plan = deepcopy(DemoInventory().search_trip(state)["plans"][0])
    hotel = next(o for o in plan["offers"] if o["kind"] == "hotel")
    if mutation == "remove":
        hotel.pop("room_rates")
    elif mutation == "wrong_room":
        hotel["room_rates"][0]["room_id"] = "missing"
    elif mutation == "wrong_fact":
        hotel["room_rates"][0]["refundable"] = False
    else:
        hotel["room_rates"][0]["room_id"] = []
    assert validate_plan(state, plan)


@pytest.mark.parametrize("value", [
    {"key": "breakfast", "value": True, "room_id": "r1"},
    {"key": "pets", "value": True, "stay_id": "s1", "room_id": "r1"},
])
def test_direct_constraint_constructor_refuses_invalid_room_scope(value):
    with pytest.raises(ValidationError):
        Constraint.model_validate(value)


def test_native_schema_accepts_same_batch_group_references_and_no_membership_guess():
    payload = {"operations": [
        {"type": "add_traveler", "value": {"id": "new-traveler-1", "type": "adult"}},
        group_op("new-group-1", ["new-traveler-1"]), group_op("new-group-2")],
        "control": {"action": "none"}, "reply": "Уточните вторую семью.", "unknowns": [], "conflicts": []}
    assert Draft202012Validator(build_operation_schema(TripState())).is_valid(payload)
    state, _ = apply_operations(TripState(), payload["operations"])
    assert state.travelers[0].group_id == "new-group-1"
    assert state.groups[1].traveler_ids == [] and state.groups[1].keep_separate is True


def test_group_setter_is_available_when_roster_hits_old_traveler_guard():
    state = TripState(travelers=[{"id": f"p{i}", "type": "adult"} for i in range(10)])
    payload = {"operations": [group_op("new-group-1", ["p0", "p1"])], "control": {"action": "none"},
               "reply": "Сохранил.", "unknowns": [], "conflicts": []}
    assert Draft202012Validator(build_operation_schema(state)).is_valid(payload)


@pytest.mark.parametrize("operation", [
    group_op("missing-group-99", ["a1"]), group_op("new-group-1", ["missing-person"]),
    group_op("new-group-1", ["a1", "a1"]),
    {"type": "upsert_group", "target_id": "new-group-1", "value": {"traveler_ids": [], "keep_separate": "true"}},
    {"type": "set_constraint", "value": {"key": "breakfast", "value": True, "room_id": "r1"}},
    {"type": "set_constraint", "value": {"key": "breakfast", "value": True, "stay_id": None, "room_id": "r1"}},
    {"type": "clear_constraint", "value": {"key": "pets", "stay_id": "s1", "room_id": "r1"}},
    {"type": "set_constraint", "value": {"key": "pets_allowed", "value": True, "stay_id": "s1", "room_id": "r1"}},
])
def test_native_schema_rejects_impossible_group_and_room_shape(operation):
    payload = {"operations": [operation], "control": {"action": "none"}, "reply": "", "unknowns": [], "conflicts": []}
    assert not Draft202012Validator(build_operation_schema(fixture())).is_valid(payload)


def test_room_scoped_constraint_on_other_stay_is_not_flattened():
    raw = fixture().model_dump(mode="json")
    other = deepcopy(raw["stays"][0])
    other.update(id="s2", check_in="2026-11-08", check_out="2026-11-10")
    raw["stays"].append(other)
    state, _ = apply_operations(TripState.model_validate(raw), [room_condition()])
    offers = DemoInventory().search_trip(state)["plans"][0]["offers"]
    first = next(o for o in offers if o.get("component_id") == "s1")
    second = next(o for o in offers if o.get("component_id") == "s2")
    assert first["room_rates"][0]["refundable"] is True
    assert "room_rates" not in second and second["refundable"] is False
