"""Independent DEMO transport cannot replace a pinned source hotel. No HTTP."""
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from otravel.domain import Constraint, TripState
from otravel.fx import parse_cbr_xml
from otravel.inventory import DemoInventory, bounded_transport_catalog
from otravel.planning import assemble_plan, validate_quote

NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)


def pinned():
    return TripState(origin="Москва", travelers=[{"id": "adult", "type": "adult"}],
        stays=[{"id": "dubai", "city": "Дубай", "hotel_id": "lp1b30d1", "check_in": "2026-11-10", "check_out": "2026-11-14",
                "rooms": [{"id": "room", "traveler_ids": ["adult"]}]}],
        legs=[{"id": "out", "origin": "Москва", "destination": "Дубай", "departure_date": "2026-11-10"},
              {"id": "back", "origin": "Дубай", "destination": "Москва", "departure_date": "2026-11-14"}],
        budget={"amount_minor": 50_000_000}, search_policy={"mode": "HYBRID", "guest_nationality": "RU", "flight_provider": "duffel"})


def fx():
    return parse_cbr_xml(b'<ValCurs Date="26.10.2026"><Valute><CharCode>USD</CharCode><Nominal>1</Nominal><Value>80</Value></Valute>'
        b'<Valute><CharCode>EUR</CharCode><Nominal>1</Nominal><Value>100</Value></Valute></ValCurs>', NOW)


def source_offers(state):
    common = {"trip_id": state.trip_id, "state_version": state.state_version}
    rows = [{"offer_id": "source-hotel", "provider": "liteapi", "source_type": "SANDBOX", "price_kind": "COMPONENT_TOTAL",
        "currency": "RUB", "amount_minor": 2_482_372, "retrieved_at": NOW.isoformat(), "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        "details": {"kind": "hotel", "fees_included": True, "mandatory_fees_known": True, "allocation_verified": True,
            "capacity_verified": True, "constraint_evidence": {"breakfast": True, "refundable": True},
            "hotel_id": "lp1b30d1", "binding": {**common, "stay_id": "dubai", "room_ids": ["room"],
                "room_assignments": [{"room_id": "room", "traveler_ids": ["adult"]}], "city": "Дубай",
                "checkin": "2026-11-10", "checkout": "2026-11-14", "hotel_id": "lp1b30d1"}}}]
    for leg, amount in zip(state.legs, (16240, 15723)):
        rows.append({"offer_id": "source-" + leg.id, "provider": "duffel", "source_type": "SANDBOX", "price_kind": "COMPONENT_TOTAL",
            "currency": "EUR", "amount_minor": amount, "retrieved_at": NOW.isoformat(), "expires_at": (NOW + timedelta(hours=1)).isoformat(),
            "details": {"kind": "flight", "fees_included": True, "group_quote": True, "passengers_count": 1,
                "received_passengers_count": 1, "constraint_evidence": {"nonstop": True},
                "binding": {**common, "leg_ids": [leg.id], "traveler_ids": ["adult"], "origin": leg.origin,
                    "destination": leg.destination, "departure_date": leg.departure_date.isoformat()}}})
    return rows


def test_pinned_native_hotel_and_external_flights_get_only_the_missing_DEMO_transfer():
    state = pinned()
    original = state.model_dump(mode="json")
    offers = source_offers(state)
    native_bytes = json.dumps(offers, sort_keys=True, ensure_ascii=False)
    plan = assemble_plan(state, offers, demo_fallback=True, fx_snapshot=fx(), now=NOW)
    assert plan["ready_for_simulated_checkout"], plan["blockers"]
    assert plan["coverage"]["complete"] and len(plan["coverage"]["required"]) == 4
    demo = [offer for offer in plan["offers"] if offer["source_type"] == "DEMO"]
    assert len(demo) == 1 and demo[0]["kind"] == "transfer" and demo[0]["component_id"] == "transfer-dubai"
    assert demo[0]["total_minor"] == 40_000 and plan["total_minor"] == 5_718_672
    assert plan["currency_totals"] == {"RUB": 2_522_372, "EUR": 31963}
    assert len([o for o in plan["offers"] if o.get("details", {}).get("kind") == "hotel"]) == 1
    assert plan["offers"][0]["details"]["hotel_id"] == "lp1b30d1"
    assert json.dumps(offers, sort_keys=True, ensure_ascii=False) == native_bytes and state.model_dump(mode="json") == original
    assert validate_quote(plan, state, now=NOW) == [] and not plan["ready_for_real_booking"]


