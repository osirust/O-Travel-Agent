"""Opt-in API admission and durable submission, not a completed resolver flow.

Fake Harness never starts a worker. Pure ASGI tests call no PostgreSQL/providers;
the explicit PG cases use only otravel_test and synthetic cached geo facts.
"""

import json
import os
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.engine import make_url

from otravel import api
from otravel.config import Settings
from otravel.domain import TripState
from otravel.geo_catalog import GeoCatalog, GeoCatalogError
from otravel.harness import TYPED_PREFIX
from otravel.llm_contract import build_operation_schema
from otravel.store import Conflict, NotFound, Store


class Resource:
    def __init__(self, events, name):
        self.events, self.name = events, name
        self.calls = []

    async def close(self):
        self.events.append(self.name + ".close")

    async def search_city(self, *args, **kwargs):
        raise AssertionError("Route/startup may not perform geographic HTTP")


class CatalogDouble:
    """Declared trusted rows for admission controls, not external geo proof."""
    def __init__(self, events):
        self.events, self.registered, self.calls = events, {}, []
        self.fail_initialization = False
        self.verify_error = None

    async def initialize(self):
        self.events.append("catalog.initialize")
        if self.fail_initialization:
            raise GeoCatalogError("catalog_unavailable")

    async def verify_places(self, refs, **kwargs):
        self.calls.append((deepcopy(refs), kwargs))
        if self.verify_error is not None:
            raise GeoCatalogError(self.verify_error)
        for pid, ref in refs.items():
            if pid not in self.registered:
                raise GeoCatalogError("unknown_snapshot")
            if ref.model_dump(mode="json") != self.registered[pid].model_dump(mode="json"):
                raise GeoCatalogError("snapshot_conflict")
        return dict(self.registered)


class QueuedHarness:
    """Observe the existing durable submit boundary without executing commands."""
    def __init__(self, settings, store, model, travel, **kwargs):
        self.store, self.kwargs, self.scheduled = store, kwargs, []

    async def start(self):
        self.store.events.append("harness.start")

    async def close(self):
        self.store.events.append("harness.close")

    def schedule(self, sid):
        self.scheduled.append(sid)


class MemoryRepository:
    def __init__(self, events):
        self.events, self.rows, self.runs, self.messages, self.created = events, {}, {}, {}, []
        self.engine = SimpleNamespace(fixture="no real database")

    async def initialize(self):
        self.events.append("store.initialize")

    async def close(self):
        self.events.append("store.close")

    async def create_session(self, state, owner_token_hash):
        self.created.append(deepcopy(state))
        sid = str(uuid4())
        state = {**deepcopy(state), "trip_id": str(uuid4()), "state_version": 0}
        self.rows[sid] = {"session_id": sid, "trip_id": state["trip_id"], "state_version": 0,
            "state": state, "status": "NEW", "waiting_reason": None, "pending_question": None,
            "owner_token_hash": owner_token_hash}
        self.messages[sid] = []
        return deepcopy(self.rows[sid])

    async def get_session(self, sid):
        if sid not in self.rows:
            raise NotFound("Нет сессии.")
        return deepcopy(self.rows[sid])

    async def list_messages(self, sid):
        return deepcopy(self.messages[sid])

    async def get_run(self, rid):
        return deepcopy(self.runs[rid])

    async def intake_message(self, sid, client_id, text, expected_version):
        for message in self.messages[sid]:
            if message["client_message_id"] == client_id:
                run = self.runs[message["run_id"]]
                if run["input_text"] != text:
                    raise Conflict("Другое содержимое.", "idempotency_conflict")
                return deepcopy(run), True
        if self.rows[sid]["state_version"] != expected_version:
            raise Conflict("Устаревшая версия.", "stale_state")
        rid = str(uuid4())
        self.runs[rid] = {"run_id": rid, "session_id": sid, "input_text": text, "status": "pending"}
        self.messages[sid].append({"client_message_id": client_id, "run_id": rid, "text": text})
        return deepcopy(self.runs[rid]), False


@pytest.fixture
def boundary(monkeypatch):
    events = []
    repo, catalog, directory = MemoryRepository(events), CatalogDouble(events), Resource(events, "directory")
    monkeypatch.setattr(api, "Harness", QueuedHarness)
    def create(*, enabled=True, use_defaults=False):
        settings = Settings("unused", "groq", 1, 8000, 0, 0, dynamic_geography_enabled=enabled)
        kwargs = {} if use_defaults else {"geo_catalog": catalog, "place_directory": directory}
        app = api.create_app(settings, repo, Resource(events, "model"), Resource(events, "travel"), **kwargs)
        return app
    return create, repo, catalog, directory, events


