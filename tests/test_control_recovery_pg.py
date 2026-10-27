"""Pending authenticated exact controls recover before the ordinary pause fence."""
import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import update
from test_api import StubLLM
from test_dynamic_place_conversation import world_app
from test_place_resume_pg import _deferred

from otravel.place_directory import MemoryPlaceCache
from otravel.store import Store, events, messages, sessions


async def _retire_own_pending(store, session_id):
    for run in await store.list_pending_runs():
        if run["session_id"] == session_id:
            await store.fail_run(run["run_id"], {"code": "controlled_fixture_finished",
                "message": "Own pending control fixture finished"}, preserve_session=True)


async def _drain_owned(harness, session_id):
    # Register the normal worker task exactly as startup/schedule_pending does.
    # A direct private _drain call has no task registration and can race the
    # recovery hook's legitimate resume.schedule call in this test harness.
    harness.schedule(session_id)
    await harness.tasks[session_id]


@pytest.mark.asyncio
async def test_durable_intake_then_crash_before_control_commit_recovers_before_pause_barrier(monkeypatch):
    model = StubLLM([])
    async with world_app(monkeypatch, model) as (_client, app, wires, travel):
        app.state.place_directory.cache = MemoryPlaceCache()
        store = app.state.store
        paused = await _deferred(store, app.state.geo_catalog)
        try:
            accepted, _ = await store.intake_message(paused["session_id"], str(uuid4()), "продолжай", paused["state_version"])
            # Simulated process stopped after intake commit: no control method
            # was entered. A new worker lifetime must recover the accepted row.
            assert (await store.get_run(accepted["run_id"]))["status"] == "pending"
            await _drain_owned(app.state.harness, paused["session_id"])
            after_run = await store.get_run(accepted["run_id"])
            assert after_run["status"] == "completed", "Accepted exact resume is blocked behind ordinary user_pause work"
            after = await store.get_session(paused["session_id"])
            assert after["waiting_reason"] == "clarification" and after["pending_question"]["field"] == "place.resolution"
            assert not model.calls and len(wires) == 1 and travel.calls == 0
            events = await store.list_events(paused["session_id"])
            assert len([event for event in events if event["event_type"] == "USER_RESUMED"]) == 1
            assert await store.list_orders(paused["session_id"]) == []
        finally:
            await _retire_own_pending(store, paused["session_id"])


@pytest.mark.asyncio
@pytest.mark.parametrize("old_action,new_button,expected_wires", [("продолжай", "pause", 0), ("подожди", "resume", 1)])
async def test_newer_committed_button_supersedes_older_pending_control_without_replaying_it(monkeypatch, old_action, new_button, expected_wires):
    model = StubLLM([])
    async with world_app(monkeypatch, model) as (_client, app, wires, _travel):
        app.state.place_directory.cache = MemoryPlaceCache()
        store = app.state.store
        paused = await _deferred(store, app.state.geo_catalog)
        try:
            accepted, _ = await store.intake_message(paused["session_id"], str(uuid4()), old_action, None)
            latest = await store.set_control(paused["session_id"], new_button)
            await _drain_owned(app.state.harness, paused["session_id"])
            saved = await store.get_run(accepted["run_id"])
            assert saved["status"] == "completed" and saved["result"]["result"]["control_superseded"] is True
            after = await store.get_session(paused["session_id"])
            assert after["state_version"] == latest["state_version"]
            assert (after["waiting_reason"] == "user_pause") is (new_button == "pause")
            assert len(wires) == expected_wires and not model.calls
            events = await store.list_events(paused["session_id"])
            assert len([e for e in events if e["event_type"] == "USER_PAUSED"]) == int(new_button == "pause")
            assert len([e for e in events if e["event_type"] == "USER_RESUMED"]) == int(new_button == "resume")
        finally:
            await _retire_own_pending(store, paused["session_id"])


