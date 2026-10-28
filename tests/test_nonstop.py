"""Nonstop requirement contracts; every supplier HTTP is a MockTransport."""
import asyncio
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import httpx
import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from test_orchestrator import FakeTravel, hybrid, search

from otravel.domain import Constraint, DomainError, apply_operations, sample_trip
from otravel.inventory import DemoInventory, reprice_plan, validate_plan
from otravel.llm_contract import build_operation_schema
from otravel.planning import assemble_plan, validate_quote
from otravel.providers import TravelGateway

NOW = datetime(2026, 10, 26, tzinfo=UTC)
REQUEST = {"slices": [{"origin": "KZN", "destination": "IST", "departure_date": "2026-11-10"}], "passengers": [{"type": "adult"}], "nonstop": True}


def response_slice(*, connections=0, stops=None, origin="KZN", destination="IST", day="2026-11-10"):
    places = [origin, *(["AYT"] if connections else []), destination]
    return {"origin": {"iata_code": origin}, "destination": {"iata_code": destination}, "segments": [
        {"origin": {"iata_code": a}, "destination": {"iata_code": b}, "departing_at": day + "T10:00:00", "stops": [] if stops is None else stops}
        for a, b in pairwise(places)]}


def duffel_response(slices):
    return {"id": "off_fixture_nonstop", "total_amount": "100.50", "total_currency": "RUB", "slices": slices,
            "expires_at": (NOW + timedelta(hours=1)).isoformat()}


def gateway_call(method, request=None, *, slices=None):
    seen = []
    def handler(http):
        seen.append(http)
        if http.method == "POST":
            assert json.loads(http.content)["data"]["max_connections"] == 0
            return httpx.Response(200, json={"data": {"offers": [duffel_response(slices)]}})
        return httpx.Response(200, json={"data": duffel_response(slices)})
    async def run():
        gateway = TravelGateway({"http_transport": httpx.MockTransport(handler), "DUFFEL_ACCESS_TOKEN": "duffel_test_synthetic_nonstop"})
        try:
            return await getattr(gateway, method)(request)
        finally:
            await gateway.close()
    return asyncio.run(run()), seen


def test_nonstop_schema_accepts_and_domain_keeps_it_distinct_from_hotel_refund():
    operation = {"type": "set_constraint", "value": {"key": "nonstop", "value": True}}
    envelope = {"operations": [operation], "control": {"action": "none"}, "reply": "Только без пересадок.", "unknowns": [], "conflicts": []}
    assert Draft202012Validator(build_operation_schema(sample_trip())).is_valid(envelope)
    state, _ = apply_operations(sample_trip(), [operation])
    assert state.constraints[-1].key == "nonstop" and not any(c.key == "refundable" for c in state.constraints)


def test_demo_nonstop_is_explicit_synthetic_per_leg_and_preserved_on_reprice():
    state, _ = apply_operations(sample_trip(), [{"type": "set_constraint", "value": {"key": "nonstop", "value": True}}])
    result = DemoInventory().search_trip(state)
    assert result["plans"]
    plan = result["plans"][0]
    flights = [o for o in plan["offers"] if o["kind"] == "flight"]
    assert len(flights) == 3 and all(o["nonstop"] is True and o["source_type"] == "DEMO" for o in flights)
    assert validate_plan(state, reprice_plan(plan)) == []
    quote = assemble_plan(state, [], demo_fallback=True)
    assert quote["ready_for_simulated_checkout"] and not quote["ready_for_real_booking"] and validate_quote(quote, state) == []


@pytest.mark.parametrize("slices,expected", [([response_slice()], True), ([response_slice(connections=1)], False),
    ([response_slice(stops=[{"id": "technical-stop"}])], False), ([], None),
    ([response_slice(origin="MOW")], None)])
def test_duffel_request_filter_is_not_response_proof(slices, expected):
    offers, seen = gateway_call("search_flights", deepcopy(REQUEST), slices=slices)
    assert len(seen) == 1 and seen[0].url.path == "/air/offer_requests"
    actual = offers[0]["details"].get("constraint_evidence", {}).get("nonstop")
    assert actual is expected and offers[0]["source_type"] == "SANDBOX"


def test_duffel_refresh_recomputes_response_fact_and_does_not_retain_old_true():
    offers, _ = gateway_call("search_flights", deepcopy(REQUEST), slices=[response_slice()])
    refreshed, seen = gateway_call("reprice", offers[0], slices=[response_slice(connections=1)])
    assert len(seen) == 1 and seen[0].method == "GET"
    assert refreshed["details"]["constraint_evidence"]["nonstop"] is False


@pytest.mark.parametrize("value", ["yes", 1, None, [], {}])
def test_nonstop_rejects_non_boolean_at_canonical_domain_boundary(value):
    with pytest.raises(ValidationError):
        Constraint(key="nonstop", value=value)


