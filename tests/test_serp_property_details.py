"""Source-derived hotel detail contract; zero external HTTP or model calls."""
import json
from copy import deepcopy
from pathlib import Path

import httpx
import pytest

from otravel.providers import ProviderError, TravelGateway
from otravel.supplier_comparison import normalize_serp_hotel_details

FIXTURE = json.loads((Path(__file__).parents[1] / "fixtures/regression/serp-hotel-details-20261027.json").read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_explicit_property_token_returns_one_matching_guest_reference_and_seller_comparison():
    seen = []

    def handler(request):
        seen.append(request)
        assert request.url.host == "serpapi.com" and request.url.path == "/search.json"
        assert request.url.params["engine"] == "google_hotels"
        assert request.url.params["property_token"] == FIXTURE["request"]["property_token"]
        assert "q" not in request.url.params and request.url.params["adults"] == "1"
        return httpx.Response(200, json=FIXTURE["details_body"])

    gateway = TravelGateway({"SERPAPI_API_KEY": "test-secret", "SERPAPI_FREE_TIER_CONFIRMED": True,
        "http_transport": httpx.MockTransport(handler)})
    try:
        offers = await gateway.search_hotels(deepcopy(FIXTURE["request"]), "serpapi")
    finally:
        await gateway.close()
    assert len(seen) == len(offers) == 1
    offer = offers[0]
    assert offer["source_type"] == "LIVE_INDICATIVE" and offer["amount_minor"] == 3_980_600
    assert offer["details"]["allocation_verified"] is False and offer["details"]["availability_verified"] is False
    comparison = offer["details"]["supplier_comparison"]
    assert comparison["status"] == "NOT_EQUIVALENT_OR_UNPROVEN"
    assert comparison["requested_guests"] == 1 and comparison["seller_count"] == 12
    assert len(comparison["comparison_groups"]) == 1
    rates = comparison["comparison_groups"][0]["rates"]
    assert len(rates) == 6 and all(rate["num_guests"] == 1 for rate in rates)
    assert {rate["supplier_name"] for rate in rates} == {"Agoda", "Booking.com", "Priceline"}
    assert comparison["selected_reference_rate"]["supplier_name"] == "Agoda"
    assert "bed_configuration" in comparison["selected_reference_rate"]["missing_evidence"]
    assert comparison["equivalent"] is False and comparison["supplier_bookable"] is False


@pytest.mark.asyncio
async def test_existing_property_list_search_remains_one_request_without_automatic_details():
    seen = []

    def handler(request):
        seen.append(request)
        assert "property_token" not in request.url.params
        return httpx.Response(200, json=FIXTURE["search_body"])

    gateway = TravelGateway({"SERPAPI_API_KEY": "test-secret", "SERPAPI_FREE_TIER_CONFIRMED": True,
        "http_transport": httpx.MockTransport(handler)})
    try:
        request = {k: v for k, v in FIXTURE["request"].items() if k != "property_token"}
        offers = await gateway.search_hotels({**request, "q": "Hotels in Dubai"}, "serpapi")
    finally:
        await gateway.close()
    assert len(seen) == len(offers) == 1 and "supplier_comparison" not in offers[0]["details"]


async def fetch_details(body=None, request=None, status=200):
    seen = []

    def handler(http_request):
        seen.append(http_request)
        return httpx.Response(status, json=FIXTURE["details_body"] if body is None else body)

    gateway = TravelGateway({"SERPAPI_API_KEY": "test-secret", "SERPAPI_FREE_TIER_CONFIRMED": True,
        "http_transport": httpx.MockTransport(handler)})
    try:
        return await gateway.search_hotels(deepcopy(FIXTURE["request"] if request is None else request), "serpapi"), seen
    finally:
        await gateway.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["", "abcd", "https://example.com/p?api_key=secret", "token with space", "токен", "x" * 2049, 123, True])
