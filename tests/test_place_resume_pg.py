"""Own PostgreSQL sessions and controlled sources only; zero external HTTP."""
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import update
from sqlalchemy.engine import make_url
from test_api import StubLLM, _headers, _session, _snapshot, _wait
from test_dynamic_place_conversation import world_app
from test_place_run import ask, selection
from test_place_run_pg import PGHarness, claimed, own_session, shown_pending

from otravel.domain import DomainError, Operation, TripState
from otravel.geo_catalog import GeoCatalog
from otravel.place_directory import MemoryPlaceCache
from otravel.place_resume import resume_deferred_place_run, resume_marker
from otravel.place_run import finish_place_run
from otravel.store import Store, sessions


@pytest_asyncio.fixture
async def repository():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("An isolated TEST_DATABASE_URL is required")
    if make_url(url).database != "otravel_test":
        pytest.fail("Only otravel_test is allowed")
    store = Store(url)
    await store.initialize()
    catalog = GeoCatalog(store.engine)
    await catalog.initialize()
    try:
        yield store, catalog
    finally:
        await store.close()


def _asked(_request, _pending, state):
    pending = ask(state)
    pending["context"]["place_resolution"]["expires_at"] = (datetime.now(UTC) + timedelta(minutes=15)).isoformat()
    return {"action": "ask", "question": pending["text"], "pending_context": pending["context"],
        "operations": [], "resolved_places": {}}


async def _deferred(store, catalog, *, selection_pending=None):
    session = await own_session(store)
    if selection_pending is not None:
        selection_pending["context"]["place_resolution"]["trip_id"] = session["trip_id"]
        from otravel.place_resolution import _choice_id
        packet = selection_pending["context"]["place_resolution"]
        for candidate in packet["candidates"]:
            candidate["candidate_id"] = _choice_id(packet, candidate)
        session = await shown_pending(store, session, selection_pending)
    session, run, _ = await claimed(store, session["session_id"])
    harness = PGHarness(store, catalog, [], datetime.now(UTC))
    await finish_place_run(harness, session, run, TripState.model_validate(session["state"]), control="wait",
        request=None if selection_pending else {"role": "destination", "kind": "city", "query": "Buenos Aires", "stay_id": "stay-istanbul"},
        selection=selection(selection_pending) if selection_pending else None)
    paused = await store.get_session(session["session_id"])
    assert paused["waiting_reason"] == "user_pause" and not harness.place_resolution.calls
    return paused


@pytest.mark.asyncio
async def test_resume_queue_survives_repository_restart_and_follows_independent_paused_facts(repository):
    store, catalog = repository
    paused = await _deferred(store, catalog)
    addition, _ = await store.intake_message(paused["session_id"], str(uuid4()), "Add saved budget", paused["state_version"])
    assert not await store.claim_run(addition["run_id"])
    resumed = await store.set_control(paused["session_id"], "resume")
    marker_id = resumed["resume_run_id"]
    pending = [run for run in await store.list_pending_runs() if run["session_id"] == paused["session_id"]]
    assert [run["run_id"] for run in pending] == [addition["run_id"], marker_id]
    restarted = Store(os.environ["TEST_DATABASE_URL"])
    try:
        assert (await restarted.get_place_resume_marker(marker_id))["session_id"] == paused["session_id"]
        assert await restarted.claim_run(addition["run_id"])
        current = await restarted.get_session(paused["session_id"])
        h = PGHarness(restarted, GeoCatalog(restarted.engine), [], datetime.now(UTC))
        await finish_place_run(h, current, await restarted.get_run(addition["run_id"]), TripState.model_validate(current["state"]),
            operations=[Operation(type="set_budget", value={"amount_minor": 7000000})])
        assert not h.place_resolution.calls
        assert await restarted.claim_run(marker_id)
        current = await restarted.get_session(paused["session_id"])
        h = PGHarness(restarted, GeoCatalog(restarted.engine), [_asked], datetime.now(UTC))
        await resume_deferred_place_run(h, current, await restarted.get_run(marker_id), current["state"])
        after = await restarted.get_session(paused["session_id"])
        assert after["state"]["budget"]["amount_minor"] == 7000000
        assert len(h.place_resolution.calls) == 1 and h.place_resolution.calls[0]["method"] == "resolve"
        assert "place_resolution" in after["pending_question"]["context"]
        assert "place_deferred_action" not in after["pending_question"]["context"]
        assert await restarted.list_orders(paused["session_id"]) == []
    finally:
        await restarted.close()


