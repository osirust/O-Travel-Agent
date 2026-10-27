"""Provider contract tests: HTTP is intercepted; no keys/accounts/quota needed."""
import asyncio
import json
import logging
import base64
import hashlib
from datetime import datetime, timezone

import httpx
import pytest

from otravel.providers import LLMGateway, ProviderError, TravelGateway


RESULT = {"operations": [{"type": "set_budget", "value": {"amount_minor": 7000000, "currency": "RUB", "scope": "whole_trip"}}],
          "control": {"action": "continue"}, "reply": "Бюджет сохранён.", "unknowns": [], "conflicts": []}
HOTEL_REQUEST = {"hotelIds": ["hotel1"], "checkin": "2026-11-10", "checkout": "2026-11-13",
                 "currency": "RUB", "guestNationality": "RU",
                 "occupancies": [{"adults": 2, "children": [6]}, {"adults": 1, "children": []}]}
FLIGHT_REQUEST = {"slices": [{"origin": "KZN", "destination": "IST", "departure_date": "2026-11-10"},
                              {"origin": "IST", "destination": "AYT", "departure_date": "2026-11-13"}],
                  "passengers": [{"type": "adult"}, {"age": 6}]}


def call(gateway, method, *args, **kwargs):
    async def run():
        try:
            return await getattr(gateway, method)(*args, **kwargs)
        finally:
            await gateway.close()
    return asyncio.run(run())


def settings(handler, **kwargs):
    return {"http_transport": httpx.MockTransport(handler), **kwargs}


def forbidden(request):
    pytest.fail("Unexpected outbound HTTP attempt")


def test_sandbox_lookup_normalizes_supported_city_and_keeps_request_small():
    def handler(request):
        assert request.url.path == "/v3.0/data/hotels"
        assert request.url.params["countryCode"] == "TR"
        assert request.url.params["cityName"] == "Istanbul"
        assert request.url.params["limit"] == "3"
        return httpx.Response(200, json={"data": [{"id": "lp-test", "name": "Synthetic hotel"}]})
    gateway = TravelGateway(settings(handler, LITEAPI_API_KEY="sand_synthetic"))
    rows = call(gateway, "resolve_hotels", "Стамбул")
    assert rows == [{"id": "lp-test", "name": "Synthetic hotel", "city": "Стамбул"}]


def test_lookup_rejects_production_key_before_http():
    gateway = TravelGateway(settings(forbidden, LITEAPI_API_KEY="prod_synthetic"))
    with pytest.raises(ProviderError) as error:
        call(gateway, "resolve_hotels", "Стамбул")
    assert error.value.code == "CONFIG_BLOCKED"


def test_liteapi_unauthorized_response_explains_access_without_exposing_key():
    gateway = TravelGateway(settings(lambda request: httpx.Response(401), LITEAPI_API_KEY="sand_private_test_value"))
    with pytest.raises(ProviderError) as error:
        call(gateway, "resolve_hotels", "Стамбул")
    assert error.value.code == "HTTP_ERROR"
    assert error.value.details["http_status"] == 401
    assert "LiteAPI" in error.value.message and "доступ" in error.value.message
    assert "sand_private_test_value" not in str(error.value.as_dict())


@pytest.mark.parametrize("config,code", [
    ({"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "fake"}, "CONFIG_BLOCKED"),
    ({"LLM_PROVIDER": "gemini", "GEMINI_FREE_TIER_CONFIRMED": True, "GEMINI_API_KEY": ""}, "CONFIG_MISSING"),
    ({"LLM_PROVIDER": "openrouter", "OPENROUTER_MODEL": "google/gemma-paid", "OPENROUTER_API_KEY": "fake"}, "CONFIG_BLOCKED"),
])
def test_llm_configuration_blocks_before_http(config, code, monkeypatch):
    monkeypatch.delenv("GEMINI_FREE_TIER_CONFIRMED", raising=False)
    gateway = LLMGateway(settings(forbidden, **config))
    with pytest.raises(ProviderError) as error:
        call(gateway, "extract_operations", {"message": "Бюджет 70 тысяч"})
    assert error.value.code == code


def test_gemini_native_json_schema_and_no_sampling_or_query_key():
    def handler(request):
        assert request.url.path.endswith("/gemini-3.8-flash:generateContent")
        assert "key" not in request.url.params
        assert request.headers["x-goog-api-key"] == "gemini-test-secret"
        body = json.loads(request.content)
        config = body["generationConfig"]
        assert config["responseMimeType"] == "application/json"
        assert config["responseJsonSchema"]["properties"]["control"]
        assert config["maxOutputTokens"] <= 4096
        assert not ({"temperature", "topP", "topK", "top_p"} & set(config))
        return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(RESULT)}]}}]})
    gateway = LLMGateway(settings(handler, GEMINI_API_KEY="gemini-test-secret", GEMINI_FREE_TIER_CONFIRMED=True))
    assert call(gateway, "extract_operations", {"message": "Бюджет 70000"}) == RESULT


