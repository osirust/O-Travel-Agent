"""Actual LiteAPI adapter contract inside PG/ASGI checkout; intercepted HTTP only."""
import json
import os
from copy import deepcopy

import httpx
import pytest
import pytest_asyncio
from test_mixed_checkout import CheckoutTravel, NoLLM, confirmation, headers, prepared, preview

from otravel.config import Settings
from otravel.providers import TravelGateway
from otravel.store import Store


class PrebookTravel(CheckoutTravel):
    def __init__(self):
        super().__init__()
        self.prebook_mode, self.prebook_delta, self.requests, self.selections = "ok", 0, [], {}
        self.lite = TravelGateway({"LITEAPI_API_KEY": "sand_fixture-prebook-pg",
            "LITEAPI_MODE": "sandbox", "http_transport": httpx.MockTransport(self.handler)})

    def handler(self, request):
        self.requests.append((request.url.host, request.url.path))
        body = json.loads(request.content)
        if request.url.path == "/v3.0/hotels/rates":
            native = "selected-" + body["checkin"]
            self.selections[native] = deepcopy(body)
            rows = self.rates(body, 0)
            amount = sum(row["retailRate"]["total"][0]["amount"] for row in rows)
            return httpx.Response(200, json={"data": [{"hotelId": body["hotelIds"][0], "roomTypes": [
                {"offerId": native, "offerRetailRate": {"amount": amount, "currency": "RUB"}, "rates": rows}]}]})
        assert request.url.host == "book.liteapi.travel" and request.url.path == "/v3.0/rates/prebook"
        assert set(body) == {"offerId", "usePaymentSdk"} and body["usePaymentSdk"] is False
        if self.prebook_mode == "unavailable":
            return httpx.Response(409, json={"error": {"code": 2001, "message": "Synthetic offer unavailable"}})
        selected = self.selections[body["offerId"]]
        rows = self.rates(selected, self.prebook_delta)
        amount = sum(row["retailRate"]["total"][0]["amount"] for row in rows)
        return httpx.Response(200, json={"data": {"offerId": body["offerId"], "prebookId": "fixture-prebook-" + selected["checkin"],
            "hotelId": selected["hotelIds"][0], "checkin": selected["checkin"], "checkout": selected["checkout"],
            "currency": "RUB", "price": amount, "sellingPriceToUser": amount, "roomTypes": [{"rates": rows}]}})

    def rates(self, request, delta):
        return [{"rateId": f"new-source-room-{i}", "occupancyNumber": i,
            "adultCount": room["adults"], "childCount": len(room["children"]), "childrenAges": room["children"],
            "maxOccupancy": room["adults"] + len(room["children"]),
            "boardType": "RO" if self.prebook_mode == "changed_board" else "BI",
            "retailRate": {"total": [{"amount": (1_000_000 + delta) / 100, "currency": "RUB"}], "taxesAndFees": None},
            "cancellationPolicies": {"refundableTag": "NRFN", "cancelPolicyInfos": []}}
            for i, room in enumerate(request["occupancies"], 1)]

    async def search_hotels(self, request, provider="liteapi"):
        return await self.lite.search_hotels(request, provider)

    async def reprice(self, offer):
        if offer["provider"] == "liteapi":
            return await self.lite.reprice(offer)
        return await super().reprice(offer)

    async def close(self):
        await self.lite.close()


@pytest_asyncio.fixture
async def prebook_api():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Explicit isolated PostgreSQL is not configured")
    from otravel.api import create_app
    store, travel = Store(url), PrebookTravel()
    app = create_app(Settings(url, "gemini", 1, 8000, 1_000_000, 2), store=store, llm=NoLLM(), travel=travel)
    failure = None
    try:
        async with (app.router.lifespan_context(app),
                    httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client):
            yield client, travel, store
    except Exception as error:
        if isinstance(error, AssertionError):
            raise
        failure = type(error).__name__
    finally:
        await store.close()
    if failure:
        pytest.fail(f"Prebook checkout infrastructure failed ({failure}); private connection details suppressed", pytrace=False)


@pytest.mark.asyncio
async def test_two_stay_six_room_prebook_price_change_quotes_and_one_idempotent_simulated_order(prebook_api):
    client, travel, store = prebook_api
    sid, token, session, selected = await prepared(client)
    travel.prebook_delta = 1000
    response = await preview(client, sid, token, session["state_version"], selected)
    assert response.status_code == 200, response.json()
    quote = response.json()
    assert quote["plan"]["total_minor"] == selected["total_minor"] + 6000
    hotels = [offer for offer in quote["plan"]["offers"] if offer.get("provider") == "liteapi"]
    assert len(hotels) == 2 and all(offer["details"]["sandbox_prebook_verified"] for offer in hotels)
    assert all(offer["source_type"] == "SANDBOX" for offer in hotels)
    assert travel.requests == [("api.liteapi.travel", "/v3.0/hotels/rates")] * 2 + [
        ("book.liteapi.travel", "/v3.0/rates/prebook")] * 2
    calls_before = deepcopy(travel.requests)
    payload = confirmation(quote)
    first = await client.post(f"/api/v1/sessions/{sid}/checkout/confirm", headers=headers(token), json=payload)
    repeated = await client.post(f"/api/v1/sessions/{sid}/checkout/confirm", headers=headers(token), json=payload)
    assert first.status_code == repeated.status_code == 200
    assert first.json()["order_id"] == repeated.json()["order_id"]
    assert travel.requests == calls_before
    assert len(await store.list_orders(sid)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["unavailable", "changed_board"])
async def test_prebook_failure_or_fresh_hard_condition_violation_stales_old_consent_and_preserves_trip(prebook_api, mode):
    client, travel, store = prebook_api
    sid, token, session, selected = await prepared(client)
    first = await preview(client, sid, token, session["state_version"], selected)
    assert first.status_code == 200
    old_quote = first.json()
    before = (await client.get(f"/api/v1/sessions/{sid}", headers=headers(token))).json()
    travel.prebook_mode = mode
    failed = await preview(client, sid, token, session["state_version"], selected)
    assert failed.status_code == (503 if mode == "unavailable" else 422)
    stale = await client.post(f"/api/v1/sessions/{sid}/checkout/confirm", headers=headers(token), json=confirmation(old_quote))
    assert stale.status_code == 409
    after = (await client.get(f"/api/v1/sessions/{sid}", headers=headers(token))).json()
    assert after["trip"] == before["trip"] and after["state_version"] == before["state_version"]
    assert await store.list_orders(sid) == []
