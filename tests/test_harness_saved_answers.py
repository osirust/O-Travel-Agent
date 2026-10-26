"""Saved-fact answers through ASGI + isolated PostgreSQL, not actual NLU.

The model and supplier gateways are controlled. These tests do not contact the
8084 runtime, model APIs, or live travel providers. Original corpora are unused.
"""

from __future__ import annotations

import asyncio
import re
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from test_api import StubLLM, _headers, _plans, _preview, _session, _snapshot, _typed, _wait
from test_api import api_factory as saved_answer_api_factory
from test_harness_conversational import initial_search, known_trip
from test_slot_continuation import _message, _reply

from otravel.date_slots import resolve_date_answer
from otravel.domain import sample_trip

api_factory = saved_answer_api_factory

SIDE_EFFECT_EVENTS = {
    "MODEL_CALL_STARTED",
    "LLM_REQUEST_STARTED",
    "TOOL_CALL_STARTED",
    "LIVE_SEARCH_STARTED",
    "QUOTE_CREATED",
    "ORDER_CREATED",
}


def payload(run):
    return run["result"]["result"]


async def forbid_actions(client, monkeypatch):
    """Forbid execution rather than merely counting successfully returned calls."""
    harness = client._transport.app.state.harness
    repository = client._transport.app.state.store

    async def forbidden(*args, **kwargs):
        raise AssertionError("A canonical saved-fact answer must not extract, search, or check out")

    monkeypatch.setattr(harness, "_extract", forbidden)
    monkeypatch.setattr(harness, "_search_trip", forbidden)
    monkeypatch.setattr(repository, "create_quote", forbidden)
    monkeypatch.setattr(repository, "confirm_quote", forbidden)


async def event_baseline(repository, sid):
    return {row["event_id"] for row in await repository.list_events(sid)}


async def assert_no_actions(repository, sid, baseline):
    new = [row for row in await repository.list_events(sid) if row["event_id"] not in baseline]
    assert not any(row["event_type"] in SIDE_EFFECT_EVENTS for row in new)
    assert await repository.list_orders(sid) == []


async def seed_pending(repository, sid, field, *, target=None, context=None):
    """Persist one precise question via the same Store/CAS contract as a worker."""
    current = await repository.get_session(sid)
    run, _ = await repository.intake_message(
        sid, str(uuid4()), "controlled pending setup", current["state_version"]
    )
    assert await repository.claim_run(run["id"])
    pending = {
        "question_id": "saved-answer-question-" + str(uuid4()),
        "text": "Уточните сохранённый параметр поездки?",
        "questions": ["Уточните сохранённый параметр поездки?"],
        "field": field,
        "target_id": target,
        "state_version": current["state_version"],
        "reason": "clarification",
        "context": deepcopy(context or {}),
    }
    await repository.finish_run(
        run["id"],
        current["state_version"],
        None,
        "WAITING_USER",
        pending["text"],
        pending_question=pending,
        waiting_reason="clarification",
        result={"controlled_fixture": True},
    )
    return pending


