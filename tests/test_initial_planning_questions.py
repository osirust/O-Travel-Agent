"""Original106 NLU01 question loss; controlled response and real isolated PG."""
import pytest
from test_api import StubLLM, _session
from test_api import api_factory as planning_api_factory
from test_slot_continuation import _message, _reply

api_factory = planning_api_factory
ACTUAL = {"speech_act": "ASK", "control": {"action": "continue"}, "operations": [], "conflicts": [],
    "information_subject": None,
    "unknowns": ["Откуда вы планируете отправляться?", "Какой бюджет вы рассматриваете?",
        "В какие конкретные даты вы хотите поехать?", "Сколько человек будет путешествовать?"],
    "reply": "Понятно, вы ищете теплое место для поездки осенью по доступной цене. Чтобы помочь вам найти подходящие варианты, мне нужно уточнить несколько деталей:"}


@pytest.mark.asyncio
async def test_original_nlu01_ask_retains_one_grounded_missing_question(api_factory):
    model = StubLLM([ACTUAL])
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, {"search_policy": {"mode": "HYBRID",
            "hotel_provider": "liteapi", "flight_provider": "duffel", "guest_nationality": None}})
        run, after = await _message(client, sid, token, "Хочу в тепло осенью, где недорого.")
        assert after["trip"] == created["trip"] and after["state_version"] == 0
        assert after["status"] == "WAITING_USER" and after["waiting_reason"] == "clarification"
        assert after["pending_question"]["field"] == "origin.city"
        assert "город" in (await _reply(client, sid, token, run)).casefold()
        assert len(model.calls) == 1
        events = await client._transport.app.state.store.list_events(sid)
        assert not any(row["event_type"] == "TOOL_CALL_STARTED" for row in events)
