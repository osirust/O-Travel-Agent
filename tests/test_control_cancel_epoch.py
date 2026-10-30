"""Pure task interleavings around committed controls; no engine or app startup."""

import asyncio
from copy import deepcopy

import pytest

from otravel.harness import Harness

SESSION_ID = "controlled-session"


class ControlledStore:
    def __init__(self):
        self.pause_committed = asyncio.Event()
        self.release_old_pause = asyncio.Event()
        self.current = {"session_id": SESSION_ID, "state_version": 10,
            "active_run_id": "old-run", "waiting_reason": None, "control_applied": True}

    async def get_session(self, session_id):
        assert session_id == SESSION_ID
        return deepcopy(self.current)

    async def set_control(self, session_id, action, reply="", **kwargs):
        assert session_id == SESSION_ID and action in {"pause", "resume"}
        self.current.update(state_version=self.current["state_version"] + 1,
            waiting_reason="user_pause" if action == "pause" else None)
        committed = deepcopy(self.current)
        if action == "pause":
            # Commit has occurred; only delivery of its result is delayed.
            self.pause_committed.set()
            await self.release_old_pause.wait()
        return committed

    async def recover_accepted_controls(self, session_id, *, limit):
        committed = await self.set_control(session_id, "pause")
        return [{"session_id": session_id, "run_id": "accepted-old-pause", "action": "pause",
            "applied": True, "status": "completed", "state_version": committed["state_version"],
            "active_run_id": committed["active_run_id"]}]


async def _work_until(released):
    await released.wait()


def _harness(store):
    # Exercise the production methods with only their in-memory dependencies.
    harness = Harness.__new__(Harness)
    harness.store = store
    harness.tasks = {}
    harness.active = {}
    harness.closed = False
    return harness


async def _resume_with_new_task(harness, store, old_task, finish_old):
    await asyncio.wait_for(store.pause_committed.wait(), timeout=1)
    finish_old.set()
    await old_task
    await harness.control(SESSION_ID, "resume", schedule=False, write_reply=False)
    new_task = asyncio.create_task(_work_until(asyncio.Event()))
    harness.tasks[SESSION_ID] = new_task
    harness.active[SESSION_ID] = "new-run"
    store.current["active_run_id"] = "new-run"
    await asyncio.sleep(0)
    assert store.current["state_version"] == 12 and store.current["waiting_reason"] is None
    store.release_old_pause.set()
    return new_task


async def _cleanup(store, *tasks):
    store.release_old_pause.set()
    for task in tasks:
        if task is not None and not task.done():
            task.cancel()
    await asyncio.gather(*(task for task in tasks if task is not None), return_exceptions=True)


@pytest.mark.asyncio
async def test_delayed_recovered_pause_must_not_cancel_task_started_after_newer_resume():
    store = ControlledStore()
    harness = _harness(store)
    finish_old = asyncio.Event()
    old_task = asyncio.create_task(_work_until(finish_old))
    harness.tasks[SESSION_ID], harness.active[SESSION_ID] = old_task, "old-run"
    pause = asyncio.create_task(harness._recover_accepted_controls(SESSION_ID, limit=8))
    new_task = None
    try:
        new_task = await _resume_with_new_task(harness, store, old_task, finish_old)
        await asyncio.wait_for(pause, timeout=1)
        assert not new_task.cancelled() and not new_task.done(), "Old recovered pause cancelled work from newer resume"
        assert store.current["state_version"] == 12 and store.current["waiting_reason"] is None
    finally:
        await _cleanup(store, old_task, new_task, pause)


@pytest.mark.asyncio
async def test_delayed_direct_pause_must_not_cancel_task_started_after_newer_resume():
    store = ControlledStore()
    harness = _harness(store)
    finish_old = asyncio.Event()
    old_task = asyncio.create_task(_work_until(finish_old))
    harness.tasks[SESSION_ID], harness.active[SESSION_ID] = old_task, "old-run"
    pause = asyncio.create_task(harness.control(SESSION_ID, "pause", schedule=False, write_reply=False))
    new_task = None
    try:
        new_task = await _resume_with_new_task(harness, store, old_task, finish_old)
        await asyncio.wait_for(pause, timeout=1)
        assert not new_task.cancelled() and not new_task.done(), "Old direct pause cancelled work from newer resume"
        assert store.current["state_version"] == 12 and store.current["waiting_reason"] is None
    finally:
        await _cleanup(store, old_task, new_task, pause)
