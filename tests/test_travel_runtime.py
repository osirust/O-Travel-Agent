"""Travel-only pacing/catalogue memory: intercepted HTTP, no accounts or keys."""
import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from otravel.providers import ProviderError, TravelGateway
from otravel.travel_runtime import TravelClockError, TravelRuntime


@contextmanager
def raises_code(code):
    with pytest.raises(ProviderError) as caught:
        yield caught
    assert caught.value.code == code


class Clock:
    def __init__(self):
        self.value = datetime(2026, 10, 26, 12, tzinfo=UTC)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


def gateway(handler, clock=None, **settings):
    return TravelGateway({"http_transport": httpx.MockTransport(handler), "clock": clock or Clock(),
                          "LITEAPI_API_KEY": "sand_synthetic_private_key", **settings})


@pytest.mark.asyncio
@pytest.mark.parametrize("city,canonical,country,supplier_city", [
    ("Санкт-Петербург", "Санкт-Петербург", "RU", "St. Petersburg"),
    ("СПб", "Санкт-Петербург", "RU", "St. Petersburg"),
    ("Москва", "Москва", "RU", "Moscow"), ("Сочи", "Сочи", "RU", "Sochi"),
    ("Казань", "Казань", "RU", "Kazan"), ("Стамбул", "Стамбул", "TR", "Istanbul"),
    ("Анталья", "Анталья", "TR", "Antalya"), ("Дубай", "Дубай", "AE", "Dubai"),
])
async def test_canonical_city_uses_supplier_dictionary_label_without_renaming_user_city(city, canonical, country, supplier_city):
    def handler(request):
        assert request.url.params["countryCode"] == country
        assert request.url.params["cityName"] == supplier_city
        return httpx.Response(200, json={"data": [{"id": "fixture-1", "name": "Supplier Hotel"}]})

    adapter = gateway(handler)
    try:
        assert await adapter.resolve_hotels(city) == [{"id": "fixture-1", "name": "Supplier Hotel", "city": canonical}]
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_thirty_city_lookups_share_one_http_and_return_independent_static_rows():
    calls = []

    async def handler(request):
        calls.append(request)
        await asyncio.sleep(0)
        return httpx.Response(200, json={"data": [{"id": "hotel-1", "name": "Fixture Hotel"}]})

    adapter = gateway(handler)
    try:
        results = await asyncio.gather(*(adapter.resolve_hotels("Стамбул") for _ in range(30)))
        assert len(calls) == 1
        assert results == [[{"id": "hotel-1", "name": "Fixture Hotel", "city": "Стамбул"}]] * 30
        results[0][0]["id"] = "changed-by-caller"
        assert (await adapter.resolve_hotels("Istanbul"))[0]["id"] == "hotel-1"
        assert list(adapter._travel_runtime.hotel_lookups) == [("liteapi", "sandbox", "Стамбул")]
        assert "sand_synthetic_private_key" not in repr(adapter._travel_runtime.hotel_lookups)
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_lookup_limit_city_ttl_empty_rows_and_key_validation_are_not_price_cache():
    clock, calls = Clock(), []

    def handler(request):
        calls.append(request)
        limit = int(request.url.params["limit"])
        return httpx.Response(200, json={"data": [{"id": f"hotel-{i}", "name": "Fixture"}
                                                 for i in range(limit)]})

    adapter = gateway(handler, clock)
    try:
        assert len(await adapter.resolve_hotels("Стамбул", 3)) == 3
        assert len(await adapter.resolve_hotels("Стамбул", 1)) == 1
        assert len(calls) == 1
        assert len(await adapter.resolve_hotels("Стамбул", 4)) == 4
        await adapter.resolve_hotels("Анталья", 3)
        assert len(calls) == 3
        clock.advance(3599)
        await adapter.resolve_hotels("Стамбул", 3)
        assert len(calls) == 3
        clock.advance(1)
        await adapter.resolve_hotels("Стамбул", 3)
        assert len(calls) == 4
        adapter.settings["LITEAPI_API_KEY"] = "production-synthetic"
        with raises_code("CONFIG_BLOCKED"):
            await adapter.resolve_hotels("Стамбул", 3)
        assert len(calls) == 4
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_empty_catalogue_is_cached_but_invalid_response_is_not():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"data": None if len(calls) == 1 else []})

    adapter = gateway(handler)
    try:
        with raises_code("INVALID_RESPONSE"):
            await adapter.resolve_hotels("Стамбул")
        assert await adapter.resolve_hotels("Стамбул") == []
        assert await adapter.resolve_hotels("Стамбул") == []
        assert len(calls) == 2
    finally:
        await adapter.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("header,seconds,source", [
    ("120", 120, "retry_after_header"), ("1.5", 2, "retry_after_header"),
    (None, 30, "engineering_default"), ("bad value", 30, "engineering_default"),
    ("-1", 30, "engineering_default"), ("0", 30, "engineering_default"),
    ("nan", 30, "engineering_default"), ("inf", 30, "engineering_default"),
])
async def test_actual_429_header_blocks_next_same_provider_without_retry(header, seconds, source):
    clock, calls = Clock(), []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={} if header is None else {"Retry-After": header},
                              json={"error": "limited"}) if len(calls) == 1 else httpx.Response(200, json={"data": []})

    adapter = gateway(handler, clock)
    try:
        with pytest.raises(ProviderError) as first:
            await adapter.resolve_hotels("Стамбул")
        assert first.value.code == "RATE_LIMITED"
        assert first.value.details["retry_after_seconds"] == seconds
        assert first.value.details["cooldown_source"] == source
        assert first.value.details["vendor_reset_guaranteed"] is False
        with pytest.raises(ProviderError) as blocked:
            await adapter.resolve_hotels("Стамбул")
        assert blocked.value.code == "SOURCE_COOLDOWN"
        assert len(calls) == 1
        clock.advance(seconds - 1)
        with raises_code("SOURCE_COOLDOWN"):
            await adapter.resolve_hotels("Анталья")
        assert len(calls) == 1
        clock.advance(1)
        assert await adapter.resolve_hotels("Стамбул") == []
        assert len(calls) == 2
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_http_date_retry_after_and_other_provider_remain_independent():
    clock, calls = Clock(), []

    def handler(request):
        calls.append(request)
        if request.url.host == "api.liteapi.travel":
            return httpx.Response(429, headers={"Retry-After": format_datetime(clock() + timedelta(seconds=90), usegmt=True)})
        return httpx.Response(200, json={"data": []})

    adapter = gateway(handler, clock)
    try:
        with pytest.raises(ProviderError) as first:
            await adapter.resolve_hotels("Стамбул")
        assert first.value.details["retry_after_seconds"] == 90
        assert await adapter._request("duffel", "GET", "https://api.duffel.com/air/offers/test") == {"data": []}
        assert len(calls) == 2
        with raises_code("SOURCE_COOLDOWN"):
            await adapter._request("liteapi", "POST", "https://api.liteapi.travel/v3.0/hotels/rates", json={})
        assert len(calls) == 2
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_concurrent_provider_headers_are_task_local_and_not_reused():
    calls = []

    async def handler(request):
        calls.append(request)
        await asyncio.sleep(0)
        delay = "71" if request.url.host == "api.liteapi.travel" else "113"
        return httpx.Response(429, headers={"Retry-After": delay})

    adapter = gateway(handler)
    try:
        failures = await asyncio.gather(
            adapter._request("liteapi", "GET", "https://api.liteapi.travel/test"),
            adapter._request("duffel", "GET", "https://api.duffel.com/test"),
            return_exceptions=True,
        )
        assert [error.code for error in failures] == ["RATE_LIMITED", "RATE_LIMITED"]
        assert [error.details["retry_after_seconds"] for error in failures] == [71, 113]
        assert adapter._travel_runtime.remaining("liteapi")["retry_after_seconds"] == 71
        assert adapter._travel_runtime.remaining("duffel")["retry_after_seconds"] == 113
        assert adapter._travel_retry_after.get() is None
        assert len(calls) == 2
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_timeout_does_not_retry_or_create_cooldown():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        return httpx.Response(200, json={"data": []})

    adapter = gateway(handler)
    try:
        with raises_code("REQUEST_TIMEOUT"):
            await adapter.resolve_hotels("Стамбул")
        assert len(calls) == 1
        assert await adapter.resolve_hotels("Стамбул") == []
        assert len(calls) == 2
    finally:
        await adapter.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 500, 503])