def test_no_fallback_policy_does_not_add_even_an_allowed_transport_snapshot():
    state = pinned()
    state.search_policy.demo_fallback = False
    plan = assemble_plan(state, source_offers(state), demo_fallback=False, fx_snapshot=fx(), now=NOW)
    assert not plan["ready_for_simulated_checkout"] and plan["total_minor"] is None
    assert plan["coverage"]["missing"] == ["transfer:dubai"]
    assert all(o["source_type"] != "DEMO" for o in plan["offers"])


@pytest.mark.parametrize("forge_pin", [False, True])
def test_independent_transport_catalog_never_authorizes_a_different_DEMO_hotel(forge_pin):
    state = pinned()
    other = deepcopy(state)
    other.stays[0].hotel_id = None  # Deliberately craft a hotel from a different test state.
    foreign_hotel = deepcopy(next(o for o in DemoInventory().search_trip(other)["plans"][0]["offers"] if o["kind"] == "hotel"))
    if forge_pin:
        foreign_hotel["hotel_id"] = "lp1b30d1"
    assert DemoInventory().search_trip(state, catalogue_only=True)["plans"] == []
    plan = assemble_plan(state, [foreign_hotel, *source_offers(state)[1:]], demo_fallback=True, fx_snapshot=fx(), now=NOW)
    assert "unknown_demo_snapshot" in {issue["code"] for issue in plan["blockers"]}
    assert not plan["ready_for_simulated_checkout"]
    assert not any(o.get("kind") == "hotel" for o in plan["offers"])
    assert state.stays[0].hotel_id == "lp1b30d1"


def test_independent_catalog_keeps_pin_and_rejects_unknown_or_oversized_transport_pets():
    state = pinned()
    before = state.model_dump(mode="json")
    valid = bounded_transport_catalog(state)
    assert len(valid["offers"]) == 3 and all(o["kind"] in {"flight", "transfer"} for o in valid["offers"])
    assert state.model_dump(mode="json") == before
    for pets in (True, {"kind": "dog", "weight_kg": 40}):
        blocked = deepcopy(state)
        blocked.constraints.append(Constraint(key="pets", value=pets))
        result = bounded_transport_catalog(blocked)
        assert result["offers"] == [] and (result["questions"] or result["violations"])


def test_nonstop_and_geography_facts_cannot_be_relaxed_by_transport_only_fallback():
    state = pinned()
    state.constraints.append(Constraint(key="nonstop", value=True))
    allowed = bounded_transport_catalog(state)
    direct = next(o for o in allowed["offers"] if o["kind"] == "flight")
    assert direct["nonstop"] is True and direct["segment_count"] == 1 and direct["stops_count"] == 0
    tampered = deepcopy(direct)
    tampered.update(nonstop=False, stops_count=1)
    plan = assemble_plan(state, [source_offers(state)[0], tampered, source_offers(state)[2]],
                         demo_fallback=True, fx_snapshot=fx(), now=NOW)
    assert "unknown_demo_snapshot" in {issue["code"] for issue in plan["blockers"]}
    assert not plan["ready_for_simulated_checkout"]
    outside = deepcopy(state)
    outside.stays[0].city = "Белград"
    outside.legs[0].destination = "Белград"
    outside.legs[1].origin = "Белград"
    assert bounded_transport_catalog(outside)["offers"] == []
