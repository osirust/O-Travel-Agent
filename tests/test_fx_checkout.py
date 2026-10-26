"""Controlled reference-FX checkout: isolated PostgreSQL + ASGI, no vendor HTTP."""
import json
import os
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from test_mixed_checkout import CheckoutTravel, confirmation, headers, plans, preview, wait

from otravel.config import Settings
from otravel.domain import TripState
from otravel.fx import FxUnavailable, parse_cbr_xml
from otravel.store import Store, StoreError


class NoModel:
    def __init__(self):
        self.calls = 0

    async def extract_operations(self, context):
        self.calls += 1
        raise AssertionError("FX checkout must not call an LLM")

    async def close(self):
        pass


class NativeCurrencyTravel(CheckoutTravel):
    def __init__(self):
        super().__init__()
        self.issued = []

    def offer(self, request, provider, kind):
        offer = super().offer(request, provider, kind)
        offer["source_type"] = "SANDBOX"
        offer["amount_minor"] = 1_000_000 if kind == "hotel" else 1000
        offer["currency"] = "RUB" if kind == "hotel" else "EUR"
        offer["details"].update(mandatory_fees_known=True, capacity_verified=True, group_quote=True)
        if kind == "flight":
            offer["details"]["received_passengers_count"] = len(request["passengers"])
        self.issued.append(deepcopy(offer))
        return offer


