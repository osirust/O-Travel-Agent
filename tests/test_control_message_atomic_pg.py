"""Accepted plaintext controls commit once, including their durable completion.

Only own otravel_test rows and controlled source callbacks are used.
"""
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.engine import make_url
from test_api import StubLLM, _headers, _session, _snapshot, _wait
from test_dynamic_place_conversation import world_app
from test_place_resume_pg import _asked, _deferred
from test_place_run_pg import PGHarness, own_session

from otravel.domain import Operation, TripState, sample_trip
from otravel.geo_catalog import GeoCatalog
from otravel.harness import Harness
from otravel.place_directory import MemoryPlaceCache
from otravel.place_resume import resume_deferred_place_run
from otravel.place_run import finish_place_run
from otravel.store import Conflict, NotFound, Store


class SimulatedCrash(BaseException):
    pass


class ControlHarness(PGHarness):
    control = Harness.control
    control_message = Harness.control_message

    def __init__(self, store, catalog, answers=()):
        super().__init__(store, catalog, answers, datetime.now(UTC))
        self.tasks = {}
        self.scheduled = []

    def schedule(self, session_id):
        self.scheduled.append(session_id)


@pytest_asyncio.fixture
async def database():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Isolated TEST_DATABASE_URL is required")
    if make_url(url).database != "otravel_test":
        pytest.fail("Only otravel_test is allowed")
    store = Store(url)
    owned_sessions = []
    original_create = store.create_session
    async def create_owned(*args, **kwargs):
        created = await original_create(*args, **kwargs)
        owned_sessions.append(created["session_id"])
        return created
    store.create_session = create_owned
    await store.initialize()
    catalog = GeoCatalog(store.engine)
    await catalog.initialize()
    try:
        yield store, catalog
    finally:
        # Negative/crash assertions deliberately leave accepted work pending.
        # Retire only rows created by this fixture so another app's global
        # startup recovery cannot consume these synthetic leftovers as NLU.
        for run in await store.list_pending_runs():
            if run["session_id"] in owned_sessions:
                await store.fail_run(run["run_id"], {"code": "controlled_fixture_finished",
                    "message": "Isolated negative/crash fixture finished"}, preserve_session=True)
        await store.close()


@pytest.mark.asyncio
async def test_crash_after_control_commit_before_harness_finish_does_not_leave_resume_for_nlu(database, monkeypatch):
    store, catalog = database
    paused = await _deferred(store, catalog)
    accepted, duplicate = await store.intake_message(paused["session_id"], str(uuid4()), "продолжай", paused["state_version"])
    assert not duplicate
    original = store.set_control
    async def crash_after_committed_control(*args, **kwargs):
        await original(*args, **kwargs)
        raise SimulatedCrash
    monkeypatch.setattr(store, "set_control", crash_after_committed_control)
    harness = ControlHarness(store, catalog)
    with pytest.raises(SimulatedCrash):
        await harness.control_message(accepted, "resume")
    restarted = Store(os.environ["TEST_DATABASE_URL"])
    try:
        saved = await restarted.get_run(accepted["run_id"])
        assert saved["status"] == "completed", "Accepted resume remains pending and can be sent to NLU after restart"
        current = await restarted.get_session(paused["session_id"])
        marker = current["pending_question"]["context"]["place_deferred_action"]["resume_run_id"]
        queued = [run for run in await restarted.list_pending_runs() if run["session_id"] == paused["session_id"]]
        assert [run["run_id"] for run in queued] == [marker]
        assert await restarted.claim_run(marker)
        current = await restarted.get_session(paused["session_id"])
        h = ControlHarness(restarted, GeoCatalog(restarted.engine), [_asked])
        await resume_deferred_place_run(h, current, await restarted.get_run(marker), current["state"])
        after = await restarted.get_session(paused["session_id"])
        assert len(h.place_resolution.calls) == 1 and after["pending_question"]["field"] == "place.resolution"
        assert not harness.scheduled and not harness.place_resolution.calls
        assert await restarted.list_orders(paused["session_id"]) == []
    finally:
        await restarted.close()