@pytest.mark.asyncio
async def test_latest_pending_pause_wins_and_ordinary_paused_message_is_never_claimed(monkeypatch):
    model = StubLLM([])
    async with world_app(monkeypatch, model) as (_client, app, wires, _travel):
        store = app.state.store
        paused = await _deferred(store, app.state.geo_catalog)
        try:
            older, _ = await store.intake_message(paused["session_id"], str(uuid4()), "продолжай", None)
            ordinary, _ = await store.intake_message(paused["session_id"], str(uuid4()), "Хочу добавить ребёнка позже", None)
            newer, _ = await store.intake_message(paused["session_id"], str(uuid4()), "подожди", None)
            await _drain_owned(app.state.harness, paused["session_id"])
            assert (await store.get_run(older["run_id"]))["result"]["result"]["control_superseded"] is True
            assert (await store.get_run(newer["run_id"]))["status"] == "completed"
            assert (await store.get_run(ordinary["run_id"]))["status"] == "pending"
            assert (await store.get_session(paused["session_id"]))["waiting_reason"] == "user_pause"
            assert not await store.claim_run(ordinary["run_id"]) and not model.calls and not wires
        finally:
            await _retire_own_pending(store, paused["session_id"])


@pytest.mark.asyncio
async def test_two_store_workers_recover_one_exact_resume_once_without_duplicate_revision(monkeypatch):
    model = StubLLM([])
    async with world_app(monkeypatch, model) as (_client, app, wires, _travel):
        store = app.state.store
        paused = await _deferred(store, app.state.geo_catalog)
        other = Store(os.environ["TEST_DATABASE_URL"])
        try:
            accepted, _ = await store.intake_message(paused["session_id"], str(uuid4()), "продолжай", paused["state_version"])
            outcomes = await asyncio.gather(store.recover_accepted_controls(paused["session_id"], limit=8),
                other.recover_accepted_controls(paused["session_id"], limit=8))
            assert (await store.get_run(accepted["run_id"]))["status"] == "completed"
            assert sum(item["applied"] for group in outcomes for item in group) == 1
            after = await store.get_session(paused["session_id"])
            assert after["state_version"] == paused["state_version"]+1
            pending = [run for run in await store.list_pending_runs() if run["session_id"] == paused["session_id"]]
            assert len(pending) == 1 and pending[0]["run_id"] == after["pending_question"]["context"]["place_deferred_action"]["resume_run_id"]
            events = await store.list_events(paused["session_id"])
            assert len([e for e in events if e["event_type"] == "USER_RESUMED"]) == 1
            assert not model.calls and not wires
        finally:
            await _retire_own_pending(store, paused["session_id"])
            await other.close()


@pytest.mark.asyncio
async def test_recovery_rejects_stale_version_and_leaves_ordinary_pause_in_place(monkeypatch):
    model = StubLLM([])
    async with world_app(monkeypatch, model) as (_client, app, wires, _travel):
        store = app.state.store
        paused = await _deferred(store, app.state.geo_catalog)
        try:
            accepted, _ = await store.intake_message(paused["session_id"], str(uuid4()), "продолжай", paused["state_version"])
            # Independent fact CAS while paused changes no control decision.
            state = dict(paused["state"])
            state["budget"] = {**state["budget"], "amount_minor": 7000000}
            dummy, _ = await store.intake_message(paused["session_id"], str(uuid4()), "Controlled saved fact", None)
            await store.finish_run(dummy["run_id"], paused["state_version"], state, "WAITING_USER", "Controlled fact saved",
                pending_question=paused["pending_question"], waiting_reason="user_pause")
            after_fact = await store.get_session(paused["session_id"])
            await _drain_owned(app.state.harness, paused["session_id"])
            assert (await store.get_run(accepted["run_id"]))["status"] == "failed"
            after = await store.get_session(paused["session_id"])
            assert after["state"] == after_fact["state"] and after["waiting_reason"] == "user_pause"
            assert not model.calls and not wires
        finally:
            await _retire_own_pending(store, paused["session_id"])