def test_openrouter_zero_price_no_paid_fallback():
    def handler(request):
        body = json.loads(request.content)
        assert body["model"] == "google/gemma-4-31b-it:free"
        assert "models" not in body
        assert body["provider"]["allow_fallbacks"] is False
        assert body["provider"]["max_price"] == {"prompt": 0, "completion": 0, "request": 0}
        assert body["response_format"] == {"type": "json_object"}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(RESULT)}}]})
    gateway = LLMGateway(settings(handler, llm_provider="openrouter", openrouter_model="google/gemma-4-31b-it:free", openrouter_api_key="router-secret"))
    assert call(gateway, "extract_operations", {"message": "Бюджет 70000"}) == RESULT


@pytest.mark.parametrize("content", ["not json", "[]", '{"operations":[]}', json.dumps({**RESULT, "control": {"action": "buy_now"}})])
def test_invalid_llm_response_is_structured_error(content):
    handler = lambda request: httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    gateway = LLMGateway(settings(handler, LLM_PROVIDER="openrouter", OPENROUTER_API_KEY="fake"))
    with pytest.raises(ProviderError) as error:
        call(gateway, "extract_operations", {})
    assert error.value.code == "INVALID_LLM_RESPONSE"


@pytest.mark.parametrize("metadata,valid", [
    ({"speech_act": "ASK", "information_subject": "Сербия"}, True),
    ({"speech_act": "PROPOSE", "information_subject": None}, True),
    ({"speech_act": "BOOK_NOW"}, False),
    ({"speech_act": []}, False),
    ({"information_subject": 42}, False),
    ({"information_subject": "x" * 81}, False),
    ({"unapproved_action": "buy"}, False),
])
def test_provider_accepts_bounded_speech_metadata_without_relaxing_unknown_fields(metadata, valid):
    payload = {**RESULT, **metadata}
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload)}}]})
    gateway = LLMGateway(settings(handler, LLM_PROVIDER="openrouter", OPENROUTER_API_KEY="fake"))
    if valid:
        assert call(gateway, "extract_operations", {}) == payload
    else:
        with pytest.raises(ProviderError) as error:
            call(gateway, "extract_operations", {})
        assert error.value.code == "INVALID_LLM_RESPONSE"


@pytest.mark.parametrize("provider,method,case_request,config", [
    ("liteapi", "search_hotels", HOTEL_REQUEST, {"LITEAPI_API_KEY": ""}),
    ("duffel", "search_flights", FLIGHT_REQUEST, {"DUFFEL_ACCESS_TOKEN": "duffel_live_fake"}),
    ("serpapi", "search_hotels", HOTEL_REQUEST, {"SERPAPI_API_KEY": "fake", "SERPAPI_FREE_TIER_CONFIRMED": False}),
])
def test_missing_key_or_forbidden_modes_never_call_http(provider, method, case_request, config):
    gateway = TravelGateway(settings(forbidden, **config))
    with pytest.raises(ProviderError) as error:
        call(gateway, method, case_request, provider=provider)
    assert error.value.code in {"CONFIG_MISSING", "CONFIG_BLOCKED"}


@pytest.mark.parametrize("sandbox_key", ["sand_lite-secret", "sandbox_lite-secret"])
def test_liteapi_multiroom_total_and_sandbox_provenance(sandbox_key):
    def handler(request):
        body = json.loads(request.content)
        assert body["occupancies"] == HOTEL_REQUEST["occupancies"]
        assert request.headers["X-API-Key"] == sandbox_key
        return httpx.Response(200, json={"data": [{"hotelId": "hotel1", "name": "Fixture Hotel", "roomTypes": [
            {"offerId": "native-offer1", "offerRetailRate": {"amount": "12000.50", "currency": "RUB"},
             "rates": [{"rateId": "roomA"}, {"rateId": "roomB"}]}]}]})
    gateway = TravelGateway(settings(handler, LITEAPI_API_KEY=sandbox_key))
    offer = call(gateway, "search_hotels", HOTEL_REQUEST)[0]
    assert offer["amount_minor"] == 1200050
    assert offer["source_type"] == "SANDBOX"
    assert offer["price_kind"] == "PROVIDER_ROOMS_STAY_TOTAL"
    assert offer["details"]["availability_verified"] is False
    assert len(offer["details"]["occupancies"]) == 2