def assert_unchanged(before, after):
    for key in ("trip", "state_version", "status", "waiting_reason", "pending_question"):
        assert after[key] == before[key], key


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question,kind",
    [
        ("Какой у нас сейчас бюджет?", "budget"),
        ("На какие даты у нас сейчас поездка?", "dates"),
        ("Какой у нас сохранённый маршрут?", "route"),
        ("Почему первый план подходит под наши пожелания?", "why"),
    ],
)
async def test_saved_fact_question_uses_no_model_and_preserves_current_quote(
    api_factory, monkeypatch, question, kind
):
    model = StubLLM()
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, known_trip())
        searched = await initial_search(client, sid, token)
        before = await _snapshot(client, sid, token)
        quote = await _preview(client, sid, token, _plans(searched)[0], before["state_version"])
        repository = client._transport.app.state.store
        quote_before = await repository.get_quote(quote["quote_id"])
        baseline = await event_baseline(repository, sid)
        await forbid_actions(client, monkeypatch)
        run, after = await _message(client, sid, token, question)
        assert payload(run)["response_source"] == "canonical_saved_answer"
        assert not model.calls and not run.get("llm")
        assert_unchanged(before, after)
        assert await repository.get_quote(quote["quote_id"]) == quote_before
        if "plans" in payload(run):
            assert [plan["plan_id"] for plan in _plans(run)] == [plan["plan_id"] for plan in _plans(searched)]
        reply = await _reply(client, sid, token, run)
        if kind == "budget":
            assert "75000" in re.sub(r"\s", "", reply)
        elif kind == "dates":
            assert any(value in reply for value in ("13.11.2026", "2026-11-13"))
            assert any(value in reply for value in ("18.11.2026", "2026-11-18"))
        elif kind == "route":
            assert "Москва" in reply and "Казань" in reply
        else:
            assert any(value in reply.casefold() for value in ("demo", "демонстрац", "вымышлен", "синтет"))
        await assert_no_actions(repository, sid, baseline)


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_kind", ["budget_scope", "date_proposal"])
async def test_other_saved_fact_preserves_exact_owned_pending_context(api_factory, monkeypatch, pending_kind):
    model = StubLLM()
    async with api_factory(model) as (client, _):
        initial = sample_trip().model_dump(mode="json") if pending_kind == "date_proposal" else known_trip()
        sid, token, created = await _session(client, initial)
        repository = client._transport.app.state.store
        if pending_kind == "date_proposal":
            packet = resolve_date_answer(
                created["trip"], None, "Выезд 14.11.2026", datetime(2026, 10, 25, 12, tzinfo=UTC)
            )["pending_context"]
            assert "date_proposal" in packet
            await seed_pending(repository, sid, "stay.check_out", context=packet)
            question = "Какой у нас сейчас бюджет?"
        else:
            await seed_pending(
                repository,
                sid,
                "budget.amount_minor",
                context={"budget_scope_ambiguous": True, "children_known": True, "children_count": 0},
            )
            question = "Какой у нас сохранённый маршрут?"
        before = await _snapshot(client, sid, token)
        baseline = await event_baseline(repository, sid)
        await forbid_actions(client, monkeypatch)
        run, after = await _message(client, sid, token, question)
        assert payload(run)["response_source"] == "canonical_saved_answer"
        assert_unchanged(before, after)
        assert not model.calls
        await assert_no_actions(repository, sid, baseline)


@pytest.mark.asyncio
async def test_recognized_saved_question_renderer_failure_never_falls_back_to_model(api_factory, monkeypatch):
    from otravel import trip_answers

    async with api_factory(StubLLM()) as (client, model):
        sid, token, _ = await _session(client, known_trip())
        searched = await initial_search(client, sid, token)
        repository = client._transport.app.state.store
        snap = await _snapshot(client, sid, token)
        quote = await _preview(client, sid, token, _plans(searched)[0], snap["state_version"])
        await seed_pending(repository, sid, "budget.amount_minor", context={"budget_scope_ambiguous": True})
        before = await _snapshot(client, sid, token)
        quote_before = await repository.get_quote(quote["quote_id"])
        baseline = await event_baseline(repository, sid)
        monkeypatch.setattr(trip_answers, "render_saved_answer", lambda *args, **kwargs: None)
        await forbid_actions(client, monkeypatch)
        run, after = await _message(client, sid, token, "Какой у нас сейчас бюджет?")
        assert payload(run)["response_source"] == "canonical_saved_answer" and not model.calls
        reply = (await _reply(client, sid, token, run)).casefold()
        assert "не удалось" in reply and "сохран" in reply
        assert_unchanged(before, after)
        assert await repository.get_quote(quote["quote_id"]) == quote_before
        await assert_no_actions(repository, sid, baseline)


