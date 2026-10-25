"""RUNTIME-001/002 regression edges; real PG, controlled adapters, zero vendor HTTP."""

import asyncio
from copy import deepcopy

import pytest

from otravel.domain import sample_trip
from otravel.providers import ProviderError
from test_api import api_factory, StubLLM, _headers, _session, _snapshot, _trip, _wait


def _answer(operations=None, control="none", unknowns=None, conflicts=None):
    return {"operations": operations or [], "control": {"action": control}, "reply": "",
            "unknowns": unknowns or [], "conflicts": conflicts or []}


@pytest.mark.asyncio
async def test_explicit_wait_survives_rejected_unknown_traveler_patch(api_factory):
    model = StubLLM([_answer([{"type": "update_traveler", "target_id": "unknown-child-id", "value": {"age": 4}}], control="wait")])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, sample_trip().model_dump(mode="json"))
        path = f"/api/v1/sessions/{sid}"
        before = _trip(await _snapshot(client, sid, token))
        posted = await client.post(path + "/messages", json={"client_message_id": "wait-invalid-child", "text": "Подожди, я ещё дополню: исправь гостя"}, headers=_headers(token))
        await _wait(client, posted.json()["run_id"], token)
        after = await _snapshot(client, sid, token)
        assert after["waiting_reason"] == "user_pause" and after["pending_question"] is None
        assert _trip(after) == before
        queued = await client.post(path + "/messages", json={"client_message_id": "still-adding", "text": "Я добавлю ещё условие"}, headers=_headers(token))
        await asyncio.sleep(0.05)
        assert len(model.calls) == 1
        polled = await client.get(f"/api/v1/runs/{queued.json()['run_id']}", headers=_headers(token))
        assert polled.json()["status"] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("declared", [True, False])
async def test_conflicts_block_patch_but_preserve_explicit_wait(declared, api_factory):
    operations = [{"type": "set_budget", "value": {"amount_minor": 20_000_000}},
                  {"type": "set_budget", "value": {"amount_minor": 30_000_000}}]
    model = StubLLM([_answer(operations, control="wait", conflicts=["Какой бюджет окончательный?"] if declared else [])])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, sample_trip().model_dump(mode="json"))
        before = await _snapshot(client, sid, token)
        posted = await client.post(f"/api/v1/sessions/{sid}/messages", json={"client_message_id": "wait-conflict", "text": "Пауза. Бюджет уточню позже"}, headers=_headers(token))
        await _wait(client, posted.json()["run_id"], token)
        after = await _snapshot(client, sid, token)
        assert after["waiting_reason"] == "user_pause"
        assert _trip(after) == _trip(before) and after["state_version"] == before["state_version"]


@pytest.mark.asyncio
async def test_missing_age_is_clarification_and_answer_can_continue_without_resume(api_factory):
    model = StubLLM([
        _answer([{"type": "add_traveler", "value": {"id": "child-pending-age", "type": "child", "group_id": "family-1"}}], control="wait", unknowns=["Сколько лет ребёнку?"]),
        _answer([{"type": "update_traveler", "target_id": "child-pending-age", "value": {"age": 6}}]),
    ])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, sample_trip().model_dump(mode="json"))
        path = f"/api/v1/sessions/{sid}/messages"
        first = await client.post(path, json={"client_message_id": "unknown-age", "text": "Добавь ребёнка, возраст пока не назвал"}, headers=_headers(token))
        await _wait(client, first.json()["run_id"], token)
        waiting = await _snapshot(client, sid, token)
        assert waiting["waiting_reason"] == "clarification"
        assert waiting["pending_question"] and "лет" in waiting["pending_question"]["text"]
        second = await client.post(path, json={"client_message_id": "age-answer", "text": "Шесть лет"}, headers=_headers(token))
        await _wait(client, second.json()["run_id"], token)
        after = await _snapshot(client, sid, token)
        assert len(model.calls) == 2 and after["waiting_reason"] != "user_pause"
        people = _trip(after)["travelers"]
        assert len(people) == 10
        assert next(person for person in people if person["id"] == "child-pending-age")["age"] == 6


@pytest.mark.asyncio
async def test_cancel_ignores_invalid_proposed_patch_instead_of_losing_stop(api_factory):
    model = StubLLM([_answer([{"type": "update_traveler", "target_id": "unknown-child-id", "value": {"age": 4}}], control="cancel")])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, sample_trip().model_dump(mode="json"))
        before = _trip(await _snapshot(client, sid, token))
        posted = await client.post(f"/api/v1/sessions/{sid}/messages", json={"client_message_id": "cancel-invalid-patch", "text": "Прерви текущую обработку"}, headers=_headers(token))
        result = await _wait(client, posted.json()["run_id"], token)
        after = _trip(await _snapshot(client, sid, token))
        assert result["status"] == "cancelled"
        assert {key: value for key, value in after.items() if key != "state_version"} == {key: value for key, value in before.items() if key != "state_version"}