@pytest.mark.parametrize("change", [{"hotelIds": []}, {"checkout": "2026-11-01"},
                                    {"occupancies": [{"adults": 2, "children": [-1]}]}, {"guestNationality": ""}])
def test_liteapi_rejects_incomplete_inputs_before_http(change):
    gateway = TravelGateway(settings(forbidden, LITEAPI_API_KEY="sand_fake"))
    with pytest.raises(ProviderError) as error:
        call(gateway, "search_hotels", {**HOTEL_REQUEST, **change})
    assert error.value.code == "INVALID_REQUEST"


def test_liteapi_does_not_sum_alternative_room_rates():
    handler = lambda request: httpx.Response(200, json={"data": [{"hotelId": "hotel1", "roomTypes": [
        {"offerId": "offer1", "rates": [{"retailRate": {"total": [{"amount": 10, "currency": "RUB"}]}},
                                          {"retailRate": {"total": [{"amount": 20, "currency": "RUB"}]}}]}]}]})
    gateway = TravelGateway(settings(handler, LITEAPI_API_KEY="sand_fake"))
    with pytest.raises(ProviderError) as error:
        call(gateway, "search_hotels", HOTEL_REQUEST)
    assert error.value.code == "INVALID_RESPONSE"


def test_liteapi_attaches_only_reported_multiroom_and_fee_evidence():
    def handler(request):
        rooms = json.loads(request.content)["occupancies"]
        rates = [{"rateId": f"rate-{i}", "occupancyNumber": i + 1, "adultCount": room["adults"],
                  "childCount": len(room["children"]), "childrenAges": room["children"],
                  "maxOccupancy": room["adults"] + len(room["children"]), "boardType": "BI",
                  "retailRate": {"taxesAndFees": None}} for i, room in enumerate(rooms)]
        return httpx.Response(200, json={"data": [{"hotelId": "lp-fixture", "roomTypes": [{
            "offerId": "native-fixture", "supplierId": "supplier-a", "supplier": "Synthetic supplier",
            "offerRetailRate": {"amount": "100.00", "currency": "RUB"}, "rates": rates}]}]})
    gateway = TravelGateway(settings(handler, LITEAPI_API_KEY="sand_synthetic"))
    details = call(gateway, "search_hotels", HOTEL_REQUEST)[0]["details"]
    assert details["allocation_verified"] is True
    assert details["mandatory_fees_known"] is True
    assert details["constraint_evidence"]["breakfast"] is True
    assert details["availability_verified"] is False
    assert details["supplier_name"] == "Synthetic supplier"
    assert details["supplier_verified"] is False


@pytest.mark.parametrize("key", ["prod_fake-key", "unknown-key", "SANDBOX_fake-key"])
@pytest.mark.parametrize("method", ["search_hotels", "reprice"])
def test_liteapi_production_and_unknown_keys_block_search_and_reprice_before_http(key, method):
    gateway = TravelGateway(settings(forbidden, LITEAPI_API_KEY=key, LITEAPI_MODE="sandbox"))
    argument = HOTEL_REQUEST if method == "search_hotels" else {
        "provider": "liteapi", "details": {"native_offer_id": "offer1", "search_request": HOTEL_REQUEST}}
    with pytest.raises(ProviderError) as error:
        call(gateway, method, argument)
    assert error.value.code == "CONFIG_BLOCKED"


def test_liteapi_sandbox_key_does_not_allow_live_mode():
    gateway = TravelGateway(settings(forbidden, LITEAPI_API_KEY="sand_fake", LITEAPI_MODE="live"))
    with pytest.raises(ProviderError) as error:
        call(gateway, "search_hotels", HOTEL_REQUEST)
    assert error.value.code == "CONFIG_BLOCKED"


def test_duffel_multicity_group_search_only_test_and_no_order():
    def handler(request):
        assert request.url.path == "/air/offer_requests"
        data = json.loads(request.content)["data"]
        assert len(data["slices"]) == 2
        assert data["passengers"] == [{"type": "adult"}, {"age": 6}]
        assert request.headers["Duffel-Version"] == "v2"
        return httpx.Response(200, json={"data": {"offers": [{"id": "off_test1", "total_amount": "100.50",
            "total_currency": "USD", "slices": [], "expires_at": "2026-11-01T00:00:00Z"}]}})
    gateway = TravelGateway(settings(handler, DUFFEL_ACCESS_TOKEN="duffel_test_fake"))
    offer = call(gateway, "search_flights", FLIGHT_REQUEST)[0]
    assert offer["amount_minor"] == 10050
    assert offer["source_type"] == "SANDBOX"
    # The response omits passengers. Request quantity remains diagnostic only;
    # it must not be promoted to a verified group size or priced group quote.
    assert offer["details"]["passengers_count"] is None
    assert offer["details"]["requested_passengers_count"] == 2
    assert offer["details"]["group_quote"] is False
    assert offer["details"]["passenger_evidence"]["status"] == "unknown"