@pytest.mark.asyncio
async def test_latest_explicit_empty_batch_does_not_revive_prior_same_version_plans(api_factory, monkeypatch):
    async with api_factory(StubLLM()) as (client, model):
        sid, token, _ = await _session(client, known_trip())
        await initial_search(client, sid, token)
        harness = client._transport.app.state.harness

        async def no_matches(*args, **kwargs):
            return {"plans": [], "questions": [], "source_type": "DEMO", "violations": [], "warnings": []}

        monkeypatch.setattr(harness, "_search_trip", no_matches)
        before = await _snapshot(client, sid, token)
        accepted = await client.post(
            f"/api/v1/sessions/{sid}/search",
            headers=_headers(token),
            json={
                "client_message_id": str(uuid4()),
                "expected_version": before["state_version"],
                "mode": "DEMO",
            },
        )
        assert accepted.status_code == 202
        empty_run = await _wait(client, accepted.json()["run_id"], token)
        assert empty_run["status"] == "completed" and payload(empty_run)["plans"] == []
        before = await _snapshot(client, sid, token)
        await forbid_actions(client, monkeypatch)
        run, after = await _message(client, sid, token, "Почему первый план подходит под наши пожелания?")
        assert payload(run)["response_source"] == "canonical_saved_answer" and not _plans(run)
        assert_unchanged(before, after)
        assert not model.calls and "suggested_action" not in payload(run)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["foreign_session", "foreign_trip", "future_version", "stale_version"])
async def test_noncurrent_or_foreign_plan_is_not_a_saved_why_answer(api_factory, monkeypatch, invalid):
    async with api_factory(StubLLM()) as (client, model):
        sid, token, _ = await _session(client, known_trip())
        searched = await initial_search(client, sid, token)
        repository = client._transport.app.state.store
        old_id = searched["run_id"]
        original = repository.get_run
        altered = deepcopy(await original(old_id))
        if invalid == "stale_version":
            before_edit = await _snapshot(client, sid, token)
            edited, _ = await _typed(
                client,
                sid,
                token,
                before_edit["state_version"],
                [{"type": "set_budget", "value": {"amount_minor": 8_200_000, "currency": "RUB"}}],
            )
            assert edited["status"] == "completed"
        elif invalid == "foreign_session":
            altered["session_id"] = "not-this-session"
        else:
            for plan in altered["result"]["result"]["plans"]:
                if invalid == "foreign_trip":
                    plan["trip_id"] = "not-this-trip"
                else:
                    plan["state_version"] += 1

        async def substituted(run_id):
            return deepcopy(altered) if run_id == old_id else await original(run_id)

        monkeypatch.setattr(repository, "get_run", substituted)
        before = await _snapshot(client, sid, token)
        await forbid_actions(client, monkeypatch)
        run, after = await _message(client, sid, token, "Почему первый план подходит под наши пожелания?")
        assert payload(run)["response_source"] == "canonical_saved_answer"
        assert not _plans(run) and "suggested_action" not in payload(run)
        assert_unchanged(before, after)
        assert not model.calls


@pytest.mark.asyncio
async def test_ambiguous_why_question_asks_plan_position_without_model(api_factory, monkeypatch):
    async with api_factory(StubLLM()) as (client, model):
        sid, token, _ = await _session(client, known_trip())
        searched = await initial_search(client, sid, token)
        assert len(_plans(searched)) > 1
        before = await _snapshot(client, sid, token)
        await forbid_actions(client, monkeypatch)
        run, after = await _message(client, sid, token, "Почему этот план подходит?")
        assert payload(run)["response_source"] == "canonical_saved_answer"
        reply = (await _reply(client, sid, token, run)).casefold()
        assert any(value in reply for value in ("план", "вариант"))
        assert any(value in reply for value in ("уточните", "какой", "номер"))
        assert "suggested_action" not in payload(run)
        assert_unchanged(before, after)
        assert not model.calls


