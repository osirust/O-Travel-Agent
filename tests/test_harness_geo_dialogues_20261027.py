"""Connected12-turn geo/party/pause regression on real PG, controlled model.

Model answers are injected, supplier HTTP forbidden. This is not actual NLU.
"""
from copy import deepcopy
from uuid import uuid4

import pytest
from test_api import StubLLM, _headers, _session, _snapshot, _wait
from test_api import api_factory as geo_api_factory
from test_harness_saved_answers import seed_pending
from test_slot_continuation import RAW_FIELD, _message, _reply

from otravel.geography import parse_destination

api_factory = geo_api_factory


async def assert_turn(client, sid, token, text):
    run, snapshot = await _message(client, sid, token, text)
    assert run["status"] == "completed" and not run.get("error")
    reply = await _reply(client, sid, token, run)
    assert not RAW_FIELD.search(reply)
    return run, snapshot, reply


@pytest.mark.asyncio
async def test_twelve_turns_country_refusal_typo_additions_and_pause_preserve_owned_trip(api_factory):
    model = StubLLM([
        # Exact city-in-country now asks through the bound server slot; it
        # does not consume a model answer before the independent child edit.
        {"operations": [{"type": "add_traveler", "value": {"id": "child", "type": "child", "age": 7}},
            {"type": "upsert_stay", "target_id": "hotel", "value": {"city": "Стамбул"}}]},
        {"operations": [{"type": "update_traveler", "target_id": "child", "value": {"age": 8}}]},
        {"operations": [{"type": "set_budget", "value": {"amount_minor": 7_000_000, "currency": "RUB"}}]},
    ])
    initial = {"origin": "Москва", "travelers": [{"id": "a", "type": "adult"}],
        "stays": [{"id": "hotel", "city": "Белград"}],
        "budget": {"amount_minor": 8_000_000, "currency": "RUB"}, "search_policy": {"mode": "DEMO"}}
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, initial)
        repository = client._transport.app.state.store
        await seed_pending(repository, sid, "destination.city", target="hotel", context={
            "requested_destination": parse_destination("Сербия")["requested_destination"]})
        _, first, _ = await assert_turn(client, sid, token, "Стамбул")
        assert first["trip"] == created["trip"] and first["pending_question"]["context"]["confirmation_required"] is True
        _, second, _ = await assert_turn(client, sid, token, "нет")
        assert second["trip"] == created["trip"]
        assert "proposed_destination" not in second["pending_question"]["context"]
        _, third, _ = await assert_turn(client, sid, token, "да")
        assert third["trip"] == created["trip"] and third["pending_question"]["context"]["requested_destination"]["country_code"] == "RS"
        _, fourth, acknowledgement = await assert_turn(client, sid, token, "Белгород.")
        assert fourth["trip"] == created["trip"]
        assert "Понял как «Белград», Сербия." in acknowledgement
        _, fifth, question = await assert_turn(client, sid, token, "Стамбул в Сербии")
        assert fifth["trip"] == created["trip"] and "Турция" in question
        _, sixth, _ = await assert_turn(client, sid, token, "Нет, добавь ребёнка семи лет. Взрослый остаётся.")
        assert sixth["trip"]["travelers"] == [*created["trip"]["travelers"],
            {"id": "child", "type": "child", "age": 7, "birth_date": None, "group_id": None}]
        assert sixth["trip"]["stays"] == created["trip"]["stays"]
        assert sixth["pending_question"]["context"]["requested_destination"]["country_code"] == "RS"
        assert "proposed_destination" not in sixth["pending_question"]["context"]
        _, seventh, _ = await assert_turn(client, sid, token, "да")
        assert seventh["trip"] == sixth["trip"]
        _, eighth, _ = await assert_turn(client, sid, token, "Стамбул")
        assert eighth["trip"] == sixth["trip"]
        _, ninth, _ = await assert_turn(client, sid, token, "Да, исправь возраст ребёнка на восемь лет.")
        assert ninth["trip"]["stays"][0]["id"] == "hotel" and ninth["trip"]["stays"][0]["city"] == "Стамбул"
        assert next(p["age"] for p in ninth["trip"]["travelers"] if p["id"] == "child") == 8
        assert ninth["trip"]["origin"] == "Москва" and ninth["trip"]["budget"] == created["trip"]["budget"]
        assert ninth["pending_question"]["context"]["requested_destination"]["country_code"] == "TR"
        _, tenth = await _message(client, sid, token, "подожди")
        assert tenth["waiting_reason"] == "user_pause"
        paused = deepcopy(tenth["trip"])
        calls = len(model.calls)
        accepted = await client.post(f"/api/v1/sessions/{sid}/messages", headers=_headers(token),
            json={"client_message_id": str(uuid4()), "expected_version": tenth["state_version"],
                  "text": "Теперь установи общий бюджет 70000 рублей."})
        assert accepted.status_code == 202
        queued = await _snapshot(client, sid, token)
        assert queued["trip"] == paused and queued["waiting_reason"] == "user_pause" and len(model.calls) == calls
        await _message(client, sid, token, "продолжай")
        budget_run = await _wait(client, accepted.json()["run_id"], token)
        assert budget_run["status"] == "completed"
        final = await _snapshot(client, sid, token)
        assert final["trip"]["budget"]["amount_minor"] == 7_000_000
        for field in ("origin", "travelers", "groups", "stays", "legs", "preferences", "constraints"):
            assert final["trip"][field] == paused[field]
        assert len(model.calls) == 3
        events = await repository.list_events(sid)
        assert not any(e["event_type"] in {"TOOL_CALL_STARTED", "QUOTE_CREATED", "DEMO_ORDER_CREATED"} for e in events)
        assert await repository.list_orders(sid) == []