async def session(client, initial=None):
    response = await client.post("/api/v1/sessions", json={"initial_trip": initial} if initial else {})
    assert response.status_code == 201
    body = response.json()
    return body["session_id"], {"Authorization": "Bearer " + body["session_token"]}


def resolve(**changes):
    return {"client_message_id": str(uuid4()), "expected_version": 0, "role": "destination",
        "kind": "city", "query": "Буэнос-Айрес", "country_iso2": "AR", "stay_id": "new-stay-1", **changes}


def select(**changes):
    return {"client_message_id": str(uuid4()), "expected_version": 0,
        "resolution_id": "owned-worker-resolution", "candidate_id": "explicit-source-candidate", **changes}


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["operation-schema", "places/resolve", "places/select"])
async def test_schema_and_places_require_own_session_bearer_before_any_queue(boundary, endpoint):
    create, repo, _catalog, _directory, _events = boundary
    app = create()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        sid, headers = await session(client)
        _, foreign = await session(client)
        if endpoint == "operation-schema":
            anonymous = await client.get(f"/api/v1/sessions/{sid}/{endpoint}")
            denied = await client.get(f"/api/v1/sessions/{sid}/{endpoint}", headers=foreign)
            owned = await client.get(f"/api/v1/sessions/{sid}/{endpoint}", headers=headers)
            assert owned.status_code == 200
        else:
            body = resolve() if endpoint.endswith("resolve") else select()
            anonymous = await client.post(f"/api/v1/sessions/{sid}/{endpoint}", json=body)
            denied = await client.post(f"/api/v1/sessions/{sid}/{endpoint}", headers=foreign, json=body)
        assert anonymous.status_code == 401 and denied.status_code == 403 and repo.runs == {}


@pytest.mark.asyncio
async def test_schema_is_opt_in_per_current_state_and_rejects_stale_requested_version(boundary):
    create, repo, _catalog, _directory, _events = boundary
    for enabled in (False, True):
        app = create(enabled=enabled)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            sid, headers = await session(client)
            response = await client.get(f"/api/v1/sessions/{sid}/operation-schema", headers=headers)
            row = response.json()
            assert row["session_id"] == sid and row["state_version"] == 0
            assert row["schema"] == build_operation_schema(repo.rows[sid]["state"], dynamic_places=enabled)
            stale = await client.get(f"/api/v1/sessions/{sid}/operation-schema?state_version=1", headers=headers)
            assert stale.status_code == 409 and not repo.runs


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["resolve", "select"])
async def test_disabled_feature_refuses_geographic_routes_without_using_injected_resources(boundary, endpoint):
    create, repo, catalog, directory, events = boundary
    app = create(enabled=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        sid, headers = await session(client)
        response = await client.post(f"/api/v1/sessions/{sid}/places/{endpoint}", headers=headers,
            json=resolve() if endpoint == "resolve" else select())
        assert response.status_code == 409 and response.json()["error"]["code"] == "dynamic_geography_not_configured"
        assert not repo.runs and not catalog.calls and not directory.calls and not events
        assert app.state.geo_catalog is None and app.state.place_directory is None


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["resolve", "select"])
async def test_places_only_durably_submit_typed_request_and_identical_replay_never_schedules_twice(boundary, endpoint):
    create, repo, _catalog, directory, _events = boundary
    app = create()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        sid, headers = await session(client)
        body = resolve() if endpoint == "resolve" else select()
        first = await client.post(f"/api/v1/sessions/{sid}/places/{endpoint}", headers=headers, json=body)
        again = await client.post(f"/api/v1/sessions/{sid}/places/{endpoint}", headers=headers, json=body)
        assert first.status_code == again.status_code == 202
        assert first.json()["run_id"] == again.json()["run_id"] and again.json()["duplicate"] is True
        run = repo.runs[first.json()["run_id"]]
        payload = json.loads(run["input_text"][len(TYPED_PREFIX):])
        assert payload["kind"] == endpoint + "_place" and "base_state_hash" in payload
        field = "request" if endpoint == "resolve" else "selection"
        assert payload[field] == {k: v for k, v in body.items() if k not in {"client_message_id", "expected_version"}}
        assert app.state.harness.scheduled == [sid] and not directory.calls
        assert repo.rows[sid]["state"] == TripState.model_validate(repo.rows[sid]["state"]).model_dump(mode="json")


@pytest.mark.asyncio
@pytest.mark.parametrize("patch", [{"expected_version": True}, {"expected_version": -1}, {"query": " "},
    {"query": "x"}, {"query": "city\nsecret"}, {"query": "x" * 161}, {"country_iso2": "ar"},
    {"role": "provider"}, {"stay_id": "foreign-stay"}, {"stay_id": None},
    {"provider_bindings": {"liteapi": {"country_iso2": "AR"}}}, {"authorization": "synthetic-private"}])
