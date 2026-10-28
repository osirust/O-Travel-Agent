"""Original actual compound's omitted room count yields owned ASK before search."""
import json
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from test_api import StubLLM, _snapshot
from test_duration_grounding_pg import message
from test_return_preservation_pg import return_api  # noqa: F401


class NoRoomSearch:
    def __init__(self):
        self.calls = []

    async def forbidden(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("No search until explicit universal room count is satisfied")

    resolve_hotels = search_hotels = search_flights = resolve_air_place = list_countries = forbidden

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_cached_original_compound_with_one_room_in_each_city_is_held_with_exact_count_question(return_api):  # noqa: F811
    path = Path(__file__).parents[1] / "fixtures/regression/universal-room-count-complex-r5-20261028.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    model = StubLLM([fixture["checked_contract"]["value"]])
    travel = NoRoomSearch()
    client, repository = await return_api(model, fixture["before_trip"]["location_refs"], travel=travel)
    token = str(uuid4())
    own = await repository.create_session(fixture["before_trip"], sha256(token.encode()).hexdigest())
    sid = own["session_id"]
    initial = await _snapshot(client, sid, token)
    run, after = await message(client, sid, token, fixture["input"])
    assert run["status"] == "completed" and not run.get("error"), run.get("error")
    assert after["trip"] == initial["trip"] and after["state_version"] == initial["state_version"]
    question = after["pending_question"]
    assert question["field"] == "stay.rooms" and after["waiting_reason"] == "clarification"
    assert question["context"]["requested_room_count"] == 2
    assert question["context"]["force_clarification"] is True
    assert all(value in question["text"] for value in ("2", "Istanbul", "Antalya"))
    assert "полный состав каждого номера" in question["text"]
    notes = run["result"]["result"]["operation_normalizations"]
    assert any(note.get("code") == "universal_room_count_not_covered" for note in notes)
    assert len(model.calls) == 1 and travel.calls == [] and await repository.list_orders(sid) == []
