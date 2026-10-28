"""Actual029 country-disambiguation shape, controlled Store/API regression."""
import pytest
from test_api import StubLLM, _session
from test_api import api_factory as country_api_factory
from test_harness_saved_answers import seed_pending
from test_slot_continuation import _message, _reply

api_factory = country_api_factory


@pytest.mark.asyncio
async def test_country_disambiguation_binds_pending_before_geography_qa(api_factory):
    model = StubLLM()
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client, {
            "origin": "Москва", "travelers": [{"id": "owner", "type": "adult"}]})
        repository = client._transport.app.state.store
        await seed_pending(repository, sid, "destination.city", context={
            "proposed_destination": {"kind": "city", "name": "Стамбул", "country_code": "TR"},
            "confirmation_required": True})
        run, after = await _message(client, sid, token, "страна Грузия")
        assert after["trip"] == before["trip"] and len(model.calls) == 0
        pending = after["pending_question"]
        assert pending["field"] == "destination.city"
        assert pending["context"]["requested_destination"]["country_code"] == "GE"
        assert "confirmation_required" not in pending["context"]
        text = await _reply(client, sid, token, run)
        assert "Грузия" in text and "город" in text
        assert await repository.list_orders(sid) == []
