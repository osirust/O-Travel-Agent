"""Speech acts through the harness with controlled model outputs and real PG.

These are behavioral guards, not evidence of actual model classification.
"""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from otravel.domain import sample_trip
from test_api import StubLLM, _headers, _session
from test_api import api_factory as conversation_api_factory
from test_slot_continuation import _message, _reply

api_factory = conversation_api_factory


@pytest.mark.asyncio
async def test_information_question_cannot_start_intake_or_apply_model_city_guess(api_factory):
    model = StubLLM([{"speech_act": "ASK", "information_subject": "Сербия",
        "operations": [{"type": "set_origin", "value": {"city": "Стамбул"}}],
        "unknowns": ["destination.city"], "reply": "Непроверенная цена 999 рублей"}])
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, {})
        run, after = await _message(client, sid, token, "Сербию знаешь?")
        assert after["trip"] == created["trip"]
        assert after["state_version"] == 0 and after["pending_question"] is None
        reply = await _reply(client, sid, token, run)
        assert "Сербия" in reply and "страна" in reply
        assert "999" not in reply and "В какой город" not in reply
        events = (await client.get(f"/api/v1/sessions/{sid}/events", headers=_headers(token))).json()["events"]
        assert not any(e["event_type"] == "TOOL_CALL_STARTED" for e in events)


@pytest.mark.asyncio
async def test_information_question_preserves_existing_country_and_exact_pending_slot(api_factory):
    model = StubLLM([{}, {"speech_act": "ASK", "information_subject": "Стамбул"}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, {})
        _, before = await _message(client, sid, token, "В Сербию хочу")
        run, after = await _message(client, sid, token, "А Стамбул в какой стране?")
        assert after["pending_question"] == before["pending_question"]
        assert after["trip"] == before["trip"]
        assert "Турция" in await _reply(client, sid, token, run)
        assert after["pending_question"]["requested_destination"]["country_code"] == "RS"


@pytest.mark.asyncio
async def test_hypothetical_budget_is_candidate_data_not_trip_edit(api_factory):
    operation = {"type": "set_budget", "value": {"amount_minor": 10_000_000}}
    model = StubLLM([{"speech_act": "PROPOSE", "operations": [operation]}])
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, sample_trip().model_dump(mode="json"))
        run, after = await _message(client, sid, token, "Может, сократить бюджет до ста тысяч?")
        assert after["trip"] == created["trip"] and after["state_version"] == 0
        result = run["result"]["result"]
        assert result["candidate_operations"][0]["type"] == "set_budget"
        assert "поездка не изменена" in await _reply(client, sid, token, run)
        assert result.get("plans", []) == []


@pytest.mark.asyncio
async def test_tentative_country_retains_country_clarification_without_committing_route(api_factory):
    model = StubLLM([{"speech_act": "PROPOSE", "information_subject": "Сербия"}])
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, {})
        run, after = await _message(client, sid, token, "В Сербию может быть поехать?")
        assert after["trip"] == created["trip"]
        assert after["pending_question"]["requested_destination"]["country_code"] == "RS"
        assert "город" in (await _reply(client, sid, token, run)).casefold()


@pytest.mark.asyncio
async def test_minimum_cost_persists_preference_without_invented_budget_or_lost_country(api_factory):
    model = StubLLM([{}, {"operations": [{"type": "set_preference", "value": {
        "key": "price_priority", "value": "lowest_total"}}]}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, {})
        _, before = await _message(client, sid, token, "Сербия")
        _, after = await _message(client, sid, token, "Минимум стоимости")
        assert after["trip"]["budget"] is None
        assert after["trip"]["preferences"] == [{"key": "price_priority", "value": "lowest_total", "weight": 1.0}]
        assert (after["pending_question"] or {}).get("context", {}).get("requested_destination", {}).get("country_code") == "RS"
        expected = deepcopy(before["trip"])
        expected["preferences"] = after["trip"]["preferences"]
        expected["state_version"] = after["trip"]["state_version"]
        assert expected == after["trip"]


@pytest.mark.asyncio
async def test_compound_request_keeps_all_known_facts_even_outside_search_catalogue(api_factory):
    model = StubLLM([{"operations": [
        {"type": "set_origin", "value": {"city": "Москва"}},
        {"type": "upsert_stay", "target_id": "stay-belgrade", "value": {"city": "Белград"}},
        {"type": "add_traveler", "value": {"id": "owner", "type": "adult"}},
        {"type": "set_preference", "value": {"key": "departure_time", "value": "early_morning"}},
    ], "unknowns": ["adult_count", "children_count"]}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, {})
        run, after = await _message(client, sid, token,
            "белград в сербии из москвы рано утром, детей нет, один взрослый")
        assert after["trip"]["origin"] == "Москва"
        assert after["trip"]["stays"][0]["city"] == "Белград"
        assert [p["type"] for p in after["trip"]["travelers"]] == ["adult"]
        assert after["trip"]["budget"] is None
        assert after["trip"]["preferences"] == [{"key": "departure_time", "value": "early_morning", "weight": 1.0}]
        assert after["pending_question"]["field"] not in {"party.adults", "party.children"}
        assert "Сколько взрослых" not in await _reply(client, sid, token, run)
        assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_confirmed_destination_clears_old_country_candidate_and_updates_display_context(api_factory):
    model = StubLLM([{}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, {})
        await _message(client, sid, token, "Сербия")
        await _message(client, sid, token, "Белград")
        await _message(client, sid, token, "Санкт-Петербург")
        _, after = await _message(client, sid, token, "да")
        assert after["trip"]["stays"][0]["city"] == "Санкт-Петербург"
        pending = after["pending_question"]
        assert pending["field"] == "stay.check_in"
        assert pending["context"]["requested_destination"]["name"] == "Санкт-Петербург"
        assert pending["context"]["requested_destination"]["country_code"] == "RU"
        assert not pending["context"].get("confirmation_required")
        assert "proposed_destination" not in pending["context"]


@pytest.mark.asyncio
async def test_absolute_checkin_then_tomorrow_checkout_asks_about_same_date_without_model(api_factory, monkeypatch):
    import otravel.harness as module
    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 25, 12, tzinfo=timezone.utc).astimezone(tz) if tz else cls(2026, 10, 25, 12)
    monkeypatch.setattr(module, "datetime", FixedClock)
    model = StubLLM([{"unknowns": ["stay.check_in"]}])
    initial = {"origin": "Москва", "travelers": [{"id": "owner", "type": "adult"}],
        "stays": [{"id": "chosen-stay", "city": "Санкт-Петербург", "rooms": [{"id": "room", "traveler_ids": ["owner"]}]}]}
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, initial)
        await _message(client, sid, token, "Уточним даты")
        _, checkin = await _message(client, sid, token, "26.10.2026")
        assert checkin["trip"]["stays"][0]["check_in"] == "2026-10-26"
        assert checkin["pending_question"]["field"] == "stay.check_out"
        run, after = await _message(client, sid, token, "завтра")
        assert after["trip"] == checkin["trip"] and after["state_version"] == checkin["state_version"]
        reply = await _reply(client, sid, token, run)
        assert "26.10.2026" in reply and "позже" in reply
        assert after["pending_question"]["target_id"] == "chosen-stay"
        assert after["pending_question"]["field"] == "stay.check_out"
        assert len(model.calls) == 1