async def test_resolve_input_and_current_target_schema_fail_before_durable_queue(boundary, patch):
    create, repo, _catalog, directory, _events = boundary
    app = create()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        sid, headers = await session(client)
        response = await client.post(f"/api/v1/sessions/{sid}/places/resolve", headers=headers, json=resolve(**patch))
        assert response.status_code == 422 and not repo.runs and not directory.calls
        assert "synthetic-private" not in response.text


@pytest.mark.asyncio
async def test_origin_role_requires_null_stay_and_stale_body_cannot_queue_new_resolution(boundary):
    create, repo, _catalog, directory, _events = boundary
    app = create()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        sid, headers = await session(client)
        invalid = await client.post(f"/api/v1/sessions/{sid}/places/resolve", headers=headers,
            json=resolve(role="origin", stay_id="new-stay-1"))
        assert invalid.status_code == 422 and not repo.runs
        repo.rows[sid]["state_version"] = repo.rows[sid]["state"]["state_version"] = 1
        stale = await client.post(f"/api/v1/sessions/{sid}/places/resolve", headers=headers,
            json=resolve(role="origin", stay_id=None))
        assert stale.status_code == 409 and not repo.runs and not directory.calls


@pytest.mark.asyncio
async def test_identical_resolution_replay_survives_a_later_removed_target_without_new_work(boundary):
    create, repo, _catalog, _directory, _events = boundary
    app = create()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        sid, headers = await session(client, {"stays": [{"id": "owned-custom-stay", "city": "Стамбул"}]})
        body = resolve(stay_id="owned-custom-stay")
        first = await client.post(f"/api/v1/sessions/{sid}/places/resolve", headers=headers, json=body)
        assert first.status_code == 202
        rid = first.json()["run_id"]
        # Declared post-completion edit; this tests the API's replay boundary,
        # not a fabricated successful resolver or a real running worker.
        repo.runs[rid]["status"] = "completed"
        repo.rows[sid]["state"]["stays"] = []
        repo.rows[sid]["state_version"] = repo.rows[sid]["state"]["state_version"] = 1
        replay = await client.post(f"/api/v1/sessions/{sid}/places/resolve", headers=headers, json=body)
        assert replay.status_code == 202 and replay.json()["duplicate"] is True
        assert replay.json()["run_id"] == rid and len(repo.runs) == 1
        assert app.state.harness.scheduled == [sid]


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["place_ref", "location_ref", "location_refs", "provider_bindings", "resolved_places", "origin_place_ref"])
@pytest.mark.parametrize("endpoint", ["commands", "live/search"])
async def test_generic_and_live_routes_cannot_be_used_to_register_raw_place_or_supplier_authority(boundary, key, endpoint):
    create, repo, _catalog, directory, _events = boundary
    app = create()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        sid, headers = await session(client)
        if endpoint == "commands":
            body = {"client_message_id": str(uuid4()), "expected_version": 0, "operations": [
                {"type": "set_origin", "value": {"city": "Москва", "nested": {key: {"country_iso2": "AR"}}}}]}
        else:
            body = {"client_message_id": str(uuid4()), "expected_version": 0, "kind": "hotels", "provider": "liteapi",
                "request": {"nested": {key: {"country_iso2": "AR"}}}}
        response = await client.post(f"/api/v1/sessions/{sid}/{endpoint}", headers=headers, json=body)
        assert response.status_code == 400 and not repo.runs and not directory.calls


@pytest.mark.asyncio
async def test_lifespan_initializes_catalog_before_recovery_and_closes_directory_before_engine(boundary):
    create, _repo, _catalog, _directory, events = boundary
    app = create()
    async with app.router.lifespan_context(app):
        assert events == ["store.initialize", "catalog.initialize", "harness.start"]
        assert app.state.harness.kwargs["geo_catalog"] is app.state.geo_catalog
        assert app.state.harness.kwargs["place_directory"] is app.state.place_directory
    assert events.index("directory.close") < events.index("store.close")
    assert not app.state.place_directory.calls


@pytest.mark.asyncio
async def test_catalog_startup_failure_closes_directory_and_engine_without_starting_recovery(boundary):
    create, _repo, catalog, _directory, events = boundary
    catalog.fail_initialization = True
    app = create()
    with pytest.raises(RuntimeError, match="startup failed"):
        async with app.router.lifespan_context(app):
            raise AssertionError("Startup must not complete")
    assert "harness.start" not in events and events.index("directory.close") < events.index("store.close")


