import pytest
from test_api import StubLLM, _session
from test_api import api_factory as wait_api_factory
from test_harness_conversational import known_trip
from test_slot_continuation import _message

api_factory = wait_api_factory


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["К поездке ему исполнится18. Что ещё нужно уточнить для такого состава? Пока ничего не меняй.",
    "Пока ничего не меняй."])
async def test_actual_hypothesis_no_edit_does_not_require_invented_resume(api_factory, monkeypatch, text):
    model = StubLLM([{"operations": [], "control": {"action": "wait"}, "speech_act": "CHANGE",
        "unknowns": [], "conflicts": [], "reply": "Подожду уточнений."},
        {"operations": [{"type": "set_budget", "value": {"amount_minor": 14500000, "currency": "RUB"}}],
            "control": {"action": "continue"}, "unknowns": [], "conflicts": [], "speech_act": "CHANGE", "reply": "Бюджет предложен."}])
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client, known_trip())
        harness = client._transport.app.state.harness
        original_search = harness._search_trip
        async def forbidden_search(*_args, **_kwargs):
            raise AssertionError("A model wait/no-op must not start a new search")
        monkeypatch.setattr(harness, "_search_trip", forbidden_search)
        _, first = await _message(client, sid, token, text)
        assert first["trip"] == before["trip"] and first["waiting_reason"] != "user_pause"
        monkeypatch.setattr(harness, "_search_trip", original_search)
        _, second = await _message(client, sid, token, "Общий бюджет теперь145000 рублей.")
        assert second["trip"]["budget"]["amount_minor"] == 14500000 and len(model.calls) == 2
        assert not any(row["event_type"] == "USER_PAUSED" for row in await client._transport.app.state.store.list_events(sid))


@pytest.mark.asyncio
async def test_explicit_user_pause_remains_a_real_control_barrier(api_factory):
    model = StubLLM([{"operations": [], "control": {"action": "wait"}, "speech_act": "CHANGE",
        "unknowns": [], "conflicts": [], "reply": "Жду."}])
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client, known_trip())
        _, after = await _message(client, sid, token, "Поставь на паузу, я ещё дополню.")
        assert after["waiting_reason"] == "user_pause"
        assert after["trip"]["origin"] == before["trip"]["origin"]
