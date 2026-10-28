"""UI preview identity context; real isolated PG, no model/vendor inference."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import update
from test_api import StubLLM, _headers, _session, _snapshot, _wait
from test_api import api_factory as selected_api_factory

from otravel.selected_plan import project_current_selection
from otravel.store import quotes

api_factory = selected_api_factory
TEXT = "Замени только второй отель, первый сохрани."


def initial_trip():
    return {"origin": "Москва", "search_policy": {"mode": "DEMO"},
        "travelers": [{"id": "adult", "type": "adult"}],
        "stays": [{"id": "ist", "city": "Стамбул", "check_in": "2026-11-10", "check_out": "2026-11-13",
            "rooms": [{"id": "ist-room", "traveler_ids": ["adult"]}]},
            {"id": "ayt", "city": "Анталья", "check_in": "2026-11-13", "check_out": "2026-11-17",
            "rooms": [{"id": "ayt-room", "traveler_ids": ["adult"]}]}],
        "budget": {"amount_minor": 100_000_000, "currency": "RUB"}}


async def prepare_third_plan(client, model):
    sid, token, created = await _session(client, initial_trip())
    base = f"/api/v1/sessions/{sid}"
    accepted = await client.post(base + "/search", headers=_headers(token), json={})
    run = await _wait(client, accepted.json()["run_id"], token)
    plans = run["result"]["result"]["plans"]
    assert len(plans) == 3
    first_hotels = [o["hotel_id"] for o in plans[0]["offers"] if o.get("kind") == "hotel"]
    selected_hotels = [o["hotel_id"] for o in plans[2]["offers"] if o.get("kind") == "hotel"]
    assert first_hotels != selected_hotels
    response = await client.post(base + "/checkout/preview", headers=_headers(token),
        json={"plan_id": plans[2]["plan_id"], "state_version": created["state_version"]})
    assert response.status_code == 200
    quote = response.json()
    store = client._transport.app.state.store
    current = await store.get_session(sid)
    assert all(stay["hotel_id"] is None for stay in current["state"]["stays"])
    assert model.calls == [] and await store.list_orders(sid) == []
    return sid, token, plans, quote, store, current


async def context_without_model(client, store, current):
    # Genuine persisted intake, but deliberately not scheduled for extraction.
    run, _ = await store.intake_message(current["session_id"], str(uuid4()), TEXT, current["state_version"])
    try:
        return await client._transport.app.state.harness._extraction_context(current, run)
    finally:
        await store.fail_run(run["run_id"], {"code": "context_probe_complete", "message": "Controlled context probe complete."}, preserve_session=True)


@pytest.mark.asyncio
async def test_preview_third_plan_is_durable_without_pinning_hotel_preferences(api_factory):
    model = StubLLM()
    async with api_factory(model) as (client, _):
        sid, token, plans, quote, store, current = await prepare_third_plan(client, model)
        saved = await store.get_quote(quote["quote_id"])
        assert saved["status"] == "active" and saved["plan"]["plan_id"] == plans[2]["plan_id"]
        assert saved["state_version"] == current["state_version"]
        selected = await store.current_selected_plan(sid, current["state_version"])
        assert selected["plan_id"] == plans[2]["plan_id"] and selected["source"] == "ui_preview_selection"
        assert (await _snapshot(client, sid, token))["trip"] == current["state"]
        assert model.calls == []


@pytest.mark.asyncio
async def test_extraction_context_exposes_ui_third_plan_selection_not_historical_first_card(api_factory):
    model = StubLLM()
    async with api_factory(model) as (client, _):
        sid, _, plans, quote, store, current = await prepare_third_plan(client, model)
        before = deepcopy(current)
        context = await context_without_model(client, store, current)
        assert context["first_shown_plan"]["plan_id"] == plans[0]["plan_id"]
        selected = context.get("current_selected_plan")
        assert isinstance(selected, dict), "UI-selected third preview is absent from extraction context"
        assert selected["plan_id"] == plans[2]["plan_id"]
        expected = [o["hotel_id"] for o in plans[2]["offers"] if o.get("kind") == "hotel"]
        assert [hotel["hotel_id"] for hotel in selected["hotels"]] == expected
        assert selected["reference_only"] is True and selected["prices_included"] is False
        assert not {"quote_id", "quote_version", "consent", "payment", "total_minor", "expires_at"}.intersection(selected)
        assert model.calls == [] and await store.list_orders(sid) == []
        assert (await store.get_session(sid))["state"] == before["state"]
        assert (await store.get_quote(quote["quote_id"]))["status"] == "active"


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["expired", "revoked", "ordered", "future_plan", "ambiguous_plan_version",
    "foreign_trip", "empty_offers", "unknown_stay", "wrong_city", "wrong_dates", "conflicting_hotel_id", "malformed_source"])
async def test_current_selection_rejects_stale_foreign_or_malformed_rows(api_factory, mutation):
    model = StubLLM()
    async with api_factory(model) as (client, _):
        sid, _, _, quote, store, current = await prepare_third_plan(client, model)
        row_changes = {}
        plan = deepcopy(quote["plan"])
        hotel = next(offer for offer in plan["offers"] if offer.get("kind") == "hotel")
        if mutation == "expired":
            row_changes["expires_at"] = datetime.now(UTC) - timedelta(seconds=1)
        elif mutation in {"revoked", "ordered"}:
            row_changes["status"] = mutation
        elif mutation == "future_plan":
            plan["state_version"] += 1
        elif mutation == "ambiguous_plan_version":
            plan["state_version"] = float(plan["state_version"])
        elif mutation == "foreign_trip":
            plan["trip_id"] = str(uuid4())
        elif mutation == "empty_offers":
            plan["offers"] = []
        elif mutation == "unknown_stay":
            hotel["component_id"] = "not-owned-stay"
        elif mutation == "wrong_city":
            hotel["city"] = "Дубай"
        elif mutation == "wrong_dates":
            hotel["check_in"] = "2026-11-11"
        elif mutation == "conflicting_hotel_id":
            hotel["details"] = {"hotel_id": "different-hotel"}
        elif mutation == "malformed_source":
            plan["source_type"] = []
        if not row_changes:
            row_changes["plan"] = plan
        async with store.engine.begin() as conn:
            await conn.execute(update(quotes).where(quotes.c.id == quote["quote_id"]).values(**row_changes))
        before = deepcopy(await store.get_quote(quote["quote_id"]))
        assert await store.current_selected_plan(sid, current["state_version"]) is None
        assert await store.get_quote(quote["quote_id"]) == before
        assert model.calls == [] and await store.list_orders(sid) == []


@pytest.mark.asyncio
async def test_latest_revoked_preview_does_not_resurrect_older_active_selection(api_factory):
    model = StubLLM()
    async with api_factory(model) as (client, _):
        sid, _, plans, earlier, store, current = await prepare_third_plan(client, model)
        latest = await store.create_quote(sid, current["state_version"], plans[0])
        async with store.engine.begin() as conn:
            await conn.execute(update(quotes).where(quotes.c.id == earlier["quote_id"]).values(status="active"))
            await conn.execute(update(quotes).where(quotes.c.id == latest["quote_id"]).values(status="revoked"))
        assert await store.current_selected_plan(sid, current["state_version"]) is None
        assert (await store.get_quote(earlier["quote_id"]))["status"] == "active"
        assert model.calls == []


@pytest.mark.asyncio
async def test_other_sessions_selection_is_not_visible_and_new_state_never_revives_it(api_factory):
    model = StubLLM()
    async with api_factory(model) as (client, _):
        sid, _, plans, _, store, current = await prepare_third_plan(client, model)
        other_sid, _, other_plans, _, _, other = await prepare_third_plan(client, model)
        assert sid != other_sid and plans[2]["plan_id"] != other_plans[2]["plan_id"]
        assert (await store.current_selected_plan(sid, current["state_version"]))["plan_id"] == plans[2]["plan_id"]
        assert (await store.current_selected_plan(other_sid, other["state_version"]))["plan_id"] == other_plans[2]["plan_id"]
        changed = await store.set_control(sid, "pause")
        assert await store.current_selected_plan(sid, current["state_version"]) is None
        assert await store.current_selected_plan(sid, changed["state_version"]) is None
        assert model.calls == []


def test_pure_projection_rejects_foreign_quote_owner_and_never_returns_checkout_data():
    now = datetime.now(UTC)
    state = initial_trip()
    state.update(trip_id="trip", state_version=0)
    session = {"session_id": "owner", "trip_id": "trip", "state_version": 0, "state": state}
    plan = {"trip_id": "trip", "state_version": 0, "plan_id": "chosen3", "source_type": "DEMO", "offers": [
        {"kind": "hotel", "component_id": stay["id"], "hotel_id": "hotel-" + stay["id"], "city": stay["city"],
            "check_in": stay["check_in"], "check_out": stay["check_out"], "source_type": "DEMO", "name": "Reference hotel"}
        for stay in state["stays"]]}
    quote = {"session_id": "foreign", "state_version": 0, "status": "active", "expires_at": now + timedelta(minutes=5), "plan": plan}
    assert project_current_selection(session, quote, 0, now) is None
    quote["session_id"] = "owner"
    result = project_current_selection(session, quote, 0, now)
    assert result is not None and set(result) == {"source", "selection_status", "trip_id", "plan_id", "state_version", "source_type", "hotels", "reference_only", "prices_included"}
    assert result['trip_id'] == session['trip_id']


@pytest.mark.parametrize("mutation", [None, "foreign_binding", "stale_binding", "ambiguous_binding_version", "conflicting_identity"])
def test_mixed_provider_projection_uses_owned_bindings_and_rejects_ambiguous_hotels(mutation):
    now = datetime.now(UTC)
    state = initial_trip()
    state.update(trip_id="trip", state_version=2)
    session = {"session_id": "owner", "trip_id": "trip", "state_version": 2, "state": state}
    offers = [{"provider": "liteapi", "source_type": "SANDBOX", "description": "Test supplier hotel", "details": {
        "kind": "hotel", "hotel_id": "lp-" + stay["id"], "checkin": stay["check_in"], "checkout": stay["check_out"],
        "binding": {"stay_id": stay["id"], "trip_id": "trip", "state_version": 2, "hotel_id": "lp-" + stay["id"],
            "city": stay["city"], "checkin": stay["check_in"], "checkout": stay["check_out"]}}} for stay in state["stays"]]
    if mutation == "foreign_binding":
        offers[0]["details"]["binding"]["trip_id"] = "another-trip"
    elif mutation == "stale_binding":
        offers[0]["details"]["binding"]["state_version"] = 1
    elif mutation == "ambiguous_binding_version":
        offers[0]["details"]["binding"]["state_version"] = 2.0
    elif mutation == "conflicting_identity":
        offers.append(deepcopy(offers[0]))
        offers[-1]["details"]["hotel_id"] = "other-hotel"
        offers[-1]["details"]["binding"]["hotel_id"] = "other-hotel"
    plan = {"trip_id": "trip", "state_version": 2, "plan_id": "chosen3", "source_type": "MIXED", "offers": offers}
    quote = {"session_id": "owner", "state_version": 2, "status": "active", "expires_at": now + timedelta(minutes=5), "plan": plan}
    result = project_current_selection(session, quote, 2, now)
    if mutation:
        assert result is None
    else:
        assert result["source_type"] == "MIXED"
        assert [row["hotel_id"] for row in result["hotels"]] == ["lp-ist", "lp-ayt"]
        assert all(row["name"] == "Test supplier hotel" and row["source_type"] == "SANDBOX" for row in result["hotels"])
