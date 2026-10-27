"""Fixed provider-registry transfers; controlled offers, no HTTP or SQL."""
import asyncio
from copy import deepcopy

import pytest
from test_dynamic_place_suppliers import dynamic_trip
from test_mixed_checkout import CheckoutTravel

from otravel.domain import TripState
from otravel.inventory import (
    REGISTERED_TRANSFER_CATALOGUE,
    REGISTERED_TRANSFER_PROVIDER,
    DemoInventory,
    bounded_place_transfer_catalog,
    bounded_registered_transfer_catalog,
    bounded_transport_catalog,
)
from otravel.orchestrator import TripSearch
from otravel.planning import assemble_plan


def registered_trip():
    return TripState.model_validate({"trip_id": "owner-registered", "state_version": 7,
        "origin": "Санкт-Петербург", "travelers": [{"id": "adult", "type": "adult"}],
        "stays": [{"id": "belgrade", "city": "Белград", "check_in": "2026-12-28", "check_out": "2027-01-05",
            "rooms": [{"id": "room", "traveler_ids": []}]}],
        "legs": [{"id": "out", "origin": "Санкт-Петербург", "destination": "Белград", "departure_date": "2026-12-28"},
            {"id": "back", "origin": "Белград", "destination": "Санкт-Петербург", "departure_date": "2027-01-05"}],
        "search_policy": {"mode": "HYBRID", "hotel_provider": "liteapi", "flight_provider": "duffel",
            "demo_fallback": True, "guest_nationality": "RU"}})


def controlled_search(state=None, adapter=None):
    return asyncio.run(TripSearch(adapter or CheckoutTravel()).search(state or registered_trip()))


def test_belgrade_gap_filled_only_by_explicit_synthetic_transfer_after_actual_component_facts():
    state = registered_trip()
    before = state.model_dump(mode="json")
    result = controlled_search(state)
    plan, = result["plans"]
    transfer, = [offer for offer in plan["offers"] if offer.get("source_type") == "DEMO"]
    assert transfer["provider"] == REGISTERED_TRANSFER_PROVIDER and transfer["catalogue"] == REGISTERED_TRANSFER_CATALOGUE
    assert transfer["kind"] == "transfer" and transfer["total_minor"] == 40000 and transfer["country_iso2"] == "RS"
    assert transfer["traveler_ids"] == ["adult"] and transfer["capacity"] == 4
    assert not any(key in transfer for key in ("place_id", "latitude", "longitude", "distance"))
    assert transfer["availability"] == "synthetic_not_live" and not transfer["booking_created_at_supplier"]
    assert len(plan["offers"]) == 4 and plan["source_type"] == "MIXED"
    assert plan["coverage"]["complete"] and plan["total_minor"] == 3040000
    assert plan["ready_for_simulated_checkout"] and not plan["ready_for_real_booking"]
    assert plan["room_assignments"]["belgrade"][0]["traveler_ids"] == ["adult"]
    assert state.model_dump(mode="json") == before and state.stays[0].rooms[0].traveler_ids == []


def test_new_registry_does_not_expand_old_seven_city_or_exact_ref_catalogues():
    state = registered_trip()
    assert DemoInventory().search_trip(state, catalogue_only=True)["plans"] == []
    old = bounded_transport_catalog(state)
    assert old["offers"] == [] and any(row["code"] == "unsupported_city" for row in old["violations"])
    assert bounded_place_transfer_catalog(state)["offers"] == []
    assert bounded_registered_transfer_catalog(dynamic_trip())["offers"] == []


@pytest.mark.parametrize("change", ["no_fallback", "demo_mode", "serp_hotel", "serp_flight", "unregistered"])
def test_registry_transfer_requires_exact_source_policy_and_registered_stay(change):
    data = registered_trip().model_dump(mode="json")
    if change == "no_fallback": data["search_policy"]["demo_fallback"] = False
    if change == "demo_mode": data["search_policy"]["mode"] = "DEMO"
    if change == "serp_hotel": data["search_policy"]["hotel_provider"] = "serpapi"
    if change == "serp_flight": data["search_policy"]["flight_provider"] = "serpapi"
    if change == "unregistered":
        data["stays"][0]["city"] = "Белгород"
        data["legs"][0]["destination"] = "Белгород"
        data["legs"][1]["origin"] = "Белгород"
    assert bounded_registered_transfer_catalog(data)["offers"] == []


