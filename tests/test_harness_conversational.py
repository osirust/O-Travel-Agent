"""Owner009 last four turns: cached adapter outputs + isolated PG, not NLU.

The initial route is seeded, intentionally avoiding the separately owned first
literal-route fix. No model or supplier HTTP is performed by these tests.
"""
from uuid import uuid4

import pytest
from test_api import StubLLM, _headers, _plans, _preview, _session, _snapshot, _typed, _wait
from test_api import api_factory as response_api_factory
from test_slot_continuation import _message, _reply

api_factory = response_api_factory


def known_trip():
    return {"origin": "Москва", "travelers": [{"id": "existing-adult", "type": "adult"}],
        "stays": [{"id": "kazan-stay", "city": "Казань", "check_in": "2026-11-13", "check_out": "2026-11-18",
                   "rooms": [{"id": "one-room", "traveler_ids": ["existing-adult"]}]}],
        "legs": [{"id": "out", "origin": "Москва", "destination": "Казань", "departure_date": "2026-11-13"},
                 {"id": "back", "origin": "Казань", "destination": "Москва", "departure_date": "2026-11-18"}],
        "budget": {"amount_minor": 7_500_000, "currency": "RUB", "scope": "whole_trip"}}


async def initial_search(client, sid, token):
    before = await _snapshot(client, sid, token)
    response = await client.post(f"/api/v1/sessions/{sid}/search", headers=_headers(token), json={
        "client_message_id": str(uuid4()), "expected_version": before["state_version"], "mode": "DEMO"})
    assert response.status_code == 202
    run = await _wait(client, response.json()["run_id"], token)
    assert run["status"] == "completed"
    assert _plans(run)
    return run


@pytest.mark.asyncio
async def test_owner009_hypothetical_noop_does_not_repeat_search(api_factory, monkeypatch):
    model = StubLLM([{"speech_act": "CHANGE", "control": {"action": "continue"}, "operations": [],
        "reply": "Ребёнок уже добавлен, новая стоимость подтверждена."}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, known_trip())
        await initial_search(client, sid, token)
        before = await _snapshot(client, sid, token)
        harness = client._transport.app.state.harness

        async def forbidden_search(*args, **kwargs):
            raise AssertionError("Informational no-op must not perform another search")

        monkeypatch.setattr(harness, "_search_trip", forbidden_search)
        run, after = await _message(client, sid, token,
            "А если взять ребёнка семи лет, сможешь пересобрать поездку? Пока не добавляй его.")
        assert after["trip"] == before["trip"] and after["state_version"] == before["state_version"]
        assert after["status"] == before["status"]
        reply = await _reply(client, sid, token, run)
        assert "не измен" in reply.casefold() and "пересобрать" in reply.casefold()
        assert "уже добавлен" not in reply and "подтверждена" not in reply


@pytest.mark.asyncio
async def test_owner009_last_four_turns_keep_actual_edits_and_render_current_facts(api_factory, monkeypatch):
    model = StubLLM([
        {"speech_act": "CHANGE", "control": {"action": "continue"}, "operations": [],
         "reply": "Могу пересобрать с ребёнком, пока не добавляю."},
        {"speech_act": "CHANGE", "control": {"action": "continue"}, "operations": [
            {"type": "add_traveler", "value": {"id": "new-child", "type": "child", "age": 7}},
            {"type": "upsert_stay", "target_id": "kazan-stay", "value": {"rooms": [
                {"id": "one-room", "traveler_ids": ["existing-adult", "new-child"]}]}}],
         "reply": "Добавлен ребёнок 7 лет."},
        {"speech_act": "CHANGE", "control": {"action": "continue"}, "operations": [
            {"type": "update_traveler", "target_id": "new-child", "value": {"age": 8}}],
         "reply": "1 взрослый existing-adult и 1 ребёнок 8 лет new-child."},
        {"speech_act": "CHANGE", "control": {"action": "continue"}, "operations": [],
         "reply": "Все представленные планы демонстрационные."},
    ])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, known_trip())
        seeded = await initial_search(client, sid, token)
        harness = client._transport.app.state.harness
        search = harness._search_trip
        calls = []

        async def observed_search(*args, **kwargs):
            calls.append(True)
            return await search(*args, **kwargs)

        monkeypatch.setattr(harness, "_search_trip", observed_search)
        before = await _snapshot(client, sid, token)
        hypo, unchanged = await _message(client, sid, token,
            "А если взять ребёнка семи лет, сможешь пересобрать поездку? Пока не добавляй его.")
        assert unchanged["trip"] == before["trip"] and not calls
        assert _plans(hypo) == _plans(seeded)
        added, child7 = await _message(client, sid, token,
            "Теперь действительно добавь одного ребёнка семи лет. Я, существующий взрослый, остаюсь.")
        assert len(calls) == 1 and child7["state_version"] == before["state_version"] + 1
        assert child7["trip"]["travelers"][0] == before["trip"]["travelers"][0]
        assert child7["trip"]["travelers"][1]["age"] == 7
        assert "7 лет" in await _reply(client, sid, token, added)
        corrected, child8 = await _message(client, sid, token, "Ребёнку всёже восемь, не семь. Напомни наш текущий состав.")
        assert len(calls) == 2 and child8["state_version"] == child7["state_version"] + 1
        assert child8["trip"]["travelers"][1]["age"] == 8
        roster = await _reply(client, sid, token, corrected)
        assert "1 взрослый" in roster and "1 ребёнок" in roster and "8 лет" in roster
        assert "existing-adult" not in roster and "new-child" not in roster and "7 лет" not in roster
        cart, after = await _message(client, sid, token,
            "Первый план доступен только как демонстрационный? Можно посмотреть тестовую корзину без покупки?")
        assert len(calls) == 2 and after["trip"] == child8["trip"]
        assert _plans(cart) == _plans(corrected)
        payload = cart["result"]["result"]
        assert payload["plans_preserved"] is True
        assert payload["suggested_action"] == {"type": "preview_cart", "plan_id": _plans(corrected)[0]["plan_id"],
                                               "state_version": after["state_version"]}
        reply = await _reply(client, sid, token, cart)
        assert "демонстрационный" in reply and "вымышлены" in reply and "без покупки" in reply
        assert not reply.startswith("Подобрано DEMO-планов")
        repository = client._transport.app.state.store
        assert await repository.list_orders(sid) == []
        events = await repository.list_events(sid)
        assert not any(event["event_type"] in {"QUOTE_CREATED", "ORDER_CREATED", "TOOL_CALL_STARTED"} for event in events)
        assert len(model.calls) == 4  # Controlled outputs, not actual language-model calls.


