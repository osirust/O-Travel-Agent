"""HARNESS-004 continuation through ASGI + isolated real PostgreSQL.

The external model is controlled. These assertions exercise persisted context,
not live NLU; no model/supplier HTTP or inferred travel facts are involved.
"""

from __future__ import annotations

import re
from uuid import uuid4

import pytest

from otravel.domain import sample_trip
from test_api import StubLLM, _headers, _session, _snapshot, _wait
from test_api import api_factory as slot_api_factory


api_factory = slot_api_factory
RAW_FIELD = re.compile(r"\b(?:check_in|check_out|checkout|checkin|origin_city|destination_city|adult_count|children_count|child_age|missing_[a-z_]+|party\.(?:adults|children)|traveler\.age|stay\.(?:check_in|check_out|rooms))\b")


async def _message(client, sid, token, text):
    before = await _snapshot(client, sid, token)
    accepted = await client.post(f"/api/v1/sessions/{sid}/messages", headers=_headers(token), json={
        "client_message_id": str(uuid4()), "expected_version": before["state_version"], "text": text})
    assert accepted.status_code == 202
    run = await _wait(client, accepted.json()["run_id"], token)
    assert run["status"] == "completed", "Slot answer must not fail extraction or domain validation"
    assert not run.get("error")
    return run, await _snapshot(client, sid, token)


async def _reply(client, sid, token, run):
    response = await client.get(f"/api/v1/sessions/{sid}/messages", headers=_headers(token))
    assert response.status_code == 200
    body = response.json()
    rows = body.get("messages", []) if isinstance(body, dict) else body
    replies = [row["text"] for row in rows if row["role"] == "assistant" and row.get("run_id") == run["run_id"]]
    assert len(replies) == 1, "Each completed turn must have one durable assistant reply"
    return replies[0]


def _human_pending(snapshot):
    pending = snapshot.get("pending_question")
    assert pending and pending.get("questions"), "An incomplete trip must retain a useful next question"
    for text in [pending["text"], *pending["questions"]]:
        assert isinstance(text, str) and len(text.split()) >= 4
        assert not RAW_FIELD.search(text), "A machine field leaked into a persisted question"
    return pending


def _basic_trip(**updates):
    return {"origin": "Москва", "travelers": [], "stays": [{"id": "destination-stay", "city": "Стамбул"}], **updates}


