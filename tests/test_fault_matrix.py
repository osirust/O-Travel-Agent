"""Scoped fault evidence, not qualification of a global PostgreSQL crash.

Real PG: explicitly TEST_DATABASE_URL/otravel_test only. Provider HTTP is always
HTTPX MockTransport. Commit bombs raise before the driver's COMMIT, proving known
rollback rather than a server-committed/lost-response outcome. The sole SIGKILL
targets a Python child created by this test, never PostgreSQL/Docker/other apps.
"""

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import signal
import socket
import sys
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import event, func, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import create_async_engine

from otravel.config import Settings
from otravel.domain import TripState, sample_trip
from otravel.harness import Harness
from otravel.inventory import DemoInventory
from otravel.providers import ProviderError
from otravel.store import StaleQuote, Store, revisions, runs, sessions


class ControlledHTTPModel:
    def __init__(self):
        self.calls = []
        self.last_call = {"provider": "test_mock_http", "model": "controlled-fault-answer"}

        def respond(request):
            self.calls.append(request.method)
            return httpx.Response(200, json={
                "operations": [{"type": "set_budget", "value": {"amount_minor": 74_000_000}},
                               {"type": "shift_return", "value": {"days": 2}}],
                "control": {"action": "none"}, "reply": "", "unknowns": [], "conflicts": [],
            })

        self.client = httpx.AsyncClient(transport=httpx.MockTransport(respond), trust_env=False)

    async def extract_operations(self, context):
        return (await self.client.post("https://fault-model.invalid/controlled", json={"test": True})).json()

    async def close(self):
        await self.client.aclose()


class NoTravel:
    async def search_hotels(self, *args, **kwargs):
        raise ProviderError("CONFIG_MISSING", "No vendor access in a fault test")

    search_flights = search_hotels
    weather = search_hotels

    async def close(self):
        pass