def test_serpapi_indicative_stable_ids_not_nightly_as_trip_total():
    def handler(request):
        assert request.url.params["engine"] == "google_hotels"
        return httpx.Response(200, json={"search_metadata": {"status": "Success"}, "properties": [
            {"name": "Hotel", "property_token": "property1", "rate_per_night": {"extracted_lowest": 1500.5}}]})
    request = {**HOTEL_REQUEST, "city": "Казань", "occupancies": [{"adults": 2, "children": []}]}
    async def run():
        gateway = TravelGateway(settings(handler, SERPAPI_API_KEY="serp-secret", SERPAPI_FREE_TIER_CONFIRMED=True))
        try:
            a = (await gateway.search_hotels(request, "serpapi"))[0]
            b = (await gateway.search_hotels(request, "serpapi"))[0]
            assert a["offer_id"] == b["offer_id"]
            assert a["price_kind"] == "INDICATIVE_NIGHTLY"
            assert a["source_type"] == "LIVE_INDICATIVE"
            assert a["amount_minor"] == 150050
            assert a["details"]["availability_verified"] is False
        finally:
            await gateway.close()
    asyncio.run(run())


def test_serp_hotel_presentation_keeps_only_trusted_images_and_real_rating():
    def handler(request):
        return httpx.Response(200, json={"properties": [{"name": "Hotel", "property_token": "property1", "overall_rating": 4.7, "reviews": 321,
            "images": [{"thumbnail": "https://lh3.googleusercontent.com/gps-cs-s/abc=s287"},
                       {"thumbnail": "https://lh3.googleusercontent.com.evil.test/abc"},
                       {"thumbnail": "https://lh3.googleusercontent.com/abc?api_key=bad"}],
            "total_rate": {"extracted_lowest": 1500}}]})
    request = {**HOTEL_REQUEST, "city": "Казань", "occupancies": [{"adults": 2, "children": []}]}
    gateway = TravelGateway(settings(handler, SERPAPI_API_KEY="fake", SERPAPI_FREE_TIER_CONFIRMED=True))
    presentation = call(gateway, "search_hotels", request, "serpapi")[0]["details"]["provider_data"]
    assert presentation["photos"] == ["https://lh3.googleusercontent.com/gps-cs-s/abc=s287"]
    assert presentation["rating"] == 4.7
    assert presentation["review_count"] == 321


def test_serp_hotel_reviews_are_bounded_and_never_return_private_urls():
    def handler(request):
        assert request.url.params["engine"] == "google_hotels_reviews"
        assert request.url.params["property_token"] == "property1"
        return httpx.Response(200, json={"reviews": [{"user": {"name": "Гость", "link": "https://private.example"},
            "snippet": "Удобно", "rating": 5, "best_rating": 5, "date": "2026-01-01", "source": "Google"}]})
    gateway = TravelGateway(settings(handler, SERPAPI_API_KEY="fake", SERPAPI_FREE_TIER_CONFIRMED=True))
    reviews = call(gateway, "hotel_reviews", "property1")
    assert reviews[0]["text"] == "Удобно"
    assert reviews[0]["rating"] == 5
    assert "link" not in json.dumps(reviews)


def test_serpapi_multicity_params_and_indicative_segments():
    def handler(request):
        assert request.url.params["type"] == "3"
        assert len(json.loads(request.url.params["multi_city_json"])) == 2
        return httpx.Response(200, json={"best_flights": [{"price": 12345, "flights": [
            {"departure_airport": {"id": "KZN"}, "arrival_airport": {"id": "IST"}}]}]})
    gateway = TravelGateway(settings(handler, SERPAPI_API_KEY="fake", SERPAPI_FREE_TIER_CONFIRMED=True))
    offer = call(gateway, "search_flights", {**FLIGHT_REQUEST, "adults": 1, "children": 1, "currency": "RUB"}, "serpapi")[0]
    assert offer["source_type"] == "LIVE_INDICATIVE"
    assert offer["details"]["itinerary_complete"] is False


