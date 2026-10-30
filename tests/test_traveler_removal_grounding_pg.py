"""Actual008 final JSON replay through PG/ASGI with controlled adapters only."""
import json
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import func, select
from test_api import StubLLM, _session
from test_api import api_factory as removal_api_factory
from test_slot_continuation import _message, _reply

from otravel.store import quotes

api_factory = removal_api_factory


@pytest.mark.asyncio
async def test_actual_budget_and_roster_question_cannot_delete_second_adult(api_factory):
    fixture = json.loads((Path(__file__).parents[1] / "fixtures/regression/traveler-removal-008-20261027.json")
        .read_text(encoding="utf-8"))
    model = StubLLM([deepcopy(fixture["raw_contract"]["value"])])
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, deepcopy(fixture["before_trip"]))
        before = created["trip"]
        run, after = await _message(client, sid, token, fixture["input"])
        assert run["status"] == "completed"
        assert len(model.calls) == 1
        assert after["trip"]["budget"]["amount_minor"] == 8500000
        assert after["trip"]["travelers"] == before["travelers"]
        assert len(after["trip"]["travelers"]) == 2
        for field in ("origin", "stays", "legs", "groups", "constraints", "preferences", "route_policy", "search_policy"):
            assert after["trip"][field] == before[field], field
        reply = await _reply(client, sid, token, run)
        assert "2 взрослых" in reply
        assert "85 000" in reply
        assert "удален один" not in reply.casefold().replace("ё", "е")
        store = client._transport.app.state.store
        events = await store.list_events(sid)
        checked = next(event["payload"] for event in events if event["event_type"] == "MODEL_OPERATIONS_CHECKED")
        assert any(note.get("code") == "traveler_removal_not_requested" for note in checked["normalizations"])
        contract = checked["structured_contract"]["value"]
        assert [op["type"] for op in contract["operations"]] == ["set_budget"]
        assert contract["operations"][0]["value"]["amount_minor"] == 8500000
        assert await store.list_orders(sid) == []
        async with store.engine.connect() as connection:
            assert await connection.scalar(select(func.count()).select_from(quotes).where(quotes.c.session_id == sid)) == 0
