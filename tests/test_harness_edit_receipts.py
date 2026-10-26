"""Compound edits plus questions, with controlled model output and PostgreSQL."""
from copy import deepcopy

import pytest
from test_answering import answer
from test_api import StubLLM, _session
from test_api import api_factory as receipt_api_factory
from test_harness_conversational import initial_search, known_trip
from test_slot_continuation import _message, _reply

api_factory = receipt_api_factory


@pytest.mark.asyncio
async def test_price_preference_and_question_survive_new_plan_summary(api_factory):
    # Actual pilot001:T10: extraction was correct, generic plan text hid the answer.
    model = StubLLM([{"operations": [{"type": "set_preference", "value": {
        "key": "price_priority", "value": "lowest_total", "weight": 1.0}}],
        "reply": "Подобрано DEMO-планов: 3.", "speech_act": "CHANGE"}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, known_trip())
        await initial_search(client, sid, token)
        run, after = await _message(client, sid, token,
            "Ищи минимум стоимости. Напомни, какой город выбран, откуда выезжаем и сколько нас сейчас?")
        text = await _reply(client, sid, token, run)
        assert after["trip"]["preferences"], run["result"]
        assert after["trip"]["origin"] == "Москва"
        assert after["trip"]["stays"][0]["city"] == "Казань"
        assert "Москва" in text and "Казань" in text and "1 взросл" in text
        assert "75 000" in text and "DEMO" in text
        assert "route" in run["result"]["result"]["answer_sections"]
        assert len(model.calls) == 1 and "__response_mode" not in model.calls[0]


@pytest.mark.asyncio
async def test_child_edit_and_current_party_question_report_new_age(api_factory):
    initial = known_trip()
    initial["travelers"].append({"id": "existing-child", "type": "child", "age": 7})
    initial["stays"][0]["rooms"][0]["traveler_ids"].append("existing-child")
    model = StubLLM([{"operations": [{"type": "update_traveler", "target_id": "existing-child", "value": {"age": 8}}],
        "reply": "План пересчитан.", "speech_act": "CHANGE"}])
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client, initial)
        run, after = await _message(client, sid, token, "Ребёнку всё же восемь, не семь. Напомни наш текущий состав.")
        expected = deepcopy(before["trip"])
        expected["travelers"][1]["age"] = 8
        expected["state_version"] += 1
        assert after["trip"] == expected
        text = await _reply(client, sid, token, run)
        assert "8" in text and "1 взросл" in text and "1 реб" in text
        assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_order_and_party_recall_use_readonly_sections(api_factory):
    model = StubLLM([answer("orders,party,budget")])
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client, known_trip())
        run, after = await _message(client, sid, token,
            "Напомни состав и стоимость уже созданного тестового заказа. Новый заказ не создавай.")
        text = await _reply(client, sid, token, run)
        assert "Сохранённых тестовых заказов в этой поездке нет" in text
        assert after["trip"] == before["trip"]
        assert model.calls[0]["__response_mode"] == "readonly_answer_v1"
        assert await client._transport.app.state.store.list_orders(sid) == []


@pytest.mark.asyncio
async def test_saved_facts_remain_visible_before_required_room_question(api_factory):
    initial = known_trip()
    initial["stays"][0]["rooms"] = []
    model = StubLLM([{"operations": [{"type": "set_budget", "value": {
        "amount_minor": 9_000_000, "currency": "RUB"}}], "speech_act": "CHANGE"}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, initial)
        run, after = await _message(client, sid, token,
            "Поставь общий бюджет 90 тысяч рублей. Напомни, сколько нас и откуда едем?")
        text = await _reply(client, sid, token, run)
        assert "90 000" in text and "Москва" in text and "1 взросл" in text
        assert "номер" in text and after["pending_question"]["field"] == "stay.rooms"
        assert after["trip"]["budget"]["amount_minor"] == 9_000_000
        assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_actual_cf_omitted_price_preference_is_grounded_and_answered(api_factory):
    # CF6009644 case11: model omitted the explicit edit while answering a question.
    raw = {"operations": [], "control": {"action": "continue"}, "speech_act": "ASK",
           "reply": "Сейчас выбран город Казань, едет 1 взрослый.", "unknowns": [], "conflicts": []}
    model = StubLLM([raw])
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client, known_trip())
        run, after = await _message(client, sid, token,
            'Ищи минимум стоимсти. Напомни, какой город сейчас выбран и сколько нас едет.')
        assert after['trip']['origin'] == before['trip']['origin'] == 'Москва'
        assert after['trip']['preferences'][0]['value'] == 'lowest_total'
        text = await _reply(client, sid, token, run)
        assert 'Казань' in text and '1 взросл' in text and '75 000' in text
        notes = run['result']['result']['operation_normalizations']
        assert any(n.get('finding_id') == 'AFTER80-PRICE-INTAKE-001' for n in notes)
        assert len(model.calls) == 1