@pytest.mark.parametrize("message,success", [
    ("Google Flights hasn't returned any results for this query.", True),
    ("Account quota exhausted", False),
])
def test_serpapi_distinguishes_empty_flights_from_provider_failure(message, success):
    def handler(request):
        return httpx.Response(200, json={"search_metadata": {"status": "Success"}, "error": message})
    gateway = TravelGateway(settings(handler, SERPAPI_API_KEY="fake", SERPAPI_FREE_TIER_CONFIRMED=True))
    request = {**FLIGHT_REQUEST, "adults": 1, "currency": "RUB"}
    if success:
        assert call(gateway, "search_flights", request, "serpapi") == []
    else:
        with pytest.raises(ProviderError) as error:
            call(gateway, "search_flights", request, "serpapi")
        assert error.value.code == "PROVIDER_RESPONSE_ERROR"


def test_serpapi_hotel_pagination_forwards_token_and_preserves_offer_identity():
    calls = []
    def handler(request):
        token = request.url.params.get('next_page_token')
        calls.append(token)
        pagination = {'next_page_token': 'page-two'} if token is None else {}
        return httpx.Response(200, json={'properties': [{'name': 'Hotel', 'property_token': 'hotel-one', 'total_rate': {'extracted_lowest': 1000}}], 'serpapi_pagination': pagination})
    request = {**HOTEL_REQUEST, 'city': 'Казань', 'occupancies': [{'adults': 2, 'children': []}]}
    async def run():
        gateway = TravelGateway(settings(handler, SERPAPI_API_KEY='fake', SERPAPI_FREE_TIER_CONFIRMED=True))
        try:
            first, next_token = await gateway.search_hotel_page(request)
            second, after = await gateway.search_hotel_page({**request, 'next_page_token': next_token})
            assert calls == [None, 'page-two']
            assert first[0]['offer_id'] == second[0]['offer_id']
            assert after is None
        finally:
            await gateway.close()
    asyncio.run(run())


def test_serpapi_rejects_invalid_hotel_page_token_before_http():
    gateway = TravelGateway(settings(forbidden, SERPAPI_API_KEY='fake', SERPAPI_FREE_TIER_CONFIRMED=True))
    request = {**HOTEL_REQUEST, 'city': 'Казань', 'occupancies': [{'adults': 2, 'children': []}], 'next_page_token': 'bad&key=value'}
    with pytest.raises(ProviderError):
        call(gateway, 'search_hotel_page', request)


@pytest.mark.parametrize("cabin,expected", [("economy", "1"), ("premium_economy", "2"), ("business", "3"), ("first", "4")])
def test_serpapi_forwards_selected_cabin(cabin, expected):
    def handler(request):
        assert request.url.params["travel_class"] == expected
        return httpx.Response(200, json={"best_flights": []})
    gateway = TravelGateway(settings(handler, SERPAPI_API_KEY="fake", SERPAPI_FREE_TIER_CONFIRMED=True))
    assert call(gateway, "search_flights", {**FLIGHT_REQUEST, "adults": 1, "currency": "RUB", "cabin_class": cabin}, "serpapi") == []


def test_serpapi_rejects_invalid_cabin_before_request():
    gateway = TravelGateway(settings(forbidden, SERPAPI_API_KEY="fake", SERPAPI_FREE_TIER_CONFIRMED=True))
    with pytest.raises(ProviderError):
        call(gateway, "search_flights", {**FLIGHT_REQUEST, "adults": 1, "currency": "RUB", "cabin_class": "invalid"}, "serpapi")


def test_serpapi_errors_and_http_logs_redact_key(caplog):
    secret = "do-not-log-this-serp-key"
    def handler(request):
        return httpx.Response(429, json={"error": f"bad key {secret}", "url": str(request.url)})
    gateway = TravelGateway(settings(handler, SERPAPI_API_KEY=secret, SERPAPI_FREE_TIER_CONFIRMED=True))
    request = {**HOTEL_REQUEST, "city": "Казань", "occupancies": [{"adults": 2}]}
    with caplog.at_level(logging.INFO, logger="httpx"), pytest.raises(ProviderError) as error:
        call(gateway, "search_hotels", request, "serpapi")
    assert error.value.retryable is True
    assert error.value.code == "RATE_LIMITED"
    assert secret not in caplog.text
    assert secret not in json.dumps(error.value.as_dict())
    assert "[REDACTED]" in caplog.text