class ReferenceRates:
    def __init__(self):
        self.eur = 100
        self.calls = 0
        self.mode = "valid"
        self.snapshots = []

    async def fetch_snapshot(self, now):
        from zoneinfo import ZoneInfo
        self.calls += 1
        if self.mode == "unavailable":
            raise FxUnavailable("fx_source_unavailable", "Controlled FX source unavailable")
        reference = now - timedelta(seconds=121) if self.mode == "expired" else now
        day = reference.astimezone(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y")
        xml = (f'<ValCurs Date="{day}"><Valute><CharCode>USD</CharCode><Nominal>1</Nominal><Value>80,00</Value></Valute>'
               f'<Valute><CharCode>EUR</CharCode><Nominal>1</Nominal><Value>{self.eur},00</Value></Valute></ValCurs>').encode()
        snapshot = parse_cbr_xml(xml, reference, ttl_seconds=120)
        if self.mode == "tampered":
            snapshot = json.loads(json.dumps(snapshot))
            snapshot["rates_rub_per_unit"]["EUR"] = "999"
        self.snapshots.append(deepcopy(snapshot))
        return snapshot

    async def close(self):
        pass


def group_trip():
    return TripState(origin="Казань", travelers=[
        {"id": "a", "type": "adult", "group_id": "family-a"}, {"id": "ca", "type": "child", "age": 7, "group_id": "family-a"},
        {"id": "b", "type": "adult", "group_id": "family-b"}, {"id": "cb", "type": "child", "age": 12, "group_id": "family-b"}],
        groups=[{"id": "family-a", "traveler_ids": ["a", "ca"], "keep_separate": True},
                {"id": "family-b", "traveler_ids": ["b", "cb"], "keep_separate": True}],
        stays=[{"id": "ist", "city": "Стамбул", "check_in": "2026-11-10", "check_out": "2026-11-13", "rooms": [
                    {"id": "ist-a", "traveler_ids": ["a", "ca"]}, {"id": "ist-b", "traveler_ids": ["b", "cb"]}]},
               {"id": "ayt", "city": "Анталья", "check_in": "2026-11-13", "check_out": "2026-11-17", "rooms": [
                    {"id": "ayt-a", "traveler_ids": ["a", "ca"]}, {"id": "ayt-b", "traveler_ids": ["b", "cb"]}]}],
        legs=[{"id": "out", "origin": "Казань", "destination": "Стамбул", "departure_date": "2026-11-10"},
              {"id": "between", "origin": "Стамбул", "destination": "Анталья", "departure_date": "2026-11-13"},
              {"id": "back", "origin": "Анталья", "destination": "Казань", "departure_date": "2026-11-17"}],
        budget={"amount_minor": 50_000_000, "currency": "RUB", "scope": "whole_trip"},
        search_policy={"mode": "HYBRID", "hotel_provider": "liteapi", "flight_provider": "duffel", "guest_nationality": "RU"})


@pytest_asyncio.fixture
async def fx_api(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not configured; no DB or app contacted")
    if make_url(url).database != "otravel_test":
        pytest.fail("FX integration requires the explicit isolated otravel_test database", pytrace=False)
    import test_mixed_checkout as mixed_module

    from otravel import api as api_module
    from otravel import harness as harness_module
    from otravel import orchestrator as orchestrator_module
    from otravel import store as store_module
    clock = SimpleNamespace(now=datetime.now(UTC).replace(microsecond=0))
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            current = clock.now
            value = cls(current.year, current.month, current.day, current.hour, current.minute, current.second,
                        current.microsecond, tzinfo=current.tzinfo)
            return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)
    for module in (api_module, harness_module, orchestrator_module, mixed_module):
        monkeypatch.setattr(module, "datetime", FixedDatetime)
    monkeypatch.setattr(store_module, "_now", lambda: clock.now)
    store, travel, rates, model = Store(url), NativeCurrencyTravel(), ReferenceRates(), NoModel()
    app = api_module.create_app(Settings(url, "gemini", 1, 8000, 1_000_000, 2, reference_fx_enabled=True),
                                store=store, llm=model, travel=travel, fx=rates)
    async def owned_start_only():
        # Do not replay unrelated historical pending fixtures in the shared
        # isolated test DB. Fresh HTTP intake still uses the real worker/claim.
        pass
    monkeypatch.setattr(app.state.harness, "start", owned_start_only)
    try:
        async with app.router.lifespan_context(app), httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield SimpleNamespace(client=client, store=store, travel=travel, fx=rates, model=model, clock=clock)
    except (SQLAlchemyError, OSError):
        pytest.fail("FX integration infrastructure failed; private details suppressed", pytrace=False)
    finally:
        await store.close()


def native_pairs(offers):
    return {o["offer_id"]: json.dumps({"amount_minor": o["amount_minor"], "currency": o["currency"]}, sort_keys=True).encode()
            for o in offers if o.get("source_type") != "DEMO"}


async def searched(context):
    response = await context.client.post("/api/v1/sessions", json={"initial_trip": group_trip().model_dump(mode="json")})
    assert response.status_code == 201
    session = response.json()
    sid, token = session["session_id"], session["session_token"]
    accepted = await context.client.post(f"/api/v1/sessions/{sid}/search", json={}, headers=headers(token))
    assert accepted.status_code == 202
    run = await wait(context.client, accepted.json()["run_id"], token)
    selected = plans(run)
    assert selected, run["result"]["result"]
    return sid, token, session, run, selected[0]


@pytest.mark.asyncio
async def test_reference_fx_search_fresh_preview_and_explicit_idempotent_simulated_order(fx_api):
    c = fx_api
    sid, token, session, run, plan = await searched(c)
    inventory = run["result"]["result"]
    assert inventory["tool_calls_used"] == 8  # FX + 2 lookup + 2 hotel rates + 3 flight searches.
    assert len(plan["coverage"]["required"]) == len(plan["coverage"]["covered"]) == 9
    assert plan["coverage"]["complete"] is True and not plan["blockers"]
    assert native_pairs(plan["offers"]) == native_pairs(c.travel.issued)
    flights = [o for o in plan["offers"] if o.get("details", {}).get("kind") == "flight"]
    assert len(flights) == 3 and all(o["currency"] == "EUR" and o["amount_minor"] == 1000 for o in flights)
    projected_flights = [component for component in plan["components"] if component["currency"] == "EUR"]
    assert len(projected_flights) == 3
    assert all(component["amount_minor"] == 1000 and component["budget_currency"] == "RUB"
               and component["budget_amount_minor"] == 100_000 for component in projected_flights)
    assert plan["currency_totals"]["EUR"] == 3000
    assert plan["total_minor"] == plan["currency_totals"]["RUB"] + 300_000
    assert plan["fx_reference_only"] is True and plan["fx_fees_unknown"] is True
    assert plan["ready_for_real_booking"] is False and plan["ready_for_simulated_checkout"] is True
    events = await c.store.list_events(sid)
    assert sum(e["event_type"] == "TOOL_CALL_STARTED" for e in events) == 8
    assert not any(e["event_type"] == "MODEL_CALL_STARTED" for e in events) and c.model.calls == 0
    c.fx.eur = 110
    c.clock.now += timedelta(seconds=1)
    response = await preview(c.client, sid, token, session["state_version"], plan)
    assert response.status_code == 200, response.text
    quote = response.json()
    assert quote["plan"]["fx_snapshot_id"] != plan["fx_snapshot_id"]
    assert quote["plan"]["fx_snapshot"]["rates_rub_per_unit"]["EUR"] == "110"
    assert quote["plan"]["comparison"]["delta_minor"] == 30_000
    assert quote["plan"]["total_minor"] == plan["total_minor"] + 30_000
    assert all(component["amount_minor"] == 1000 and component["budget_amount_minor"] == 110_000
               for component in quote["plan"]["components"] if component["currency"] == "EUR")
    assert native_pairs(quote["plan"]["offers"]) == native_pairs(c.travel.issued)
    expiry = datetime.fromisoformat(quote["expires_at"])
    assert expiry == datetime.fromisoformat(quote["plan"]["fx_snapshot"]["expires_at"])
    assert expiry <= min(datetime.fromisoformat(s["expires_at"]) for s in quote["plan"]["source_snapshots"] if s["source_type"] != "DEMO")
    payload = confirmation(quote)
    refused = await c.client.post(f"/api/v1/sessions/{sid}/checkout/confirm", json={**payload, "confirmed": False}, headers=headers(token))
    assert refused.status_code == 409
    first = await c.client.post(f"/api/v1/sessions/{sid}/checkout/confirm", json=payload, headers=headers(token))
    repeated = await c.client.post(f"/api/v1/sessions/{sid}/checkout/confirm", json=payload, headers=headers(token))
    assert first.status_code == repeated.status_code == 200 and first.json()["order_id"] == repeated.json()["order_id"]
    assert first.json()["is_demo"] is True and first.json()["booking_created_at_supplier"] is False
    assert first.json()["plan"] == quote["plan"]
    assert len(await c.store.list_orders(sid)) == 1 and c.fx.calls == 2 and len(c.travel.reprice_calls) == 5


@pytest.mark.asyncio
async def test_failed_fx_preview_revokes_previous_consent_and_preserves_state_and_selected_plan(fx_api):
    c = fx_api
    sid, token, session, run, plan = await searched(c)
    previous = (await preview(c.client, sid, token, session["state_version"], plan)).json()
    saved_run = deepcopy(await c.store.get_run(run["run_id"]))
    before = (await c.client.get(f"/api/v1/sessions/{sid}", headers=headers(token))).json()
    c.fx.mode = "unavailable"
    calls_before = len(c.travel.reprice_calls)
    failed = await preview(c.client, sid, token, session["state_version"], plan)
    assert failed.status_code == 503 and failed.json()["error"]["code"] == "fx_source_unavailable"
    after = (await c.client.get(f"/api/v1/sessions/{sid}", headers=headers(token))).json()
    assert after == before and (await c.store.get_run(run["run_id"])) == saved_run
    assert (await c.store.get_quote(previous["quote_id"]))["status"] == "stale"
    refused = await c.client.post(f"/api/v1/sessions/{sid}/checkout/confirm", json=confirmation(previous), headers=headers(token))
    assert refused.status_code == 409 and len(c.travel.reprice_calls) == calls_before
    assert c.fx.calls == 3 and await c.store.list_orders(sid) == [] and c.model.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["expired", "tampered"])
