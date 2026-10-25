"""Frozen owner dialogues: real PG/ASGI with deliberately wrong model outputs.

These exercise context/slot/question guards. They never call a real model or
supplier and do not substitute for the owner's subsequent local-model series.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from test_api import StubLLM, _headers, _session, _snapshot, _wait
from test_api import api_factory as owner_api_factory

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "evaluation" / "frozen-owner-dialogues.json"
api_factory = owner_api_factory
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
_RAW_FIELD = re.compile(r"\b(?:destination_city|origin_city|traveler_count|adult_count|children_count|check_in|check_out|missing_[a-z_]+|(?:destination|origin)\.city|party\.(?:adults|children)|traveler\.age|stay\.(?:check_in|check_out|rooms)|leg\.departure_date)\b")


class WrongSlotLLM(StubLLM):
    """Repeat the same wrong output on repair; no queued-call-count assumptions."""

    async def extract_operations(self, context):
        self.calls.append(deepcopy(context))
        message = context["current_message"].casefold().strip()
        answer = {"operations": [], "control": {"action": "none"}, "reply": "destination_city",
                  "unknowns": ["destination_city"], "conflicts": []}
        if message in {"в сербию может быть поехать?", "в сербию хочу"}:
            answer.update(operations=[{"type": "upsert_stay", "target_id": "wrong-country-as-city", "value": {"city": "Сербия"}}],
                          unknowns=[], reply="Город в Сербии")
        elif message == "стамбул?":
            answer.update(operations=[{"type": "set_origin", "value": {"origin": "Стамбул"}}],
                          unknowns=[], reply="Город отправления Стамбул")
        elif message == "белгрод":
            answer.update(operations=[{"type": "set_origin", "value": {"origin": "Белгрод"}}],
                          unknowns=["destination_city"], reply="destination_city")
        elif message == "из москвы":
            answer.update(operations=[{"type": "set_origin", "value": {"origin": "Москва"}}],
                          unknowns=["Кто едет?"], reply="Кто едет?")
        elif message in {"я взрослый", "1"}:
            answer.update(operations=[{"type": "1", "value": {}}], unknowns=["adult_count"], reply="adult_count")
        return deepcopy(answer)


def _pointer(value, pointer):
    for part in pointer.removeprefix("/").split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def _assert_trip(trip, baseline, expectations):
    for rule in expectations:
        actual = _pointer(trip, rule["path"])
        if rule["op"] == "eq":
            assert actual == rule["value"], f"Unexpected value at {rule['path']}"
        elif rule["op"] == "unchanged":
            assert actual == _pointer(baseline, rule["path"]), f"Unrequested mutation at {rule['path']}"
        elif rule["op"] == "len":
            assert len(actual) == rule["value"], f"Unexpected count at {rule['path']}"
        else:
            raise AssertionError("Unsupported frozen assertion: " + rule["op"])


def _requested_country(pending):
    """Do not mistake a suggested candidate's country for the user's request."""
    pending = pending or {}
    requested = pending.get("requested_destination") or (pending.get("context") or {}).get("requested_destination") or {}
    return requested.get("country_code")


async def _assistant_text(client, sid, token, run_id):
    response = await client.get(f"/api/v1/sessions/{sid}/messages", headers=_headers(token))
    assert response.status_code == 200
    body = response.json()
    rows = body.get("messages", []) if isinstance(body, dict) else body
    matches = [row for row in rows if row["role"] == "assistant" and row.get("run_id") == run_id]
    assert len(matches) == 1, "One persisted assistant message must describe this turn"
    return matches[0]["text"]


def _assert_question(snapshot, text, expected):
    assert isinstance(text, str) and len(text.strip()) >= 8, "Question must be a human sentence"
    assert not _RAW_FIELD.search(text), "Internal field name leaked into the user answer"
    lowered = text.casefold().replace("ё", "е")
    for fragment in expected.get("contains_all", []):
        assert fragment in lowered, f"Human clarification must mention {fragment}"
    if expected.get("contains_any"):
        assert any(fragment in lowered for fragment in expected["contains_any"]), "Missing expected clarification topic"
    for pattern in expected.get("forbid_regex", []):
        assert not re.search(pattern, lowered), "Already answered traveler-count question was repeated"
    pending = snapshot.get("pending_question") or {}
    questions = pending.get("questions", [])
    if "max_questions" in expected:
        assert len(questions) <= expected["max_questions"], "Ask only the next necessary clarification"
    for question in questions:
        assert isinstance(question, str) and not _RAW_FIELD.search(question), "Internal field name leaked into pending questions"
        for pattern in expected.get("forbid_regex", []):
            assert not re.search(pattern, question.casefold()), "Already answered question remained in pending context"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
async def test_frozen_owner_dialogue_with_wrong_model_is_safe_and_human(api_factory, case):
    model = WrongSlotLLM()
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, case["initial_trip"])
        baseline = created["trip"]
        for turn in case["turns"]:
            before = await _snapshot(client, sid, token)
            accepted = await client.post(f"/api/v1/sessions/{sid}/messages", headers=_headers(token), json={
                "client_message_id": str(uuid4()), "text": turn["text"], "expected_version": before["state_version"]})
            assert accepted.status_code == 202
            run = await _wait(client, accepted.json()["run_id"], token)
            assert run["status"] == "completed", "A short answer must not end as INVALID_LLM_* or failed"
            assert (run.get("error") or {}).get("code") not in turn["expect"].get("run_error_forbidden", [])
            snapshot = await _snapshot(client, sid, token)
            expected = turn["expect"]
            _assert_trip(snapshot["trip"], baseline, expected.get("assertions", []))
            for key in ("session_status", "waiting_reason"):
                if key in expected:
                    assert snapshot["status" if key == "session_status" else key] == expected[key]
            if expected.get("pending_country_code"):
                assert expected["pending_country_code"] == _requested_country(snapshot.get("pending_question")), "Requested country was lost from pending context"
            if "adult_count" in expected:
                assert sum(person["type"] == "adult" for person in snapshot["trip"]["travelers"]) == expected["adult_count"], "Adult count was lost or duplicated"
            reply = await _assistant_text(client, sid, token, run["run_id"])
            _assert_question(snapshot, reply, expected["question"])
            if "known_geography" in expected:
                geography = expected["known_geography"]
                if geography["country_code"] == "TR":
                    assert "стамбул" in reply.casefold() and "турц" in reply.casefold(), "Actual answer must know Istanbul belongs to Turkey"
                else:
                    assert "белград" in reply.casefold(), "Recognize or ask confirmation of Belgrade without inventing search coverage"
            orders = (await client.get(f"/api/v1/sessions/{sid}/orders", headers=_headers(token))).json()
            assert orders.get("orders", []) == []
        events = (await client.get(f"/api/v1/sessions/{sid}/events", headers=_headers(token))).json()
        rows = events.get("events", []) if isinstance(events, dict) else events
        assert not any(row["event_type"] == "TOOL_CALL_STARTED" for row in rows), "Clarification must not call suppliers"
        assert all(call["provider"] == "injected_test_adapter" for row in rows if row["event_type"] == "MODEL_CALL_STARTED"
                   for call in [row.get("payload", {})]), "Never consume real-provider budget in this suite"