@pytest.mark.parametrize("scope", [{"stay_id": "stay-istanbul"}, {"stay_id": "stay-istanbul", "room_id": "stay-istanbul-room-1"}])
def test_flight_constraint_cannot_be_attached_to_hotel_or_room(scope):
    with pytest.raises(DomainError):
        apply_operations(sample_trip(), [{"type": "set_constraint", "value": {"key": "nonstop", "value": True, **scope}}])


def test_false_or_clear_allows_connections_instead_of_requiring_them():
    state, _ = apply_operations(sample_trip(), [{"type": "set_constraint", "value": {"key": "nonstop", "value": False}}])
    assert DemoInventory().search_trip(state)["plans"]
    cleared, _ = apply_operations(state, [{"type": "clear_constraint", "value": {"key": "nonstop"}}])
    assert [c.key for c in cleared.constraints] == ["breakfast"]


@pytest.mark.parametrize("change", ["missing_stops", "wrong_date", "wrong_header", "extra_slice", "incomplete_connection"])
def test_incomplete_or_wrong_response_scope_never_becomes_proven_nonstop(change):
    slices = [response_slice()]
    if change == "missing_stops":
        slices[0]["segments"][0].pop("stops")
    elif change == "wrong_date":
        slices[0]["segments"][0]["departing_at"] = "2026-11-11T10:00:00"
    elif change == "wrong_header":
        slices[0]["origin"] = {"iata_code": "MOW"}
    elif change == "extra_slice":
        slices.append(response_slice())
    else:
        slices = [response_slice(connections=1)]
        slices[0]["segments"][1]["origin"] = {"iata_code": "DME"}
    offers, _ = gateway_call("search_flights", deepcopy(REQUEST), slices=slices)
    assert "nonstop" not in offers[0]["details"]["constraint_evidence"]


@pytest.mark.parametrize("fact", [None, False, 1, "true"])
def test_missing_or_nontrue_external_flight_fact_blocks_quote_even_when_request_said_nonstop(fact):
    state, _ = apply_operations(sample_trip(), [{"type": "set_constraint", "value": {"key": "nonstop", "value": True}}])
    flight = next(o for o in DemoInventory().search_trip(state)["plans"][0]["offers"] if o["kind"] == "flight")
    offer = {"offer_id": "fixture-external-flight", "provider": "duffel", "source_type": "SANDBOX", "price_kind": "COMPONENT_TOTAL",
        "currency": "RUB", "amount_minor": flight["total_minor"], "retrieved_at": NOW.isoformat(), "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        "details": {"kind": "flight", "fees_included": True, "group_quote": True,
            "passengers_count": len(state.travelers), "search_request": {"nonstop": True, "max_connections": 0},
            "constraint_evidence": {} if fact is None else {"nonstop": fact},
            "binding": {"trip_id": state.trip_id, "state_version": state.state_version, "leg_ids": [flight["component_id"]],
                "traveler_ids": flight["traveler_ids"], "origin": flight["origin"], "destination": flight["destination"], "departure_date": flight["departure_date"]}}}
    failed = assemble_plan(state, [offer], demo_fallback=True, now=NOW)
    assert not failed["ready_for_simulated_checkout"] and any(b["code"] == "hard_constraint_unverified" for b in failed["blockers"])
    offer["details"]["constraint_evidence"] = {"nonstop": True}
    proven = assemble_plan(state, [offer], demo_fallback=True, now=NOW)
    assert proven["ready_for_simulated_checkout"] and proven["source_type"] == "MIXED" and not proven["ready_for_real_booking"]
    assert validate_quote(proven, state, now=NOW) == []


def test_demo_nonstop_fact_tampering_is_rejected_on_validation_and_quote():
    state, _ = apply_operations(sample_trip(), [{"type": "set_constraint", "value": {"key": "nonstop", "value": True}}])
    plan = deepcopy(DemoInventory().search_trip(state)["plans"][0])
    flight = next(o for o in plan["offers"] if o["kind"] == "flight")
    flight["nonstop"] = False
    assert any(i["code"] == "flight_facts_mismatch" for i in validate_plan(state, plan))


@pytest.mark.parametrize("fact", [True, False, None])
def test_orchestrator_passes_requirement_and_selects_only_response_proven_flights(fact):
    class Flights(FakeTravel):
        def offer(self, request, provider, kind):
            result = super().offer(request, provider, kind)
            if kind == "flight" and fact is not None:
                result["details"]["constraint_evidence"]["nonstop"] = fact
            return result
    state = hybrid(flight_provider="duffel")
    state.constraints.append(Constraint(key="nonstop", value=True))
    gateway = Flights()
    result = search(gateway, state)
    calls = [request for method, _, request in gateway.calls if method == "search_flights"]
    assert len(calls) == 3 and all(request["nonstop"] is True for request in calls)
    assert result["plans"]
    flights = [component for component in result["plans"][0]["components"] if component["binding"].get("leg_ids")]
    if fact is True:
        assert all(component["source_type"] == "SANDBOX" for component in flights)
    else:
        assert all(component["source_type"] == "DEMO" for component in flights)
        assert all("hard_constraint_unverified" in issue["message"] for issue in result["component_errors"] if issue["details"]["component_id"] in {leg.id for leg in state.legs})
