"""Declared geography, intercepted HTTP and fake orchestration. No actual API calls."""
import asyncio
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from test_orchestrator import FakeTravel
from test_place_refs import place

from otravel.domain import CITIES, Operation, SearchPolicy, TripState, apply_operations
from otravel.orchestrator import TripSearch
from otravel.provider_cities import available_search_cities, place_coordinates_match, place_metadata_matches
from otravel.providers import ProviderError, TravelGateway

NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)


def ref(**kw):
    return place(name="Buenos Aires", country="AR", **kw)


def gateway(handler, **extra):
    return TravelGateway({"http_transport": httpx.MockTransport(handler), "clock": lambda: NOW,
        "LITEAPI_API_KEY": "sand_declared-dynamic-fixture", "LITEAPI_MODE": "sandbox",
        "DUFFEL_ACCESS_TOKEN": "duffel_test_declared-dynamic-fixture", **extra})


async def invoke(adapter, method, *args, **kw):
    try:
        return await getattr(adapter, method)(*args, **kw)
    finally:
        await adapter.close()


def hotel_row(location=None, **overrides):
    location = location or ref()
    return {"id": "hotel-declared-1", "name": "Declared Hotel", "country": location.country_iso2,
        "city": location.canonical_name, "latitude": location.latitude + 0.01,
        "longitude": location.longitude + 0.01, **overrides}


def hotel_request(location=None):
    location = location or ref()
    return {"hotelIds": ["hotel-declared-1"], "checkin": "2026-11-10", "checkout": "2026-11-13",
        "currency": "RUB", "guestNationality": "RU", "occupancies": [{"adults": 1, "children": []}],
        "place_ref": location.model_dump(mode="json")}


def rates_body():
    return {"data": [{"hotelId": "hotel-declared-1", "roomTypes": [{"offerId": "declared-rate",
        "offerRetailRate": {"amount": "100.00", "currency": "RUB"},
        "rates": [{"rateId": "declared-room", "occupancyNumber": 1, "adultCount": 1,
            "childCount": 0, "childrenAges": [], "maxOccupancy": 1, "boardType": "BI",
            "retailRate": {"taxesAndFees": None}}]}]}]}


def city_row(location=None, **overrides):
    location = location or ref()
    return {"id": "city-declared-bue-ar", "type": "city", "name": location.canonical_name,
        "iata_country_code": location.country_iso2, "iata_code": "BUE",
        "latitude": location.latitude, "longitude": location.longitude, **overrides}


def airport_row(location=None, **overrides):
    location = location or ref()
    return {"id": "airport-declared-eze-ar", "type": "airport", "name": "Declared Airport",
        "city_name": location.canonical_name, "iata_country_code": location.country_iso2,
        "iata_code": "EZE", "latitude": location.latitude + 0.02,
        "longitude": location.longitude + 0.02, "time_zone": "America/Argentina/Buenos_Aires", **overrides}


def dynamic_trip(location=None, *, demo=False):
    location = location or ref()
    state = TripState(trip_id="declared-dynamic-trip", search_policy=SearchPolicy(mode="DEMO" if demo else "HYBRID",
        hotel_provider="liteapi", flight_provider="duffel", guest_nationality="RU", demo_fallback=True))
    return apply_operations(state, [Operation(type="set_origin", value={"city": "Казань"}),
        Operation(type="add_traveler", value={"id": "adult-1", "type": "adult"}),
        Operation(type="upsert_stay", target_id="stay-world", value={"city": location.canonical_name,
            "place_id": location.place_id, "check_in": "2026-11-10", "check_out": "2026-11-13",
            "rooms": [{"id": "room-1", "traveler_ids": ["adult-1"]}]})],
        resolved_places={location.place_id: location})[0]


def test_hybrid_registry_adds_trusted_ref_names_but_demo_remains_exact_seven():
    state = dynamic_trip()
    assert available_search_cities(state) == (*CITIES, "Белград", "Buenos Aires")
    state.search_policy.mode = "DEMO"
    assert available_search_cities(state) == tuple(CITIES)
    raw = state.model_dump(mode="json"); raw["search_policy"]["mode"] = "HYBRID"
    raw["location_refs"][ref().place_id]["country_iso2"] = "broken"
    assert "Buenos Aires" not in available_search_cities(raw)