@pytest.mark.asyncio
async def test_unicode_exact_control_candidate_uses_same_canonical_command_parser(monkeypatch):
    model = StubLLM([])
    async with world_app(monkeypatch, model) as (_client, app, wires, _travel):
        store = app.state.store
        paused = await _deferred(store, app.state.geo_catalog)
        try:
            accepted, _ = await store.intake_message(paused["session_id"], str(uuid4()), " \u00a0ПРОДОЛЖАЙ!\n", None)
            result = await store.recover_accepted_controls(paused["session_id"], limit=8)
            assert result[0]["applied"] is True and (await store.get_run(accepted["run_id"]))["status"] == "completed"
            assert not model.calls and not wires
        finally:
            await _retire_own_pending(store, paused["session_id"])


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["message_role", "event_binding", "owner"])
async def test_recovery_requires_server_user_event_and_authenticated_session_provenance(monkeypatch, damage):
    model = StubLLM([])
    async with world_app(monkeypatch, model) as (_client, app, wires, _travel):
        store = app.state.store
        paused = await _deferred(store, app.state.geo_catalog)
        try:
            accepted, _ = await store.intake_message(paused["session_id"], str(uuid4()), "продолжай", None)
            async with store.engine.begin() as conn:
                if damage == "message_role":
                    await conn.execute(update(messages).where(messages.c.id == accepted["input_message_id"]).values(role="system"))
                elif damage == "event_binding":
                    await conn.execute(update(events).where(events.c.session_id == paused["session_id"],
                        events.c.run_id == accepted["run_id"], events.c.event_type == "USER_MESSAGE_RECEIVED"
                    ).values(payload={"message_id": str(uuid4())}))
                else:
                    await conn.execute(update(sessions).where(sessions.c.session_id == paused["session_id"]).values(owner_token_hash=None))
            before = await store.get_session(paused["session_id"])
            assert await store.recover_accepted_controls(paused["session_id"], limit=8) == []
            assert await store.get_session(paused["session_id"]) == before
            assert (await store.get_run(accepted["run_id"]))["status"] == "pending"
            assert not model.calls and not wires
        finally:
            await _retire_own_pending(store, paused["session_id"])


@pytest.mark.asyncio
async def test_more_than_session_recovery_batch_never_sends_remaining_exact_controls_to_nlu(monkeypatch):
    model = StubLLM([])
    async with world_app(monkeypatch, model) as (_client, app, wires, _travel):
        app.state.place_directory.cache = MemoryPlaceCache()
        store = app.state.store
        paused = await _deferred(store, app.state.geo_catalog)
        try:
            accepted = []
            for _ in range(8):
                run, _duplicate = await store.intake_message(paused["session_id"], str(uuid4()), "подожди", None)
                accepted.append(run)
            newest, _ = await store.intake_message(paused["session_id"], str(uuid4()), "продолжай", None)
            await _drain_owned(app.state.harness, paused["session_id"])
            assert (await store.get_run(newest["run_id"]))["status"] == "completed"
            completed = [await store.get_run(run["run_id"]) for run in accepted]
            assert all(run["status"] == "completed" for run in completed)
            after = await store.get_session(paused["session_id"])
            assert after["state_version"] == paused["state_version"]+1 and after["pending_question"]["field"] == "place.resolution"
            assert len(wires) == 1 and not model.calls
            saved_events = await store.list_events(paused["session_id"])
            assert len([event for event in saved_events if event["event_type"] == "USER_RESUMED"]) == 1
            assert not [event for event in saved_events if event["event_type"] == "USER_PAUSED"]
        finally:
            await _retire_own_pending(store, paused["session_id"])


@pytest.mark.asyncio
async def test_completed_old_pause_replay_does_not_cancel_newer_work_or_change_current_resume(monkeypatch):
    model = StubLLM([])
    async with world_app(monkeypatch, model) as (_client, app, wires, _travel):
        store = app.state.store
        paused = await _deferred(store, app.state.geo_catalog)
        task = None
        try:
            old_pause, _ = await store.intake_message(paused["session_id"], str(uuid4()), "подожди", None)
            await store.set_control(paused["session_id"], "pause", control_run_id=old_pause["run_id"])
            await store.set_control(paused["session_id"], "resume")
            before = await store.get_session(paused["session_id"])
            task = asyncio.create_task(asyncio.sleep(3600))
            app.state.harness.tasks[paused["session_id"]] = task
            await app.state.harness.control_message(await store.get_run(old_pause["run_id"]), "pause")
            assert not task.cancelled() and not task.done()
            assert await store.get_session(paused["session_id"]) == before
            assert not model.calls and not wires
        finally:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await _retire_own_pending(store, paused["session_id"])
