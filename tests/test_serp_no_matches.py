"""Observed Google Flights no-results is distinct from service/key/quota failure."""
import asyncio
import json
from copy import deepcopy
from pathlib import Path

import httpx
import pytest
from test_orchestrator import NOW, FakeTravel, hybrid

from otravel.orchestrator import TripSearch
from otravel.providers import ProviderError, TravelGateway
from otravel.search_explanation import render_search_explanation

SOURCE = json.loads((Path(__file__).parents[1] / "fixtures/regression/serp-google-flights-no-results-20261027.json").read_text(encoding="utf-8"))
EXACT = SOURCE["response_body"]["error"]
REQUEST = {"slices": [{"origin": "MOW", "destination": "DXB", "departure_date": "2026-11-10"}],
           "adults": 1, "children": 0, "currency": "RUB"}


def adapter(body=None, status=200):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json=deepcopy(SOURCE["response_body"] if body is None else body))
    gateway = TravelGateway({"http_transport": httpx.MockTransport(handler), "SERPAPI_API_KEY": "serp_synthetic-test-key",
                             "SERPAPI_FREE_TIER_CONFIRMED": True})
    return gateway, calls


def request_error(*, body=None, status=200, provider="serpapi", method="GET",
                  url="https://serpapi.com/search.json", params=None):
    gateway, calls = adapter(body, status)
    async def run():
        try:
            await gateway._request(provider, method, url, params={"engine": "google_flights"} if params is None else params)
        finally:
            await gateway.close()
    with pytest.raises(ProviderError) as caught:
        asyncio.run(run())
    assert len(calls) == 1
    return caught.value


@pytest.mark.parametrize("extra", [{}, {"best_flights": [], "other_flights": []}, {"errors": []}])
def test_exact_observed_successful_flights_empty_query_has_safe_nonretryable_category(extra):
    error = request_error(body={"error": EXACT, **extra})
    assert error.code == "NO_MATCHES" and error.retryable is False
    assert "отсутствие рейсов этим не доказано" in error.message
    assert EXACT not in json.dumps(error.as_dict(), ensure_ascii=False)
    assert "serp_synthetic-test-key" not in json.dumps(error.as_dict())


@pytest.mark.parametrize("body", [
    {"error": EXACT + " Please try later."}, {"error": EXACT + " "}, {"error": EXACT.casefold()},
    {"error": "Your account is out of searches"}, {"error": "Invalid API key"},
    {"error": {"message": EXACT}}, {"errors": [EXACT]}, {"error": EXACT, "errors": ["Unknown failure"]},
    {"error": EXACT, "best_flights": [{"price": 100}]}, {"error": EXACT, "other_flights": "malformed"},
])
def test_unknown_auth_conflicting_or_different_error_shapes_are_not_empty_inventory(body):
    assert request_error(body=body).code == "PROVIDER_RESPONSE_ERROR"


@pytest.mark.parametrize("change", [
    {"provider": "groq"}, {"method": "POST"}, {"url": "https://serpapi.com/account.json"},
    {"url": "https://unrelated.example/search.json"}, {"params": {"engine": "google_hotels"}},
    {"params": {"engine": "google_flights_other"}}, {"params": {}},
])
def test_source_endpoint_method_and_engine_must_all_match_the_verified_context(change):
    assert request_error(**change).code == "PROVIDER_RESPONSE_ERROR"


@pytest.mark.parametrize("status,code", [(201, "PROVIDER_RESPONSE_ERROR"), (400, "HTTP_ERROR"),
    (401, "HTTP_ERROR"), (403, "HTTP_ERROR"), (408, "HTTP_ERROR"), (429, "RATE_LIMITED"), (500, "HTTP_ERROR")])
def test_http_error_authority_is_never_overridden_by_a_no_results_body(status, code):
    assert request_error(status=status).code == code


def test_public_flight_adapter_uses_the_same_safe_category_without_retry():
    gateway, calls = adapter()
    async def run():
        try:
            with pytest.raises(ProviderError) as caught:
                await gateway.search_flights(REQUEST, "serpapi")
            return caught.value
        finally:
            await gateway.close()
    assert asyncio.run(run()).code == "NO_MATCHES"
    assert len(calls) == 1 and calls[0].url.params["engine"] == "google_flights"


def test_orchestrator_and_explanation_preserve_no_results_without_false_source_outage():
    gateway, calls = adapter()
    class Search(FakeTravel):
        async def search_flights(self, request, provider="serpapi"):
            return await gateway.search_flights(request, provider)
    state = hybrid(flight_provider="serpapi")
    before = state.model_dump(mode="json")
    async def run():
        try:
            return await TripSearch(Search(), clock=lambda: NOW).search(state)
        finally:
            await gateway.close()
    result = asyncio.run(run())
    assert len(calls) == 3 and result["plans"]
    assert [error["code"] for error in result["component_errors"]] == ["NO_MATCHES"] * 3
    explanation = render_search_explanation(state, result)
    codes = {reason["code"] for reason in explanation["summary"]["reasons"]}
    assert "no_source_offers" in codes and "source_failure" not in codes
    assert "отсутствие мест этим не доказано" in explanation["text"]
    assert "получить проверяемый ответ источника не удалось" not in explanation["text"]
    assert state.model_dump(mode="json") == before