@pytest.mark.asyncio
async def test_cart_noop_preserves_existing_quote_and_never_creates_another(api_factory, monkeypatch):
    model = StubLLM([{"control": {"action": "continue"}}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, known_trip())
        seeded = await initial_search(client, sid, token)
        before = await _snapshot(client, sid, token)
        quote = await _preview(client, sid, token, _plans(seeded)[0], before["state_version"])
        repository = client._transport.app.state.store
        original = await repository.get_quote(quote["quote_id"])

        async def forbidden_search(*args, **kwargs):
            raise AssertionError("Cart information cannot search or refresh an offer")

        monkeypatch.setattr(client._transport.app.state.harness, "_search_trip", forbidden_search)
        run, after = await _message(client, sid, token, "Первый план демонстрационный? Можно открыть тестовую корзину?")
        assert after["trip"] == before["trip"]
        assert (await repository.get_quote(quote["quote_id"])) == original
        assert run["result"]["result"]["suggested_action"]["plan_id"] == _plans(seeded)[0]["plan_id"]
        assert sum(e["event_type"] == "QUOTE_CREATED" for e in await repository.list_events(sid)) == 1
        assert await repository.list_orders(sid) == []


@pytest.mark.asyncio
async def test_information_noop_preserves_exact_pending_question_without_starting_intake(api_factory, monkeypatch):
    model = StubLLM([{"unknowns": ["origin.city"]},
                     {"control": {"action": "continue"}, "unknowns": ["destination.city"]}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, {})
        _, before = await _message(client, sid, token, "Соберём поездку")
        assert before["pending_question"]

        async def forbidden_search(*args, **kwargs):
            raise AssertionError("An answer about the current party is not a search")

        monkeypatch.setattr(client._transport.app.state.harness, "_search_trip", forbidden_search)
        run, after = await _message(client, sid, token, "Напомни наш текущий состав")
        assert after["pending_question"] == before["pending_question"]
        assert after["trip"] == before["trip"] and after["waiting_reason"] == before["waiting_reason"]
        assert "пока не указан" in await _reply(client, sid, token, run)


@pytest.mark.asyncio
async def test_latest_empty_plan_batch_blocks_older_nonempty_cart_suggestion(api_factory, monkeypatch):
    model = StubLLM([{"control": {"action": "continue"}}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, known_trip())
        await initial_search(client, sid, token)
        harness = client._transport.app.state.harness

        async def empty_search(*args, **kwargs):
            return {"plans": [], "questions": [], "source_type": "DEMO"}

        monkeypatch.setattr(harness, "_search_trip", empty_search)
        before = await _snapshot(client, sid, token)
        response = await client.post(f"/api/v1/sessions/{sid}/search", headers=_headers(token), json={
            "client_message_id": str(uuid4()), "expected_version": before["state_version"], "mode": "DEMO"})
        assert response.status_code == 202
        empty = await _wait(client, response.json()["run_id"], token)
        assert empty["status"] == "completed" and _plans(empty) == []

        async def forbidden_search(*args, **kwargs):
            raise AssertionError("No fallback search is authorized by an information question")

        monkeypatch.setattr(harness, "_search_trip", forbidden_search)
        run, after = await _message(client, sid, token, "Первый план демонстрационный? Можно открыть тестовую корзину?")
        assert "suggested_action" not in run["result"]["result"]
        assert _plans(run) == [] and "нет" in await _reply(client, sid, token, run)
        assert after["state_version"] == before["state_version"]


@pytest.mark.asyncio
async def test_stale_version_plans_do_not_become_a_current_cart(api_factory, monkeypatch):
    model = StubLLM([{"control": {"action": "continue"}}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, known_trip())
        await initial_search(client, sid, token)
        before = await _snapshot(client, sid, token)
        await _typed(client, sid, token, before["state_version"], [{"type": "set_budget", "value": {
            "amount_minor": 7_600_000, "currency": "RUB", "scope": "whole_trip"}}], search=False)
        changed = await _snapshot(client, sid, token)
        assert changed["state_version"] == before["state_version"] + 1

        async def forbidden_search(*args, **kwargs):
            raise AssertionError("An old plan must not cause implicit search")

        monkeypatch.setattr(client._transport.app.state.harness, "_search_trip", forbidden_search)
        run, after = await _message(client, sid, token, "Первый план демонстрационный? Можно открыть корзину?")
        assert after["trip"] == changed["trip"] and not _plans(run)
        assert "suggested_action" not in run["result"]["result"]
        assert await client._transport.app.state.store.list_orders(sid) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["Напомни наш текущий состав",
    "А если взять ребёнка семи лет, сможешь пересобрать поездку? Пока не добавляй его."])
async def test_empty_trip_information_noop_never_starts_intake_or_assumes_people(api_factory, monkeypatch, text):
    model = StubLLM([{"control": {"action": "continue"}, "unknowns": ["origin.city", "party.adults"],
                     "reply": "Добавлен один взрослый и ребёнок."}])
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client, {})

        async def forbidden_search(*args, **kwargs):
            raise AssertionError("Empty trip information does not authorize search/intake")

        monkeypatch.setattr(client._transport.app.state.harness, "_search_trip", forbidden_search)
        run, after = await _message(client, sid, token, text)
        assert after["trip"] == before["trip"] and after["state_version"] == 0
        assert after["trip"]["travelers"] == [] and after["trip"]["stays"] == []
        assert after["pending_question"] is None and after["waiting_reason"] is None
        assert after["status"] == before["status"]
        reply = await _reply(client, sid, token, run)
        assert "В какой город" not in reply and "Сколько взрослых" not in reply and "Добавлен" not in reply
        assert "suggested_action" not in run["result"]["result"]
        assert _plans(run) == []


@pytest.mark.asyncio
async def test_real_change_with_roster_question_retains_missing_fact_flow(api_factory, monkeypatch):
    model = StubLLM([{"operations": [{"type": "add_traveler", "value": {"id": "actual-adult", "type": "adult"}}],
                     "control": {"action": "continue"}}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, {})
        harness = client._transport.app.state.harness
        search = harness._search_trip
        calls = []

        async def observed_search(*args, **kwargs):
            calls.append(True)
            return await search(*args, **kwargs)

        monkeypatch.setattr(harness, "_search_trip", observed_search)
        run, after = await _message(client, sid, token, "Добавь одного взрослого. Напомни наш текущий состав.")
        assert after["state_version"] == 1 and len(after["trip"]["travelers"]) == 1
        assert calls and after["pending_question"] is not None and after["waiting_reason"] == "clarification"
        reply = await _reply(client, sid, token, run)
        assert "1 взрослый" in reply and "actual-adult" not in reply
        assert after["pending_question"]["text"] in reply


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "Второму ребёнку семь лет. Можно посмотреть план в тестовой корзине?",
    "Первый план демонстрационный. Корзину не открывай, только объясни источник.",
    "Если выберем второй план, можно посмотреть тестовую корзину?",
])
async def test_cart_suggestions_need_a_local_nonhypothetical_positive_request(api_factory, monkeypatch, text):
    model = StubLLM([{"control": {"action": "continue"}, "reply": "Я уже открыл корзину."}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, known_trip())
        harness = client._transport.app.state.harness
        search = harness._search_trip

        async def two_actual_plans(*args, **kwargs):
            found = await search(*args, **kwargs)
            return {**found, "plans": found["plans"][:2]}

        monkeypatch.setattr(harness, "_search_trip", two_actual_plans)
        shown = await initial_search(client, sid, token)
        assert len(_plans(shown)) == 2
        before = await _snapshot(client, sid, token)

        async def forbidden_search(*args, **kwargs):
            raise AssertionError("Cart information/hypothesis is not a new search")

        monkeypatch.setattr(harness, "_search_trip", forbidden_search)
        run, after = await _message(client, sid, token, text)
        assert after["trip"] == before["trip"] and after["state_version"] == before["state_version"]
        assert "suggested_action" not in run["result"]["result"]
        assert _plans(run) == _plans(shown)
        reply = await _reply(client, sid, token, run)
        assert "Я уже открыл" not in reply
        if "не открывай" in text:
            assert "демонстрационный" in reply and "Уточните" not in reply and "Можно открыть" not in reply
        repository = client._transport.app.state.store
        assert await repository.list_orders(sid) == []
        assert not any(e["event_type"] == "QUOTE_CREATED" for e in await repository.list_events(sid))


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["Можешь подобрать варианты по этой поездке?", "Можешь подобрать ещё?",
    "Можно поискать ещё?", "Подберёшь ещё варианты?"])
async def test_explicit_polite_search_question_executes_search_without_invented_edits(api_factory, monkeypatch, text):
    model = StubLLM([{"control": {"action": "continue"}}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, known_trip())
        await initial_search(client, sid, token)
        before = await _snapshot(client, sid, token)
        harness = client._transport.app.state.harness
        search = harness._search_trip
        calls = []

        async def observed_search(*args, **kwargs):
            calls.append(True)
            return await search(*args, **kwargs)

        monkeypatch.setattr(harness, "_search_trip", observed_search)
        run, after = await _message(client, sid, token, text)
        assert len(calls) == 1 and _plans(run)
        assert after["trip"] == before["trip"] and after["state_version"] == before["state_version"]
        assert "suggested_action" not in run["result"]["result"]
        assert not await client._transport.app.state.store.list_orders(sid)


@pytest.mark.asyncio
async def test_empty_who_question_without_model_unknowns_is_summary_without_intake(api_factory, monkeypatch):
    model = StubLLM([{"control": {"action": "continue"}}])
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client, {})

        async def forbidden_search(*args, **kwargs):
            raise AssertionError("A bare roster summary is not a search")

        monkeypatch.setattr(client._transport.app.state.harness, "_search_trip", forbidden_search)
        run, after = await _message(client, sid, token, "Кто едет?")
        assert after["trip"] == before["trip"] and after["pending_question"] is None
        assert after["state_version"] == 0 and after["waiting_reason"] is None
        assert "Состав поездки пока не указан" in await _reply(client, sid, token, run)


@pytest.mark.asyncio
async def test_server_owned_money_scope_question_survives_ask_none_information(api_factory, monkeypatch):
    from otravel.harness import ModelResult

    async with api_factory() as (client, model):
        sid, token, before = await _session(client, known_trip())
        harness = client._transport.app.state.harness

        async def code_owned_scope(session, run):
            return ModelResult.model_validate({"operations": [], "control": {"action": "none"},
                "speech_act": "ASK", "reply": "Справочный ответ.", "unknowns": ["Какой общий бюджет поездки?"], "conflicts": [],
                "pending_context": {"field": "budget.amount_minor", "budget_scope_ambiguous": True}}), None

        monkeypatch.setattr(harness, "_interpret", code_owned_scope)
        run, after = await _message(client, sid, token, "Напомни наш текущий состав? Уточним общий бюджет.")
        assert after["trip"] == before["trip"] and after["state_version"] == before["state_version"]
        assert after["pending_question"]["field"] == "budget.amount_minor"
        assert after["pending_question"]["context"]["budget_scope_ambiguous"] is True
        reply = await _reply(client, sid, token, run)
        assert "1 взрослый" in reply and after["pending_question"]["text"] in reply
        assert model.calls == []


@pytest.mark.asyncio
async def test_real_money_guard_is_not_suppressed_by_a_roster_information_question(api_factory):
    model = StubLLM([{"operations": [{"type": "set_budget", "value": {"amount_minor": 1_000_000,
        "currency": "RUB", "scope": "whole_trip"}}], "control": {"action": "continue"}}])
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client, known_trip())
        run, after = await _message(client, sid, token, "Номер до 10000 рублей за ночь. Напомни наш текущий состав?")
        assert after["trip"]["budget"] == before["trip"]["budget"]
        assert after["state_version"] == before["state_version"]
        assert after["pending_question"]["field"] == "budget.amount_minor"
        assert after["pending_question"]["context"]["budget_scope_ambiguous"] is True
        assert after["pending_question"]["text"] in await _reply(client, sid, token, run)