def test_timeout_does_not_echo_request_url_or_retry():
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout(str(request.url), request=request)
    gateway = TravelGateway(settings(handler, SERPAPI_API_KEY="timeout-secret", SERPAPI_FREE_TIER_CONFIRMED=True))
    request = {**HOTEL_REQUEST, "city": "Казань", "occupancies": [{"adults": 2}]}
    with pytest.raises(ProviderError) as error:
        call(gateway, "search_hotels", request, "serpapi")
    assert error.value.code == "REQUEST_TIMEOUT"
    assert len(calls) == 1
    assert "timeout-secret" not in str(error.value)


def test_serp_refresh_remains_indicative_not_supplier_reprice():
    def handler(request):
        price = 120 if request.url.params["no_cache"] == "true" else 100
        return httpx.Response(200, json={"properties": [{"name": "Hotel", "property_token": "p1", "total_rate": {"extracted_lowest": price}}]})
    async def run():
        gateway = TravelGateway(settings(handler, SERPAPI_API_KEY="fake", SERPAPI_FREE_TIER_CONFIRMED=True))
        try:
            request = {**HOTEL_REQUEST, "city": "Казань", "occupancies": [{"adults": 2}]}
            first = (await gateway.search_hotels(request, "serpapi"))[0]
            refreshed = await gateway.reprice(first)
            assert refreshed["offer_id"] == first["offer_id"]
            assert refreshed["amount_minor"] == 12000
            assert refreshed["source_type"] == "LIVE_INDICATIVE"
            assert refreshed["details"]["availability_verified"] is False
        finally:
            await gateway.close()
    asyncio.run(run())


def test_weather_no_key_bounded_forecast():
    def handler(request):
        assert "api_key" not in request.url.params
        assert request.url.params["forecast_days"] == "7"
        return httpx.Response(200, json={"current": {"temperature_2m": 12}, "daily": {"time": ["2026-10-25"]}, "timezone": "Europe/Moscow"})
    gateway = TravelGateway(settings(handler))
    result = call(gateway, "weather", "Казань", 55.8, 49.1)
    assert result["kind"] == "FORECAST"
    assert "CC BY 4.0" in result["attribution"]


def test_weather_invalid_coordinates_no_call():
    gateway = TravelGateway(settings(forbidden))
    with pytest.raises(ProviderError):
        call(gateway, "weather", "Казань", float("nan"), 49.1)


@pytest.mark.parametrize("provider,config", [
    ("hotelbeds", {"HOTELBEDS_API_KEY": "fake-key", "HOTELBEDS_API_SECRET": "fake-secret"}),
    ("ratehawk", {"RATEHAWK_KEY_ID": "fake-id", "RATEHAWK_API_KEY": "fake-key"}),
])
def test_optional_hotel_adapters_blocked_without_test_confirmation(provider, config, monkeypatch):
    monkeypatch.delenv("HOTELBEDS_TEST_CONFIRMED", raising=False)
    monkeypatch.delenv("RATEHAWK_TEST_CONFIRMED", raising=False)
    gateway = TravelGateway(settings(forbidden, **config))
    with pytest.raises(ProviderError) as error:
        call(gateway, "search_hotels", HOTEL_REQUEST, provider)
    assert error.value.code == "CONFIG_BLOCKED"


def test_hotelbeds_uses_test_host_sha256_signature_and_exact_child_ages():
    now = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
    secret, key = "hbx-secret", "hbx-key"
    signature = hashlib.sha256(f"{key}{secret}{int(now.timestamp())}".encode()).hexdigest()
    def handler(request):
        assert request.url.host == "api.test.hotelbeds.com"
        assert request.url.path == "/hotel-api/1.0/hotels"
        assert request.headers["Api-key"] == key
        assert request.headers["X-Signature"] == signature
        body = json.loads(request.content)
        assert body["occupancies"] == [{"rooms": 1, "adults": 2, "children": 1, "paxes": [{"type": "CH", "age": 6}]},
                                        {"rooms": 1, "adults": 1, "children": 0}]
        assert body["hotels"] == {"hotel": [3424]}
        return httpx.Response(200, json={"hotels": {"hotels": [{"code": 3424, "name": "Test Hotel", "currency": "USD",
            "rooms": [{"code": "DBL.ST", "rates": [{"rateKey": "test-rate-key", "rateType": "BOOKABLE", "net": "118.27", "rooms": 1}]}]}]}})
    gateway = TravelGateway(settings(handler, HOTELBEDS_TEST_CONFIRMED=True, HOTELBEDS_API_KEY=key,
                                      HOTELBEDS_API_SECRET=secret, clock=lambda: now))
    offer = call(gateway, "search_hotels", {**HOTEL_REQUEST, "hotel_ids": [3424]}, "hotelbeds")[0]
    assert offer["amount_minor"] == 11827
    assert offer["source_type"] == "SANDBOX"
    assert offer["price_kind"] == "PROVIDER_NET_RATE"
    assert offer["details"]["availability_verified"] is False
    assert offer["details"]["allocation_verified"] is False