@pytest.mark.asyncio
async def test_default_feature_dependencies_are_created_without_network_at_startup(boundary, monkeypatch):
    create, repo, catalog, directory, events = boundary
    seen = []
    monkeypatch.setattr(api, "GeoCatalog", lambda engine: seen.append(("catalog", engine)) or catalog)
    monkeypatch.setattr(api, "PlaceDirectory", lambda **kwargs: seen.append(("directory", kwargs)) or directory)
    app = create(use_defaults=True)
    assert seen == [("catalog", repo.engine), ("directory", {"cache": catalog})] and events == []
    async with app.router.lifespan_context(app):
        assert not directory.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["catalog_unavailable", "catalog_integrity"])
async def test_server_cannot_persist_new_refs_when_their_provenance_store_is_unavailable(boundary, code):
    from test_place_refs import place
    create, repo, catalog, _directory, _events = boundary
    catalog.verify_error = code
    app = create()
    ref = place()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/sessions", json={"initial_trip": initial_for(ref)})
        assert response.status_code == 503 and response.json()["error"]["retryable"] is True
        assert catalog.calls[0][1]["require_fresh"] is True and catalog.calls[0][1]["require_current"] is True
        assert repo.created == []


@pytest_asyncio.fixture
async def pg_boundary(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not configured; no database contacted")
    if make_url(url).database != "otravel_test":
        pytest.fail("Dynamic geography API requires otravel_test", pytrace=False)
    monkeypatch.setattr(api, "Harness", QueuedHarness)
    repository = Store(url)
    await repository.initialize()
    catalog = GeoCatalog(repository.engine)
    await catalog.initialize()
    # Reuse declared fact builders only. No provider is called to produce them.
    from test_geo_catalog import envelope, fact, key
    now = datetime.now(UTC)
    candidate = fact(1_000_000_000 + uuid4().int % 1_000_000_000)
    cache_key = key(query="API synthetic " + uuid4().hex)
    await catalog.put_directory(cache_key, envelope(cache_key, [candidate], now=now), now=now)
    ref = await catalog.admit_place(cache_key, candidate["geographic_source"]["external_id"],
        expected_source_fact_sha256=candidate["source_fact_sha256"], now=now)
    directory = Resource([], "directory")
    settings = Settings(url, "groq", 1, 8000, 0, 0, dynamic_geography_enabled=True)
    app = api.create_app(settings, repository, Resource([], "model"), Resource([], "travel"),
        geo_catalog=catalog, place_directory=directory)
    calls = []
    original = repository.create_session
    async def capture(state, **kwargs):
        calls.append(deepcopy(state))
        return await original(state, **kwargs)
    monkeypatch.setattr(repository, "create_session", capture)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield client, app, ref, calls
    finally:
        await directory.close()
        await repository.close()


def initial_for(ref):
    return {"origin_place_id": ref.place_id, "origin": ref.canonical_name,
        "location_refs": {ref.place_id: ref.model_dump(mode="json")}}


@pytest.mark.asyncio
async def test_pg_trusted_fresh_catalog_snapshot_is_verified_before_new_trip_persistence(pg_boundary):
    client, app, ref, calls = pg_boundary
    sid, headers = await session(client, initial_for(ref))
    saved = await app.state.store.get_session(sid)
    assert saved["state"]["location_refs"] == {ref.place_id: ref.model_dump(mode="json")}
    assert saved["state"]["origin"] == ref.canonical_name and len(calls) == 1
    schema = await client.get(f"/api/v1/sessions/{sid}/operation-schema", headers=headers)
    assert schema.status_code == 200 and ref.place_id in json.dumps(schema.json())
    assert app.state.harness.scheduled == [] and not app.state.place_directory.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("forgery", ["name", "coordinates", "provider", "version", "off"])
async def test_pg_client_cannot_register_forged_or_disabled_dynamic_snapshot_before_persistence(pg_boundary, forgery):
    client, app, ref, calls = pg_boundary
    state = initial_for(ref)
    payload = state["location_refs"][ref.place_id]
    if forgery == "name":
        payload["canonical_name"] = state["origin"] = "Forged city"
    elif forgery == "coordinates":
        payload["latitude"] = 20.0
    elif forgery == "provider":
        payload["provider_bindings"]["liteapi"] = {"country_iso2": "AR", "city_name": "Unverified supplier label"}
    elif forgery == "version":
        payload["ref_version"] += 1
    else:
        # A separate feature-off boundary must reject refs even if its injected
        # catalog happens to contain that exact immutable record.
        off = api.create_app(Settings("unused", "groq", 1, 8000, 0, 0), app.state.store,
            Resource([], "model"), Resource([], "travel"), geo_catalog=app.state.geo_catalog,
            place_directory=app.state.place_directory)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=off), base_url="http://test") as off_client:
            response = await off_client.post("/api/v1/sessions", json={"initial_trip": state})
        assert response.status_code == 422 and calls == []
        return
    response = await client.post("/api/v1/sessions", json={"initial_trip": state})
    assert response.status_code == 422 and calls == [] and not app.state.place_directory.calls