def test_hotel_reference_lookup_is_exact_country_city_one_http_and_only_identity():
    location, calls = ref(), []
    def handler(request):
        calls.append(request)
        assert request.url.path == "/v3.0/data/hotels"
        assert dict(request.url.params) == {"countryCode": "AR", "cityName": "Buenos Aires", "limit": "3"}
        return httpx.Response(200, json={"data": [hotel_row(location)]})
    result = asyncio.run(invoke(gateway(handler), "resolve_hotels", {"city": location.canonical_name,
        "place_ref": location.model_dump(mode="json")}))
    assert len(calls) == 1 and result[0]["identity_verified"] is True
    assert result[0]["place_id"] == location.place_id
    assert result[0]["place_ref_sha256"] == location.snapshot_sha256()
    assert "amount_minor" not in result[0] and "availability_verified" not in result[0]


@pytest.mark.parametrize("wrong", [
    {"country": "ES"}, {"city": "La Plata"}, {"latitude": None}, {"longitude": None},
    {"latitude": 40.0}, {"city": None},
])
def test_dynamic_lookup_does_not_label_other_or_unknown_location_as_selected_city(wrong):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"data": [hotel_row(**wrong)]})
    with pytest.raises(ProviderError) as error:
        asyncio.run(invoke(gateway(handler), "resolve_hotels", {"place_ref": ref().model_dump(mode="json")}))
    assert error.value.code == "GEOGRAPHY_UNVERIFIED" and len(calls) == 1


def test_same_display_name_in_us_never_uses_russian_legacy_alias_or_country():
    location = place(55, "Москва", "US", latitude=46.7, longitude=-117.0, timezone="America/Los_Angeles")
    def handler(request):
        assert request.url.params["countryCode"] == "US" and request.url.params["cityName"] == "Москва"
        return httpx.Response(200, json={"data": [hotel_row(location, country="RU")]})
    with pytest.raises(ProviderError):
        asyncio.run(invoke(gateway(handler), "resolve_hotels", {"place_ref": location.model_dump(mode="json")}))
    assert not place_metadata_matches(location, country="RU", city="Moscow", latitude=46.7, longitude=-117.0)


@pytest.mark.parametrize("latitude,valid", [(0.44, True), (0.46, False), (None, False), (True, False), (float("nan"), False)])
def test_spatial_consistency_is_a_fifty_km_guard_not_nearest_city_inference(latitude, valid):
    location = ref(latitude=0.0, longitude=0.0)
    assert place_coordinates_match(location, latitude, 0.0) is valid


def test_country_listing_is_one_bounded_sandbox_reference_request():
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url.path == "/v3.0/data/countries" and not request.url.params
        return httpx.Response(200, json={"data": [{"code": "AR", "name": "Argentina"}, {"code": "US", "name": "United States"}]})
    assert asyncio.run(invoke(gateway(handler), "list_countries")) == [
        {"country_iso2": "AR", "name": "Argentina"}, {"country_iso2": "US", "name": "United States"}]
    assert len(calls) == 1


@pytest.mark.parametrize("data", [[{"code": "AR", "name": "a"}, {"code": "AR", "name": "b"}],
    [{"code": "Argentina", "name": "Argentina"}], [{"code": "AR"}], {}, [None]])
def test_country_listing_never_guesses_or_collapses_invalid_country_rows(data):
    with pytest.raises(ProviderError) as error:
        asyncio.run(invoke(gateway(lambda _: httpx.Response(200, json={"data": data})), "list_countries"))
    assert error.value.code == "INVALID_RESPONSE"


def test_new_reference_methods_reject_live_credentials_before_http():
    def forbidden(_):
        pytest.fail("Unexpected outbound attempt")
    with pytest.raises(ProviderError) as error:
        asyncio.run(invoke(gateway(forbidden, LITEAPI_API_KEY="prod_declared"), "list_countries"))
    assert error.value.code == "CONFIG_BLOCKED"
    with pytest.raises(ProviderError) as error:
        asyncio.run(invoke(gateway(forbidden, DUFFEL_ACCESS_TOKEN="duffel_live_declared"),
            "resolve_air_place", {"place_ref": ref().model_dump(mode="json")}))
    assert error.value.code == "CONFIG_BLOCKED"