def test_hotelbeds_invalid_ids_no_request():
    gateway = TravelGateway(settings(forbidden, HOTELBEDS_TEST_CONFIRMED=True,
                                      HOTELBEDS_API_KEY="key", HOTELBEDS_API_SECRET="secret"))
    with pytest.raises(ProviderError) as error:
        call(gateway, "search_hotels", HOTEL_REQUEST, "hotelbeds")
    assert error.value.code == "INVALID_REQUEST"


def test_ratehawk_sandbox_basic_auth_and_display_currency_not_contract_amount():
    key_id, api_key = "key-id", "ratehawk-secret"
    auth = "Basic " + base64.b64encode(f"{key_id}:{api_key}".encode()).decode()
    def handler(request):
        assert request.url.host == "api-sandbox.ratehawk.com"
        assert request.url.path == "/api/b2b/v3/search/serp/hotels/"
        assert request.headers["Authorization"] == auth
        assert not request.url.params
        body = json.loads(request.content)
        assert body["guests"] == HOTEL_REQUEST["occupancies"]
        assert body["currency"] == "USD"
        assert body["hids"] == [10004873]
        return httpx.Response(200, json={"status": "ok", "data": {"hotels": [{"hid": 10004873,
            "rates": [{"match_hash": "m-test", "room_name": "Sandbox room", "payment_options": {"payment_types": [
                {"amount": "960.00", "currency_code": "EUR", "show_amount": "1121.00", "show_currency_code": "USD", "type": "now", "by": "credit_card",
                 "tax_data": {"taxes": [{"included_by_supplier": False, "amount": "40.00", "currency_code": "AED"}]}}]}}]}]}})
    gateway = TravelGateway(settings(handler, RATEHAWK_TEST_CONFIRMED=True, RATEHAWK_KEY_ID=key_id, RATEHAWK_API_KEY=api_key))
    offer = call(gateway, "search_hotels", {**HOTEL_REQUEST, "hids": [10004873], "residency": "gb", "currency": "USD"}, "ratehawk")[0]
    assert offer["amount_minor"] == 112100
    assert offer["currency"] == "USD"
    assert offer["source_type"] == "SANDBOX"
    assert offer["details"]["payment"]["tax_data"]["taxes"][0]["currency_code"] == "AED"
    assert offer["details"]["allocation_verified"] is False


def test_ratehawk_wrong_currency_and_missing_children_ages_not_silently_changed():
    gateway = TravelGateway(settings(forbidden, RATEHAWK_TEST_CONFIRMED=True, RATEHAWK_KEY_ID="id", RATEHAWK_API_KEY="secret"))
    with pytest.raises(ProviderError) as error:
        call(gateway, "search_hotels", {**HOTEL_REQUEST, "hids": [1], "residency": "gb"}, "ratehawk")
    assert error.value.code == "UNSUPPORTED_REQUEST"


def test_travelpayouts_header_token_cached_reference_not_live_group_quote():
    now = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
    def handler(request):
        assert request.url.host == "api.travelpayouts.com"
        assert request.url.path == "/aviasales/v3/prices_for_dates"
        assert request.headers["X-Access-Token"] == "travelpayouts-secret"
        assert "token" not in request.url.params
        assert request.url.params["one_way"] == "true"
        return httpx.Response(200, json={"success": True, "currency": "rub", "data": [
            {"origin": "KZN", "destination": "IST", "departure_at": "2026-11-10T07:00:00+03:00",
             "price": 19000, "airline": "TK", "flight_number": "427", "transfers": 0}]})
    gateway = TravelGateway(settings(handler, TRAVELPAYOUTS_API_TOKEN="travelpayouts-secret", clock=lambda: now))
    request = {**FLIGHT_REQUEST, "slices": FLIGHT_REQUEST["slices"][:1], "currency": "RUB"}
    offer = call(gateway, "search_flights", request, "travelpayouts")[0]
    assert offer["amount_minor"] == 1900000  # Never multiplied by party size.
    assert offer["price_kind"] == "CACHED_REFERENCE_FARE"
    assert offer["source_type"] == "LIVE_INDICATIVE"
    assert offer["details"]["cached"] is True
    assert offer["details"]["group_quote"] is False
    assert offer["details"]["as_of"] is None
    assert offer["expires_at"] == now.isoformat()  # No invented freshness.