@pytest.mark.asyncio
async def test_eight_turns_turkish_city_typo_refusal_origin_and_age_are_independent(api_factory):
    model = StubLLM([
        {"operations": [{"type": "set_origin", "value": {"city": "Москва"}}]},
        {"operations": [{"type": "update_traveler", "target_id": "child", "value": {"age": 8}}]},
    ])
    initial = {"origin": "Казань", "travelers": [{"id": "a", "type": "adult"}, {"id": "child", "type": "child", "age": 7}],
        "stays": [{"id": "hotel"}], "budget": {"amount_minor": 9_000_000, "currency": "RUB"},
        "search_policy": {"mode": "DEMO"}}
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, initial)
        repository = client._transport.app.state.store
        await seed_pending(repository, sid, "destination.city", target="hotel", context={
            "requested_destination": parse_destination("Турция")["requested_destination"]})
        _, first, text = await assert_turn(client, sid, token, "Антальа")
        assert first["trip"] == created["trip"] and "Анталья" in text
        assert first["pending_question"]["context"]["proposed_destination"]["country_code"] == "TR"
        _, second, _ = await assert_turn(client, sid, token, "нет")
        assert "proposed_destination" not in second["pending_question"]["context"]
        _, third, _ = await assert_turn(client, sid, token, "да")
        assert third["trip"] == created["trip"]
        _, fourth, _ = await assert_turn(client, sid, token, "Анталья")
        assert fourth["trip"]["stays"][0]["city"] == "Анталья" and len(model.calls) == 0
        _, fifth, facts = await assert_turn(client, sid, token,
            "Анталья и Стамбул в одной стране? Ничего менять не надо.")
        assert fifth["trip"] == fourth["trip"] and "Турц" in facts
        _, sixth, _ = await assert_turn(client, sid, token,
            "Измени только отправление: теперь из Москвы. Анталья и бюджет прежние.")
        assert sixth["trip"]["origin"] == "Москва"
        assert sixth["trip"]["stays"] == fourth["trip"]["stays"]
        _, seventh, _ = await assert_turn(client, sid, token,
            "Ребёнку восемь лет, не семь. Исправь только возраст ребёнка.")
        assert next(p["age"] for p in seventh["trip"]["travelers"] if p["id"] == "child") == 8
        _, eighth, facts = await assert_turn(client, sid, token,
            "В какой стране Анталья? Это наше действующее направление?")
        assert eighth["trip"] == seventh["trip"] and "Турц" in facts
        assert eighth["trip"]["budget"] == created["trip"]["budget"]
        assert len(model.calls) == 2
        assert not any(e["event_type"] == "TOOL_CALL_STARTED" for e in await repository.list_events(sid))
        assert await repository.list_orders(sid) == []