@pytest_asyncio.fixture
async def fault_store(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not configured; no database or child started")
    if make_url(url).database != "otravel_test":
        pytest.fail("Fault matrix requires the explicit otravel_test database", pytrace=False)
    for name in ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "LITEAPI_API_KEY", "DUFFEL_ACCESS_TOKEN", "SERPAPI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    repository = Store(url)
    failed = False
    try:
        await repository.initialize()
    except Exception:
        failed = True
    if failed:
        await repository.close()
        pytest.fail("Fault-test database initialization unavailable; details redacted", pytrace=False)
    try:
        yield repository
    finally:
        await repository.close()


@asynccontextmanager
async def fault_api(repository, model):
    from otravel.api import create_app
    settings = Settings(os.environ["TEST_DATABASE_URL"], "openrouter", 1, 8000, 1_000_000, 2)
    app = create_app(settings=settings, store=repository, llm=model, travel=NoTravel())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://fault-test") as client:
            yield client


async def create_session(client):
    response = await client.post("/api/v1/sessions", json={"initial_trip": sample_trip().model_dump(mode="json")})
    assert response.status_code == 201
    value = response.json()
    return value, {"Authorization": f"Bearer {value['session_token']}"}


async def wait_run(client, run_id, headers):
    for _ in range(150):
        response = await client.get(f"/api/v1/runs/{run_id}", headers=headers)
        assert response.status_code == 200
        value = response.json()
        if value["status"] in {"completed", "failed", "conflict", "interrupted", "cancelled"}:
            return value
        await asyncio.sleep(0.02)
    pytest.fail("Fault run did not reach a terminal status", pytrace=False)


async def row_count(repository, table, session_id):
    async with repository.engine.connect() as connection:
        return (await connection.execute(select(func.count()).select_from(table).where(table.c.session_id == session_id))).scalar_one()


class CommitBomb:
    def __init__(self, repository):
        self.repository = repository
        self.enabled = ContextVar("controlled_pre_commit_fault", default=False)
        self.triggered = False

    def before_commit(self, connection):
        if self.enabled.get() and not self.triggered:
            self.triggered = True
            raise OperationalError("COMMIT", None, RuntimeError("controlled pre-commit failure"))

    def install(self):
        event.listen(self.repository.engine.sync_engine, "commit", self.before_commit)

    def remove(self):
        event.remove(self.repository.engine.sync_engine, "commit", self.before_commit)


@pytest.mark.asyncio
async def test_refused_database_connection_before_intake_is_503_and_writes_nothing(fault_store):
    model = ControlledHTTPModel()
    async with fault_api(fault_store, model) as client:
        session, headers = await create_session(client)
        sid = session["session_id"]
        original_engine = fault_store.engine
        # Own bound, non-listening port gives a real local refusal without
        # stopping or reconfiguring any PostgreSQL/service process.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as closed_port:
            closed_port.bind(("127.0.0.1", 0))
            port = closed_port.getsockname()[1]
            broken = create_async_engine(original_engine.url.set(host="127.0.0.1", port=port),
                                         connect_args={"timeout": 0.4}, pool_pre_ping=False, hide_parameters=True)
            fault_store.engine = broken
            try:
                response = await client.post(f"/api/v1/sessions/{sid}/messages", json={"client_message_id": "db-refused", "text": "Не должен быть принят"}, headers=headers)
            finally:
                fault_store.engine = original_engine
                await broken.dispose()
        assert await row_count(fault_store, runs, sid) == 0
        assert await fault_store.list_messages(sid) == []
        assert model.calls == []
        assert response.status_code == 503, f"Expected safe 503 before intake; observed HTTP {response.status_code}"
        assert response.json()["error"]["retryable"] is True


@pytest.mark.asyncio
async def test_commit_failure_after_mock_http_rolls_back_entire_domain_revision(fault_store, monkeypatch):
    model = ControlledHTTPModel()
    async with fault_api(fault_store, model) as client:
        session, headers = await create_session(client)
        sid = session["session_id"]
        before = (await fault_store.get_session(sid))["state"]
        initial_revisions = await row_count(fault_store, revisions, sid)
        original_finish = fault_store.finish_run
        bomb = CommitBomb(fault_store)

        async def fail_finish(*args, **kwargs):
            assert model.calls == ["POST"]
            token = bomb.enabled.set(True)
            try:
                return await original_finish(*args, **kwargs)
            finally:
                bomb.enabled.reset(token)

        bomb.install()
        monkeypatch.setattr(fault_store, "finish_run", fail_finish)
        try:
            response = await client.post(f"/api/v1/sessions/{sid}/messages", json={"client_message_id": "after-http-commit-failure", "text": "Измени два поля"}, headers=headers)
            assert response.status_code == 202
            run = await wait_run(client, response.json()["run_id"], headers)
        finally:
            bomb.remove()
            monkeypatch.setattr(fault_store, "finish_run", original_finish)
        assert bomb.triggered and model.calls == ["POST"]
        assert run["status"] == "failed" and run["error"]["retryable"] is True
        current = await fault_store.get_session(sid)
        assert current["state"] == before and current["state_version"] == 0
        assert current["active_run_id"] is None
        assert current["status"] == "RECOVERABLE_ERROR"
        assert await row_count(fault_store, revisions, sid) == initial_revisions
        assert await fault_store.list_orders(sid) == []
        events = await fault_store.list_events(sid)
        assert sum(item["event_type"] == "MODEL_RESPONSE_RECEIVED" for item in events) == 1
        # The one-shot known rollback is recoverable by a new explicit form.
        recovery = await client.post(f"/api/v1/sessions/{sid}/commands", json={"client_message_id": "after-fault-recovery", "expected_version": 0, "operations": [{"type": "set_budget", "value": {"amount_minor": 78_000_000}}]}, headers=headers)
        recovered = await wait_run(client, recovery.json()["run_id"], headers)
        assert recovered["status"] == "completed"
        assert (await fault_store.get_session(sid))["state"]["budget"]["amount_minor"] == 78_000_000


@pytest.mark.asyncio
async def test_order_commit_failure_after_controlled_price_http_has_no_partial_order(fault_store, monkeypatch):
    model = ControlledHTTPModel()
    calls = []

    def price_response(request):
        calls.append(request.method)
        return httpx.Response(200, json={"source_type": "TEST_MOCK_HTTP", "price_unchanged": True})

    async with fault_api(fault_store, model) as client:
        session, headers = await create_session(client)
        sid = session["session_id"]
        stored = await fault_store.get_session(sid)
        plan = DemoInventory().search_trip(TripState.model_validate(stored["state"]))["plans"][0]
        quote = await fault_store.create_quote(sid, 0, plan)
        initial_revisions = await row_count(fault_store, revisions, sid)
        original_confirm = fault_store.confirm_quote
        bomb = CommitBomb(fault_store)
        async with httpx.AsyncClient(transport=httpx.MockTransport(price_response), trust_env=False) as price_http:
            async def confirm_after_http(*args, **kwargs):
                await price_http.post("https://fault-price.invalid/controlled-check", json={"test": True})
                token = bomb.enabled.set(True)
                try:
                    return await original_confirm(*args, **kwargs)
                finally:
                    bomb.enabled.reset(token)

            monkeypatch.setattr(fault_store, "confirm_quote", confirm_after_http)
            bomb.install()
            payload = {"quote_id": quote["quote_id"], "quote_version": 1, "state_version": 0,
                       "idempotency_key": "controlled-order-fault", "confirmed": True}
            try:
                response = await client.post(f"/api/v1/sessions/{sid}/checkout/confirm", json=payload, headers=headers)
            finally:
                bomb.remove()
            assert response.status_code == 503
            assert bomb.triggered and calls == ["POST"]
            assert await fault_store.list_orders(sid) == []
            assert (await fault_store.get_session(sid))["state_version"] == 0
            assert await row_count(fault_store, revisions, sid) == initial_revisions
            assert (await fault_store.get_quote(quote["quote_id"]))["status"] == "active"
            # Retry the same explicitly confirmed operation after known rollback.
            retry = await client.post(f"/api/v1/sessions/{sid}/checkout/confirm", json=payload, headers=headers)
            assert retry.status_code == 200
            assert len(await fault_store.list_orders(sid)) == 1
            assert retry.json()["booking_created_at_supplier"] is False
        monkeypatch.setattr(fault_store, "confirm_quote", original_confirm)
        assert model.calls == []


@pytest.mark.asyncio
async def test_owned_worker_sigkill_recovers_interrupted_without_http_replay(fault_store):
    session = await fault_store.create_session(sample_trip().model_dump(mode="json"))
    sid = session["session_id"]
    old_plan = DemoInventory().search_trip(TripState.model_validate(session["state"]))["plans"][0]
    old_quote = await fault_store.create_quote(sid, 0, old_plan)
    edit, _ = await fault_store.intake_message(sid, str(uuid4()), "Изменить бюджет перед crash", 0)
    assert await fault_store.claim_run(edit["run_id"])
    changed = deepcopy(session["state"])
    changed["budget"]["amount_minor"] = 79_000_000
    await fault_store.finish_run(edit["run_id"], 0, changed, "TRIP_DRAFT", "Изменено", changes=[{"operation": "set_budget"}])
    before = await fault_store.get_session(sid)
    run, _ = await fault_store.intake_message(sid, str(uuid4()), "Controlled read then owned crash", 1)
    # Claim in the parent dispatcher before spawn. Other legitimate Harness
    # instances sharing otravel_test then cannot steal this owned pending run
    # while the child imports Python dependencies.
    assert await fault_store.claim_run(run["run_id"])
    script = Path(__file__).resolve().parents[1] / "scripts" / "probe_crash.py"
    process = await asyncio.create_subprocess_exec(sys.executable, "-u", str(script), "--session-id", sid, "--run-id", run["run_id"],
        env={**os.environ, "OTRAVEL_OWNED_CRASH_PROBE": "1"}, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, cwd=script.parents[1])
    try:
        raw = await asyncio.wait_for(process.stdout.readline(), 12)
        marker = json.loads(raw)
        assert marker["status"] == "ready_for_owned_kill", "Owned crash probe did not reach its controlled point"
        assert marker["pid"] == process.pid and marker["http_attempts"] == 1
        # Exact subprocess object and PID created immediately above; no process search.
        process.kill()
        await asyncio.wait_for(process.wait(), 5)
        assert process.returncode != 0
        if os.name == "posix":
            assert process.returncode == -signal.SIGKILL
    finally:
        if process.returncode is None:
            process.kill()
            await asyncio.wait_for(process.wait(), 5)
    assert (await fault_store.get_session(sid))["active_run_id"] == run["run_id"]
    # Expire only this owned lease; keep the normal 120-second recovery guard so
    # healthy sessions from another parallel test process are not interrupted.
    async with fault_store.engine.begin() as connection:
        expired = await connection.execute(update(sessions).where(
            sessions.c.session_id == sid, sessions.c.active_run_id == run["run_id"]
        ).values(active_run_started_at=datetime.now(timezone.utc) - timedelta(seconds=121)))
        assert expired.rowcount == 1
    recovered = await fault_store.recover_stale_runs()
    assert run["run_id"] in {item["run_id"] for item in recovered}
    assert (await fault_store.get_run(run["run_id"]))["status"] == "interrupted"
    after = await fault_store.get_session(sid)
    assert after["active_run_id"] is None and after["state"] == before["state"]
    assert after["state_version"] == before["state_version"] == 1
    assert run["run_id"] not in {item["run_id"] for item in await fault_store.list_pending_runs()}
    model = ControlledHTTPModel()
    harness = Harness(Settings(os.environ["TEST_DATABASE_URL"], "openrouter", 1, 8000, 1_000_000, 2), fault_store, model, NoTravel())
    try:
        harness.schedule(sid)
        await asyncio.wait_for(harness.tasks[sid], 2)
        assert model.calls == []
    finally:
        await harness.close()
        await model.close()
    completed = [item for item in await fault_store.list_events(sid) if item["event_type"] == "FAULT_PROBE_HTTP_COMPLETED"]
    assert len(completed) == 1 and completed[0]["payload"]["source_type"] == "TEST_MOCK_HTTP"
    assert (await fault_store.get_quote(old_quote["quote_id"]))["status"] == "stale"
    with pytest.raises(StaleQuote):
        await fault_store.confirm_quote(sid, old_quote["quote_id"], 1, "cannot-revive-old-consent", True, 1)
    assert await fault_store.list_orders(sid) == []
