"""Controlled ModelResult + isolated PostgreSQL; no model or supplier requests."""
import json
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from otravel.domain import sample_trip
from otravel.providers import LLMGateway
from otravel.store import events
from test_api import StubLLM, _headers, _session, _snapshot, _wait
from test_api import api_factory as context_api_factory
from test_slot_continuation import _message

api_factory = context_api_factory


@pytest.mark.asyncio
async def test_rejected_destination_is_removed_from_persisted_context_and_late_yes_cannot_apply(api_factory):
    model = StubLLM([{}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, {})
        await _message(client, sid, token, "Сербия")
        _, proposed = await _message(client, sid, token, "Стамбул")
        assert proposed["pending_question"]["context"]["confirmation_required"] is True
        _, rejected = await _message(client, sid, token, "нет")
        context = rejected["pending_question"]["context"]
        assert not context.get("confirmation_required")
        assert "proposed_destination" not in context
        assert context["requested_destination"]["country_code"] == "RS"
        assert rejected["trip"] == proposed["trip"]
        _, after_yes = await _message(client, sid, token, "да")
        assert after_yes["trip"] == rejected["trip"]
        assert not after_yes["trip"]["stays"] and after_yes["trip"]["origin"] is None


@pytest.mark.asyncio
async def test_new_country_removes_previous_city_confirmation_without_changing_route(api_factory):
    model = StubLLM([{}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, {})
        await _message(client, sid, token, "Сербия")
        _, before = await _message(client, sid, token, "Стамбул")
        _, after = await _message(client, sid, token, "Россия")
        context = after["pending_question"]["context"]
        assert context["requested_destination"]["country_code"] == "RU"
        assert not context.get("confirmation_required") and "proposed_destination" not in context
        assert after["trip"] == before["trip"]


@pytest.mark.asyncio
async def test_deferred_destination_proposal_cannot_answer_new_origin_question(api_factory):
    model = StubLLM([{}, {"operations": [{"type": "set_preference", "value": {
        "key": "price_priority", "value": "lowest_total"}}]}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, {})
        await _message(client, sid, token, "Сербия")
        await _message(client, sid, token, "Стамбул")
        _, origin_question = await _message(client, sid, token, "Минимум стоимости")
        assert origin_question["pending_question"]["field"] == "origin.city"
        _, after = await _message(client, sid, token, "да")
        assert after["trip"] == origin_question["trip"]
        assert after["trip"]["origin"] is None
        assert after["pending_question"]["context"]["requested_destination"]["country_code"] == "RS"


@pytest.mark.asyncio
async def test_untargeted_date_for_multiple_stays_asks_which_stay_instead_of_binding_first(api_factory):
    initial = sample_trip().model_dump(mode="json")
    for stay in initial["stays"]:
        stay["check_out"] = None
    model = StubLLM([{"unknowns": ["stay.check_out"]}])
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, initial)
        _, after = await _message(client, sid, token, "Выезд завтра")
        assert after["trip"] == created["trip"]
        assert after["pending_question"]["field"] == "stay.check_out"
        assert after["pending_question"]["target_id"] is None
        assert after["pending_question"]["context"]["target_selection_required"] is True
        assert len(model.calls) == 0


@pytest.mark.asyncio
async def test_explicit_checkout_overrides_current_checkin_slot_without_changing_checkin(api_factory):
    initial = {"origin": "Москва", "travelers": [{"id": "owner", "type": "adult"}],
        "stays": [{"id": "stay", "city": "Санкт-Петербург", "rooms": [{"id": "room", "traveler_ids": ["owner"]}]}]}
    model = StubLLM([{"unknowns": ["stay.check_in"]}, {"operations": [
        {"type": "upsert_stay", "target_id": "stay", "value": {"check_in": "2090-10-30"}}]}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, initial)
        await _message(client, sid, token, "Уточним даты")
        _, after = await _message(client, sid, token, "Выезд 30.10.2090")
        assert after["trip"]["stays"][0]["check_in"] is None
        assert after["trip"]["stays"][0]["check_out"] == "2090-10-30"
        assert after["pending_question"]["field"] == "stay.check_in"
        assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_unit_price_cannot_replace_total_budget_and_unambiguous_preference_survives(api_factory):
    model = StubLLM([{"operations": [
        {"type": "set_budget", "value": {"amount_minor": 1_000_000, "currency": "RUB", "scope": "whole_trip"}},
        {"type": "set_preference", "value": {"key": "price_priority", "value": "lowest_total"}}]}])
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client, sample_trip().model_dump(mode="json"))
        _, after = await _message(client, sid, token, "Хочу номер до 10000 рублей за ночь, предпочтительно подешевле")
        assert after["trip"]["budget"] == before["trip"]["budget"]
        assert after["trip"]["preferences"] == [{"key": "price_priority", "value": "lowest_total", "weight": 1.0}]
        assert after["pending_question"]["field"] == "budget.amount_minor"
        assert after["waiting_reason"] == "clarification"


@pytest.mark.asyncio
async def test_audit_distinguishes_model_arguments_from_grounded_values_without_storing_secrets(api_factory, monkeypatch):
    canary = "synthetic-provider-audit-secret-canary"
    monkeypatch.setenv("GROQ_API_KEY", canary)
    model = StubLLM([{"operations": [{"type": "set_budget", "value": {"amount_minor": 100000, "currency": "RUB"}}],
        "reply": "Проверочный ответ " + canary}])
    async with api_factory(model) as (client, _):
        repository = client._transport.app.state.store
        sid, token, _ = await _session(client, sample_trip().model_dump(mode="json"))
        run, after = await _message(client, sid, token, "Общий бюджет теперь 200000 рублей")
        assert after["trip"]["budget"]["amount_minor"] == 20000000
        async with repository.engine.connect() as conn:
            rows = (await conn.execute(select(events.c.event_type, events.c.payload).where(events.c.run_id == run["run_id"]))).mappings().all()
        raw = next(row["payload"] for row in rows if row["event_type"] == "MODEL_EXTRACTION_VALIDATED")
        checked = next(row["payload"] for row in rows if row["event_type"] == "MODEL_OPERATIONS_CHECKED")
        assert raw["structured_contract"]["value"]["operations"][0]["value"]["amount_minor"] == 100000
        assert checked["structured_contract"]["value"]["operations"][0]["value"]["amount_minor"] == 20000000
        assert canary not in str(rows)
        assert "[REDACTED]" in raw["structured_contract"]["value"]["reply"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_kind", ["json", "schema"])
async def test_consumed_tokens_are_persisted_when_generation_fails_validation(api_factory, failure_kind, monkeypatch):
    requests = []
    canary = "synthetic-invalid-generation-secret"
    def handler(request):
        requests.append(request)
        content = "{" if failure_kind == "json" else json.dumps({
            "operations": [{"type": "unknown_operation", "value": {}}],
            "control": {"action": "none"}, "reply": "", "unknowns": [], "conflicts": []})
        return httpx.Response(200, json={
            "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": content}]}}],
            "usageMetadata": {"promptTokenCount": 23, "candidatesTokenCount": 8,
                              "totalTokenCount": 31, "raw_secret": canary}})
    gateway = LLMGateway({"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": canary,
        "GEMINI_MODEL": "gemini-3.8-flash",
        "GEMINI_FREE_TIER_CONFIRMED": True, "http_transport": httpx.MockTransport(handler)})
    async with api_factory(gateway) as (client, _):
        repository = client._transport.app.state.store
        # This test audits failed-generation persistence, with fake HTTP. Keep
        # its real PostgreSQL reservations in the explicit test-adapter bucket
        # with the fixture's high test budget. Unique labels retain provenance;
        # Store intentionally pools these labels rather than isolating quotas.
        reserve = repository.reserve_model_call
        budget_namespace = "test_usage_" + uuid4().hex
        async def reserve_test_call(session_id, run_id, limit, provider):
            assert provider == "gemini" and limit == 40
            return await reserve(session_id, run_id, 1_000_000, budget_namespace)
        monkeypatch.setattr(repository, "reserve_model_call", reserve_test_call)
        sid, token, before = await _session(client, sample_trip().model_dump(mode="json"))
        accepted = await client.post(f"/api/v1/sessions/{sid}/messages", headers=_headers(token),
            json={"client_message_id": str(uuid4()), "text": "Уточним пожелания по отдыху"})
        assert accepted.status_code == 202
        run = await _wait(client, accepted.json()["run_id"], token)
        after = await _snapshot(client, sid, token)
        assert run["status"] == "failed"
        assert requests, run.get("error")
        assert after["trip"] == before["trip"]
        async with repository.engine.connect() as conn:
            rows = (await conn.execute(select(events.c.payload).where(
                events.c.run_id == run["run_id"], events.c.event_type == "MODEL_CALL_FAILED"))).scalars().all()
            reservations = (await conn.execute(select(events.c.payload).where(
                events.c.run_id == run["run_id"], events.c.event_type == "MODEL_CALL_STARTED"))).scalars().all()
        assert len(rows) == len(requests) == 2  # Initial call and one bounded repair.
        assert reservations == [{"provider": budget_namespace}] * len(requests)
        for event in rows:
            assert event["usage"] == {"input_tokens": 23, "output_tokens": 8, "total_tokens": 31}
            assert len(event["request_metrics"]["prompt_sha256"]) == 64
        assert canary not in str(rows) and "raw_secret" not in str(rows)
        assert sum(event["usage"]["total_tokens"] for event in rows) == 31 * len(requests)
