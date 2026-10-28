"""Pure planning boundaries. No providers, credentials, DB or wall-clock reads."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from otravel.domain import sample_trip
from otravel.inventory import DemoInventory, validate_plan
from otravel.planning import assemble_plan, compare_plans, required_components, validate_quote


NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)


def demo_offers(state=None):
    return deepcopy(DemoInventory().search_trip(state or sample_trip())["plans"][0]["offers"])


def provider_offers(state=None):
    state = state or sample_trip()
    result = []
    for original in demo_offers(state):
        kind = original["kind"]
        binding = {"trip_id": state.trip_id, "state_version": state.state_version}
        if kind == "hotel":
            binding.update(stay_id=original["component_id"], room_ids=[r["room_id"] for r in original["room_assignments"]],
                           room_assignments=deepcopy(original["room_assignments"]), city=original["city"],
                           checkin=original["check_in"], checkout=original["check_out"], hotel_id=original["hotel_id"])
        elif kind == "transfer":
            binding.update(stay_id=original["component_id"].removeprefix("transfer-"), city=original["city"],
                           date=original["date"], traveler_ids=original["traveler_ids"])
        else:
            binding.update(leg_ids=[original["component_id"]], traveler_ids=original["traveler_ids"])
        result.append({"offer_id": "fixture-" + original["offer_id"], "provider": "serpapi" if kind != "transfer" else "fixture_transfer",
                       "source_type": "LIVE_INDICATIVE", "price_kind": "COMPONENT_TOTAL", "currency": "RUB",
                       "amount_minor": original["total_minor"], "retrieved_at": NOW.isoformat(),
                       "expires_at": (NOW + timedelta(hours=1)).isoformat(), "description": "Contract fixture, not real inventory",
                       "details": {"kind": kind, "binding": binding, "fees_included": True,
                                   "allocation_verified": True, "group_quote": True,
                                   "passengers_count": len(state.travelers),
                                   "constraint_evidence": {"breakfast": True}}})
    return result


def codes(plan):
    return {issue["code"] for issue in plan["blockers"]}


def test_full_complex_demo_fallback_keeps_legacy_snapshot_and_exact_sum():
    state = sample_trip()
    before = state.model_dump(mode="json")
    plan = assemble_plan(state, [], demo_fallback=True)
    assert state.model_dump(mode="json") == before
    assert plan["total_minor"] == 33_340_000
    assert plan["source_type"] == "DEMO"
    assert plan["coverage"]["complete"]
    assert len(plan["coverage"]["required"]) == 11  # 6 rooms, 3 legs, 2 transfers.
    assert plan["ready_for_simulated_checkout"] is True
    assert plan["ready_for_real_booking"] is False
    assert plan["booking_created"] is False
    assert plan["offers"] == demo_offers(state)
    assert validate_plan(state, plan) == []
    assert validate_quote(plan, state) == []
    assert plan == assemble_plan(state, [], demo_fallback=True)


def test_missing_all_components_has_no_complete_total():
    plan = assemble_plan(sample_trip(), [])
    assert "missing_components" in codes(plan)
    assert plan["total_minor"] is None
    assert plan["ready_for_simulated_checkout"] is False


def test_indicative_is_allowed_for_simulated_checkout_when_total_is_proven():
    state = sample_trip()
    offers = provider_offers(state)
    original = deepcopy(offers)
    plan = assemble_plan(state, offers, now=NOW)
    assert offers == original
    assert plan["blockers"] == []
    assert plan["total_minor"] == 33_340_000
    assert plan["source_type"] == "LIVE_INDICATIVE"
    assert plan["ready_for_simulated_checkout"] is True
    assert plan["ready_for_real_booking"] is False
    assert plan["warnings"]
    assert validate_quote(plan, state, current_offers=offers, now=NOW) == []


def test_mixed_plan_uses_only_missing_demo_components_and_keeps_sources():
    state = sample_trip()
    flight = next(o for o in provider_offers(state) if o["details"]["kind"] == "flight")
    flight["amount_minor"] += 1000
    plan = assemble_plan(state, [flight], demo_fallback=True, now=NOW)
    assert plan["source_type"] == "MIXED"
    assert plan["total_minor"] == 33_341_000
    assert plan["ready_for_simulated_checkout"] is True
    assert len(plan["components"]) == 7
    assert sum(c["demo_fallback"] for c in plan["components"]) == 6
    assert {c["source_type"] for c in plan["components"]} == {"DEMO", "LIVE_INDICATIVE"}


@pytest.mark.parametrize("provider,kind", [("liteapi", "hotel"), ("duffel", "flight"), ("hotelbeds", "hotel"),
                                           ("ratehawk", "hotel"), ("serpapi", "hotel"), ("travelpayouts", "flight")])
def test_provider_labels_cannot_promote_sandbox_or_search_to_bookable(provider, kind):
    state = sample_trip()
    offers = provider_offers(state)
    offer = next(o for o in offers if o["details"]["kind"] == kind)
    offer.update(provider=provider, source_type="LIVE_BOOKABLE")
    offer["details"].update(availability_verified=True, supplier_reprice_verified=True)
    plan = assemble_plan(state, offers, now=NOW)
    expected = "SANDBOX" if provider in {"liteapi", "duffel", "hotelbeds", "ratehawk"} else "LIVE_INDICATIVE"
    assert next(o for o in plan["offers"] if o["offer_id"] == offer["offer_id"])["source_type"] == expected
    assert plan["ready_for_real_booking"] is False


@pytest.mark.parametrize("missing", ["binding", "fees_included", "allocation_verified"])
def test_unknown_binding_fees_or_allocation_blocks_full_total(missing):
    offers = provider_offers()
    offers[0]["details"].pop(missing)
    plan = assemble_plan(sample_trip(), offers, now=NOW)
    assert plan["blockers"]
    assert plan["total_minor"] is None
    assert plan["ready_for_simulated_checkout"] is False


@pytest.mark.parametrize("price_kind", ["INDICATIVE_NIGHTLY", "CACHED_REFERENCE_FARE", "PROVIDER_NET_RATE", "unknown"])
def test_nightly_reference_or_net_is_not_added_as_a_group_component_total(price_kind):
    offers = provider_offers()
    offers[0]["price_kind"] = price_kind
    plan = assemble_plan(sample_trip(), offers, now=NOW)
    assert "unsupported_price_basis" in codes(plan)
    assert plan["total_minor"] is None


def test_fx_never_sums_incomparable_currencies_or_invents_a_rate():
    offers = provider_offers()
    offers[0]["currency"] = "EUR"
    plan = assemble_plan(sample_trip(), offers, now=NOW)
    assert "fx_required" in codes(plan)
    assert plan["total_minor"] is None
    assert set(plan["currency_totals"]) == {"RUB", "EUR"}


def test_one_common_currency_is_supported_without_inventing_fx():
    state = sample_trip()
    offers = provider_offers(state)
    state.budget.currency = "USD"
    for offer in offers:
        offer["currency"] = "USD"
    plan = assemble_plan(state, offers, now=NOW)
    assert plan["currency"] == "USD"
    assert plan["total_minor"] == 33_340_000
    assert "fx_required" not in codes(plan)


@pytest.mark.parametrize("change,expected", [
    ({"stay_id": "foreign"}, "unknown_stay_binding"),
    ({"city": "Дубай"}, "stay_binding_mismatch"),
    ({"checkout": "2026-11-30"}, "stay_binding_mismatch"),
    ({"state_version": 99}, "stale_offer_binding"),
    ({"trip_id": "foreign-trip"}, "stale_offer_binding"),
    ({"room_ids": ["foreign-room"]}, "invalid_room_binding"),
])
def test_strict_hotel_source_binding(change, expected):
    offers = provider_offers()
    offers[0]["details"]["binding"].update(change)
    plan = assemble_plan(sample_trip(), offers, now=NOW)
    assert expected in codes(plan)
    assert not plan["ready_for_simulated_checkout"]


def test_overlap_and_duplicate_offer_id_do_not_create_double_charges():
    offers = provider_offers()
    offers.append(deepcopy(offers[0]))
    plan = assemble_plan(sample_trip(), offers, now=NOW)
    assert {"overlapping_components", "duplicate_offer_id"}.issubset(codes(plan))
    assert plan["total_minor"] is None


def test_lost_passenger_in_transport_and_hotel_is_explicit():
    offers = provider_offers()
    offers[0]["details"]["binding"]["room_assignments"][0]["traveler_ids"].pop()
    next(o for o in offers if o["details"]["kind"] == "flight")["details"]["binding"]["traveler_ids"].pop()
    plan = assemble_plan(sample_trip(), offers, now=NOW)
    assert {"changed_allocation", "stay_guest_coverage", "passenger_coverage"}.issubset(codes(plan))


@pytest.mark.parametrize("change,expected", [({"expires_at": "2026-10-24T12:00:00Z"}, "invalid_source_lifetime"),
    ({"retrieved_at": "2026-10-24T10:00:00Z", "expires_at": "2026-10-24T12:00:00Z"}, "stale_source_snapshot"),
    ({"expires_at": "not-a-date"}, "invalid_source_lifetime"), ({"retrieved_at": "2026-10-25T12:00:00"}, "invalid_source_lifetime")])
def test_source_time_boundaries(change, expected):
    offers = provider_offers()
    offers[0].update(change)
    assert expected in codes(assemble_plan(sample_trip(), offers, now=NOW))


def test_quote_stales_on_state_edit_even_without_version_increment_and_source_price_change():
    state = sample_trip()
    offers = provider_offers(state)
    plan = assemble_plan(state, offers, now=NOW)
    state.budget.amount_minor += 1
    assert "stale_quote_state" in {i["code"] for i in validate_quote(plan, state, now=NOW)}
    state.budget.amount_minor -= 1
    current = deepcopy(offers)
    current[0]["amount_minor"] += 1
    assert "source_snapshot_changed" in {i["code"] for i in validate_quote(plan, state, current_offers=current, now=NOW)}


@pytest.mark.parametrize("field,value", [("total_minor", 1), ("currency", "USD"), ("source_type", "LIVE_BOOKABLE"),
                                       ("ready_for_real_booking", True), ("checkout_mode", "REAL"), ("components", [])])
def test_tampered_quote_fields_are_rejected(field, value):
    state = sample_trip()
    plan = assemble_plan(state, [], demo_fallback=True)
    plan[field] = value
    assert "quote_snapshot_changed" in {i["code"] for i in validate_quote(plan, state)}


def test_before_after_reports_price_and_source_change_without_double_summing_rooms():
    state = sample_trip()
    before = assemble_plan(state, [], demo_fallback=True)
    offers = provider_offers(state)
    offers[0]["amount_minor"] += 123
    after = assemble_plan(state, offers, now=NOW, previous_plan=before)
    comparison = compare_plans(before, after)
    assert comparison == after["comparison"]
    assert comparison["delta_minor"] == 123
    assert comparison["source_changed"]
    assert comparison["requires_new_confirmation"]
    assert comparison["changes"]


@pytest.mark.parametrize("value", [None, 0, "bad", {"not": "a list"}])
def test_malformed_offers_container_returns_blockers(value):
    plan = assemble_plan(sample_trip(), value)
    assert "invalid_offers" in codes(plan)
    assert plan["total_minor"] is None


def test_altered_demo_snapshot_cannot_be_accepted_as_catalog_truth():
    offers = demo_offers()
    offers[0]["total_minor"] = 1
    plan = assemble_plan(sample_trip(), offers)
    assert "unknown_demo_snapshot" in codes(plan)
    assert not plan["ready_for_simulated_checkout"]


def test_partial_room_coverage_is_not_silently_completed_with_a_second_hotel():
    offer = provider_offers()[0]
    binding = offer["details"]["binding"]
    binding["room_ids"] = binding["room_ids"][:1]
    binding["room_assignments"] = binding["room_assignments"][:1]
    plan = assemble_plan(sample_trip(), [offer], demo_fallback=True, now=NOW)
    assert "missing_components" in codes(plan)
    assert plan["total_minor"] is None
    assert any("вторым отелем" in warning for warning in plan["warnings"])


@pytest.mark.parametrize("bad", [None, 1, "wrong", [{"room_id": "wrong", "traveler_ids": None}]])
def test_malformed_allocation_is_blocked_without_crashing(bad):
    offers = provider_offers()
    offers[0]["details"]["binding"]["room_assignments"] = bad
    plan = assemble_plan(sample_trip(), offers, now=NOW)
    assert plan["blockers"]
    assert plan["total_minor"] is None


def test_room_capacity_and_room_count_price_scope_are_checked():
    offers = provider_offers()
    offers[0]["details"]["binding"]["room_assignments"][0]["capacity"] = 1
    offers[0]["details"]["search_request"] = {"occupancies": [{"adults": 2}]}
    plan = assemble_plan(sample_trip(), offers, now=NOW)
    assert {"room_capacity", "room_quote_scope_mismatch"}.issubset(codes(plan))


def test_requested_leg_route_mismatch_and_incomplete_multicity_are_blockers():
    offers = provider_offers()
    flight = next(o for o in offers if o["details"]["kind"] == "flight")
    flight["details"]["binding"]["departure_date"] = "2026-11-30"
    assert "leg_binding_mismatch" in codes(assemble_plan(sample_trip(), offers, now=NOW))
    flight["details"]["binding"].pop("departure_date")
    flight["details"]["binding"]["leg_ids"] = [leg.id for leg in sample_trip().legs]
    flight["details"]["itinerary_complete"] = False
    assert "incomplete_itinerary" in codes(assemble_plan(sample_trip(), offers, now=NOW))


def test_explicit_clock_and_new_preview_are_required_after_source_expiry():
    offers = provider_offers()
    assert "clock_required" in codes(assemble_plan(sample_trip(), offers))
    plan = assemble_plan(sample_trip(), offers, now=NOW)
    assert "stale_source_snapshot" in {i["code"] for i in validate_quote(plan, sample_trip(), now=NOW + timedelta(hours=2))}


def test_removed_source_and_modified_source_metadata_invalidate_quote():
    offers = provider_offers()
    plan = assemble_plan(sample_trip(), offers, now=NOW)
    current = deepcopy(offers)
    current.pop()
    assert "source_snapshot_changed" in {i["code"] for i in validate_quote(plan, sample_trip(), current_offers=current, now=NOW)}
    offers[0]["details"]["fees_included"] = False
    assert "source_snapshot_changed" in {i["code"] for i in validate_quote(plan, sample_trip(), current_offers=offers, now=NOW)}


def test_budget_excess_is_a_known_total_but_not_checkout_permission():
    state = sample_trip()
    offers = provider_offers(state)
    state.budget.amount_minor = 1
    plan = assemble_plan(state, offers, now=NOW)
    assert plan["total_minor"] == 33_340_000
    assert "budget_exceeded" in codes(plan)
    assert plan["ready_for_simulated_checkout"] is False


@pytest.mark.parametrize("evidence", [{}, {"breakfast": False}, {"breakfast": "true"}, {"breakfast": 1}])
def test_unverified_hard_condition_is_not_satisfied_by_a_warning(evidence):
    offers = provider_offers()
    offers[0]["details"]["constraint_evidence"] = evidence
    plan = assemble_plan(sample_trip(), offers, now=NOW)
    assert "hard_constraint_unverified" in codes(plan)
    assert not plan["ready_for_simulated_checkout"]


def test_source_pin_is_case_insensitive():
    offers = provider_offers()
    offers[0].update(provider="SerpApi", source_type="LIVE_BOOKABLE")
    offers[0]["details"].update(supplier_reprice_verified=True, availability_verified=True)
    plan = assemble_plan(sample_trip(), offers, now=NOW)
    assert plan["offers"][0]["source_type"] == "LIVE_INDICATIVE"