@pytest.mark.parametrize("condition,code", [
    ({"key": "wheelchair", "value": True, "hard": True}, "unsupported_constraint"),
    ({"key": "breakfast", "value": "yes", "hard": True}, "invalid_constraint"),
    ({"key": "pets", "value": {"kind": "dog", "weight_kg": 21}, "hard": True}, "transport_pets"),
])
def test_existing_hard_and_pet_guards_still_block_transfer(condition, code):
    data = registered_trip().model_dump(mode="json")
    data["constraints"] = [condition]
    result = bounded_registered_transfer_catalog(data)
    assert result["offers"] == [] and code in {row["code"] for row in result["violations"]}


def test_malformed_nonstop_is_rejected_by_existing_domain_before_catalogue():
    from pydantic import ValidationError
    data = registered_trip().model_dump(mode="json")
    data["constraints"] = [{"key": "nonstop", "value": "yes", "hard": True}]
    with pytest.raises(ValidationError):
        bounded_registered_transfer_catalog(data)


def test_five_people_and_pet_use_two_vehicles_and_canonical_pet_fee():
    data = registered_trip().model_dump(mode="json")
    data["travelers"] = [{"id": f"adult-{index}", "type": "adult"} for index in range(5)]
    data["stays"][0]["rooms"].append({"id": "room2", "traveler_ids": []})
    data["constraints"] = [{"key": "pets", "value": {"kind": "cat", "weight_kg": 4}, "hard": True}]
    transfer, = bounded_registered_transfer_catalog(data)["offers"]
    assert transfer["vehicle_count"] == 2 and transfer["seats_per_vehicle"] == 4 and transfer["capacity"] == 8
    assert transfer["total_minor"] == 5 * 40000 + 100000 and len(transfer["traveler_ids"]) == 5


@pytest.mark.parametrize("field,value", [("total_minor", 1), ("capacity", 1), ("country_iso2", "US"),
    ("registry_iata_code", "LED"), ("check_out", "2027-01-06"), ("state_version", 8)])
def test_modified_registered_demo_snapshot_cannot_enter_assembled_plan(field, value):
    state = registered_trip()
    good = controlled_search(state)["plans"][0]
    original, = bounded_registered_transfer_catalog(state)["offers"]
    forged = deepcopy(original)
    forged[field] = value
    external = [deepcopy(offer) for offer in good["offers"] if offer["source_type"] != "DEMO"]
    # Use source retrieval time, not an invented later clock, for this pure check.
    from datetime import datetime
    now = datetime.fromisoformat(external[0]["retrieved_at"])
    plan = assemble_plan(state, [*external, forged], demo_fallback=False, now=now)
    assert "unknown_demo_snapshot" in {issue["code"] for issue in plan["blockers"]}
    assert not plan["ready_for_simulated_checkout"]


def test_registered_transfer_cannot_claim_external_live_source():
    state = registered_trip()
    forged = bounded_registered_transfer_catalog(state)["offers"][0]
    forged["source_type"] = "LIVE_BOOKABLE"
    plan = assemble_plan(state, [forged], demo_fallback=False)
    assert plan["offers"] == [] and "unknown_demo_snapshot" in {row["code"] for row in plan["blockers"]}


def test_transfer_does_not_fill_unproven_hotel_fees_or_missing_real_components():
    class NoHotelEvidence(CheckoutTravel):
        async def search_hotels(self, request, provider="liteapi"):
            rows = await super().search_hotels(request, provider)
            rows[0]["details"].pop("fees_included")
            return rows
    result = controlled_search(adapter=NoHotelEvidence())
    assert result["plans"] == [] and not result["partial_plan"]["ready_for_simulated_checkout"]
    assert not any(offer["source_type"] == "DEMO" for offer in result["partial_plan"]["offers"])