async def test_invalid_property_tokens_fail_before_any_http(token):
    def forbidden(_):
        pytest.fail("Invalid request must not use a search credit")

    gateway = TravelGateway({"SERPAPI_API_KEY": "test-secret", "SERPAPI_FREE_TIER_CONFIRMED": True,
        "http_transport": httpx.MockTransport(forbidden)})
    try:
        with pytest.raises(ProviderError) as error:
            await gateway.search_hotels({**FIXTURE["request"], "property_token": token}, "serpapi")
    finally:
        await gateway.close()
    assert error.value.code == "INVALID_REQUEST"


@pytest.mark.asyncio
@pytest.mark.parametrize("occupancies", [[{"adults": 2, "children": []}], [{"adults": 1, "children": [7]}],
    [{"adults": 1}, {"adults": 1}]])
async def test_details_outside_actual_singleadult_scope_blocked_before_http(occupancies):
    def forbidden(_):
        pytest.fail("Unsupported allocation must not use a search credit")

    gateway = TravelGateway({"SERPAPI_API_KEY": "test-secret", "SERPAPI_FREE_TIER_CONFIRMED": True,
        "http_transport": httpx.MockTransport(forbidden)})
    try:
        with pytest.raises(ProviderError) as error:
            await gateway.search_hotels({**FIXTURE["request"], "occupancies": occupancies}, "serpapi")
    finally:
        await gateway.close()
    assert error.value.code == "UNSUPPORTED_REQUEST"


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("property_token", "another-property"), ("property_token", None), ("name", ""), ("type", "vacation rental")])
async def test_wrong_property_or_category_cannot_be_substituted(field, value):
    body = deepcopy(FIXTURE["details_body"])
    body[field] = value
    with pytest.raises(ProviderError) as error:
        await fetch_details(body)
    assert error.value.code == "REPRICE_IDENTITY_MISMATCH"


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("currency", "USD"), ("check_in_date", "2026-11-11"),
    ("check_out_date", "2026-11-14"), ("adults", 2), ("adults", True), ("children", 1), ("engine", "google_flights")])
async def test_changed_response_query_does_not_become_same_conditions(field, value):
    body = deepcopy(FIXTURE["details_body"])
    body["search_parameters"][field] = value
    with pytest.raises(ProviderError) as error:
        await fetch_details(body)
    assert error.value.code == "RESPONSE_QUERY_MISMATCH"


@pytest.mark.asyncio
async def test_missing_query_echo_fails_closed():
    body = deepcopy(FIXTURE["details_body"])
    del body["search_parameters"]
    with pytest.raises(ProviderError) as error:
        await fetch_details(body)
    assert error.value.code == "RESPONSE_QUERY_MISMATCH"


@pytest.mark.asyncio
@pytest.mark.parametrize("condition", ["no_rooms", "other_guest_only", "unknown_guest", "after_taxes_missing"])
async def test_seller_topline_nightly_or_capacity_cannot_replace_requested_guest_room_total(condition):
    body = deepcopy(FIXTURE["details_body"])
    for seller in body["featured_prices"]:
        if condition == "no_rooms":
            seller.pop("rooms", None)
        for room in seller.get("rooms", []):
            for rate in room.get("rates", []):
                if condition == "other_guest_only":
                    rate["num_guests"] = 2
                elif condition == "unknown_guest":
                    rate.pop("num_guests", None)
                elif condition == "after_taxes_missing":
                    rate["total_rate"].pop("extracted_after_taxes_fees", None)
    with pytest.raises(ProviderError) as error:
        await fetch_details(body)
    assert error.value.code == "NO_MATCHES"


