"""After80 routes/groups through ASGI + isolated PG; controlled LLM only.

No actual model/supplier HTTP, owner runtime or original 80-dialogue DB.
"""
import json
from copy import deepcopy

import pytest
from test_api import StubLLM, _confirmation, _headers, _plans, _preview, _session, _snapshot
from test_api import api_factory as groups_routes_api_factory
from test_harness_conversational import initial_search, known_trip
from test_slot_continuation import _message, _reply

from otravel.domain import TripState

api_factory = groups_routes_api_factory


def answer(operations=(), control="none", **other):
    return {"speech_act": "CHANGE", "operations": list(operations), "control": {"action": control},
            "reply": "Сохранил указанные изменения.", **other}


def route_intake(kind="one_way", end=None):
    return [{"type": "set_origin", "value": {"city": "Казань"}},
        {"type": "set_route_policy", "value": {"kind": kind, **({"end_city": end} if end else {})}},
        {"type": "add_traveler", "value": {"id": "new-traveler-1", "type": "adult"}},
        {"type": "upsert_stay", "target_id": "new-stay-1", "value": {"city": "Анталья", "check_in": "2026-11-06", "check_out": "2026-11-08",
            "rooms": [{"id": "new-room-1", "traveler_ids": ["new-traveler-1"]}]}},
        {"type": "set_budget", "value": {"amount_minor": 50_000_000}}]


def family_intake():
    people = [("adult", None), ("adult", None), ("child", 6), ("adult", None), ("adult", None), ("child", 10)]
    return [{"type": "set_origin", "value": {"city": "Москва"}},
        *[{"type": "add_traveler", "value": {"id": f"new-traveler-{i}", "type": kind, **({"age": age} if age else {})}}
          for i, (kind, age) in enumerate(people, 1)],
        {"type": "upsert_stay", "target_id": "new-stay-1", "value": {"city": "Казань", "check_in": "2026-11-06", "check_out": "2026-11-08",
            "rooms": [{"id": "new-room-1", "traveler_ids": []}, {"id": "new-room-2", "traveler_ids": []}]}},
        {"type": "set_budget", "value": {"amount_minor": 50_000_000}}]


def room_fixture():
    return TripState(origin="Москва", travelers=[{"id": f"a{i}", "type": "adult"} for i in range(1, 5)],
        stays=[{"id": "s1", "city": "Казань", "check_in": "2026-11-06", "check_out": "2026-11-08",
            "rooms": [{"id": "r1", "traveler_ids": ["a1", "a2"]}, {"id": "r2", "traveler_ids": ["a3", "a4"]}]}],
        budget={"amount_minor": 50_000_000}).model_dump(mode="json")