async def test_invalid_fresh_fx_snapshot_blocks_new_quote_and_old_consent(mode, fx_api):
    c = fx_api
    sid, token, session, _, plan = await searched(c)
    previous = (await preview(c.client, sid, token, session["state_version"], plan)).json()
    before = (await c.client.get(f"/api/v1/sessions/{sid}", headers=headers(token))).json()
    c.fx.mode = mode
    failed = await preview(c.client, sid, token, session["state_version"], plan)
    assert failed.status_code in {409, 422, 503}, failed.text
    assert failed.json()["error"]["code"].startswith("fx_"), failed.text
    assert (await c.store.get_quote(previous["quote_id"]))["status"] == "stale"
    assert (await c.client.get(f"/api/v1/sessions/{sid}", headers=headers(token))).json() == before
    refused = await c.client.post(f"/api/v1/sessions/{sid}/checkout/confirm", json=confirmation(previous), headers=headers(token))
    assert refused.status_code == 409 and await c.store.list_orders(sid) == []


@pytest.mark.asyncio
async def test_fx_expiry_blocks_confirmation_without_any_refresh_inside_confirm(fx_api):
    c = fx_api
    sid, token, session, _, plan = await searched(c)
    quote = (await preview(c.client, sid, token, session["state_version"], plan)).json()
    c.clock.now = datetime.fromisoformat(quote["expires_at"]) + timedelta(seconds=1)
    counts = c.fx.calls, len(c.travel.reprice_calls)
    refused = await c.client.post(f"/api/v1/sessions/{sid}/checkout/confirm", json=confirmation(quote), headers=headers(token))
    assert refused.status_code == 409 and await c.store.list_orders(sid) == []
    assert (c.fx.calls, len(c.travel.reprice_calls)) == counts


@pytest.mark.asyncio
async def test_store_rejects_tampered_fx_before_persisting_a_new_quote(fx_api):
    c = fx_api
    sid, _, session, _, plan = await searched(c)
    bad = deepcopy(plan)
    bad["fx_snapshot"]["source_quotes"]["EUR"]["value_rub"] = "999"
    with pytest.raises(StoreError) as error:
        await c.store.create_quote(sid, session["state_version"], bad)
    assert error.value.code.startswith("fx_")
    assert await c.store.list_orders(sid) == [] and c.fx.calls == 1 and c.travel.reprice_calls == []
