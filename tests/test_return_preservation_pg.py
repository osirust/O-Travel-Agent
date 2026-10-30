"""Cached actual correction over real isolated PG; no model/travel HTTP."""
import json
import os
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.engine import make_url
from test_api import StubLLM, _session, _snapshot
from test_duration_grounding_pg import message
from test_geo_catalog import admit, key
from test_mixed_checkout import CheckoutTravel

from otravel.api import create_app
from otravel.config import Settings
from otravel.domain import sample_trip
from otravel.geo_catalog import GeoCatalog
from otravel.place_directory import PlaceDirectory
from otravel.store import Store

FIXTURE = Path(__file__).parents[1] / "fixtures/regression/return-preservation-complex-r5-20261028.json"


@pytest_asyncio.fixture
async def return_api(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Only isolated TEST_DATABASE_URL is authorized")
    if make_url(url).database != "otravel_test":
        pytest.fail("Only otravel_test is authorized", pytrace=False)
    opened = []

    async def create(model, refs=None, travel=None):
        repository = Store(url)
        catalog = GeoCatalog(repository.engine)
        await catalog.initialize()
        for ref in (refs or {}).values():
            fact = {field: value for field, value in ref.items()
                if field not in {"place_id", "ref_version", "provider_bindings"}}
            fact["language"] = "en"
            admitted, _ = await admit(catalog, key(operation="get_place", role="city", language="en",
                query=str(fact["geographic_source"]["external_id"]), country_iso2=None, count=1),
                fact, now=datetime.now(UTC))
            assert admitted.model_dump(mode="json") == ref

        def unexpected_directory(_request):
            raise AssertionError("No external directory HTTP allowed for exact cached existing refs")

        directory = PlaceDirectory(cache=catalog, transport=httpx.MockTransport(unexpected_directory))
        settings = Settings(url, "gemini", 1, 8000, 1000000, 1, max_local_llm_calls_per_day=0,
            default_search_mode="HYBRID", default_hotel_provider="liteapi", default_flight_provider="duffel",
            dynamic_geography_enabled=True)
        app = create_app(settings, store=repository, llm=model, travel=travel or CheckoutTravel(),
            geo_catalog=catalog, place_directory=directory)

        async def no_foreign_recovery():
            pass

        monkeypatch.setattr(app.state.harness, "start", no_foreign_recovery)
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://return-controlled")
        opened.append((client, lifespan, repository))
        return client, repository

    yield create
    for client, lifespan, repository in reversed(opened):
        await client.aclose()
        await lifespan.__aexit__(None, None, None)
        await repository.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("with_original_pending", [False, True])
async def test_exact_actual_correction_drops_unasked_return_replay_and_saves_two_rooms_in_each_city(return_api, with_original_pending):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    model = StubLLM([fixture["raw_contract"]["value"]])
    client, repository = await return_api(model, fixture["before_trip"]["location_refs"])
    # This is a server-owned actual state with trusted location refs. The HTTP
    # create endpoint deliberately refuses client-supplied provenance; seed a
    # new own PG session through Store rather than weaken that boundary.
    token = str(uuid4())
    own = await repository.create_session(fixture["before_trip"], sha256(token.encode()).hexdigest())
    sid = own["session_id"]
    initial = await _snapshot(client, sid, token)
    if with_original_pending:
        pending = deepcopy(fixture["observed_blocked_pending"])
        pending.update(question_id="controlled-replayed-return-conflict", state_version=initial["state_version"])
        shown, duplicate = await repository.intake_message(sid, str(uuid4()), "Controlled prior generic clarification",
            initial["state_version"])
        assert not duplicate and await repository.claim_run(shown["run_id"])
        await repository.finish_run(shown["run_id"], initial["state_version"], None, "WAITING_USER", pending["text"],
            pending_question=pending, waiting_reason="clarification")
    run, after = await message(client, sid, token, fixture["input"])
    assert run["status"] == "completed" and not run.get("error"), run.get("error")
    trip = after["trip"]
    assert after["state_version"] == initial["state_version"] + 1
    assert trip["stays"][-1]["check_out"] == trip["legs"][-1]["departure_date"] == "2026-11-19"
    assert [stay["check_out"] for stay in trip["stays"]] == ["2026-11-13", "2026-11-19"]
    assert [len(stay["rooms"]) for stay in trip["stays"]] == [2, 2]
    assert all(stay["rooms"][0]["traveler_ids"] == ["new-traveler-1", "new-traveler-3", "new-traveler-4"]
        and stay["rooms"][1]["traveler_ids"] == ["new-traveler-2"] for stay in trip["stays"])
    assert next(person for person in trip["travelers"] if person["id"] == "new-traveler-4")["group_id"] == "new-group-1"
    assert len(trip["groups"][0]["traveler_ids"]) == 4 and trip["budget"]["amount_minor"] == 118000000
    notes = run["result"]["result"]["operation_normalizations"]
    assert sum(note.get("code") == "unrequested_return_shift_suppressed" for note in notes) == 1
    assert len(model.calls) == 1 and await repository.list_orders(sid) == []
    assert after.get("pending_question") is None


@pytest.mark.asyncio
async def test_explicit_current_return_shift_is_not_suppressed(return_api):
    before = sample_trip().model_dump(mode="json")
    model = StubLLM([{"operations": [{"type": "shift_return", "value": {"days": 2}}],
        "speech_act": "CHANGE", "control": {"action": "continue"}}])
    client, repository = await return_api(model)
    sid, token, initial = await _session(client, before)
    run, after = await message(client, sid, token,
        "Даты Стамбула сохрани, но возвращение перенеси на два дня позже.")
    assert run["status"] == "completed" and not run.get("error"), run.get("error")
    expected = (date.fromisoformat(before["stays"][-1]["check_out"]) + timedelta(days=2)).isoformat()
    assert after["trip"]["stays"][-1]["check_out"] == after["trip"]["legs"][-1]["departure_date"] == expected
    assert after["state_version"] == initial["state_version"] + 1
    assert not any(note.get("code") == "unrequested_return_shift_suppressed"
        for note in run["result"]["result"].get("operation_normalizations", []))
    assert await repository.list_orders(sid) == []


@pytest.mark.asyncio
async def test_shift_suppression_does_not_remove_independent_budget_conflict_or_create_order(return_api):
    before = sample_trip().model_dump(mode="json")
    model = StubLLM([{"operations": [{"type": "shift_return", "value": {"days": 2}},
        {"type": "set_budget", "value": {"amount_minor": 8000000, "currency": "RUB"}},
        {"type": "set_budget", "value": {"amount_minor": 9000000, "currency": "RUB"}}],
        "speech_act": "CHANGE", "control": {"action": "continue"}}])
    client, repository = await return_api(model)
    sid, token, initial = await _session(client, before)
    run, after = await message(client, sid, token, "Даты и возвращение сохрани. Поменяй стоимость.")
    assert run["status"] == "completed" and not run.get("error"), run.get("error")
    assert after["state_version"] == initial["state_version"] and after["trip"] == initial["trip"]
    assert after["pending_question"] is not None and after["waiting_reason"] == "clarification"
    assert await repository.list_orders(sid) == []