async def test_non_429_does_not_create_source_cooldown(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={}) if len(calls) == 1 else httpx.Response(200, json={"data": []})

    adapter = gateway(handler)
    try:
        with raises_code("HTTP_ERROR"):
            await adapter.resolve_hotels("Стамбул")
        assert await adapter.resolve_hotels("Стамбул") == []
        assert len(calls) == 2
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_cache_and_cooldown_are_private_to_gateway_instance():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"data": [{"id": "hotel-1"}]})

    first, second = gateway(handler), gateway(handler)
    try:
        await first.resolve_hotels("Стамбул")
        await second.resolve_hotels("Стамбул")
        first._travel_runtime.block("liteapi", 100)
        assert await second.resolve_hotels("Анталья")
        assert len(calls) == 3
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_hotel_rates_and_reprice_always_make_fresh_http_requests():
    calls, prices = [], iter(["100", "110", "125"])

    def handler(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "hotel-1"}]})
        if request.url.host == "book.liteapi.travel":
            price = next(prices)
            return httpx.Response(200, json={"data": {"offerId": "offer-1", "prebookId": "fixture-prebook",
                "hotelId": "hotel-1", "checkin": "2026-11-10", "checkout": "2026-11-13", "currency": "RUB",
                "price": price, "sellingPriceToUser": price, "roomTypes": [{"rates": [{"rateId": "fresh-room-1",
                    "occupancyNumber": 1, "adultCount": 1, "childCount": 0, "childrenAges": [], "maxOccupancy": 1,
                    "retailRate": {"total": [{"amount": price, "currency": "RUB"}], "taxesAndFees": None}}]}]}})
        return httpx.Response(200, json={"data": [{"hotelId": "hotel-1", "roomTypes": [{
            "offerId": "offer-1", "offerRetailRate": {"amount": next(prices), "currency": "RUB"},
            "rates": [{"rateId": "room-1"}],
        }]}]})

    request = {"hotelIds": ["hotel-1"], "checkin": "2026-11-10", "checkout": "2026-11-13",
               "currency": "RUB", "guestNationality": "RU", "occupancies": [{"adults": 1, "children": []}]}
    adapter = gateway(handler)
    try:
        await adapter.resolve_hotels("Стамбул")
        first = (await adapter.search_hotels(request))[0]
        second = (await adapter.search_hotels(request))[0]
        third = await adapter.reprice(first)
        assert [first["amount_minor"], second["amount_minor"], third["amount_minor"]] == [10000, 11000, 12500]
        assert third["details"]["reprice_kind"] == "sandbox_selected_offer_prebook"
        await adapter.resolve_hotels("Стамбул")
        assert [item.method for item in calls] == ["GET", "POST", "POST", "POST"]
        assert [item.url.host for item in calls] == ["api.liteapi.travel"] * 3 + ["book.liteapi.travel"]
        assert all(set(row) == {"id", "name", "city"} for row in adapter._travel_runtime.get_hotels("Стамбул", 3))
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_naive_clock_rejected_before_outbound_http():
    calls = []
    adapter = gateway(lambda request: calls.append(request), lambda: datetime(2026, 10, 26))
    try:
        with raises_code("INVALID_CLOCK"):
            await adapter.resolve_hotels("Стамбул")
        assert calls == []
    finally:
        await adapter.close()