@pytest.mark.asyncio
async def test_repeated_controls_supersede_old_marker_and_completed_retry_has_zero_source_calls(repository):
    store, catalog = repository
    paused = await _deferred(store, catalog)
    first = await store.set_control(paused["session_id"], "resume")
    await store.set_control(paused["session_id"], "pause")
    latest = await store.set_control(paused["session_id"], "resume")
    assert await store.claim_run(first["resume_run_id"])
    current = await store.get_session(paused["session_id"])
    h = PGHarness(store, catalog, [], datetime.now(UTC))
    await resume_deferred_place_run(h, current, await store.get_run(first["resume_run_id"]), current["state"])
    assert not h.place_resolution.calls
    assert await store.claim_run(latest["resume_run_id"])
    current = await store.get_session(paused["session_id"])
    h = PGHarness(store, catalog, [_asked], datetime.now(UTC))
    await resume_deferred_place_run(h, current, await store.get_run(latest["resume_run_id"]), current["state"])
    await resume_deferred_place_run(h, await store.get_session(paused["session_id"]),
        await store.get_run(latest["resume_run_id"]), current["state"])
    assert len(h.place_resolution.calls) == 1


@pytest.mark.asyncio
async def test_selection_expiring_after_resume_is_reasked_without_source_calls(repository):
    store, catalog = repository
    # The query/selection is a declared source fixture, not real geocoder proof.
    from otravel.domain import sample_trip
    pending = ask(sample_trip())
    pending["context"]["place_resolution"]["expires_at"] = (datetime.now(UTC) + timedelta(minutes=15)).isoformat()
    paused = await _deferred(store, catalog, selection_pending=pending)
    resumed = await store.set_control(paused["session_id"], "resume")
    assert await store.claim_run(resumed["resume_run_id"])
    current = await store.get_session(paused["session_id"])
    h = PGHarness(store, catalog, [], datetime.now(UTC)+timedelta(minutes=16))
    await resume_deferred_place_run(h, current, await store.get_run(resumed["resume_run_id"]), current["state"])
    after = await store.get_session(paused["session_id"])
    assert not h.place_resolution.calls and not h.budget_calls and after["state"] == current["state"]
    assert after["pending_question"]["field"] == "place.city_query" and "устарел" in after["pending_question"]["text"]
    assert await store.list_orders(paused["session_id"]) == []


@pytest.mark.asyncio
async def test_user_created_typed_marker_has_no_server_provenance(repository):
    store, catalog = repository
    session = await own_session(store)
    run_id = str(uuid4())
    text = resume_marker(session["trip_id"], str(uuid4()), run_id)
    accepted, _ = await store.intake_message(session["session_id"], "place-resume:"+run_id, text, 0)
    assert await store.get_place_resume_marker(accepted["run_id"]) is None
    assert await store.claim_run(accepted["run_id"])
    current = await store.get_session(session["session_id"])
    h = PGHarness(store, catalog, [], datetime.now(UTC))
    with pytest.raises(DomainError, match="сервером"):
        await resume_deferred_place_run(h, current, await store.get_run(accepted["run_id"]), current["state"])
    await store.fail_run(accepted["run_id"], {"code": "controlled_rejected_marker", "message": "Controlled invalid marker"}, preserve_session=True)
    assert not h.place_resolution.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["candidate", "epoch", "trip"])
async def test_corrupted_historical_deferred_authority_is_reasked_at_resume_without_queue(repository, damage):
    from copy import deepcopy

    from otravel.domain import sample_trip
    store, catalog = repository
    pending = ask(sample_trip())
    pending["context"]["place_resolution"]["expires_at"] = (datetime.now(UTC)+timedelta(minutes=15)).isoformat()
    paused = await _deferred(store, catalog, selection_pending=pending)
    broken = deepcopy(paused["pending_question"])
    deferred = broken["context"]["place_deferred_action"]
    if damage == "candidate":
        deferred["pending_question"]["context"]["place_resolution"]["candidates"][0]["fact_sha256"] = "f"*64
    elif damage == "epoch":
        deferred["state_version"] += 1
    else:
        deferred["trip_id"] = str(uuid4())
    # Simulate damaged persisted data in this test's own row, not a public input.
    async with store.engine.begin() as conn:
        await conn.execute(update(sessions).where(sessions.c.session_id == paused["session_id"]).values(pending_question=broken))
    resumed = await store.set_control(paused["session_id"], "resume")
    assert "resume_run_id" not in resumed and resumed["waiting_reason"] == "clarification"
    assert resumed["pending_question"]["field"] == "place.city_query" and "устарел" in resumed["pending_question"]["text"]
    assert not [run for run in await store.list_pending_runs() if run["session_id"] == paused["session_id"]]
    assert await store.list_orders(paused["session_id"]) == []


