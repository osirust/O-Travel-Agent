"""Real isolated PostgreSQL worker persistence; controlled sources, zero HTTP/model."""
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from test_geo_catalog import admit, fact, key
from test_place_run import ControlledCoordinator, Harness, ask, selected, selection

from otravel.domain import Operation, TripState, sample_trip
from otravel.geo_catalog import GeoCatalog
from otravel.inventory import DemoInventory
from otravel.place_run import finish_place_run
from otravel.store import Conflict, Store, revisions


class PGHarness(Harness):
    def __init__(self, repository, catalog, answers, now):
        super().__init__(answers)
        self.store, self.geo_catalog = repository, catalog
        self.place_resolution.clock = lambda: now

    async def _ensure_current_run(self, session, run):
        latest = await self.store.get_session(session["session_id"])
        active = await self.store.get_run(run["run_id"])
        if (latest["state_version"] != session["state_version"] or latest.get("waiting_reason") == "user_pause"
                or latest.get("active_run_id") != run["run_id"] or active["status"] != "running"):
            raise Conflict("Controlled stale lease", "stale_state")


@pytest_asyncio.fixture
async def pg():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Isolated TEST_DATABASE_URL is required; no connection attempted")
    if make_url(url).database != "otravel_test":
        pytest.fail("Only otravel_test is allowed", pytrace=False)
    repository = Store(url)
    await repository.initialize()
    catalog = GeoCatalog(repository.engine)
    await catalog.initialize()
    try:
        yield repository, catalog
    finally:
        await repository.close()


async def claimed(repository, session_id, *, text="Controlled place action"):
    session = await repository.get_session(session_id)
    message_key = str(uuid4())
    accepted, reused = await repository.intake_message(session_id, message_key, text, session["state_version"])
    assert not reused and await repository.claim_run(accepted["run_id"])
    return await repository.get_session(session_id), await repository.get_run(accepted["run_id"]), message_key


async def own_session(repository):
    made = await repository.create_session(sample_trip().model_dump(mode="json"), "a" * 64)
    return await repository.get_session(made["session_id"])


async def shown_pending(repository, session, pending):
    current, run, _ = await claimed(repository, session["session_id"], text="Controlled displayed geography choice")
    await repository.finish_run(run["run_id"], current["state_version"], None, "WAITING_USER", pending["text"],
        pending_question=pending, waiting_reason="clarification")
    return await repository.get_session(session["session_id"])


async def revision_count(repository, sid):
    async with repository.engine.connect() as conn:
        return (await conn.execute(select(func.count()).select_from(revisions).where(revisions.c.session_id == sid))).scalar_one()


@pytest.mark.asyncio
async def test_pg_selected_and_independent_budget_are_one_revision_stale_quote_and_idempotent_intake(pg):
    repository, catalog = pg
    now = datetime.now(UTC)
    source = fact(1_000_000_000 + uuid4().int % 1_000_000_000)
    directory_key = key(operation="get_place", role="destination", query=str(source["geographic_source"]["external_id"]), country_iso2=None, count=1)
    place, _ = await admit(catalog, directory_key, source, now=now)
    session = await own_session(repository); state = TripState.model_validate(session["state"])
    plan = DemoInventory().search_trip(state)["plans"][0]
    quote = await repository.create_quote(session["session_id"], 0, plan)
    pending = ask(state, source_id=place.geographic_source.external_id)
    pending["context"]["place_resolution"]["expires_at"] = (now.replace(microsecond=0) + timedelta(minutes=15)).isoformat()
    session = await shown_pending(repository, session, pending)
    old_count = await revision_count(repository, session["session_id"])
    session, run, message_key = await claimed(repository, session["session_id"])
    h = PGHarness(repository, catalog, [selected(place)], now)
    await finish_place_run(h, session, run, TripState.model_validate(session["state"]), selection=selection(pending),
        operations=[Operation(type="set_budget", value={"amount_minor": 7000000})])
    after = await repository.get_session(session["session_id"])
    assert after["state_version"] == 1 and after["state"]["budget"]["amount_minor"] == 7000000
    assert after["state"]["stays"][0]["place_id"] == place.place_id
    assert await revision_count(repository, session["session_id"]) == old_count + 1
    assert after["owner_token_hash"] == session["owner_token_hash"] and after["trip_id"] == session["trip_id"]
    assert (await repository.get_quote(quote["quote_id"]))["status"] == "stale"
    replay, reused = await repository.intake_message(session["session_id"], message_key, run["input_text"], 0)
    assert reused and replay["run_id"] == run["run_id"] and replay["status"] == "completed"
    assert await revision_count(repository, session["session_id"]) == old_count + 1
    assert await repository.list_orders(session["session_id"]) == []


