"""Real isolated PostgreSQL + ASGI checkout, fake travel only, no vendor calls."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from otravel.config import Settings
from otravel.domain import SearchPolicy, sample_trip
from otravel.providers import ProviderError
from otravel.store import Store, StaleQuote


class NoLLM:
    async def extract_operations(self, context):
        raise AssertionError("Mixed checkout test must not call an LLM")

    async def close(self):
        pass


class CheckoutTravel:
    def __init__(self):
        self.mode = "ok"
        self.delta = 0
        self.expiry_seconds = 180
        self.reprice_calls = []
        self.catalog_revision = 1
        self.search_delta = 0
        self.gone_ids = set()

    async def resolve_hotels(self, city, limit=3):
        suffix = f"-v{self.catalog_revision}" if self.catalog_revision > 1 else ""
        return [{"id": "fixture-hotel-" + city + suffix}]

    def offer(self, request, provider, kind):
        now = datetime.now(timezone.utc)
        identity = request["checkin"] if kind == "hotel" else request["slices"][0]["departure_date"]
        native = f"fixture-{kind}-{identity}"
        if self.catalog_revision > 1:
            native += f"-v{self.catalog_revision}"
        details = {"kind": kind, "native_offer_id": native, "search_request": deepcopy(request),
                   "fees_included": True, "allocation_verified": True, "group_quote": True,
                   "constraint_evidence": {"breakfast": True}}
        if kind == "hotel":
            details["hotel_id"] = request["hotelIds"][0]
        else:
            details["passengers_count"] = len(request["passengers"])
        return {"offer_id": native, "provider": provider,
                "source_type": "SANDBOX" if provider == "liteapi" else "LIVE_INDICATIVE",
                "price_kind": "COMPONENT_TOTAL", "amount_minor": 1_000_000 + self.search_delta, "currency": "RUB",
                "retrieved_at": now.isoformat(), "expires_at": (now + timedelta(seconds=self.expiry_seconds)).isoformat(),
                "details": details}

    async def search_hotels(self, request, provider="liteapi"):
        return [self.offer(request, provider, "hotel")]

    async def search_flights(self, request, provider="serpapi"):
        return [self.offer(request, provider, "flight")]

    async def reprice(self, offer):
        self.reprice_calls.append(offer["offer_id"])
        if offer["offer_id"] in self.gone_ids:
            raise ProviderError("OFFER_UNAVAILABLE", "Выбранный fixture offer больше не доступен.", provider=offer["provider"])
        if self.mode == "provider_failure":
            raise ProviderError("REQUEST_TIMEOUT", "Synthetic provider timeout", True, offer["provider"])
        result = deepcopy(offer)
        now = datetime.now(timezone.utc)
        result.update(amount_minor=offer["amount_minor"] + self.delta, retrieved_at=now.isoformat(),
                      expires_at=(now + timedelta(seconds=self.expiry_seconds)).isoformat())
        # Real adapters return a new source response without orchestration binding.
        result["details"].pop("binding", None)
        if self.mode == "drop_fees":
            result["details"].pop("fees_included")
        elif self.mode == "drop_constraints":
            result["details"].pop("constraint_evidence")
        elif self.mode == "change_request":
            result["details"]["search_request"]["currency"] = "USD"
        elif self.mode == "change_identity":
            result["details"]["native_offer_id"] = "different-provider-offer"
        elif self.mode == "change_binding":
            result["details"]["binding"] = {**offer["details"]["binding"], "trip_id": "another-trip"}
        elif self.mode == "expired":
            result["expires_at"] = (now - timedelta(seconds=1)).isoformat()
        return result

    async def close(self):
        pass


@pytest_asyncio.fixture
async def mixed_api():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not configured; no app, DB or provider contacted")
    from otravel.api import create_app
    store = Store(url)
    gateway = CheckoutTravel()
    app = create_app(Settings(url, "gemini", 1, 8000, 1_000_000, 2), store=store, llm=NoLLM(), travel=gateway)
    infrastructure_error = None
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                yield client, gateway, store
    except Exception as exc:
        if isinstance(exc, AssertionError):
            raise
        infrastructure_error = type(exc).__name__
    finally:
        await store.close()
    if infrastructure_error:
        pytest.fail(f"Mixed checkout infrastructure failed ({infrastructure_error}); private details suppressed", pytrace=False)


def headers(token):
    return {"Authorization": "Bearer " + token}


def plans(value):
    if isinstance(value, dict):
        if isinstance(value.get("plans"), list):
            return value["plans"]
        for key in ("result", "run"):
            found = plans(value.get(key))
            if found:
                return found
    return []


async def wait(client, run_id, token):
    for _ in range(150):
        response = await client.get("/api/v1/runs/" + run_id, headers=headers(token))
        assert response.status_code == 200
        run = response.json()
        if run["status"] in {"completed", "failed", "conflict", "interrupted", "cancelled"}:
            assert run["status"] == "completed", run.get("error")
            return run
        await asyncio.sleep(0.02)
    pytest.fail("Bounded mixed run wait expired")


async def prepared(client):
    state = sample_trip()
    state.search_policy = SearchPolicy(mode="HYBRID", guest_nationality="RU")
    response = await client.post("/api/v1/sessions", json={"initial_trip": state.model_dump(mode="json")})
    assert response.status_code == 201
    session = response.json()
    sid, token = session["session_id"], session["session_token"]
    response = await client.post(f"/api/v1/sessions/{sid}/search", json={}, headers=headers(token))
    assert response.status_code == 202
    run = await wait(client, response.json()["run_id"], token)
    selected = plans(run)[0]
    assert selected["source_type"] == "MIXED"
    return sid, token, session, selected


async def preview(client, sid, token, version, plan):
    return await client.post(f"/api/v1/sessions/{sid}/checkout/preview", headers=headers(token),
                             json={"plan_id": plan["plan_id"], "state_version": version})


def confirmation(quote, key=None):
    return {"quote_id": quote["quote_id"], "quote_version": quote["quote_version"],
            "state_version": quote["state_version"], "confirmed": True, "idempotency_key": key or str(uuid4())}


@pytest.mark.asyncio
async def test_mixed_search_preview_refresh_and_idempotent_simulated_confirm(mixed_api):
    client, gateway, _ = mixed_api
    sid, token, session, plan = await prepared(client)
    gateway.delta = 100
    response = await preview(client, sid, token, session["state_version"], plan)
    assert response.status_code == 200, response.text
    quote = response.json()
    assert len(gateway.reprice_calls) == 5  # 2 hotels + 3 legs; DEMO transfers local.
    assert quote["plan"]["total_minor"] == 5_720_500
    assert quote["plan"]["source_type"] == "MIXED"
    assert quote["plan"]["comparison"]["delta_minor"] == 500
    assert quote["plan"]["ready_for_real_booking"] is False
    assert quote["plan"]["ready_for_simulated_checkout"] is True
    assert len(quote["plan"]["source_snapshots"]) == 7
    request = confirmation(quote)
    first = await client.post(f"/api/v1/sessions/{sid}/checkout/confirm", json=request, headers=headers(token))
    repeated = await client.post(f"/api/v1/sessions/{sid}/checkout/confirm", json=request, headers=headers(token))
    assert first.status_code == repeated.status_code == 200
    assert first.json()["order_id"] == repeated.json()["order_id"]
    assert first.json()["status"] == "BOOKED_DEMO"
    assert first.json()["booking_created_at_supplier"] is False
    orders = await client.get(f"/api/v1/sessions/{sid}/orders", headers=headers(token))
    assert len(orders.json()["orders"]) == 1
    assert len(gateway.reprice_calls) == 5  # Confirm never calls a supplier.


@pytest.mark.asyncio
async def test_new_preview_price_change_stales_previous_active_quote(mixed_api, monkeypatch):
    """MONEY-07/BOOK-03: source repricing revokes old consent; not an NLU test."""
    import otravel.api as api_module
    import otravel.orchestrator as orchestrator_module
    import otravel.store as store_module

    # This case isolates a price change. Independent host clock reads previously
    # moved backwards between search and preview, making a fresh fixture appear
    # future-dated. Expiry/clock rejection has separate explicit tests below.
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = cls(2026, 10, 26, tzinfo=timezone.utc)
            return instant.astimezone(tz) if tz is not None else instant.replace(tzinfo=None)

    monkeypatch.setitem(globals(), "datetime", FixedDatetime)
    monkeypatch.setattr(api_module, "datetime", FixedDatetime)
    monkeypatch.setattr(orchestrator_module, "datetime", FixedDatetime)
    monkeypatch.setattr(store_module, "_now", lambda: FixedDatetime.now(timezone.utc))
    client, gateway, store = mixed_api
    sid, token, session, plan = await prepared(client)
    first_response = await preview(client, sid, token, session["state_version"], plan)
    assert first_response.status_code == 200, first_response.text
    first = first_response.json()
    assert datetime.fromisoformat(first["created_at"]) == FixedDatetime.now(timezone.utc)
    assert all(datetime.fromisoformat(snapshot["retrieved_at"]) == FixedDatetime.now(timezone.utc)
               for snapshot in first["plan"]["source_snapshots"] if snapshot["source_type"] != "DEMO")
    gateway.delta = 150_000  # Authoritative fake source: each tariff increases 15%.
    second_response = await preview(client, sid, token, session["state_version"], plan)
    assert second_response.status_code == 200, second_response.text
    second = second_response.json()
    assert second["plan"]["total_minor"] == 6_470_000
    assert second["quote_id"] != first["quote_id"]
    assert second["plan"]["snapshot_hash"] != first["plan"]["snapshot_hash"]
    assert (await store.get_quote(first["quote_id"]))["status"] == "stale"
    stale = await client.post(f"/api/v1/sessions/{sid}/checkout/confirm", json=confirmation(first), headers=headers(token))
    assert stale.status_code == 409
    accepted = await client.post(f"/api/v1/sessions/{sid}/checkout/confirm", json=confirmation(second), headers=headers(token))
    assert accepted.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["provider_failure", "drop_fees", "drop_constraints", "change_request", "change_identity", "change_binding", "expired"])
async def test_refresh_failure_or_lost_evidence_preserves_trip_and_revokes_old_consent(mixed_api, mode):
    client, gateway, store = mixed_api
    sid, token, session, plan = await prepared(client)
    previous = (await preview(client, sid, token, session["state_version"], plan)).json()
    before = (await client.get(f"/api/v1/sessions/{sid}", headers=headers(token))).json()
    gateway.mode = mode
    rejected = await preview(client, sid, token, session["state_version"], plan)
    assert rejected.status_code in {409, 422, 503}, rejected.text
    after = (await client.get(f"/api/v1/sessions/{sid}", headers=headers(token))).json()
    assert after["trip"] == before["trip"]
    assert after["state_version"] == before["state_version"]
    assert (await store.get_quote(previous["quote_id"]))["status"] == "stale"
    stale = await client.post(f"/api/v1/sessions/{sid}/checkout/confirm", json=confirmation(previous), headers=headers(token))
    assert stale.status_code == 409
    assert (await client.get(f"/api/v1/sessions/{sid}/orders", headers=headers(token))).json()["orders"] == []


@pytest.mark.asyncio
async def test_quote_ttl_is_minimum_of_external_expiries_and_600_seconds(mixed_api):
    client, gateway, _ = mixed_api
    sid, token, session, plan = await prepared(client)
    gateway.expiry_seconds = 30
    response = await preview(client, sid, token, session["state_version"], plan)
    assert response.status_code == 200, response.text
    quote = response.json()
    expiry = datetime.fromisoformat(quote["expires_at"])
    source_expiries = [datetime.fromisoformat(row["expires_at"]) for row in quote["plan"]["source_snapshots"] if row["source_type"] != "DEMO"]
    assert expiry == min(source_expiries)
    assert expiry <= datetime.fromisoformat(quote["created_at"]) + timedelta(seconds=600)


@pytest.mark.asyncio
async def test_expired_source_snapshot_blocks_preview_before_refresh(mixed_api, monkeypatch):
    import otravel.api as api_module
    client, gateway, _ = mixed_api
    sid, token, session, plan = await prepared(client)
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    class Future:
        @staticmethod
        def now(tz):
            return future
    monkeypatch.setattr(api_module, "datetime", Future)
    rejected = await preview(client, sid, token, session["state_version"], plan)
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "stale_source_snapshot"
    assert gateway.reprice_calls == []


@pytest.mark.asyncio
async def test_store_expired_external_source_rejected_and_legacy_ttl_capped(mixed_api):
    client, _, store = mixed_api
    sid, _, session, plan = await prepared(client)
    legacy = await store.create_quote(sid, session["state_version"], {"total_rub": 100}, expires_seconds=3600)
    assert datetime.fromisoformat(legacy["expires_at"]) == datetime.fromisoformat(legacy["created_at"]) + timedelta(seconds=600)
    bad = deepcopy(plan)
    next(s for s in bad["source_snapshots"] if s["source_type"] != "DEMO")["expires_at"] = "2000-01-01T00:00:00Z"
    with pytest.raises(StaleQuote):
        await store.create_quote(sid, session["state_version"], bad)


@pytest.mark.asyncio
async def test_runtime_undo_after_mixed_quote_never_revives_consent(mixed_api):
    client, gateway, _ = mixed_api
    sid, token, _, _ = await prepared(client)
    prefix = f"/api/v1/sessions/{sid}"
    response = await client.post(prefix + "/commands", headers=headers(token), json={
        "client_message_id": str(uuid4()), "expected_version": 0,
        "operations": [{"type": "set_budget", "value": {"amount_minor": 75_000_000}}], "search": True})
    assert response.status_code == 202
    edited_run = await wait(client, response.json()["run_id"], token)
    edited = (await client.get(prefix, headers=headers(token))).json()
    quote_response = await preview(client, sid, token, edited["state_version"], plans(edited_run)[0])
    assert quote_response.status_code == 200, quote_response.text
    quote = quote_response.json()
    response = await client.post(prefix + "/control", headers=headers(token), json={
        "action": "undo", "client_message_id": str(uuid4()), "expected_version": edited["state_version"]})
    assert response.status_code == 202
    await wait(client, response.json()["run_id"], token)
    after = (await client.get(prefix, headers=headers(token))).json()
    assert after["state_version"] == edited["state_version"] + 1
    assert after["trip"]["budget"]["amount_minor"] == 80_000_000
    stale = await client.post(prefix + "/checkout/confirm", json=confirmation(quote), headers=headers(token))
    assert stale.status_code == 409
    assert (await client.get(prefix + "/orders", headers=headers(token))).json()["orders"] == []


@pytest.mark.asyncio
async def test_ux08_disappeared_selected_hotel_requires_alternative_search_and_new_consent(mixed_api):
    """UX-08 backend flow: reject disappearance, offer alternative on explicit re-search, reconfirm.

    This checks API state/checkout and actual fake source IDs, not natural-language
    interpretation or automatic unsolicited re-search after a provider error.
    """
    client, gateway, store = mixed_api
    sid, token, session, plan = await prepared(client)
    first_response = await preview(client, sid, token, session["state_version"], plan)
    assert first_response.status_code == 200
    first_quote = first_response.json()
    hotel = next(o for o in plan["offers"] if o.get("details", {}).get("kind") == "hotel")
    gateway.gone_ids.add(hotel["offer_id"])
    unavailable = await preview(client, sid, token, session["state_version"], plan)
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "OFFER_UNAVAILABLE"
    assert (await store.get_quote(first_quote["quote_id"]))["status"] == "stale"
    gateway.catalog_revision = 2
    gateway.search_delta = 100_000
    response = await client.post(f"/api/v1/sessions/{sid}/search", json={}, headers=headers(token))
    assert response.status_code == 202
    alternative = plans(await wait(client, response.json()["run_id"], token))[0]
    assert alternative["plan_id"] != plan["plan_id"]
    changed_hotel = next(o for o in alternative["offers"] if o.get("details", {}).get("kind") == "hotel")
    assert changed_hotel["details"]["hotel_id"] != hotel["details"]["hotel_id"]
    assert alternative["trip_id"] == plan["trip_id"]
    assert alternative["state_version"] == plan["state_version"]
    assert alternative["total_minor"] == 6_220_000
    assert (await client.get(f"/api/v1/sessions/{sid}/orders", headers=headers(token))).json()["orders"] == []
    next_response = await preview(client, sid, token, session["state_version"], alternative)
    assert next_response.status_code == 200, next_response.text
    old = await client.post(f"/api/v1/sessions/{sid}/checkout/confirm", json=confirmation(first_quote), headers=headers(token))
    assert old.status_code == 409
    confirmed = await client.post(f"/api/v1/sessions/{sid}/checkout/confirm", json=confirmation(next_response.json()), headers=headers(token))
    assert confirmed.status_code == 200
    assert confirmed.json()["booking_created_at_supplier"] is False


@pytest.mark.asyncio
async def test_external_source_expiry_blocks_confirmation_without_supplier_calls(mixed_api, monkeypatch):
    import otravel.store as store_module
    client, gateway, _ = mixed_api
    sid, token, session, plan = await prepared(client)
    response = await preview(client, sid, token, session["state_version"], plan)
    assert response.status_code == 200
    quote = response.json()
    later = datetime.fromisoformat(quote["expires_at"]) + timedelta(seconds=1)
    monkeypatch.setattr(store_module, "_now", lambda: later)
    calls_before = list(gateway.reprice_calls)
    expired = await client.post(f"/api/v1/sessions/{sid}/checkout/confirm", json=confirmation(quote), headers=headers(token))
    assert expired.status_code == 409
    assert gateway.reprice_calls == calls_before
    assert (await client.get(f"/api/v1/sessions/{sid}/orders", headers=headers(token))).json()["orders"] == []


@pytest.mark.asyncio
async def test_concurrent_previews_leave_only_one_active_quote(mixed_api):
    client, _, store = mixed_api
    sid, token, session, plan = await prepared(client)
    responses = await asyncio.gather(*[preview(client, sid, token, session["state_version"], plan) for _ in range(2)])
    assert all(response.status_code == 200 for response in responses)
    quoted = [response.json() for response in responses]
    assert len({quote["quote_id"] for quote in quoted}) == 2
    persisted = [await store.get_quote(quote["quote_id"]) for quote in quoted]
    assert sorted(quote["status"] for quote in persisted) == ["active", "stale"]
    stale = next(quote for quote in persisted if quote["status"] == "stale")
    denied = await client.post(f"/api/v1/sessions/{sid}/checkout/confirm", json=confirmation(stale), headers=headers(token))
    assert denied.status_code == 409