@pytest.mark.asyncio
async def test_country_bound_resolve_resume_keeps_the_original_country_source_and_epoch(repository):
    store, catalog = repository
    session = await own_session(store)
    now = datetime.now(UTC)
    h = PGHarness(store, catalog, [], now)
    state = TripState.model_validate(session["state"])
    request = {"role": "destination", "stay_id": "stay-istanbul", "query": "Argentina"}
    metadata = {"source_sha256": "b"*64, "retrieved_at": now.isoformat(), "expires_at": (now+timedelta(days=1)).isoformat()}
    country = h.place_resolution._country_ref(state.model_dump(mode="json"), request,
        {"country_iso2": "AR", "name": "Argentina"}, metadata)
    packet = h.place_resolution._ask_city(state.model_dump(mode="json"), request, country)
    pending = {"question_id": "country", "state_version": 0, "text": packet["question"],
        "field": "place.city_query", "target_id": "stay-istanbul", "context": packet["pending_context"]}
    await shown_pending(store, session, pending)
    current, run, _ = await claimed(store, session["session_id"])
    await finish_place_run(h, current, run, TripState.model_validate(current["state"]), control="wait",
        request={"role": "destination", "stay_id": "stay-istanbul", "kind": "city", "query": "Buenos Aires"})
    resumed = await store.set_control(session["session_id"], "resume")
    assert await store.claim_run(resumed["resume_run_id"])
    current = await store.get_session(session["session_id"])
    h = PGHarness(store, catalog, [_asked], datetime.now(UTC))
    await resume_deferred_place_run(h, current, await store.get_run(resumed["resume_run_id"]), current["state"])
    assert len(h.place_resolution.calls) == 1
    ref = h.place_resolution.calls[0]["value"]["country_ref"]
    assert ref["country_iso2"] == "AR" and ref["state_version"] == current["state_version"]
    assert ref["retrieved_at"] == metadata["retrieved_at"] and ref["expires_at"] == metadata["expires_at"]


@pytest.mark.asyncio
async def test_resume_marker_cannot_finish_or_copy_pending_between_sessions(repository):
    store, catalog = repository
    paused = await _deferred(store, catalog)
    resumed = await store.set_control(paused["session_id"], "resume")
    assert await store.claim_run(resumed["resume_run_id"])
    foreign = await own_session(store)
    h = PGHarness(store, catalog, [], datetime.now(UTC))
    with pytest.raises(DomainError, match="сервером"):
        await resume_deferred_place_run(h, foreign, await store.get_run(resumed["resume_run_id"]), foreign["state"])
    assert await store.get_session(foreign["session_id"]) == foreign
    assert not h.place_resolution.calls
    await store.fail_run(resumed["resume_run_id"], {"code": "controlled_foreign_resume", "message": "Controlled wrong-session call"}, preserve_session=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["button", "plaintext"])
async def test_api_pause_addition_resume_executes_durable_place_action_without_extra_model(monkeypatch, method):
    model = StubLLM([
        {"operations": [{"type": "resolve_place", "target_id": None, "value": {
            "role": "destination", "kind": "city", "query": "Buenos Aires", "stay_id": "new-stay-1"}}],
            "control": {"action": "wait"}, "speech_act": "CHANGE"},
        {"operations": [{"type": "set_budget", "value": {"amount_minor": 7000000, "currency": "RUB"}}],
            "control": {"action": "none"}, "speech_act": "CHANGE"},
    ])
    async with world_app(monkeypatch, model) as (client, app, wires, travel):
        app.state.place_directory.cache = MemoryPlaceCache()
        sid, token, _ = await _session(client)
        response = await client.post(f"/api/v1/sessions/{sid}/messages", headers=_headers(token), json={
            "client_message_id": str(uuid4()), "text": "Выбери город Buenos Aires для поездки, подожди, я ещё дополню"})
        assert (await _wait(client, response.json()["run_id"], token))["status"] == "completed"
        paused = await _snapshot(client, sid, token)
        assert paused["waiting_reason"] == "user_pause" and not wires
        addition = await client.post(f"/api/v1/sessions/{sid}/messages", headers=_headers(token), json={
            "client_message_id": str(uuid4()), "text": "Общий бюджет поездки 70000 рублей"})
        assert (await app.state.store.get_run(addition.json()["run_id"]))["status"] == "pending"
        assert not wires and len(model.calls) == 1
        if method == "button":
            response = await client.post(f"/api/v1/sessions/{sid}/control", headers=_headers(token), json={"action": "resume"})
            assert response.status_code == 200
            marker_id = response.json()["resume_run_id"]
        else:
            response = await client.post(f"/api/v1/sessions/{sid}/messages", headers=_headers(token), json={
                "client_message_id": str(uuid4()), "text": "продолжай"})
            assert (await _wait(client, response.json()["run_id"], token))["status"] == "completed"
            events = await app.state.store.list_events(sid)
            marker_id = [event["run_id"] for event in events if event["event_type"] == "PLACE_RESUME_QUEUED"][-1]
        marker = await _wait(client, marker_id, token)
        assert marker["status"] == "completed", marker.get("error")
        after = await _snapshot(client, sid, token)
        assert after["trip"]["budget"]["amount_minor"] == 7000000
        assert after["pending_question"]["field"] == "place.resolution"
        assert len(wires) == 1 and travel.calls == 0 and len(model.calls) == 2
        public = await client.get(f"/api/v1/sessions/{sid}/messages", headers=_headers(token))
        assert not any(row["role"] == "system" for row in public.json()["messages"])
        assert await app.state.store.list_orders(sid) == []