@pytest.mark.asyncio
@pytest.mark.parametrize("no_children", ["0", "нет", "без детей"])
async def test_one_adult_then_no_children_advances_without_child_loop_or_trip_revision(api_factory, no_children):
    model = StubLLM([{"unknowns": ["adult_count"]}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, _basic_trip())
        _, asked = await _message(client, sid, token, "Кто едет?")
        assert _human_pending(asked)["field"] == "party.adults"
        _, adults = await _message(client, sid, token, "1")
        assert _human_pending(adults)["field"] == "party.children"
        assert [(person["type"], person["age"]) for person in adults["trip"]["travelers"]] == [("adult", None)]
        run, done = await _message(client, sid, token, no_children)
        assert done["trip"] == adults["trip"], "Zero children is context, not an invented child or trip edit"
        assert done["state_version"] == adults["state_version"]
        pending = _human_pending(done)
        assert pending["field"] != "party.children", "Answered zero-children slot was preserved instead of advanced"
        assert pending.get("context", {}).get("children_known") is True
        assert pending.get("context", {}).get("children_count") == 0
        text = await _reply(client, sid, token, run)
        assert not re.search(r"поедут ли.*дет|сколько.*дет", text.casefold())
        assert len(model.calls) == 1, "Known numeric/count slots must not invoke the model again"


@pytest.mark.asyncio
async def test_two_existing_unknown_child_ages_bind_individual_stable_ids_after_count(api_factory):
    people = [{"id": "owner-adult", "type": "adult"}, {"id": "younger-child-x", "type": "child"}, {"id": "older-child-y", "type": "child"}]
    model = StubLLM([{"unknowns": ["children_count"]}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, _basic_trip(travelers=people))
        await _message(client, sid, token, "Уточним состав группы")
        _, first = await _message(client, sid, token, "2")
        assert first["pending_question"]["target_id"] == "younger-child-x"
        assert first["pending_question"]["field"] == "traveler.age"
        _, second = await _message(client, sid, token, "4")
        assert second["pending_question"]["target_id"] == "older-child-y"
        ages = {person["id"]: person["age"] for person in second["trip"]["travelers"]}
        assert ages == {"owner-adult": None, "younger-child-x": 4, "older-child-y": None}
        _, done = await _message(client, sid, token, "7 лет")
        assert {person["id"]: person["age"] for person in done["trip"]["travelers"]} == {
            "owner-adult": None, "younger-child-x": 4, "older-child-y": 7}
        assert done["trip"]["origin"] == "Москва" and len(done["trip"]["travelers"]) == 3
        assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_new_child_count_then_ages_preserves_ids_and_existing_adult(api_factory):
    model = StubLLM([{"unknowns": ["children_count"]}])
    initial = _basic_trip(travelers=[{"id": "existing-parent", "type": "adult", "age": 35}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, initial)
        await _message(client, sid, token, "Будут ли дети?")
        _, counted = await _message(client, sid, token, "2")
        children = [person for person in counted["trip"]["travelers"] if person["type"] == "child"]
        ids = [person["id"] for person in children]
        assert len(ids) == len(set(ids)) == 2 and all(person["age"] is None for person in children)
        assert counted["pending_question"]["target_id"] == ids[0]
        _, aged_once = await _message(client, sid, token, "4 года")
        assert aged_once["pending_question"]["target_id"] == ids[1]
        _, done = await _message(client, sid, token, "9")
        assert [(p["id"], p["age"]) for p in done["trip"]["travelers"] if p["type"] == "child"] == [(ids[0], 4), (ids[1], 9)]
        assert done["trip"]["travelers"][0] == counted["trip"]["travelers"][0]
        assert done["trip"]["travelers"][0]["id"] == "existing-parent"
        assert done["trip"]["travelers"][0]["age"] == 35
        assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_multiple_missing_ages_get_an_explicit_target_before_accepting_numeric_age(api_factory):
    people = [{"id": "adult", "type": "adult"}, {"id": "child-first", "type": "child"}, {"id": "child-second", "type": "child"}]
    model = StubLLM([{"unknowns": ["child_age"]}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, _basic_trip(travelers=people))
        _, asked = await _message(client, sid, token, "Осталось уточнить возраст детей")
        pending = _human_pending(asked)
        assert pending["field"] == "traveler.age"
        assert pending["target_id"] in {"child-first", "child-second"}, "Age question must identify a child before a bare number can be applied"
        selected = pending["target_id"]
        _, answered = await _message(client, sid, token, "4")
        assert next(p["age"] for p in answered["trip"]["travelers"] if p["id"] == selected) == 4
        assert all(p["age"] is None for p in answered["trip"]["travelers"] if p["id"] != selected)
        assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_known_origin_is_not_asked_again_when_model_returns_stale_origin_slot(api_factory):
    model = StubLLM([{"unknowns": ["origin_city", "adult_count"]}])
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, _basic_trip())
        run, asked = await _message(client, sid, token, "Каких данных ещё не хватает?")
        assert asked["trip"] == created["trip"]
        assert asked["trip"]["origin"] == "Москва"
        assert _human_pending(asked)["field"] == "party.adults"
        text = (await _reply(client, sid, token, run)).casefold()
        assert "взросл" in text and "из какого" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize("missing,word", [("check_in", "заезд"), ("check_out", "выезд")])
async def test_post_search_pending_dates_and_actual_reply_have_no_raw_field_names(api_factory, missing, word):
    initial = sample_trip().model_dump(mode="json")
    initial["stays"][0][missing] = None
    model = StubLLM()
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, initial)
        accepted = await client.post(f"/api/v1/sessions/{sid}/search", headers=_headers(token), json={
            "client_message_id": str(uuid4()), "expected_version": created["state_version"], "mode": "DEMO"})
        assert accepted.status_code == 202
        run = await _wait(client, accepted.json()["run_id"], token)
        assert run["status"] == "completed"
        asked = await _snapshot(client, sid, token)
        pending = _human_pending(asked)
        assert pending["field"] == f"stay.{missing}"
        assert pending["target_id"] == initial["stays"][0]["id"]
        assert word in pending["text"].casefold()
        text = await _reply(client, sid, token, run)
        assert not RAW_FIELD.search(text) and word in text.casefold()
        assert asked["trip"] == created["trip"]
        assert len(model.calls) == 0


@pytest.mark.asyncio
async def test_destination_country_context_does_not_restrict_moscow_origin(api_factory):
    # Complete dates/room/party avoid unrelated missing slots after choosing city.
    initial = {"travelers": [{"id": "owner", "type": "adult"}], "stays": [{
        "id": "chosen-destination", "check_in": "2027-03-01", "check_out": "2027-03-05",
        "rooms": [{"id": "room", "traveler_ids": ["owner"]}]}]}
    model = StubLLM()
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, initial)
        _, country = await _message(client, sid, token, "Турция")
        assert country["pending_question"]["requested_destination"]["country_code"] == "TR"
        _, city = await _message(client, sid, token, "Стамбул")
        assert city["trip"]["stays"][0]["city"] == "Стамбул"
        assert city["pending_question"]["field"] == "origin.city"
        _, origin = await _message(client, sid, token, "из Москвы")
        assert origin["trip"]["origin"] == "Москва", "Destination TR must not be misread as an origin-country restriction"
        assert origin["trip"]["stays"][0]["city"] == "Стамбул"
        pending = origin.get("pending_question") or {}
        assert not pending.get("context", {}).get("confirmation_required"), "No departure-country change was requested"
        assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_room_count_machine_slot_is_humanized_and_bound_to_exact_stay(api_factory):
    initial = _basic_trip(travelers=[{"id": "adult", "type": "adult"}])
    model = StubLLM([{"unknowns": ["stay.rooms"]}])
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, initial)
        run, asked = await _message(client, sid, token, "Уточним количество номеров")
        pending = _human_pending(asked)
        assert pending["field"] == "stay.rooms" and pending["target_id"] == "destination-stay"
        assert "номер" in pending["text"].casefold()
        assert "Стамбул" in pending["text"]
        assert asked["trip"] == created["trip"]
        assert not RAW_FIELD.search(await _reply(client, sid, token, run))