def test_dynamic_rates_require_fresh_hotel_identity_lookup_before_http():
    def forbidden(_):
        pytest.fail("A rate request without lookup identity must be blocked")
    with pytest.raises(ProviderError) as error:
        asyncio.run(invoke(gateway(forbidden), "search_hotels", hotel_request()))
    assert error.value.code == "GEOGRAPHY_UNVERIFIED"


def test_verified_hotel_lookup_then_rates_are_two_explicit_single_requests():
    calls = []
    def handler(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [hotel_row()]})
        data = json.loads(request.content)
        assert "place_ref" not in data and "geographic_source" not in data
        assert data["hotelIds"] == ["hotel-declared-1"]
        return httpx.Response(200, json=rates_body())
    async def run():
        adapter = gateway(handler)
        try:
            await adapter.resolve_hotels({"place_ref": ref().model_dump(mode="json")})
            result = await adapter.search_hotels(hotel_request())
            assert result[0]["details"]["geography_evidence"]["identity_verified"] is True
            assert result[0]["details"]["availability_verified"] is False
            assert result[0]["source_type"] == "SANDBOX"
        finally:
            await adapter.close()
    asyncio.run(run())
    assert len(calls) == 2


def test_stale_identity_and_conflicting_rate_metadata_are_never_ready():
    now = [NOW]
    calls = []
    def handler(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [hotel_row()]})
        result = rates_body()
        result["hotels"] = [{"id": "hotel-declared-1", "country_code": "ES", "city_name": "Buenos Aires", "latitude": -34.6, "longitude": -58.38}]
        return httpx.Response(200, json=result)
    async def run():
        adapter = gateway(handler, clock=lambda: now[0])
        try:
            await adapter.resolve_hotels({"place_ref": ref().model_dump(mode="json")})
            with pytest.raises(ProviderError) as error:
                await adapter.search_hotels(hotel_request())
            assert error.value.code == "GEOGRAPHY_UNVERIFIED"
            now[0] += timedelta(seconds=3601)
            with pytest.raises(ProviderError) as error:
                await adapter.search_hotels(hotel_request())
            assert error.value.code == "GEOGRAPHY_UNVERIFIED"
        finally:
            await adapter.close()
    asyncio.run(run())
    assert len(calls) == 2


def test_air_lookup_prefers_explicit_unique_metro_city_without_airport_timezone_inference():
    location = ref(timezone=None)
    city = city_row(location, latitude=None, longitude=None, airports=[airport_row(location)])
    before = location.model_dump(mode="json")
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url.path == "/places/suggestions"
        assert dict(request.url.params) == {"query": "Buenos Aires"}
        return httpx.Response(200, json={"data": [city, airport_row(location)]})
    result = asyncio.run(invoke(gateway(handler), "resolve_air_place", {"place_ref": before}))
    assert result["status"] == "RESOLVED" and result["iata_code"] == "BUE" and result["place_type"] == "city"
    assert result["availability_verified"] is False and len(calls) == 1
    assert location.model_dump(mode="json") == before and location.timezone is None


@pytest.mark.parametrize("rows,status", [
    ([city_row(latitude=None, longitude=None)], "UNVERIFIED"),
    ([city_row(iata_country_code="ES")], "UNVERIFIED"),
    ([city_row(latitude=40.0)], "UNVERIFIED"),
    ([city_row(), city_row(id="second-city", iata_code="XXX")], "AMBIGUOUS"),
    ([airport_row(), airport_row(id="second-airport", iata_code="AEP")], "AMBIGUOUS"),
    ([], "NO_MATCH"),
])
def test_air_lookup_never_guesses_missing_spatial_identity_or_ambiguous_airport(rows, status):
    result = asyncio.run(invoke(gateway(lambda _: httpx.Response(200, json={"data": rows})),
        "resolve_air_place", {"place_ref": ref().model_dump(mode="json")}))
    assert result["status"] == status and result["iata_code"] is None
    assert not result["identity_verified"] and not result["availability_verified"]