@pytest.mark.asyncio
async def test_pause_control_crash_commits_completion_and_remains_paused(database, monkeypatch):
    store, catalog = database
    session = await own_session(store)
    accepted, _ = await store.intake_message(session["session_id"], str(uuid4()), "подожди", 0)
    original = store.set_control
    async def crash_after_committed_control(*args, **kwargs):
        await original(*args, **kwargs)
        raise SimulatedCrash
    monkeypatch.setattr(store, "set_control", crash_after_committed_control)
    with pytest.raises(SimulatedCrash):
        await ControlHarness(store, catalog).control_message(accepted, "pause")
    saved = await store.get_run(accepted["run_id"])
    assert saved["status"] == "completed", "Accepted pause remains pending after its control commit"
    current = await store.get_session(session["session_id"])
    assert current["waiting_reason"] == "user_pause" and current["state_version"] == 1
    assert not [run for run in await store.list_pending_runs() if run["session_id"] == session["session_id"]]


@pytest.mark.asyncio
async def test_control_tx_failure_rolls_back_revision_marker_and_completion_then_retry_commits_once(database, monkeypatch):
    store, catalog = database
    paused = await _deferred(store, catalog)
    accepted, _ = await store.intake_message(paused["session_id"], str(uuid4()), "продолжай", paused["state_version"])
    before = await store.get_session(paused["session_id"])
    original = store._event
    async def fail_before_commit(conn, session, run_id, event_type, payload):
        if event_type == "PLACE_RESUME_QUEUED":
            raise SimulatedCrash
        return await original(conn, session, run_id, event_type, payload)
    monkeypatch.setattr(store, "_event", fail_before_commit)
    with pytest.raises(SimulatedCrash):
        await store.set_control(paused["session_id"], "resume", control_run_id=accepted["run_id"])
    assert await store.get_session(paused["session_id"]) == before
    assert (await store.get_run(accepted["run_id"]))["status"] == "pending"
    pending = [run for run in await store.list_pending_runs() if run["session_id"] == paused["session_id"]]
    assert [run["run_id"] for run in pending] == [accepted["run_id"]]
    monkeypatch.setattr(store, "_event", original)
    committed = await store.set_control(paused["session_id"], "resume", control_run_id=accepted["run_id"])
    after = await store.get_session(paused["session_id"])
    replay = await store.set_control(paused["session_id"], "resume", control_run_id=accepted["run_id"])
    assert replay["control_run_result"] == committed["control_run_result"]
    assert await store.get_session(paused["session_id"]) == after
    assert after["state_version"] == before["state_version"]+1
    saved = await store.get_run(accepted["run_id"])
    assert saved["status"] == "completed"
    assert saved["based_version"] == committed["control_run_result"]["run"]["based_version"] == before["state_version"]
    events = await store.list_events(paused["session_id"])
    assert len([event for event in events if event["event_type"] == "USER_RESUMED"]) == 1
    assert len([event for event in events if event["event_type"] == "PLACE_RESUME_QUEUED"]) == 1


@pytest.mark.asyncio
async def test_completed_control_replay_after_continuation_has_no_new_control_or_source_action(database):
    store, catalog = database
    paused = await _deferred(store, catalog)
    accepted, _ = await store.intake_message(paused["session_id"], str(uuid4()), "продолжай", paused["state_version"])
    harness = ControlHarness(store, catalog)
    first = await harness.control_message(accepted, "resume")
    current = await store.get_session(paused["session_id"])
    marker = current["pending_question"]["context"]["place_deferred_action"]["resume_run_id"]
    assert await store.claim_run(marker)
    current = await store.get_session(paused["session_id"])
    executor = ControlHarness(store, catalog, [_asked])
    await resume_deferred_place_run(executor, current, await store.get_run(marker), current["state"])
    after = await store.get_session(paused["session_id"])
    events = await store.list_events(paused["session_id"])
    replay = await harness.control_message(await store.get_run(accepted["run_id"]), "resume")
    assert replay == first and await store.get_session(paused["session_id"]) == after
    assert await store.list_events(paused["session_id"]) == events
    assert len(executor.place_resolution.calls) == 1 and not harness.place_resolution.calls


@pytest.mark.asyncio
async def test_foreign_or_noncontrol_run_cannot_modify_another_session(database):
    store, _catalog = database
    first, second = await own_session(store), await own_session(store)
    accepted, _ = await store.intake_message(first["session_id"], str(uuid4()), "подожди", 0)
    with pytest.raises(NotFound):
        await store.set_control(second["session_id"], "pause", control_run_id=accepted["run_id"])
    assert await store.get_session(second["session_id"]) == second
    text, _ = await store.intake_message(first["session_id"], str(uuid4()), "Данные поездки", 0)
    with pytest.raises(Conflict, match="точной"):
        await store.set_control(first["session_id"], "resume", control_run_id=text["run_id"])
    assert await store.get_session(first["session_id"]) == first