@pytest.mark.asyncio
async def test_reprice_keeps_property_identity_and_indicative_status_with_one_explicit_refresh():
    calls = []

    def handler(request):
        calls.append(request)
        body = deepcopy(FIXTURE["details_body"])
        if len(calls) > 1:
            for rate in body["featured_prices"][0]["rooms"][0]["rates"]:
                if rate.get("num_guests") == 1:
                    rate["total_rate"]["extracted_after_taxes_fees"] = 40_000
        return httpx.Response(200, json=body)

    gateway = TravelGateway({"SERPAPI_API_KEY": "test-secret", "SERPAPI_FREE_TIER_CONFIRMED": True,
        "http_transport": httpx.MockTransport(handler)})
    try:
        first = (await gateway.search_hotels(deepcopy(FIXTURE["request"]), "serpapi"))[0]
        refreshed = await gateway.reprice(first)
    finally:
        await gateway.close()
    assert len(calls) == 2 and calls[-1].url.params["no_cache"] == "true"
    assert first["offer_id"] == refreshed["offer_id"] and first["amount_minor"] == 3_980_600
    assert refreshed["amount_minor"] == 4_000_000 and refreshed["source_type"] == "LIVE_INDICATIVE"
    assert refreshed["details"]["reprice_kind"] == "search_refresh_only"
    assert refreshed["details"]["search_request"] == FIXTURE["request"]
    assert refreshed["details"]["supplier_comparison"]["supplier_bookable"] is False
    assert refreshed["details"]["comparison_reference_changed"] is False


@pytest.mark.asyncio
async def test_property_refresh_marks_changed_reference_instead_of_claiming_same_selected_room():
    calls = []

    def handler(request):
        calls.append(request)
        body = deepcopy(FIXTURE["details_body"])
        if len(calls) > 1:
            body["featured_prices"][0]["rooms"].pop(0)
        return httpx.Response(200, json=body)

    gateway = TravelGateway({"SERPAPI_API_KEY": "test-secret", "SERPAPI_FREE_TIER_CONFIRMED": True,
        "http_transport": httpx.MockTransport(handler)})
    try:
        first = (await gateway.search_hotels(deepcopy(FIXTURE["request"]), "serpapi"))[0]
        refreshed = await gateway.reprice(first)
    finally:
        await gateway.close()
    assert len(calls) == 2 and refreshed["details"]["comparison_reference_changed"] is True
    assert refreshed["source_type"] == "LIVE_INDICATIVE" and refreshed["details"]["availability_verified"] is False


def test_disclosed_meal_beds_cancellation_and_fee_scope_are_facts_with_missingness():
    comparison = normalize_serp_hotel_details(FIXTURE["details_body"], FIXTURE["request"])
    rates = comparison["comparison_groups"][0]["rates"]
    agoda = next(r for r in rates if r["supplier_name"] == "Agoda" and r["room_name"] == "Superior")
    priceline = next(r for r in rates if r["supplier_name"] == "Priceline")
    assert agoda["breakfast_included"] is True and priceline["breakfast_included"] is True
    assert agoda["beds"] == [] and priceline["beds"] == [{"type": "Double", "count": 1}]
    assert agoda["free_cancellation"] is True and agoda["cancellation_until_date_text"] == "Nov 2"
    assert agoda["cancellation_deadline_verified"] is False
    assert "cancellation_deadline_year_timezone" in agoda["missing_evidence"]
    assert all(r["equivalent"] is False and "final_mandatory_fees" in r["missing_evidence"] for r in rates)
    assert comparison["excluded"]["other_guest_count"] == 51


def test_comparison_output_is_bounded_and_drops_supplier_links_images_and_tokens():
    body = deepcopy(FIXTURE["details_body"])
    rate = next(r for r in body["featured_prices"][0]["rooms"][0]["rates"] if r.get("num_guests") == 1)
    body["featured_prices"][0]["rooms"][0]["rates"] = [rate] * 20
    body["featured_prices"] = [body["featured_prices"][0]] * 20
    comparison = normalize_serp_hotel_details(body, FIXTURE["request"])
    assert len(comparison["comparison_groups"][0]["rates"]) == 64
    assert comparison["excluded"]["matching_rate_limit_reached"] is True
    rendered = json.dumps(comparison)
    assert '"link"' not in rendered and '"images"' not in rendered and "api_key" not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [(401, "HTTP_ERROR"), (403, "HTTP_ERROR"), (429, "RATE_LIMITED"), (503, "HTTP_ERROR")])
async def test_auth_rate_and_server_errors_stay_errors_without_retry(status, code):
    with pytest.raises(ProviderError) as error:
        await fetch_details({"error": "upstream test-secret"}, status=status)
    assert error.value.code == code and "test-secret" not in str(error.value)