@pytest.mark.asyncio
async def test_six_turns_initial_country_conflict_saves_party_budget_then_compound_accepts_new_stay(api_factory):
    model = StubLLM([
        {"operations": [{"type": "set_origin", "value": {"city": "Москва"}},
            {"type": "add_traveler", "value": {"id": "a", "type": "adult"}},
            {"type": "add_traveler", "value": {"id": "b", "type": "adult"}},
            {"type": "set_budget", "value": {"amount_minor": 10_000_000, "currency": "RUB"}},
            {"type": "upsert_stay", "target_id": "new-stay-1", "value": {"city": "Стамбул"}}]},
        {"operations": [{"type": "add_traveler", "value": {"id": "child", "type": "child", "age": 7}}]},
        {"operations": [{"type": "set_budget", "value": {"amount_minor": 9_500_000, "currency": "RUB"}}]},
    ])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, {"search_policy": {"mode": "DEMO"}})
        repository = client._transport.app.state.store
        _, first, text = await assert_turn(client, sid, token,
            "Стамбул в Сербии. Из Москвы. Нас двое взрослых, общий бюджет 100000 рублей.")
        assert "Турц" in text and first["trip"]["origin"] == "Москва" and len(first["trip"]["travelers"]) == 2
        assert first["trip"]["budget"]["amount_minor"] == 10_000_000 and first["trip"]["stays"] == []
        _, second, _ = await assert_turn(client, sid, token, "Да, добавь ребёнка семи лет.")
        assert len(second["trip"]["stays"]) == 1 and second["trip"]["stays"][0]["city"] == "Стамбул"
        assert second["trip"]["stays"][0]["id"] == first["pending_question"]["target_id"]
        assert len(second["trip"]["travelers"]) == 3 and second["trip"]["budget"] == first["trip"]["budget"]
        _, third, text = await assert_turn(client, sid, token, "Стамбул в Турции? Это вопрос, ничего не меняй.")
        assert third["trip"] == second["trip"] and "Турц" in text
        _, paused = await _message(client, sid, token, "подожди")
        assert paused["waiting_reason"] == "user_pause"
        accepted = await client.post(f"/api/v1/sessions/{sid}/messages", headers=_headers(token), json={
            "client_message_id": str(uuid4()), "expected_version": paused["state_version"],
            "text": "Общий бюджет установи 95000 рублей."})
        assert accepted.status_code == 202 and (await _snapshot(client, sid, token))["trip"] == paused["trip"]
        await _message(client, sid, token, "продолжай")
        assert (await _wait(client, accepted.json()["run_id"], token))["status"] == "completed"
        final = await _snapshot(client, sid, token)
        assert final["trip"]["budget"]["amount_minor"] == 9_500_000
        for field in ("origin", "travelers", "groups", "stays", "legs", "preferences", "constraints"):
            assert final["trip"][field] == paused["trip"][field]
        assert len(model.calls) == 3
        assert not any(e["event_type"] == "TOOL_CALL_STARTED" for e in await repository.list_events(sid))
        assert await repository.list_orders(sid) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("first_text,origin,destination,typo_field,question", [
    ("Из Мосвкы в Анталью хочу. Один взрослый без детей, общий бюджет 90000 рублей.",
     "Москва", "Анталья", "origin.city", "Анталья в Турции? Это вопрос, ничего не меняй."),
    ("Из Казани в Санкт-Петербрг хочу. Один взрослый без детей, общий бюджет 90000 рублей.",
     "Казань", "Санкт-Петербург", "destination.city", "Питер и Санкт-Петербург один город? Ничего не меняй."),
])
async def test_six_turns_typo_role_confirmation_and_paused_budget_keep_other_facts(api_factory, first_text, origin, destination, typo_field, question):
    model = StubLLM([
        {"operations": [{"type": "set_origin", "value": {"city": origin}},
            {"type": "add_traveler", "value": {"id": "a", "type": "adult"}},
            {"type": "upsert_stay", "target_id": "new-stay-1", "value": {"city": destination}},
            {"type": "set_budget", "value": {"amount_minor": 9_000_000, "currency": "RUB"}}]},
        {"operations": [{"type": "set_budget", "value": {"amount_minor": 8_500_000, "currency": "RUB"}}]},
    ])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, {"search_policy": {"mode": "DEMO"}})
        repository = client._transport.app.state.store
        _, first, text = await assert_turn(client, sid, token, first_text)
        pending = first["pending_question"]
        assert pending["field"] == typo_field and pending["context"]["proposal_field"] == typo_field
        assert "имеете в виду" in text and first["trip"]["budget"]["amount_minor"] == 9_000_000
        assert len(first["trip"]["travelers"]) == 1
        if typo_field == "origin.city":
            assert first["trip"]["origin"] is None and first["trip"]["stays"][0]["city"] == destination
        else:
            assert first["trip"]["origin"] == origin and first["trip"]["stays"] == []
        _, second, _ = await assert_turn(client, sid, token, "да")
        assert second["trip"]["origin"] == origin and len(second["trip"]["stays"]) == 1
        assert second["trip"]["stays"][0]["city"] == destination
        _, third, text = await assert_turn(client, sid, token, question)
        assert third["trip"] == second["trip"]
        assert "Турц" in text if destination == "Анталья" else "одного города" in text
        _, paused = await _message(client, sid, token, "подожди")
        assert paused["waiting_reason"] == "user_pause"
        accepted = await client.post(f"/api/v1/sessions/{sid}/messages", headers=_headers(token), json={
            "client_message_id": str(uuid4()), "expected_version": paused["state_version"],
            "text": "Установи общий бюджет 85000 рублей."})
        assert accepted.status_code == 202 and (await _snapshot(client, sid, token))["trip"] == paused["trip"]
        await _message(client, sid, token, "продолжай")
        assert (await _wait(client, accepted.json()["run_id"], token))["status"] == "completed"
        final = await _snapshot(client, sid, token)
        assert final["trip"]["budget"]["amount_minor"] == 8_500_000
        for field in ("origin", "travelers", "groups", "stays", "legs", "preferences", "constraints"):
            assert final["trip"][field] == paused["trip"][field]
        assert len(model.calls) == 2
        assert not any(e["event_type"] == "TOOL_CALL_STARTED" for e in await repository.list_events(sid))
        assert await repository.list_orders(sid) == []