def test_explicit_airport_to_metro_association_can_support_unique_city_identity():
    nested = city_row(latitude=None, longitude=None)
    nested.pop("type")
    result = asyncio.run(invoke(gateway(lambda _: httpx.Response(200, json={"data": [airport_row(city=nested)]})),
        "resolve_air_place", {"place_ref": ref().model_dump(mode="json")}))
    assert result["status"] == "RESOLVED" and result["provider_place_id"] == nested["id"]


class DynamicFakeTravel(FakeTravel):
    def __init__(self, *, air_bad=False, fail=False):
        super().__init__(fail=fail)
        self.air_bad = air_bad

    def offer(self, request, provider, kind):
        row = super().offer(request, provider, kind)
        row["retrieved_at"] = NOW.isoformat()
        row["expires_at"] = (NOW + timedelta(hours=1)).isoformat()
        return row

    async def resolve_hotels(self, request, limit=3):
        if isinstance(request, str):
            return await super().resolve_hotels(request, limit)
        self.calls.append(("resolve_hotels", None, deepcopy(request)))
        if self.fail:
            raise ProviderError("CONFIG_MISSING", "Fixture source unavailable")
        location = request["place_ref"]
        from otravel.place_refs import CityRefSnapshot
        identity = CityRefSnapshot.model_validate(location)
        return [{"id": "fixture-" + identity.place_id, "place_id": identity.place_id,
            "place_ref_sha256": identity.snapshot_sha256(), "identity_verified": True}]

    async def resolve_air_place(self, request):
        self.calls.append(("resolve_air_place", None, deepcopy(request)))
        if self.fail:
            raise ProviderError("CONFIG_MISSING", "Fixture source unavailable")
        from otravel.place_refs import CityRefSnapshot
        identity = CityRefSnapshot.model_validate(request["place_ref"])
        return {"status": "UNVERIFIED" if self.air_bad else "RESOLVED", "identity_verified": not self.air_bad,
            "place_id": identity.place_id, "place_ref_sha256": identity.snapshot_sha256(),
            "country_iso2": identity.country_iso2, "iata_code": "COR" if identity.country_iso2 == "AR" else "ODB"}

    async def search_hotels(self, request, provider="liteapi"):
        rows = await super().search_hotels(request, provider)
        if "place_ref" in request:
            from otravel.place_refs import CityRefSnapshot
            identity = CityRefSnapshot.model_validate(request["place_ref"])
            for row in rows:
                row["details"].update(place_id=identity.place_id, place_ref_sha256=identity.snapshot_sha256())
        return rows


def test_dynamic_orchestration_memoizes_air_by_ref_and_preserves_partial_real_components():
    state, adapter = dynamic_trip(), DynamicFakeTravel()
    # Explicit no-fallback branch retains the earlier partial-only contract.
    # Policy-enabled dynamic transfer fallback is qualified separately.
    state.search_policy.demo_fallback = False
    before = state.model_dump(mode="json")
    result = asyncio.run(TripSearch(adapter, clock=lambda: NOW).search(state))
    assert state.model_dump(mode="json") == before
    assert len([row for row in adapter.calls if row[0] == "resolve_air_place"]) == 1
    assert len([row for row in adapter.calls if row[0] == "search_flights"]) == 2
    assert result["tool_calls_used"] == len(adapter.calls) == 5
    assert result["plans"] == [] and result["effective_demo_fallback"] is False
    partial = result["partial_plan"]
    assert len(partial["offers"]) == 3 and all(row["source_type"] == "SANDBOX" for row in partial["offers"])
    assert partial["coverage"]["missing"] == ["transfer:stay-world"]
    assert not partial["ready_for_simulated_checkout"] and not result["ready_for_real_booking"]


