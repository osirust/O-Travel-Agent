"""Owned place choice priority through real PG/ASGI; controlled external data."""
import os
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.engine import make_url
from test_api import StubLLM, _headers, _session, _snapshot, _wait
from test_place_choice_independent_facts import cached
from test_slot_continuation import _message, _reply

from otravel.api import create_app
from otravel.config import Settings
from otravel.geo_catalog import GeoCatalog
from otravel.place_directory import PlaceDirectory
from otravel.place_resolution import _choice_id
from otravel.store import Store


def fresh_cached():
    """Replay packet shape at a fresh test epoch, not revive source authority.

    Pure tests retain the original absolute TTL/clock. PG uses a new controlled
    packet because Harness's early short-reply path reads the real wall clock.
    The original fixture remains unchanged; the transition must preserve this
    newly displayed packet's TTL and candidate scope without any refresh.
    """
    fixture, before, pending, _source_clock = cached()
    now = datetime.now(UTC)
    packet = pending["context"]["place_resolution"]
    packet["expires_at"] = (now + timedelta(minutes=15)).isoformat()
    for candidate in packet["candidates"]:
        candidate["candidate_id"] = _choice_id(packet, candidate)
    return fixture, before, pending, now


class NoTourHTTP:
    def __init__(self):
        self.calls = []

    async def _forbidden(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("No hotel/flight search is authorized before the unresolved place choice")

    resolve_hotels = search_hotels = search_flights = resolve_air_place = list_countries = weather = _forbidden

    async def close(self):
        pass


@asynccontextmanager
async def place_app(monkeypatch, model, *, now, handler=None):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Only isolated TEST_DATABASE_URL is authorized")
    if make_url(url).database != "otravel_test":
        pytest.fail("Only otravel_test is authorized", pytrace=False)
    repository = Store(url)
    catalog = GeoCatalog(repository.engine)
    wires = []

    def controlled(request):
        wires.append(request)
        if handler is None:
            raise AssertionError("No directory call is authorized while preserving a shown choice")
        return handler(request)

    directory = PlaceDirectory(cache=catalog, transport=httpx.MockTransport(controlled), clock=lambda: now)
    travel = NoTourHTTP()
    settings = Settings(url, "gemini", 1, 8000, 1000000, 1, max_local_llm_calls_per_day=0,
        default_search_mode="HYBRID", default_hotel_provider="liteapi", default_flight_provider="duffel",
        dynamic_geography_enabled=True)
    app = create_app(settings, store=repository, llm=model, travel=travel, geo_catalog=catalog, place_directory=directory)
    app.state.harness.place_resolution.clock = lambda: now
    async def no_foreign_recovery():
        pass
    monkeypatch.setattr(app.state.harness, "start", no_foreign_recovery)
    try:
        async with app.router.lifespan_context(app), httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://place-choice-controlled") as client:
            yield client, app, wires, travel
    finally:
        await repository.close()


async def seed_cached_pending(app, sid, state, pending):
    """Use Store's worker CAS to display one server-owned cached packet."""
    pending = deepcopy(pending)
    pending.update(state_version=state["state_version"], question_id="controlled-cached-place-question")
    packet = pending["context"]["place_resolution"]
    packet.update(trip_id=state["trip"]["trip_id"], state_version=state["state_version"])
    for candidate in packet["candidates"]:
        candidate["candidate_id"] = _choice_id(packet, candidate)
    store = app.state.store
    run, duplicate = await store.intake_message(sid, str(uuid4()), "Controlled displayed place choice", state["state_version"])
    assert not duplicate and await store.claim_run(run["run_id"])
    await store.finish_run(run["run_id"], state["state_version"], None, "WAITING_USER", pending["text"],
        pending_question=pending, waiting_reason="clarification")
    return pending


@pytest.mark.asyncio
async def test_exact_cached_bulk005_date_and_model_checkout_question_keep_place_choice_and_cas(monkeypatch):
    fixture, _before, pending, now = fresh_cached()
    model = StubLLM([fixture["raw_contract"]["value"]])
    async with place_app(monkeypatch, model, now=now) as (client, app, wires, travel):
        sid, token, initial = await _session(client, fixture["before_trip"])
        shown = await seed_cached_pending(app, sid, initial, pending)
        run, after = await _message(client, sid, token, fixture["input"])
        saved = after["pending_question"]
        packet = saved["context"]["place_resolution"]
        assert saved["field"] == "place.resolution" and saved["text"] == shown["text"]
        assert saved["state_version"] == packet["state_version"] == after["state_version"] == 1
        assert packet["expires_at"] == shown["context"]["place_resolution"]["expires_at"]
        assert [row["source_id"] for row in packet["candidates"]] == [292223, 11048853, 8469788]
        assert after["trip"]["stays"][0]["check_in"] == "2026-11-18"
        assert after["trip"]["stays"][0].get("place_id") is None and not after["trip"].get("location_refs")
        assert len(model.calls) == 1 and not wires and not travel.calls
        assert run["result"]["result"]["place_resolution"]["action"] == "maintained_pending"
        assert await app.state.store.list_orders(sid) == []


@pytest.mark.asyncio
async def test_canonical_invalid_date_keeps_choice_and_calendar_reason_without_model_or_search(monkeypatch):
    fixture, _before, pending, now = fresh_cached()
    model = StubLLM([])
    async with place_app(monkeypatch, model, now=now) as (client, app, wires, travel):
        sid, token, initial = await _session(client, fixture["before_trip"])
        shown = await seed_cached_pending(app, sid, initial, pending)
        run, after = await _message(client, sid, token, "Заезд 31.02.2027")
        assert after["trip"] == initial["trip"] and after["state_version"] == initial["state_version"]
        assert after["pending_question"]["field"] == "place.resolution"
        assert after["pending_question"]["context"]["place_resolution"] == shown["context"]["place_resolution"]
        reply = await _reply(client, sid, token, run)
        assert "не существует" in reply and shown["text"] in reply
        assert run["result"]["result"]["independent_notice"]["source"] == "canonical_slot"
        assert not model.calls and not wires and not travel.calls


@pytest.mark.asyncio
async def test_complete_legacy_trip_cannot_search_while_owned_identity_choice_is_outstanding(monkeypatch):
    fixture, _before, pending, now = fresh_cached()
    initial_trip = deepcopy(fixture["before_trip"])
    initial_trip["stays"][0].update(check_in="2026-11-18", check_out="2026-11-24", rooms=[{
        "id": "ready-room", "traveler_ids": [person["id"] for person in initial_trip["travelers"]]}])
    model = StubLLM([{"operations": [{"type": "set_budget", "value": {"amount_minor": 50000000, "currency": "RUB"}}],
        "control": {"action": "continue"}, "speech_act": "CHANGE", "unknowns": ["Подтвердите выбор конкретного места"]}])
    async with place_app(monkeypatch, model, now=now) as (client, app, wires, travel):
        sid, token, initial = await _session(client, initial_trip)
        await seed_cached_pending(app, sid, initial, pending)
        _run, after = await _message(client, sid, token, "Общий бюджет 500 тысяч рублей на всю поездку")
        assert after["trip"]["budget"]["amount_minor"] == 50000000
        assert after["pending_question"]["field"] == "place.resolution"
        assert len(after["pending_question"]["context"]["place_resolution"]["candidates"]) == 3
        assert not wires and not travel.calls and await app.state.store.list_orders(sid) == []


@pytest.mark.asyncio
async def test_typed_date_form_preserves_fresh_choice_and_blocks_requested_search_until_selection(monkeypatch):
    fixture, _before, pending, now = fresh_cached()
    model = StubLLM([])
    async with place_app(monkeypatch, model, now=now) as (client, app, wires, travel):
        sid, token, initial = await _session(client, fixture["before_trip"])
        shown = await seed_cached_pending(app, sid, initial, pending)
        response = await client.post(f"/api/v1/sessions/{sid}/commands", headers=_headers(token), json={
            "client_message_id": str(uuid4()), "expected_version": initial["state_version"], "search": True,
            "operations": [{"type": "upsert_stay", "target_id": "new-stay-1", "value": {"check_in": "2026-11-18"}}]})
        assert response.status_code == 202
        run = await _wait(client, response.json()["run_id"], token)
        assert run["status"] == "completed"
        after = await _snapshot(client, sid, token)
        assert after["trip"]["stays"][0]["check_in"] == "2026-11-18"
        assert after["pending_question"]["field"] == "place.resolution"
        packet = after["pending_question"]["context"]["place_resolution"]
        assert packet["state_version"] == after["state_version"] == 1
        assert packet["expires_at"] == shown["context"]["place_resolution"]["expires_at"]
        assert not model.calls and not wires and not travel.calls
        assert run["result"]["result"]["place_resolution"]["action"] == "maintained_pending"


@pytest.mark.asyncio
async def test_real_controlled_directory_choice_survives_date_then_explicit_selection_keeps_saved_date(monkeypatch):
    fixture, _before, _pending, _clock = cached()
    now = datetime.now(UTC)
    # Isolate the controlled directory keys/IDs from append-only test DB cache.
    # The separate cached fixture test retains actual BULK005 source identities.
    selected_id = 1_000_000_000 + uuid4().int % 900_000_000
    query = "Дубай проверка-" + uuid4().hex
    rows = [{"id": identifier, "name": name, "country_code": "AE", "country": "ОАЭ",
        "latitude": latitude, "longitude": longitude, "timezone": "Asia/Dubai", "feature_code": "PPL"}
        for identifier, name, latitude, longitude in [(selected_id, "Дубай", 25.0657, 55.17128),
            (selected_id + 1, "Дубаи Инвестменц Парк", 24.98, 55.18), (selected_id + 2, "Дубай Марина", 25.08, 55.14)]]

    def handler(request):
        assert request.url.host == "geocoding-api.open-meteo.com"
        if request.url.path == "/v1/search":
            return httpx.Response(200, json={"results": rows})
        assert request.url.path == "/v1/get" and request.url.params["id"] == str(selected_id)
        return httpx.Response(200, json={**rows[0], "name": "Dubai", "country": "United Arab Emirates"})

    model = StubLLM([fixture["raw_contract"]["value"]])
    async with place_app(monkeypatch, model, now=now, handler=handler) as (client, app, wires, travel):
        sid, token, initial = await _session(client, fixture["before_trip"])
        response = await client.post(f"/api/v1/sessions/{sid}/places/resolve", headers=_headers(token), json={
            "role": "destination", "kind": "city", "query": query, "stay_id": "new-stay-1",
            "expected_version": initial["state_version"], "client_message_id": str(uuid4())})
        assert response.status_code == 202
        assert (await _wait(client, response.json()["run_id"], token))["status"] == "completed"
        shown = await _snapshot(client, sid, token)
        assert len(shown["pending_question"]["context"]["place_resolution"]["candidates"]) == 3
        directory_calls = len(wires)
        _run, dated = await _message(client, sid, token, fixture["input"])
        assert dated["pending_question"]["field"] == "place.resolution" and len(wires) == directory_calls
        _run, selected = await _message(client, sid, token, "1")
        stay = selected["trip"]["stays"][0]
        assert stay["place_id"] == f"geo:openmeteo:{selected_id}" and stay["city"] == "Dubai"
        assert stay["check_in"] == "2026-11-18" and stay["check_out"] is None
        assert selected["pending_question"]["field"] == "stay.check_out"
        assert len(model.calls) == 1 and len(wires) > directory_calls and not travel.calls
        assert await app.state.store.list_orders(sid) == []