def forbid_supplier_http(client, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("No supplier HTTP is permitted in this controlled DEMO/PG qualification")
    for name in ("search_hotels", "search_flights", "weather"):
        monkeypatch.setattr(client._transport.app.state.travel, name, forbidden)


@pytest.mark.asyncio
@pytest.mark.parametrize("control", ["none", "continue"])
async def test_declared_oneway_initial_model_operation_and_later_yes_never_invent_return(api_factory, monkeypatch, control):
    model = StubLLM([answer(route_intake(), control), answer()])
    async with api_factory(model) as (client, _):
        forbid_supplier_http(client, monkeypatch)
        sid, token, _ = await _session(client, {"search_policy": {"mode": "DEMO"}})
        run, snapshot = await _message(client, sid, token,
            "Из Казани в Анталью 6–8 ноября 2026, один взрослый без детей, один номер. Только в одну сторону, обратный перелёт не нужен. Бюджет 500000 рублей.")
        assert snapshot["trip"]["route_policy"]["kind"] == "one_way"
        assert [(leg["origin"], leg["destination"]) for leg in snapshot["trip"]["legs"]] == [("Казань", "Анталья")]
        assert len(model.calls) == 1 and _plans(run)
        quote = await _preview(client, sid, token, _plans(run)[0], snapshot["state_version"])
        assert quote["status"] == "active"
        _, after = await _message(client, sid, token, "да")
        assert after["trip"] == snapshot["trip"]
        assert len(after["trip"]["legs"]) == 1 and not after.get("pending_question")
        assert await client._transport.app.state.store.list_orders(sid) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("origin_words", ["Начинаем в Казани", "Из Казани"])
async def test_open_jaw_terminal_city_is_transport_endpoint_not_phantom_hotel(api_factory, monkeypatch, origin_words):
    model = StubLLM([answer(route_intake("open_jaw", "Москва"))])
    async with api_factory(model) as (client, _):
        forbid_supplier_http(client, monkeypatch)
        sid, token, _ = await _session(client, {"search_policy": {"mode": "DEMO"}})
        run, snapshot = await _message(client, sid, token,
            f"{origin_words}, отель в Анталье с 6 по 8 ноября 2026. Один взрослый без детей, номер один. Поездка заканчивается в Москве, отель в Москве не нужен. Бюджет 500000 рублей.")
        trip = snapshot["trip"]
        assert len(trip["stays"]) == 1 and trip["stays"][0]["city"] == "Анталья"
        assert trip["route_policy"] == {"kind": "open_jaw", "end_city": "Москва"}
        assert [(leg["origin"], leg["destination"]) for leg in trip["legs"]] == [("Казань", "Анталья"), ("Анталья", "Москва")], {
            "origin": trip["origin"], "stays": trip["stays"], "pending": snapshot.get("pending_question"),
            "normalizations": run.get("result", {}).get("result", {}).get("operation_normalizations")}
        result = run.get("result", {}).get("result", {})
        assert _plans(run) and all(len([o for o in p["offers"] if o["kind"] == "hotel"]) == 1 for p in _plans(run)), json.dumps({
            "trip": trip, "pending": snapshot.get("pending_question"),
            "result": {key: result.get(key) for key in ("questions", "violations", "warnings", "operation_normalizations")}}, ensure_ascii=True)
        assert (await _preview(client, sid, token, _plans(run)[0], snapshot["state_version"]))["status"] == "active"


@pytest.mark.asyncio
async def test_model_clear_preference_removes_only_old_morning_and_stales_old_quote(api_factory, monkeypatch):
    initial = known_trip()
    initial["preferences"] = [{"key": "departure_time", "value": "early_morning"}, {"key": "price_priority", "value": "lowest_total"}]
    model = StubLLM([answer([{"type": "clear_preference", "value": {"key": "departure_time"}}])])
    async with api_factory(model) as (client, _):
        forbid_supplier_http(client, monkeypatch)
        sid, token, _ = await _session(client, initial)
        searched = await initial_search(client, sid, token)
        before = await _snapshot(client, sid, token)
        old = await _preview(client, sid, token, _plans(searched)[0], before["state_version"])
        run, after = await _message(client, sid, token, "Утренний вылет больше не нужен. Сними это пожелание, оставь время вылета свободным.")
        assert [(p["key"], p["value"]) for p in after["trip"]["preferences"]] == [("price_priority", "lowest_total")]
        assert after["trip"]["travelers"] == before["trip"]["travelers"] and after["trip"]["stays"] == before["trip"]["stays"]
        assert after["state_version"] == before["state_version"] + 1 and len(model.calls) == 1
        denied = await client.post(f"/api/v1/sessions/{sid}/checkout/confirm", headers=_headers(token), json=_confirmation(old, before["state_version"]))
        assert denied.status_code == 409
        assert _plans(run)


@pytest.mark.asyncio
@pytest.mark.parametrize("with_question", [False, True])
async def test_initial_two_families_no_mix_keeps_intake_and_asks_membership_without_search(api_factory, monkeypatch, with_question):
    model = StubLLM([answer(family_intake())])
    async with api_factory(model) as (client, _):
        forbid_supplier_http(client, monkeypatch)
        sid, token, _ = await _session(client, {"search_policy": {"mode": "DEMO"}})
        text = "Из Москвы в Казань 6–8 ноября 2026. Нас две семьи, всего 4 взрослых и дети 6 и 10 лет, два номера, семьи не смешивай. Бюджет 500000 рублей."
        run, snapshot = await _message(client, sid, token, text + (" Какие даты нужны?" if with_question else ""))
        trip = snapshot["trip"]
        assert len(trip["travelers"]) == 6 and trip["budget"]["amount_minor"] == 50_000_000
        assert trip["origin"] == "Москва" and len(trip["stays"][0]["rooms"]) == 2
        assert len(trip["groups"]) == 2 and all(g["keep_separate"] and not g["traveler_ids"] for g in trip["groups"])
        assert not _plans(run) and snapshot["status"] == "WAITING_USER" and snapshot["waiting_reason"] == "clarification"
        pending = snapshot["pending_question"]
        assert pending["field"] == "group.membership" and pending["target_id"] == trip["groups"][0]["id"]
        assert "групп" in (await _reply(client, sid, token, run)).lower() and len(model.calls) == 1


@pytest.mark.asyncio
async def test_unknown_family_count_bound_two_uses_zero_additional_model_calls(api_factory, monkeypatch):
    model = StubLLM([answer(family_intake())])
    async with api_factory(model) as (client, _):
        forbid_supplier_http(client, monkeypatch)
        sid, token, _ = await _session(client, {"search_policy": {"mode": "DEMO"}})
        _, first = await _message(client, sid, token,
            "Из Москвы в Казань 6–8 ноября 2026. Едут 4 взрослых и двое детей 6 и 10 лет. Нужно два номера, семьи не смешивай. Бюджет 500000 рублей.")
        assert first["trip"]["groups"] == [] and len(first["trip"]["travelers"]) == 6
        assert first["pending_question"]["context"]["membership_unknown"] is True
        calls = len(model.calls)
        run, after = await _message(client, sid, token, "2")
        assert len(model.calls) == calls and run["result"]["result"]["response_source"] == "contextual_slot"
        assert len(after["trip"]["groups"]) == 2 and all(g["keep_separate"] and not g["traveler_ids"] for g in after["trip"]["groups"])
        assert not _plans(run) and after["pending_question"]["field"] == "group.membership"


@pytest.mark.asyncio
async def test_membership_and_room_assignment_model_batch_resolves_placeholders_and_quotes(api_factory, monkeypatch):
    groups = [{"type": "upsert_group", "target_id": f"new-group-{i}", "value": {
        "traveler_ids": [f"new-traveler-{n}" for n in numbers], "keep_separate": True}}
        for i, numbers in ((1, (1, 2, 3)), (2, (4, 5, 6)))]
    rooms = [{"id": "new-room-1", "traveler_ids": ["new-traveler-1", "new-traveler-2", "new-traveler-3"]},
             {"id": "new-room-2", "traveler_ids": ["new-traveler-4", "new-traveler-5", "new-traveler-6"]}]
    model = StubLLM([answer(family_intake()), answer([*groups,
        {"type": "upsert_stay", "target_id": "new-stay-1", "value": {"rooms": rooms}}])])
    async with api_factory(model) as (client, _):
        forbid_supplier_http(client, monkeypatch)
        sid, token, _ = await _session(client, {"search_policy": {"mode": "DEMO"}})
        await _message(client, sid, token, "Две семьи, семьи не смешивай. Из Москвы в Казань 6–8 ноября 2026, 4 взрослых и дети 6 и 10 лет, два номера, бюджет 500000 рублей.")
        run, after = await _message(client, sid, token,
            "Первая семья — первые двое взрослых и ребёнок шести лет, второй номер второй семье: оставшиеся двое взрослых и ребёнок десяти лет. Первый номер первой семье.")
        assert len(model.calls) == 2 and after["trip"]["stays"][0]["rooms"] == rooms
        assert not after.get("pending_question") and after["status"] == "OPTIONS_READY" and _plans(run)
        assert [g["traveler_ids"] for g in after["trip"]["groups"]] == [op["value"]["traveler_ids"] for op in groups]
        quote = await _preview(client, sid, token, _plans(run)[0], after["state_version"])
        assert quote["status"] == "active"


@pytest.mark.asyncio
async def test_omitted_adult_removal_blocks_entire_wrong_room_batch_and_old_quote(api_factory, monkeypatch):
    initial = room_fixture()
    wrong = {"type": "upsert_stay", "target_id": "s1", "value": {"rooms": [
        {"id": "r1", "traveler_ids": ["a1", "a2"]}, {"id": "r2", "traveler_ids": ["a3"]}]}}
    model = StubLLM([answer([wrong])])
    async with api_factory(model) as (client, _):
        forbid_supplier_http(client, monkeypatch)
        sid, token, _ = await _session(client, initial)
        searched = await initial_search(client, sid, token)
        before = await _snapshot(client, sid, token)
        old = await _preview(client, sid, token, _plans(searched)[0], before["state_version"])
        _, after = await _message(client, sid, token, "Один взрослый из второго номера не поедет. Там остаётся один взрослый. Первый номер не трогай.")
        assert after["trip"] == before["trip"] and after["state_version"] == before["state_version"]
        assert after["pending_question"]["field"] == "party.removal" and after["waiting_reason"] == "clarification"
        response = await client.post(f"/api/v1/sessions/{sid}/checkout/preview", headers=_headers(token),
            json={"plan_id": _plans(searched)[0]["plan_id"], "state_version": after["state_version"]})
        assert response.status_code == 409
        confirm = await client.post(f"/api/v1/sessions/{sid}/checkout/confirm", headers=_headers(token), json=_confirmation(old, after["state_version"]))
        assert confirm.status_code == 409 and await client._transport.app.state.store.list_orders(sid) == []


@pytest.mark.asyncio
async def test_model_room_rate_constraints_stay_exact_and_demo_price_quote_is_per_room(api_factory, monkeypatch):
    ops = [{"type": "set_constraint", "value": {"key": "refundable", "value": True, "stay_id": "s1", "room_id": "r1"}},
           {"type": "set_constraint", "value": {"key": "breakfast", "value": False, "stay_id": "s1", "room_id": "r2"}}]
    model = StubLLM([answer(ops)])
    async with api_factory(model) as (client, _):
        forbid_supplier_http(client, monkeypatch)
        sid, token, _ = await _session(client, room_fixture())
        run, snapshot = await _message(client, sid, token,
            "В первом номере нужен возвратный тариф, во втором номере тариф без завтрака. Другие номера не меняй.")
        constraints = snapshot["trip"]["constraints"]
        assert [(c["key"], c["stay_id"], c["room_id"], c["value"]) for c in constraints] == [("refundable", "s1", "r1", True), ("breakfast", "s1", "r2", False)]
        assert _plans(run) and not snapshot.get("pending_question")
        hotel = next(o for o in _plans(run)[0]["offers"] if o["kind"] == "hotel")
        rates = {r["room_id"]: r for r in hotel["room_rates"]}
        assert rates["r1"]["refundable"] is True and rates["r2"]["breakfast"] is False
        assert hotel["rate_type"] == "mixed" and hotel["refundable"] is None and hotel["total_minor"] == 1_634_500
        assert sum(line["amount_minor"] for line in hotel["breakdown"]) == hotel["total_minor"]
        assert (await _preview(client, sid, token, _plans(run)[0], snapshot["state_version"]))["status"] == "active"


@pytest.mark.asyncio
async def test_saved_one_way_state_without_derived_legs_searches_and_quotes_only_outbound(api_factory, monkeypatch):
    initial = known_trip()
    initial["route_policy"] = {"kind": "one_way"}
    initial["legs"] = []
    async with api_factory(StubLLM()) as (client, model):
        forbid_supplier_http(client, monkeypatch)
        sid, token, created = await _session(client, initial)
        run = await initial_search(client, sid, token)
        for plan in _plans(run):
            routes = [(o["origin"], o["destination"]) for o in plan["offers"] if o["kind"] == "flight"]
            assert routes == [("Москва", "Казань")]
        assert (await _preview(client, sid, token, _plans(run)[0], created["state_version"]))["status"] == "active"
        assert not model.calls


@pytest.mark.asyncio
async def test_saved_separation_survives_model_false_setter_in_budget_edit(api_factory, monkeypatch):
    initial = room_fixture()
    initial["groups"] = [{"id": "g1", "traveler_ids": ["a1", "a2"], "keep_separate": True},
                         {"id": "g2", "traveler_ids": ["a3", "a4"], "keep_separate": True}]
    for person in initial["travelers"]:
        person["group_id"] = "g1" if person["id"] in {"a1", "a2"} else "g2"
    model = StubLLM([answer([
        {"type": "set_budget", "value": {"amount_minor": 45_000_000}},
        {"type": "upsert_group", "target_id": "g1", "value": {"traveler_ids": ["a1", "a2"], "keep_separate": False}},
    ])])
    before = deepcopy(initial)
    async with api_factory(model) as (client, _):
        forbid_supplier_http(client, monkeypatch)
        sid, token, _ = await _session(client, initial)
        run, after = await _message(client, sid, token, "Общий бюджет теперь 450000 рублей, состав и номера не меняй.")
        assert after["trip"]["groups"] == before["groups"]
        assert after["trip"]["budget"]["amount_minor"] == 45_000_000
        assert after["trip"]["travelers"] == before["travelers"] and after["trip"]["stays"] == before["stays"]
        assert _plans(run) and not after.get("pending_question")
        assert (await _preview(client, sid, token, _plans(run)[0], after["state_version"]))["status"] == "active"
