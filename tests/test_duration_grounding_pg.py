"""Cached model durations through real PG/ASGI; fake model and travel only."""
import json
import os
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.engine import make_url
from test_api import StubLLM, _headers, _session, _snapshot, _wait
from test_mixed_checkout import CheckoutTravel

from otravel.api import create_app
from otravel.config import Settings
from otravel.domain import TripState, apply_operations
from otravel.store import Store

FIXTURE = Path(__file__).parents[1] / "fixtures/regression/duration-initial-complex-r3-20261027.json"


def source():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest_asyncio.fixture
async def duration_api(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Only isolated TEST_DATABASE_URL is authorized")
    if make_url(url).database != "otravel_test":
        pytest.fail("Only otravel_test is authorized", pytrace=False)
    opened = []

    async def create(model):
        repository = Store(url)
        gateway = CheckoutTravel()
        settings = Settings(url, "gemini", 1, 8000, 1000000, 1, max_local_llm_calls_per_day=0,
            default_search_mode="HYBRID", default_hotel_provider="liteapi", default_flight_provider="duffel")
        app = create_app(settings, store=repository, llm=model, travel=gateway)
        async def no_foreign_recovery():
            pass
        monkeypatch.setattr(app.state.harness, "start", no_foreign_recovery)
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://duration-controlled")
        opened.append((client, lifespan, repository))
        return client, repository

    yield create
    for client, lifespan, repository in reversed(opened):
        await client.aclose()
        await lifespan.__aexit__(None, None, None)
        await repository.close()


async def message(client, sid, token, text):
    before = await _snapshot(client, sid, token)
    response = await client.post(f"/api/v1/sessions/{sid}/messages", headers=_headers(token), json={
        "client_message_id": str(uuid4()), "expected_version": before["state_version"], "text": text})
    assert response.status_code == 202
    run = await _wait(client, response.json()["run_id"], token)
    return run, await _snapshot(client, sid, token)


@pytest.mark.asyncio
async def test_actual_cached_dates_only_intake_does_not_persist_hard_nights_and_next_checkout_edit_succeeds(duration_api):
    fixture = source()
    model = StubLLM([fixture["raw_contract"]["value"], fixture["followup"]["raw_contract"]["value"]])
    client, repository = await duration_api(model)
    sid, token, _created = await _session(client, fixture["before_trip"])
    initial, first = await message(client, sid, token, fixture["input"])
    assert initial["status"] == "completed" and not initial.get("error")
    assert [stay.get("requested_nights") for stay in first["trip"]["stays"]] == [None, None]
    assert [stay["check_out"] for stay in first["trip"]["stays"]] == ["2026-11-13", "2026-11-17"]
    note_rows = initial["result"]["result"]["operation_normalizations"]
    assert sum(note.get("code") == "unasked_requested_nights_removed" for note in note_rows) == 2
    followup, final = await message(client, sid, token, fixture["followup"]["input"])
    assert followup["status"] == "completed" and not followup.get("error")
    assert [stay.get("requested_nights") for stay in final["trip"]["stays"]] == [None, None]
    assert [stay["check_out"] for stay in final["trip"]["stays"]] == ["2026-11-13", "2026-11-19"]
    assert final["trip"]["legs"][-1]["departure_date"] == "2026-11-19"
    assert len(final["trip"]["travelers"]) == 4 and len(model.calls) == 2
    assert [len(stay["rooms"]) for stay in final["trip"]["stays"]] == [2, 2]
    assert await repository.list_orders(sid) == []


@pytest.mark.asyncio
async def test_existing_nonnull_legacy_duration_stays_hard_and_rejected_batch_is_atomic(duration_api):
    fixture = source()
    legacy, _ = apply_operations(TripState.model_validate(fixture["before_trip"]), fixture["raw_contract"]["value"]["operations"])
    assert [stay.requested_nights for stay in legacy.stays] == [3, 4]
    model = StubLLM([fixture["followup"]["raw_contract"]["value"]])
    client, repository = await duration_api(model)
    sid, token, initial = await _session(client, legacy.model_dump(mode="json"))
    run, final = await message(client, sid, token, fixture["followup"]["input"])
    assert run["status"] == "failed"
    assert run["error"]["details"]["domain_error_code"] == "duration_mismatch"
    assert final["trip"] == initial["trip"] and final["state_version"] == initial["state_version"]
    assert [stay["requested_nights"] for stay in final["trip"]["stays"]] == [3, 4]
    assert len(final["trip"]["travelers"]) == 3 and await repository.list_orders(sid) == []


@pytest.mark.asyncio
async def test_explicit_duration_and_typed_commands_keep_requested_nights(duration_api):
    fixture = source()
    model = StubLLM([fixture["raw_contract"]["value"]])
    client, repository = await duration_api(model)
    sid, token, _initial = await _session(client, fixture["before_trip"])
    text = fixture["input"] + " В Стамбуле три ночи, в Анталье четыре ночи."
    run, after = await message(client, sid, token, text)
    assert run["status"] == "completed" and [stay["requested_nights"] for stay in after["trip"]["stays"]] == [3, 4]
    assert not any(note.get("code") == "unasked_requested_nights_removed"
        for note in run["result"]["result"].get("operation_normalizations", []))
    typed_model = StubLLM([])
    typed_client, typed_repository = await duration_api(typed_model)
    typed_sid, typed_token, typed_initial = await _session(typed_client, fixture["before_trip"])
    response = await typed_client.post(f"/api/v1/sessions/{typed_sid}/commands", headers=_headers(typed_token), json={
        "client_message_id": str(uuid4()), "expected_version": typed_initial["state_version"], "search": False,
        "operations": deepcopy(fixture["raw_contract"]["value"]["operations"])})
    assert response.status_code == 202
    typed_run = await _wait(typed_client, response.json()["run_id"], typed_token)
    typed_final = await _snapshot(typed_client, typed_sid, typed_token)
    assert typed_run["status"] == "completed" and [stay["requested_nights"] for stay in typed_final["trip"]["stays"]] == [3, 4]
    assert typed_model.calls == []
    assert await repository.list_orders(sid) == await typed_repository.list_orders(typed_sid) == []