@pytest.mark.asyncio
async def test_stale_accepted_control_does_not_change_newer_revision(database):
    store, _catalog = database
    session = await own_session(store)
    accepted, _ = await store.intake_message(session["session_id"], str(uuid4()), "продолжай", 0)
    await store.set_control(session["session_id"], "pause")
    before = await store.get_session(session["session_id"])
    result = await store.set_control(session["session_id"], "resume", control_run_id=accepted["run_id"])
    assert result["control_applied"] is False and result["control_run_result"]["result"]["control_superseded"] is True
    assert (await store.get_run(accepted["run_id"]))["status"] == "completed"
    assert await store.get_session(session["session_id"]) == before


@pytest.mark.asyncio
async def test_running_owned_control_finishes_its_lease_atomically(database):
    store, _catalog = database
    session = await own_session(store)
    accepted, _ = await store.intake_message(session["session_id"], str(uuid4()), "подожди", 0)
    assert await store.claim_run(accepted["run_id"])
    done = await store.set_control(session["session_id"], "pause", control_run_id=accepted["run_id"])
    assert done["control_run_result"]["result"]["control_committed_atomically"] is True
    after = await store.get_session(session["session_id"])
    assert after["active_run_id"] is None and after["waiting_reason"] == "user_pause"
    assert (await store.get_run(accepted["run_id"]))["status"] == "completed"


@pytest.mark.asyncio
async def test_api_crash_boundary_real_session_worker_restart_has_zero_nlu_and_no_repeated_control(monkeypatch):
    key = str(uuid4())
    first_model = StubLLM([])
    async with world_app(monkeypatch, first_model) as (client, app, wires, _travel):
        sid, token, _ = await _session(client, sample_trip().model_dump(mode="json"))
        store = app.state.store
        current = await store.get_session(sid)
        accepted, _ = await store.intake_message(sid, str(uuid4()), "Controlled deferred travel", current["state_version"])
        assert await store.claim_run(accepted["run_id"])
        current = await store.get_session(sid)
        controlled = ControlHarness(store, app.state.geo_catalog)
        await finish_place_run(controlled, current, await store.get_run(accepted["run_id"]), TripState.model_validate(current["state"]),
            request={"kind": "city", "query": "Buenos Aires", "role": "destination", "stay_id": "stay-istanbul"},
            operations=[Operation(type="set_budget", value={"amount_minor": 7000000})], control="wait")
        paused = await _snapshot(client, sid, token)
        original = store.set_control
        async def crash_after_commit(*args, **kwargs):
            await original(*args, **kwargs)
            raise SimulatedCrash
        monkeypatch.setattr(store, "set_control", crash_after_commit)
        with pytest.raises(SimulatedCrash):
            await client.post(f"/api/v1/sessions/{sid}/messages", headers=_headers(token), json={
                "client_message_id": key, "expected_version": paused["state_version"], "text": "продолжай"})
        inputs = [row for row in await store.list_messages(sid) if row.get("client_message_id") == key]
        control_id = inputs[0]["run_id"]
        assert (await store.get_run(control_id))["status"] == "completed"
        persisted = await store.get_session(sid)
        marker = persisted["pending_question"]["context"]["place_deferred_action"]["resume_run_id"]
        assert not first_model.calls and not wires
    second_model = StubLLM([])
    async with world_app(monkeypatch, second_model) as (client, app, wires, travel):
        app.state.place_directory.cache = MemoryPlaceCache()
        app.state.harness.schedule(sid)  # Only this test's session; never global recovery.
        assert (await _wait(client, marker, token))["status"] == "completed"
        after = await _snapshot(client, sid, token)
        assert after["trip"]["budget"]["amount_minor"] == 7000000
        assert after["pending_question"]["field"] == "place.resolution"
        assert len(wires) == 1 and travel.calls == 0 and not second_model.calls
        events = await app.state.store.list_events(sid)
        assert len([event for event in events if event["event_type"] == "USER_RESUMED"]) == 1
        replay = await client.post(f"/api/v1/sessions/{sid}/messages", headers=_headers(token), json={
            "client_message_id": key, "expected_version": paused["state_version"], "text": "продолжай"})
        assert replay.json()["duplicate"] is True and replay.json()["run_id"] == control_id
        assert await _snapshot(client, sid, token) == after
        assert len(wires) == 1 and not second_model.calls
        assert await app.state.store.list_events(sid) == events
        assert await app.state.store.list_orders(sid) == []