@pytest.mark.asyncio
async def test_pg_explicit_wait_without_facts_revokes_quote_once_and_makes_no_source_calls(pg):
    repository, catalog = pg
    now = datetime.now(UTC)
    session = await own_session(repository); state = TripState.model_validate(session["state"])
    quote = await repository.create_quote(session["session_id"], 0, DemoInventory().search_trip(state)["plans"][0])
    initial = await revision_count(repository, session["session_id"])
    session, run, _ = await claimed(repository, session["session_id"])
    h = PGHarness(repository, catalog, [], now)
    await finish_place_run(h, session, run, state, control="wait",
        request={"role": "origin", "kind": "city", "query": "Buenos Aires", "stay_id": None})
    after = await repository.get_session(session["session_id"])
    assert after["state_version"] == 1 and after["waiting_reason"] == "user_pause"
    assert await revision_count(repository, session["session_id"]) == initial + 1
    assert (await repository.get_quote(quote["quote_id"]))["status"] == "stale"
    assert after["pending_question"]["context"]["place_deferred_action"]["state_version"] == 1
    assert not h.place_resolution.calls and not h.budget_calls and not h.searches


@pytest.mark.asyncio
async def test_pg_country_maintenance_updates_available_facts_and_owned_epoch_without_search(pg):
    repository, catalog = pg
    now = datetime.now(UTC)
    session = await own_session(repository); state = TripState.model_validate(session["state"])
    h = PGHarness(repository, catalog, [], now)
    request = {"role": "destination", "stay_id": "stay-istanbul", "query": "Argentina"}
    metadata = {"source_sha256": "b" * 64, "retrieved_at": now.isoformat(), "expires_at": (now + timedelta(days=1)).isoformat()}
    country = h.place_resolution._country_ref(state.model_dump(mode="json"), request,
        {"country_iso2": "AR", "name": "Argentina"}, metadata)
    packet = h.place_resolution._ask_city(state.model_dump(mode="json"), request, country)
    pending = {"question_id": "declared", "state_version": 0, "text": packet["question"], "field": "place.city_query",
        "target_id": "stay-istanbul", "context": packet["pending_context"]}
    session = await shown_pending(repository, session, pending)
    session, run, _ = await claimed(repository, session["session_id"])
    await finish_place_run(h, session, run, state, operations=[
        Operation(type="set_budget", value={"amount_minor": 7000000}),
        Operation(type="update_traveler", target_id="child-1", value={"age": 4})])
    after = await repository.get_session(session["session_id"])
    saved = after["pending_question"]["context"]["place_resolution"]["country_ref"]
    assert after["state_version"] == saved["state_version"] == 1 and saved["country_iso2"] == "AR"
    assert saved["expires_at"] == metadata["expires_at"] and "Argentina" in after["pending_question"]["text"]
    assert after["state"]["budget"]["amount_minor"] == 7000000
    assert next(p for p in after["state"]["travelers"] if p["id"] == "child-1")["age"] == 4
    assert not h.place_resolution.calls and not h.searches


@pytest.mark.asyncio
async def test_pg_foreign_catalog_snapshot_preserves_budget_but_does_not_apply_city(pg):
    repository, catalog = pg
    now = datetime.now(UTC)
    session = await own_session(repository); state = TripState.model_validate(session["state"])
    pending = ask(state)
    pending["context"]["place_resolution"]["expires_at"] = (now + timedelta(minutes=15)).isoformat()
    session = await shown_pending(repository, session, pending)
    session, run, _ = await claimed(repository, session["session_id"])
    h = PGHarness(repository, catalog, [selected()], now)
    await finish_place_run(h, session, run, state, selection=selection(pending),
        operations=[Operation(type="set_budget", value={"amount_minor": 7000000})])
    after = await repository.get_session(session["session_id"])
    assert after["state_version"] == 1 and after["state"]["budget"]["amount_minor"] == 7000000
    assert after["state"]["stays"][0]["city"] == "Стамбул" and "location_refs" not in after["state"]
    assert after["waiting_reason"] == "clarification" and not h.searches


@pytest.mark.asyncio
async def test_pg_mid_source_control_change_cannot_overwrite_newer_pause(pg):
    repository, catalog = pg
    now = datetime.now(UTC)
    session = await own_session(repository); state = TripState.model_validate(session["state"])
    session, run, _ = await claimed(repository, session["session_id"])
    class Pausing(ControlledCoordinator):
        async def resolve(self, request, current, *, before_call):
            await before_call({"provider": "openmeteo", "method": "GET", "operation": "declared"})
            await repository.set_control(session["session_id"], "pause")
            return {"action": "unavailable", "question": "Ignored stale source response"}
    h = PGHarness(repository, catalog, [], now); h.place_resolution = Pausing([]); h.place_resolution.clock = lambda: now
    with pytest.raises(Conflict):
        await finish_place_run(h, session, run, state, request={"role": "origin", "kind": "city", "query": "Buenos Aires", "stay_id": None},
            operations=[Operation(type="set_budget", value={"amount_minor": 7000000})])
    after = await repository.get_session(session["session_id"])
    assert after["state_version"] == 1 and after["waiting_reason"] == "user_pause"
    assert after["state"]["budget"]["amount_minor"] == state.budget.amount_minor
    assert "location_refs" not in after["state"] and not h.searches
    # Leave this owned test run terminal; no pending/run recovery pollution.
    await repository.fail_run(run["run_id"], {"code": "controlled_stale_cleanup", "message": "Controlled stale outcome"}, preserve_session=True)
