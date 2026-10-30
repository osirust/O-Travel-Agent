"""Captured075 question binding through real API/PG, with no remote calls."""
import pytest
from test_api import StubLLM, _session
from test_api import api_factory as question_api_factory
from test_slot_continuation import _message, _reply

from otravel.clarifications import question_field

api_factory = question_api_factory
TEXT = ("Из Москвы в Дубай 12–18 декабря 2026. Один взрослый без детей, один номер. "
        "Общий бюджет 125000 рублей.")
UNKNOWN = ("Количество ночей (6 дней — уточните, сколько ночей) и возраст взрослого, "
           "если Known: от 18 до 120.")
RAW = {"reply": "", "control": {"action": "continue"}, "speech_act": "CHANGE",
       "information_subject": None, "unknowns": [UNKNOWN], "conflicts": [],
       "operations": [
           {"type": "set_origin", "value": {"city": "Москва"}, "target_id": None},
           {"type": "set_route_policy", "value": {"kind": "round_trip"}, "target_id": None},
           {"type": "add_traveler", "value": {"id": "new-traveler-1", "age": None,
                                             "type": "adult"}, "target_id": None},
           {"type": "upsert_stay", "target_id": "new-stay-1", "value": {
               "city": "Дубай", "rooms": [{"id": "new-room-1", "traveler_ids": ["new-traveler-1"]}],
               "check_in": "2026-12-12", "check_out": "2026-12-18", "requested_nights": None,
               "hotel_id": None}},
           {"type": "set_budget", "target_id": None, "value": {
               "scope": "whole_trip", "currency": "RUB", "amount_minor": 12500000}}]}


@pytest.mark.asyncio
async def test_captured075_question_text_matches_owned_children_slot_and_preserves_facts(api_factory):
    model = StubLLM([RAW])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client)
        run, after = await _message(client, sid, token, TEXT)
        assert len(model.calls) == 1
        assert [(p["type"], p["age"]) for p in after["trip"]["travelers"]] == [("adult", None)]
        stay = after["trip"]["stays"][0]
        assert (stay["check_in"], stay["check_out"]) == ("2026-12-12", "2026-12-18")
        assert stay["rooms"][0]["traveler_ids"] == ["new-traveler-1"]
        assert after["trip"]["budget"]["amount_minor"] == 12500000
        pending = after["pending_question"]
        assert pending["field"] == "party.children"
        assert question_field(pending["text"]) == pending["field"]
        assert pending["questions"] == [pending["text"]]
        reply = await _reply(client, sid, token, run)
        assert pending["text"] in reply and "Сколько взрослых" not in reply
        store = client._transport.app.state.store
        events = await store.list_events(sid)
        assert not any(e["event_type"] in {"TOOL_CALL_STARTED", "QUOTE_CREATED", "ORDER_CREATED",
                                          "DEMO_ORDER_CREATED"} for e in events)
        assert await store.list_orders(sid) == []
        _, answered = await _message(client, sid, token, "Дети не едут")
        assert len(model.calls) == 1, "A bound child-count answer must not ask the model again"
        assert answered["state_version"] == after["state_version"]
        assert answered["trip"] == after["trip"]
        pending = answered.get("pending_question") or {}
        assert pending.get("field") not in {"party.children", "party.adults"}
        assert [(p["id"], p["type"], p["age"]) for p in answered["trip"]["travelers"]] == [
            ("new-traveler-1", "adult", None)]