@pytest.mark.asyncio
async def test_future_assistant_batch_after_input_sequence_cannot_hide_actual_own_plan(
    api_factory, monkeypatch
):
    async with api_factory(StubLLM()) as (client, model):
        sid, token, _ = await _session(client, known_trip())
        searched = await initial_search(client, sid, token)
        repository = client._transport.app.state.store
        list_messages = repository.list_messages
        get_run = repository.get_run
        future_id = str(uuid4())
        future = deepcopy(await get_run(searched["run_id"]))
        future["id"] = future["run_id"] = future_id
        future["result"]["result"]["plans"] = []

        async def messages_with_future(session_id):
            rows = await list_messages(session_id)
            if session_id == sid:
                rows.append(
                    {
                        "id": "controlled-future-message",
                        "session_id": sid,
                        "role": "assistant",
                        "text": "controlled future empty batch",
                        "run_id": future_id,
                        "sequence": max(row["sequence"] for row in rows) + 1000,
                    }
                )
            return rows

        async def run_with_future(run_id):
            return deepcopy(future) if run_id == future_id else await get_run(run_id)

        monkeypatch.setattr(repository, "list_messages", messages_with_future)
        monkeypatch.setattr(repository, "get_run", run_with_future)
        before = await _snapshot(client, sid, token)
        await forbid_actions(client, monkeypatch)
        run, after = await _message(client, sid, token, "Почему первый план подходит под наши пожелания?")
        assert payload(run)["response_source"] == "canonical_saved_answer" and not model.calls
        reply = (await _reply(client, sid, token, run)).casefold()
        assert any(value in reply for value in ("demo", "демонстрац", "вымышлен", "синтет"))
        if "plans" in payload(run):
            assert [plan["plan_id"] for plan in _plans(run)] == [plan["plan_id"] for plan in _plans(searched)]
        assert_unchanged(before, after)


@pytest.mark.asyncio
@pytest.mark.parametrize("spoof_marker", [False, True])
async def test_mixed_edit_or_forged_model_marker_cannot_enter_trusted_saved_answer(api_factory, spoof_marker):
    output = {
        "speech_act": "CHANGE",
        "operations": [
            {
                "type": "set_budget",
                "value": {"amount_minor": 8_100_000, "currency": "RUB", "scope": "whole_trip"},
            }
        ],
        "control": {"action": "none"},
    }
    if spoof_marker:
        output["response_source"] = "canonical_saved_answer"
    async with api_factory(StubLLM([output])) as (client, model):
        sid, token, before = await _session(client, known_trip())
        text = (
            "Измени общий бюджет на 81000 рублей"
            if spoof_marker
            else "Измени общий бюджет на 81000 рублей и напомни, какой у нас маршрут."
        )
        run, after = await _message(client, sid, token, text)
        assert len(model.calls) == 1
        assert after["trip"]["budget"]["amount_minor"] == 8_100_000
        assert after["state_version"] == before["state_version"] + 1
        assert after["trip"]["stays"] == before["trip"]["stays"]
        assert run.get("llm") is not None
        assert payload(run)["response_source"] == "llm"


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["1", "да", "завтра"])
async def test_short_bound_answer_keeps_priority_over_canonical_saved_question(api_factory, answer):
    initial = known_trip()
    if answer == "1":
        initial["travelers"] = []
        initial["stays"][0]["rooms"] = []
    elif answer == "завтра":
        today = datetime.now(ZoneInfo("Europe/Moscow")).date()
        initial["stays"][0]["check_in"] = today.isoformat()
        initial["stays"][0]["check_out"] = None
        initial["legs"] = []
    async with api_factory(StubLLM()) as (client, model):
        sid, token, _ = await _session(client, initial)
        repository = client._transport.app.state.store
        if answer == "1":
            await seed_pending(repository, sid, "party.adults")
        elif answer == "да":
            await seed_pending(
                repository,
                sid,
                "destination.city",
                target="kazan-stay",
                context={
                    "requested_destination": {"kind": "country", "name": "Сербия", "country_code": "RS"},
                    "proposed_destination": {"kind": "city", "name": "Санкт-Петербург", "country_code": "RU"},
                    "confirmation_required": True,
                    "proposal_field": "destination.city",
                },
            )
        else:
            await seed_pending(repository, sid, "stay.check_out", target="kazan-stay")
        run, after = await _message(client, sid, token, answer)
        assert payload(run)["response_source"] == "contextual_slot" and not model.calls
        if answer == "1":
            assert [person["type"] for person in after["trip"]["travelers"]] == ["adult"]
        elif answer == "да":
            assert after["trip"]["origin"] == "Москва"
            assert after["trip"]["stays"][0]["city"] == "Санкт-Петербург"
        else:
            assert after["trip"]["stays"][0]["check_out"] == (today + timedelta(days=1)).isoformat()


