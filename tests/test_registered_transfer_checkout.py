"""Registered Belgrade transfer checkout with isolated PG and fake suppliers."""
import os
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.engine import make_url
from test_mixed_checkout import CheckoutTravel, NoLLM, confirmation, headers, plans, preview, wait
from test_registered_transfer_demo import registered_trip

from otravel.api import create_app
from otravel.config import Settings
from otravel.inventory import REGISTERED_TRANSFER_PROVIDER
from otravel.store import Store


@pytest_asyncio.fixture
async def registered_checkout(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Only isolated TEST_DATABASE_URL is authorized")
    if make_url(url).database != "otravel_test":
        pytest.fail("Only otravel_test is authorized", pytrace=False)
    repository, gateway = Store(url), CheckoutTravel()
    settings = Settings(url, "gemini", 1, 8000, 1000000, 1, default_search_mode="HYBRID",
        default_hotel_provider="liteapi", default_flight_provider="duffel", max_local_llm_calls_per_day=0)
    app = create_app(settings, store=repository, llm=NoLLM(), travel=gateway)
    async def no_foreign_recovery():
        pass
    monkeypatch.setattr(app.state.harness, "start", no_foreign_recovery)
    try:
        async with app.router.lifespan_context(app), httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://registered-controlled") as client:
            response = await client.post("/api/v1/sessions", json={"initial_trip": registered_trip().model_dump(mode="json")})
            assert response.status_code == 201
            yield client, gateway, repository, response.json()
    finally:
        await repository.close()


async def search(client, session):
    sid, token = session["session_id"], session["session_token"]
    response = await client.post(f"/api/v1/sessions/{sid}/search", headers=headers(token), json={"mode": "HYBRID"})
    assert response.status_code == 202
    run = await wait(client, response.json()["run_id"], token)
    return run, plans(run)


@pytest.mark.asyncio
async def test_registered_city_refresh_and_idempotent_simulated_checkout(registered_checkout):
    client, gateway, repository, session = registered_checkout
    sid, token = session["session_id"], session["session_token"]
    _run, options = await search(client, session)
    plan, = options
    transfer, = [offer for offer in plan["offers"] if offer.get("provider") == REGISTERED_TRANSFER_PROVIDER]
    assert plan["source_type"] == "MIXED" and plan["coverage"]["complete"]
    assert plan["total_minor"] == 3040000 and transfer["country_iso2"] == "RS"
    assert not plan["ready_for_real_booking"] and plan["ready_for_simulated_checkout"]
    original_trip = (await repository.get_session(sid))["state"]
    assert original_trip["stays"][0]["rooms"][0]["traveler_ids"] == []
    assert plan["room_assignments"]["belgrade"][0]["traveler_ids"] == ["adult"]
    gateway.delta = 100
    response = await preview(client, sid, token, session["state_version"], plan)
    assert response.status_code == 200
    quote = response.json()
    assert quote["plan"]["total_minor"] == 3040300 and quote["plan"]["repriced"]
    assert len(gateway.reprice_calls) == 3 and transfer["offer_id"] not in gateway.reprice_calls
    assert next(offer for offer in quote["plan"]["offers"] if offer.get("provider") == REGISTERED_TRANSFER_PROVIDER) == transfer
    body = confirmation(quote)
    first = await client.post(f"/api/v1/sessions/{sid}/checkout/confirm", headers=headers(token), json=body)
    repeat = await client.post(f"/api/v1/sessions/{sid}/checkout/confirm", headers=headers(token), json=body)
    assert first.status_code == repeat.status_code == 200
    assert first.json() == repeat.json() and first.json()["status"] == "BOOKED_DEMO"
    assert first.json()["booking_created_at_supplier"] is False and len(await repository.list_orders(sid)) == 1
    final_trip = (await repository.get_session(sid))["state"]
    assert final_trip["state_version"] == original_trip["state_version"] + 1
    assert {key: value for key, value in final_trip.items() if key != "state_version"} == {
        key: value for key, value in original_trip.items() if key != "state_version"}


@pytest.mark.asyncio
async def test_disabling_fallback_revisions_invalidates_quote_and_keeps_transfer_gap(registered_checkout):
    client, _gateway, repository, session = registered_checkout
    sid, token = session["session_id"], session["session_token"]
    _run, options = await search(client, session)
    response = await preview(client, sid, token, session["state_version"], options[0])
    assert response.status_code == 200
    quote = response.json()
    response = await client.post(f"/api/v1/sessions/{sid}/commands", headers=headers(token), json={
        "client_message_id": str(uuid4()), "expected_version": session["state_version"], "search": False,
        "operations": [{"type": "set_search_policy", "value": {"demo_fallback": False}}]})
    assert response.status_code == 202
    await wait(client, response.json()["run_id"], token)
    rejected = await client.post(f"/api/v1/sessions/{sid}/checkout/confirm", headers=headers(token), json=confirmation(quote))
    assert rejected.status_code == 409
    run, options = await search(client, session)
    assert options == [] and await repository.list_orders(sid) == []
    partial = run["result"]["result"]["partial_plan"]
    assert partial["coverage"]["missing"] == ["transfer:belgrade"]
    assert not any(offer.get("provider") == REGISTERED_TRANSFER_PROVIDER for offer in partial["offers"])


@pytest.mark.asyncio
async def test_refreshed_fee_gap_cannot_be_hidden_by_valid_registered_transfer(registered_checkout):
    client, gateway, repository, session = registered_checkout
    sid, token = session["session_id"], session["session_token"]
    _run, options = await search(client, session)
    response = await preview(client, sid, token, session["state_version"], options[0])
    assert response.status_code == 200
    old = response.json()
    gateway.mode = "drop_fees"
    rejected = await preview(client, sid, token, session["state_version"], options[0])
    assert rejected.status_code == 422 and rejected.json()["error"]["code"] == "mandatory_fees_unknown"
    confirmed = await client.post(f"/api/v1/sessions/{sid}/checkout/confirm", headers=headers(token), json=confirmation(old))
    assert confirmed.status_code == 409 and await repository.list_orders(sid) == []