def test_aware_clock_converts_to_utc_and_monotonic_expiry_survives_wall_jump():
    clock, elapsed = Clock(), [100.0]
    clock.value = datetime(2026, 10, 26, 15, tzinfo=timezone(timedelta(hours=3)))
    runtime = TravelRuntime(clock=clock, monotonic=lambda: elapsed[0])
    assert runtime.now() == datetime(2026, 10, 26, 12, tzinfo=UTC)
    runtime.block("liteapi", 60)
    runtime.put_hotels("Стамбул", 3, [{"id": "hotel-1"}])
    clock.advance(-10000)
    elapsed[0] += 59
    assert runtime.remaining("liteapi")["retry_after_seconds"] == 1
    elapsed[0] += 1
    assert runtime.remaining("liteapi") is None
    assert runtime.get_hotels("Стамбул", 3) == [{"id": "hotel-1"}]
    elapsed[0] = 3700
    assert runtime.get_hotels("Стамбул", 3) is None


@pytest.mark.parametrize("value", [True, False, None, "bad", "nan", "inf", "-3", "0", "1" * 129])
def test_unreliable_retry_after_not_accepted_as_vendor_reset(value):
    assert TravelRuntime(clock=Clock()).retry_after(value) is None


def test_later_cooldown_not_shortened_and_clock_rejects_nonfinite():
    runtime = TravelRuntime(clock=Clock())
    runtime.block("liteapi", 90)
    assert runtime.block("liteapi", 1)["retry_after_seconds"] == 90
    with pytest.raises(ValueError, match="Unknown travel provider"):
        runtime.block("zai", 10)
    with pytest.raises(TravelClockError):
        TravelRuntime(clock=Clock(), monotonic=lambda: float("nan")).remaining("liteapi")