@pytest.mark.asyncio
async def test_unknown_target_without_pause_hint_stays_clarification(api_factory):
    model = StubLLM([_answer([{"type": "update_traveler", "target_id": "unknown-child-id", "value": {"age": 4}}], control="wait")])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, sample_trip().model_dump(mode="json"))
        posted = await client.post(f"/api/v1/sessions/{sid}/messages", json={"client_message_id": "target-clarification", "text": "Этому ребёнку четыре"}, headers=_headers(token))
        await _wait(client, posted.json()["run_id"], token)
        assert (await _snapshot(client, sid, token))["waiting_reason"] == "clarification"


class _DelayedModel(StubLLM):
    def __init__(self, error=False):
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.error = error

    async def extract_operations(self, context):
        self.calls.append(deepcopy(context))
        self.started.set()
        await self.release.wait()
        if self.error:
            raise ProviderError("INVALID_LLM_RESPONSE", "Controlled invalid schema")
        return _answer([{"type": "set_budget", "value": {"amount_minor": 79_000_000}}], control="continue")


class _SpyTravel:
    def __init__(self, block_first=False):
        self.calls = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block_first = block_first

    async def resolve_hotels(self, *args, **kwargs):
        self.calls.append("resolve_hotels")
        self.started.set()
        if self.block_first:
            await self.release.wait()
        return [{"id": "controlled-provider-hotel"}]

    async def search_hotels(self, *args, **kwargs):
        self.calls.append("search_hotels")
        return []

    async def search_flights(self, *args, **kwargs):
        self.calls.append("search_flights")
        return []


def _hybrid_trip():
    state = sample_trip().model_dump(mode="json")
    state["search_policy"] = {"mode": "HYBRID", "hotel_provider": "liteapi", "flight_provider": "duffel", "guest_nationality": "RU", "demo_fallback": True}
    return state


@pytest.mark.asyncio
async def test_second_api_instance_pause_fences_tools_after_first_worker_model(api_factory):
    model = _DelayedModel()
    async with api_factory(model) as (first, _), api_factory() as (second, _):
        spy = _SpyTravel()
        first._transport.app.state.harness.travel = spy
        sid, token, _ = await _session(first, _hybrid_trip())
        path = f"/api/v1/sessions/{sid}"
        posted = await first.post(path + "/messages", json={"client_message_id": "cross-worker-stop", "text": "Пересобери поездку"}, headers=_headers(token))
        await asyncio.wait_for(model.started.wait(), 5)
        paused = await second.post(path + "/control", json={"action": "pause"}, headers=_headers(token))
        assert paused.status_code == 200
        model.release.set()
        result = await _wait(first, posted.json()["run_id"], token)
        assert result["status"] in {"failed", "conflict", "interrupted"}
        assert spy.calls == []
        after = await _snapshot(first, sid, token)
        assert after["waiting_reason"] == "user_pause"
        assert _trip(after)["budget"]["amount_minor"] == 80_000_000


@pytest.mark.asyncio
async def test_second_api_instance_pause_prevents_next_component_call(api_factory):
    model = StubLLM([_answer(control="continue")])
    async with api_factory(model) as (first, _), api_factory() as (second, _):
        spy = _SpyTravel(block_first=True)
        first._transport.app.state.harness.travel = spy
        sid, token, _ = await _session(first, _hybrid_trip())
        path = f"/api/v1/sessions/{sid}"
        posted = await first.post(path + "/messages", json={"client_message_id": "stop-between-components", "text": "Покажи варианты"}, headers=_headers(token))
        await asyncio.wait_for(spy.started.wait(), 5)
        await second.post(path + "/control", json={"action": "pause"}, headers=_headers(token))
        spy.release.set()
        result = await _wait(first, posted.json()["run_id"], token)
        assert result["status"] in {"failed", "conflict", "interrupted"}
        # The first call was already in-flight; cancellation cannot unsend it.
        assert spy.calls == ["resolve_hotels"]
        assert (await _snapshot(first, sid, token))["waiting_reason"] == "user_pause"


@pytest.mark.asyncio
async def test_second_api_instance_pause_prevents_model_schema_retry(api_factory):
    model = _DelayedModel(error=True)
    async with api_factory(model) as (first, _), api_factory() as (second, _):
        sid, token, _ = await _session(first, sample_trip().model_dump(mode="json"))
        path = f"/api/v1/sessions/{sid}"
        posted = await first.post(path + "/messages", json={"client_message_id": "stop-before-retry", "text": "Измени поездку"}, headers=_headers(token))
        await asyncio.wait_for(model.started.wait(), 5)
        await second.post(path + "/control", json={"action": "pause"}, headers=_headers(token))
        model.release.set()
        result = await _wait(first, posted.json()["run_id"], token)
        assert result["status"] in {"failed", "conflict", "interrupted"}
        assert len(model.calls) == 1
        assert (await _snapshot(first, sid, token))["waiting_reason"] == "user_pause"
