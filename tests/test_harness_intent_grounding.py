"""After80 geo/aux canaries: actual ASGI + isolated PG, controlled ModelResult.

No real LLM/travel HTTP. The initial raw outcomes are deliberately wrong where
the test exercises grounding; this is not a replay or NLU score of the80 chats.
"""
from uuid import uuid4

import pytest
from test_api import StubLLM, _session
from test_api import api_factory as intent_api_factory
from test_harness_conversational import known_trip
from test_slot_continuation import _message, _reply

api_factory = intent_api_factory


@pytest.mark.asyncio
async def test_reverse_initial_city_grounding_saves_all_literal_facts(api_factory):
    model = StubLLM([{"operations": [
        {"type": "set_origin", "value": {"city": "Москва"}},
        {"type": "add_traveler", "value": {"id": "owner", "type": "adult"}},
        {"type": "set_preference", "value": {"key": "departure_time", "value": "early_morning"}}],
        "unknowns": ["destination_city"]}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, {})
        run, after = await _message(client, sid, token,
            "Белград в Сербии из Москвы рано утром, детей нет, один взрослый")
        assert after["trip"]["origin"] == "Москва"
        assert len(after["trip"]["stays"]) == 1 and after["trip"]["stays"][0]["city"] == "Белград"
        assert after["trip"]["stays"][0]["rooms"] == [] and after["trip"]["stays"][0]["check_in"] is None
        assert len(after["trip"]["travelers"]) == 1 and after["trip"]["travelers"][0]["id"] == "owner"
        assert after["trip"]["preferences"] == [{"key": "departure_time", "value": "early_morning", "weight": 1.0}]
        assert len(model.calls) == 1 and run["status"] == "completed"


@pytest.mark.asyncio
async def test_explicit_destination_change_protects_origin_and_replaces_owned_country_context(api_factory):
    model = StubLLM([{"operations": [{"type": "set_origin", "value": {"city": "Санкт-Петербург"}}],
                     "unknowns": ["origin_city"]}])
    initial = {"origin": "Москва", "travelers": [{"id": "owner", "type": "adult"}],
               "stays": [{"id": "current-stay", "city": "Белград"}],
               "preferences": [{"key": "departure_time", "value": "early_morning"}],
               "budget": {"amount_minor": 9_500_000}, "search_policy": {"guest_nationality": "RS"}}
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, initial)
        repository = client._transport.app.state.store
        stored = await repository.get_session(sid)
        setup, _ = await repository.intake_message(sid, str(uuid4()), "controlled owned country pending", stored["state_version"])
        assert await repository.claim_run(setup["run_id"])
        pending = {"field": "destination.city", "target_id": "current-stay", "state_version": stored["state_version"],
            "text": "Какой город в Сербии выбираете?", "questions": ["Какой город в Сербии выбираете?"],
            "context": {"requested_destination": {"kind": "country", "name": "Сербия", "country_code": "RS"},
                        "proposed_destination": {"kind": "city", "name": "Стамбул", "country_code": "TR"},
                        "confirmation_required": True, "proposal_field": "destination.city"}}
        await repository.finish_run(setup["run_id"], stored["state_version"], None, "WAITING_USER", pending["text"],
                                    pending_question=pending, waiting_reason="clarification")
        _, after = await _message(client, sid, token,
            "Меняем Сербию на Россию: выбираю Санкт-Петербург. Ранний вылет и состав прежние.")
        assert after["trip"]["origin"] == "Москва"
        assert after["trip"]["stays"][0]["id"] == "current-stay" and after["trip"]["stays"][0]["city"] == "Санкт-Петербург"
        assert after["trip"]["travelers"] == created["trip"]["travelers"]
        assert after["trip"]["budget"] == created["trip"]["budget"]
        assert after["trip"]["preferences"] == created["trip"]["preferences"]
        assert after["trip"]["search_policy"]["guest_nationality"] == "RS"  # Citizenship is not destination country.
        context = after["pending_question"]["context"]
        assert context["requested_destination"]["country_code"] == "RU"
        assert context["requested_destination"]["name"] == "Санкт-Петербург"
        assert "confirmation_required" not in context and "proposed_destination" not in context
        assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_invented_departure_is_removed_and_specific_origin_question_wins(api_factory):
    model = StubLLM([{"operations": [
        {"type": "set_origin", "value": {"city": "Казань"}},
        {"type": "add_traveler", "value": {"id": "adult1", "type": "adult"}},
        {"type": "add_traveler", "value": {"id": "adult2", "type": "adult"}},
        {"type": "upsert_stay", "target_id": "stay", "value": {"city": "Анталья"}}],
        "unknowns": ["stay.check_in"]}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, {})
        run, after = await _message(client, sid, token,
            "Мы с супругой хотим на море в Анталью. Нас двое взрослых, без детей.")
        assert after["trip"]["origin"] is None
        assert after["trip"]["stays"][0]["city"] == "Анталья" and len(after["trip"]["travelers"]) == 2
        assert after["pending_question"]["field"] == "origin.city"
        assert "Из какого города" in after["pending_question"]["text"]
        assert after["pending_question"]["text"] in await _reply(client, sid, token, run)
        assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_budget_edit_drops_unrequested_soft_constraint_hard_refund_and_hotel_pin(api_factory):
    model = StubLLM([{"operations": [
        {"type": "set_budget", "value": {"amount_minor": 8_000_000}},
        {"type": "set_constraint", "value": {"key": "breakfast", "value": True, "hard": False}},
        {"type": "set_constraint", "value": {"key": "refundable", "value": True, "hard": True}},
        {"type": "upsert_stay", "target_id": "kazan-stay", "value": {"hotel_id": "demo-hotel-kazan-garden"}}]}])
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, known_trip())
        run, after = await _message(client, sid, token, "Общий бюджет теперь 80000 рублей, остальные данные не меняй.")
        assert after["trip"]["budget"]["amount_minor"] == 8_000_000
        assert after["trip"]["constraints"] == [] and after["trip"]["stays"] == created["trip"]["stays"]
        codes = {note.get("code") for note in run["result"]["result"]["operation_normalizations"]}
        assert {"unrequested_aux_constraint_suppressed", "unrequested_hotel_choice_suppressed"} <= codes
        assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_mixed_date_and_budget_edit_retained_without_unrequested_auxiliary_choices(api_factory):
    model = StubLLM([{"operations": [
        {"type": "set_budget", "value": {"amount_minor": 7_600_000}},
        {"type": "upsert_stay", "target_id": "kazan-stay", "value": {"city": "Казань", "check_out": "2026-11-19", "hotel_id": "demo-hotel-kazan-garden"}},
        {"type": "set_constraint", "value": {"key": "breakfast", "value": True, "hard": False}}]}])
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client, known_trip())
        _, after = await _message(client, sid, token,
            "Выезд перенеси на 19 ноября 2026 и общий бюджет поставь 76000 рублей. Другие пожелания не меняй.")
        assert after["trip"]["budget"]["amount_minor"] == 7_600_000
        assert after["trip"]["stays"][0]["check_out"] == "2026-11-19"
        assert after["trip"]["stays"][0]["check_in"] == before["trip"]["stays"][0]["check_in"]
        assert after["trip"]["stays"][0]["hotel_id"] is None and after["trip"]["constraints"] == []
        assert after["trip"]["origin"] == before["trip"]["origin"] and after["trip"]["travelers"] == before["trip"]["travelers"]
        assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_explicit_auxiliary_selections_and_negative_constraints_are_not_blanket_removed(api_factory):
    model = StubLLM([{"operations": [
        {"type": "upsert_stay", "target_id": "kazan-stay", "value": {"hotel_id": "demo-hotel-kazan-garden"}},
        {"type": "set_constraint", "value": {"key": "breakfast", "value": True, "hard": False}},
        {"type": "set_constraint", "value": {"key": "refundable", "value": False, "hard": True}}]}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, known_trip())
        _, after = await _message(client, sid, token,
            "Выбираю второй отель. Завтрак желательно, возвратный тариф не нужен.")
        assert after["trip"]["stays"][0]["hotel_id"] == "demo-hotel-kazan-garden"
        assert {(c["key"], c["value"], c["hard"]) for c in after["trip"]["constraints"]} == {
            ("breakfast", True, False), ("refundable", False, True)}
        assert len(model.calls) == 1