@pytest.mark.asyncio
async def test_saved_fact_question_during_user_pause_remains_pending_without_resuming(
    api_factory, monkeypatch
):
    async with api_factory(StubLLM()) as (client, model):
        sid, token, _ = await _session(client, known_trip())
        response = await client.post(
            f"/api/v1/sessions/{sid}/control", headers=_headers(token), json={"action": "pause"}
        )
        assert response.status_code == 200
        before = await _snapshot(client, sid, token)
        await forbid_actions(client, monkeypatch)
        response = await client.post(
            f"/api/v1/sessions/{sid}/messages",
            headers=_headers(token),
            json={
                "client_message_id": str(uuid4()),
                "expected_version": before["state_version"],
                "text": "Какой у нас сейчас бюджет?",
            },
        )
        assert response.status_code == 202
        await asyncio.sleep(0.05)
        run = await client.get(f"/api/v1/runs/{response.json()['run_id']}", headers=_headers(token))
        assert run.status_code == 200 and run.json()["status"] == "pending"
        after = await _snapshot(client, sid, token)
        assert_unchanged(before, after)
        assert after["waiting_reason"] == "user_pause" and not model.calls


@pytest.mark.asyncio
async def test_pause_before_canonical_finish_rejects_stale_answer_and_does_not_revive_quote(
    api_factory, monkeypatch
):
    async with api_factory(StubLLM()) as (client, model):
        sid, token, _ = await _session(client, known_trip())
        searched = await initial_search(client, sid, token)
        before = await _snapshot(client, sid, token)
        quote = await _preview(client, sid, token, _plans(searched)[0], before["state_version"])
        repository = client._transport.app.state.store
        finish = repository.finish_run
        interrupted = False

        async def pause_then_finish(run_id, expected_version, state, *args, **kwargs):
            nonlocal interrupted
            if state is None and not interrupted:
                interrupted = True
                await repository.set_control(sid, "pause")
            return await finish(run_id, expected_version, state, *args, **kwargs)

        monkeypatch.setattr(repository, "finish_run", pause_then_finish)
        await forbid_actions(client, monkeypatch)
        accepted = await client.post(
            f"/api/v1/sessions/{sid}/messages",
            headers=_headers(token),
            json={
                "client_message_id": str(uuid4()),
                "expected_version": before["state_version"],
                "text": "Какой у нас сейчас бюджет?",
            },
        )
        assert accepted.status_code == 202
        run = await _wait(client, accepted.json()["run_id"], token)
        assert interrupted and run["status"] == "conflict"
        after = await _snapshot(client, sid, token)
        expected = deepcopy(before["trip"])
        expected["state_version"] += 1
        assert after["trip"] == expected and after["waiting_reason"] == "user_pause"
        assert (await repository.get_quote(quote["quote_id"]))["status"] != "active"
        assert not model.calls and await repository.list_orders(sid) == []
        events = [row for row in await repository.list_events(sid) if row["run_id"] == run["run_id"]]
        proposed = [row for row in events if row["event_type"] == "SAVED_FACTS_ANSWER_PROPOSED"]
        assert len(proposed) == 1
        assert proposed[0]["payload"]["based_state_version"] == before["state_version"]
        assert proposed[0]["payload"]["model_calls"] == 0
        assert any(row["event_type"] == "RUN_CONFLICT" for row in events)
        assert not any(row["event_type"] == "RUN_COMPLETED" for row in events)