def test_failed_world_sources_and_seed_homonym_never_receive_russian_demo_components():
    location = place(55, "Москва", "US", latitude=46.7, longitude=-117.0, timezone="America/Los_Angeles")
    state, adapter = dynamic_trip(location), DynamicFakeTravel(fail=True)
    result = asyncio.run(TripSearch(adapter, clock=lambda: NOW).search(state))
    assert not result["plans"] and not result["partial_plan"]["offers"]
    assert result["partial_plan"]["components"] == []
    assert result["effective_demo_fallback"] is False and state.stays[0].place_id == location.place_id
    demo = dynamic_trip(location, demo=True)
    idle = DynamicFakeTravel()
    blocked = asyncio.run(TripSearch(idle, clock=lambda: NOW).search(demo))
    assert idle.calls == [] and blocked["plans"] == [] and blocked["partial_plan"] is None
    assert blocked["violations"][0]["code"] == "unsupported_demo_geography"


def test_ambiguous_dynamic_air_identity_is_one_lookup_and_no_flight_search():
    adapter = DynamicFakeTravel(air_bad=True)
    result = asyncio.run(TripSearch(adapter, clock=lambda: NOW).search(dynamic_trip()))
    assert len([row for row in adapter.calls if row[0] == "resolve_air_place"]) == 1
    assert not any(row[0] == "search_flights" for row in adapter.calls)
    assert len([row for row in result["component_errors"] if row["code"] == "AIR_PLACE_UNVERIFIED"]) == 2
    assert len(result["partial_plan"]["offers"]) == 1


def test_new_air_resolution_obeys_same_explicit_tool_budget_without_hidden_calls():
    adapter = DynamicFakeTravel()
    result = asyncio.run(TripSearch(adapter, max_tool_calls=2, clock=lambda: NOW).search(dynamic_trip()))
    assert result["tool_calls_used"] == len(adapter.calls) == 2
    assert [row[0] for row in adapter.calls] == ["resolve_hotels", "search_hotels"]
    assert len(result["partial_plan"]["offers"]) == 1 and not result["plans"]



def test_two_same_name_physical_refs_have_separate_air_lookups_and_codes():
    a = place(1, "Cordoba", "AR")
    b = place(2, "Cordoba", "ES", timezone="Europe/Madrid")
    state = dynamic_trip(a)
    state.search_policy.demo_fallback = False
    state, _ = apply_operations(state, [
        Operation(type="upsert_stay", target_id="stay-world", value={"check_out": "2026-11-12"}),
        Operation(type="upsert_stay", target_id="stay-second", value={"city": b.canonical_name,
            "place_id": b.place_id, "check_in": "2026-11-12", "check_out": "2026-11-15",
            "rooms": [{"id": "room-1", "traveler_ids": ["adult-1"]}]})], resolved_places={b.place_id: b})
    adapter = DynamicFakeTravel()
    result = asyncio.run(TripSearch(adapter, clock=lambda: NOW).search(state))
    lookups = [row for row in adapter.calls if row[0] == "resolve_air_place"]
    assert len(lookups) == 2
    assert {row[2]["place_ref"]["place_id"] for row in lookups} == {a.place_id, b.place_id}
    flights = [row[2] for row in adapter.calls if row[0] == "search_flights"]
    assert flights[1]["slices"][0]["origin"] == "COR" and flights[1]["slices"][0]["destination"] == "ODB"
    assert result["tool_calls_used"] == len(adapter.calls) == 9
    assert all(row["source_type"] != "DEMO" for row in result["partial_plan"]["offers"])


def test_full_dynamic_supplier_gateway_with_five_intercepted_http_requests_is_honestly_partial():
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path == "/v3.0/data/hotels":
            return httpx.Response(200, json={"data": [hotel_row()]})
        if request.url.path == "/v3.0/hotels/rates":
            return httpx.Response(200, json=rates_body())
        if request.url.path == "/places/suggestions":
            return httpx.Response(200, json={"data": [city_row()]})
        assert request.url.path == "/air/offer_requests"
        data = json.loads(request.content)["data"]
        assert "place_refs" not in data and "geographic_source" not in data
        leg = data["slices"][0]
        segment = {"origin": {"iata_code": leg["origin"]}, "destination": {"iata_code": leg["destination"]},
            "departing_at": leg["departure_date"] + "T09:00:00", "stops": []}
        offer = {"id": "declared-flight-" + leg["departure_date"], "total_amount": "100.00", "total_currency": "RUB",
            "expires_at": (NOW + timedelta(hours=1)).isoformat(), "passengers": [{"id": "pas1", "type": "adult"}],
            "slices": [{"origin": segment["origin"], "destination": segment["destination"], "segments": [segment]}]}
        return httpx.Response(200, json={"data": {"offers": [offer]}})
    async def run():
        adapter = gateway(handler)
        try:
            state = dynamic_trip()
            state.search_policy.demo_fallback = False
            return await TripSearch(adapter, clock=lambda: NOW).search(state)
        finally:
            await adapter.close()
    result = asyncio.run(run())
    assert result["tool_calls_used"] == len(calls) == 5
    partial = result["partial_plan"]
    assert len(partial["offers"]) == 3 and all(row["source_type"] == "SANDBOX" for row in partial["offers"])
    assert partial["coverage"]["missing"] == ["transfer:stay-world"]
    assert not result["plans"] and not partial["ready_for_simulated_checkout"] and not result["ready_for_real_booking"]


