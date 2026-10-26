"""Recorded money errors as controlled wrong model scalars, isolated PG.

The original raw LLM contracts were not recorded. These are harness grounding
checks from observed before/after amounts, not a claim of original NLU replay.
"""
from copy import deepcopy

import pytest
from test_api import StubLLM, _session
from test_api import api_factory as money_api_factory
from test_harness_conversational import known_trip
from test_slot_continuation import _message, _reply

api_factory = money_api_factory


@pytest.mark.asyncio
@pytest.mark.parametrize("text,before,wrong,expected,currency", [
    ("Поставь общий бюджет 75000 рублей на двоих. Это сумма всей поездки, не за ночь и не на человека.", 9_000_000, 9_000_000, 7_500_000, "RUB"),
    ("Добавь к общему бюджету 5000 рублей, остальные условия оставь как есть.", 13_000_000, 13_050_000, 13_500_000, "RUB"),
    ("Уменьши общий бюджет на десять процентов, все люди и даты прежние.", 10_000_000, 9_999_000, 9_000_000, "RUB"),
    ("Общий лимит поставь точно 110000 рублей 50 копеек, не округляй.", 11_000_000, 11_000_000, 11_000_050, "RUB"),
    ("Теперь устанавливаю общий бюджет 2000 USD на всю поездку. Это новая сумма в долларах, рубли по неизвестному курсу не пересчитывай.", 10_000_000, 200_000_000, 200_000, "USD"),
])
async def test_money_caller_grounds_wrong_scalar_and_preserves_every_other_trip_fact(api_factory, text, before, wrong, expected, currency):
    initial = known_trip()
    initial["budget"]["amount_minor"] = before
    model = StubLLM([{"operations": [{"type": "set_budget", "value": {
        "amount_minor": wrong, "currency": currency, "scope": "whole_trip"}}]}])
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, initial)
        run, after = await _message(client, sid, token, text)
        assert len(model.calls) == 1 and run["status"] == "completed"
        saved = deepcopy(created["trip"])
        saved["budget"] = {"amount_minor": expected, "currency": currency, "scope": "whole_trip"}
        saved["state_version"] += expected != before or currency != "RUB"
        assert after["trip"] == saved
        assert after["state_version"] == saved["state_version"]
        repository = client._transport.app.state.store
        assert await repository.list_orders(sid) == []


@pytest.mark.asyncio
async def test_fractional_kopeck_percent_asks_specific_question_and_keeps_original_budget(api_factory):
    initial = known_trip()
    initial["budget"]["amount_minor"] = 101
    model = StubLLM([{"operations": [{"type": "set_budget", "value": {
        "amount_minor": 90, "currency": "RUB", "scope": "whole_trip"}}]}])
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client, initial)
        run, after = await _message(client, sid, token, "Уменьши общий бюджет на 10 процентов")
        assert after["trip"] == before["trip"] and after["state_version"] == before["state_version"]
        assert after["waiting_reason"] == "clarification"
        assert after["pending_question"]["field"] == "budget.amount_minor"
        reply = (await _reply(client, sid, token, run)).casefold()
        assert "копей" in reply and "точн" in reply
        assert "за ночь" not in reply and "на человека" not in reply
        assert len(model.calls) == 1