@pytest.mark.asyncio
@pytest.mark.parametrize("unknown", ["youngest_age", "child-1", "age_missing_for_youngest"])
@pytest.mark.parametrize("answer", ["4", "Ему четыре года на дату выезда. Это единственное уточнение."])
async def test_unrecognized_model_field_falls_back_to_missing_domain_slot_before_answer(api_factory, unknown, answer):
    initial = sample_trip().model_dump(mode="json")
    initial["travelers"][6]["age"] = None
    model = StubLLM([{"unknowns": [unknown]}])
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, initial)
        first, asked = await _message(client, sid, token, "Подбери поездку, возраст младшего пока не указан")
        pending = _human_pending(asked)
        assert pending["field"] == "traveler.age"
        assert pending["target_id"] == initial["travelers"][6]["id"]
        assert "ребёнку" in await _reply(client, sid, token, first)
        _, answered = await _message(client, sid, token, answer)
        assert answered["trip"]["travelers"][6]["age"] == 4
        assert answered["trip"]["travelers"][7] == created["trip"]["travelers"][7]
        for field in ("groups", "stays", "legs", "budget", "constraints"):
            assert answered["trip"][field] == created["trip"][field]
        assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_natural_question_is_preserved_when_domain_has_another_missing_slot(api_factory):
    initial = sample_trip().model_dump(mode="json")
    initial["travelers"][6]["age"] = None
    question = "Кому нужен отдельный номер?"
    model = StubLLM([{"unknowns": [question]}])
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, initial)
        run, asked = await _message(client, sid, token, "Уточним отдельный номер")
        assert await _reply(client, sid, token, run) == question
        assert asked["trip"] == created["trip"]