def test_dynamic_flights_need_fresh_air_identity_and_actual_response_route():
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path == "/places/suggestions":
            return httpx.Response(200, json={"data": [city_row()]})
        assert request.url.path == "/air/offer_requests"
        return httpx.Response(200, json={"data": {"offers": [{"id": "bad-route", "total_amount": "1.00",
            "total_currency": "RUB", "passengers": [{"id": "p", "type": "adult"}], "slices": []}]}})
    request = {"slices": [{"origin": "KZN", "destination": "BUE", "departure_date": "2026-11-10"}],
        "currency": "RUB", "passengers": [{"type": "adult"}],
        "place_refs": {"destination": ref().model_dump(mode="json")}}
    async def run():
        adapter = gateway(handler)
        try:
            with pytest.raises(ProviderError) as error:
                await adapter.search_flights(request)
            assert error.value.code == "AIR_PLACE_UNVERIFIED" and calls == []
            await adapter.resolve_air_place({"place_ref": ref().model_dump(mode="json")})
            with pytest.raises(ProviderError) as error:
                await adapter.search_flights(request)
            assert error.value.code == "ITINERARY_UNVERIFIED"
        finally:
            await adapter.close()
    asyncio.run(run())
    assert len(calls) == 2



def test_dynamic_pinned_hotel_is_not_silently_replaced_by_first_lookup_result():
    state = dynamic_trip()
    state, _ = apply_operations(state, [Operation(type="replace_hotel", target_id="stay-world", value={"hotel_id": "previous-explicit-hotel"})])
    adapter = DynamicFakeTravel()
    result = asyncio.run(TripSearch(adapter, clock=lambda: NOW).search(state))
    assert not any(row[0] == "search_hotels" for row in adapter.calls)
    assert state.stays[0].hotel_id == "previous-explicit-hotel"
    assert not any(row.get("details", {}).get("kind") == "hotel" for row in result["partial_plan"]["offers"])
    assert any(row["code"] == "NO_MATCHES" and row["details"]["component_id"] == "stay-world" for row in result["component_errors"])


def test_persistence_fence_before_new_air_resolver_prevents_its_http_and_followup():
    adapter = DynamicFakeTravel()
    def failing_sink(event, payload):
        if event == "TOOL_CALL_STARTED" and payload.get("method") == "resolve_air_place":
            raise RuntimeError("declared persistence fence")
    async def run():
        with pytest.raises(RuntimeError, match="persistence fence"):
            await TripSearch(adapter, clock=lambda: NOW).search(dynamic_trip(), event_sink=failing_sink)
    asyncio.run(run())
    assert [row[0] for row in adapter.calls] == ["resolve_hotels", "search_hotels"]


def test_legacy_alias_equivalence_never_crosses_reference_country():
    ru = place(66, "Saint Petersburg", "RU", latitude=59.93, longitude=30.31, timezone="Europe/Moscow")
    us = place(77, "Saint Petersburg", "US", latitude=27.77, longitude=-82.64, timezone="America/New_York")
    assert place_metadata_matches(ru, country="RU", city="St. Petersburg", latitude=59.93, longitude=30.31)
    assert not place_metadata_matches(us, country="US", city="St. Petersburg", latitude=27.77, longitude=-82.64)
    assert not place_metadata_matches(us, country="RU", city="Saint Petersburg", latitude=27.77, longitude=-82.64)