def test_travelpayouts_multicity_unsupported_before_call():
    gateway = TravelGateway(settings(forbidden, TRAVELPAYOUTS_API_TOKEN="fake"))
    with pytest.raises(ProviderError) as error:
        call(gateway, "search_flights", {**FLIGHT_REQUEST, "currency": "RUB"}, "travelpayouts")
    assert error.value.code == "UNSUPPORTED_REQUEST"


@pytest.mark.parametrize("provider", ["hotelbeds", "ratehawk", "travelpayouts"])
def test_optional_readonly_adapters_do_not_pretend_to_reprice(provider):
    gateway = TravelGateway(settings(forbidden))
    with pytest.raises(ProviderError) as error:
        call(gateway, "reprice", {"provider": provider, "source_type": "SANDBOX", "details": {}})
    assert error.value.code == "UNSUPPORTED_REPRICE"


@pytest.mark.parametrize("provider,method,case_request,config", [
    ("hotelbeds", "search_hotels", {**HOTEL_REQUEST, "hotel_ids": [1]}, {"HOTELBEDS_TEST_CONFIRMED": True, "HOTELBEDS_API_KEY": "", "HOTELBEDS_API_SECRET": ""}),
    ("ratehawk", "search_hotels", {**HOTEL_REQUEST, "hids": [1], "residency": "gb", "currency": "USD"}, {"RATEHAWK_TEST_CONFIRMED": True, "RATEHAWK_KEY_ID": "", "RATEHAWK_API_KEY": ""}),
    ("travelpayouts", "search_flights", {**FLIGHT_REQUEST, "slices": FLIGHT_REQUEST["slices"][:1], "currency": "RUB"}, {"TRAVELPAYOUTS_API_TOKEN": ""}),
])
def test_optional_missing_keys_never_make_http_calls(provider, method, case_request, config):
    gateway = TravelGateway(settings(forbidden, **config))
    with pytest.raises(ProviderError) as error:
        call(gateway, method, case_request, provider)
    assert error.value.code == "CONFIG_MISSING"


def test_error_details_drop_provider_environment_secrets_without_reading_env():
    error = ProviderError("CONFIG_MISSING", "Не настроено", details={
        "HOTELBEDS_API_SECRET": "hbx-private", "RATEHAWK_API_KEY": "rh-private",
        "DUFFEL_ACCESS_TOKEN": "duffel-private", "nested": {"X-API-Key": "header-private"},
        "provider": "hotelbeds"})
    encoded = json.dumps(error.as_dict())
    assert "private" not in encoded
    assert error.details == {"nested": {}, "provider": "hotelbeds"}


@pytest.mark.parametrize("raw_label,expected", [
    ("upstream_provider_shared_pool", "upstream_provider_shared_pool"),
    ("account_quota", "account_quota"),
    ("key_rate_limit", "key_rate_limit"),
    ("workspace_credit_limit", "workspace_credit_limit"),
    ("exhausted_global_quota", "exhausted_global_quota"),
    ("new-provider-id-quota-label", "unknown"),
    ("", "unknown"),
])
def test_short_credentials_do_not_corrupt_allowlisted_limit_policy_categories(raw_label, expected):
    # Exercise the same cross-provider registration that exposed the suite bug.
    ratehawk = TravelGateway(settings(forbidden, RATEHAWK_TEST_CONFIRMED=True,
                                       RATEHAWK_KEY_ID="id", RATEHAWK_API_KEY="private-test-rh-credential"))
    with pytest.raises(ProviderError):
        call(ratehawk, "search_hotels", {}, "ratehawk")
    secret = "private-test-router-credential"
    handler = lambda request: httpx.Response(429, json={"error": {"message": secret, "metadata": {
        "limit_source": raw_label, "provider_name": "display id", "raw": secret}}})
    gateway = LLMGateway(settings(handler, LLM_PROVIDER="openrouter", OPENROUTER_API_KEY=secret))
    with pytest.raises(ProviderError) as error:
        call(gateway, "extract_operations", {})
    assert error.value.details["limit_source"] == expected
    assert error.value.details["provider_name"] == "display [REDACTED]"
    assert "raw" not in error.value.details
    assert secret not in json.dumps(error.value.as_dict())
    # General redaction remains strict, including the very short credential.
    arbitrary = ProviderError("TEST", "contains id", details={"free_text": "contains id"})
    assert arbitrary.message == "contains [REDACTED]"
    assert arbitrary.details["free_text"] == "contains [REDACTED]"
