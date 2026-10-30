"""Room-count facts and unknown allocation; no NLU/network/DB qualification."""
from copy import deepcopy

import pytest

from otravel.domain import TripState, apply_operations
from otravel.group_semantics import guard_group_separation
from otravel.room_intent import ground_room_count_operations

CF08 = "Из Москвы в Казань. Нас две семьи, всего четыре взрослых и дети шести и десяти лет, два номера. Семьи не смешивай. Кто относится к какой семье, я пока не указал. Даты ещё не выбраны. Общий бюджет 280000 рублей."


def stay(identifier="stay", city="Казань", rooms=None):
    return {"id": identifier, "city": city, "rooms": rooms or []}


def upsert(identifier="stay", **value):
    return {"type": "upsert_stay", "target_id": identifier, "value": value}


def test_CF08_omitted_room_count_is_grounded_without_inventing_membership_or_dates():
    before = TripState()
    ops = [{"type": "set_origin", "value": {"city": "Москва"}},
           {"type": "set_budget", "value": {"amount_minor": 28_000_000}},
           *[{"type": "add_traveler", "value": {"id": f"a{i}", "type": "adult"}} for i in range(4)],
           *[{"type": "add_traveler", "value": {"id": f"c{i}", "type": "child", "age": age}} for i, age in enumerate((6, 10))],
           upsert(city="Казань")]
    original = deepcopy(ops)
    grounded, meta = ground_room_count_operations(before, ops, CF08)
    separation = guard_group_separation(before, grounded, CF08)
    after = apply_operations(before, separation["operations"])[0]
    assert len(after.stays) == 1 and after.stays[0].city == "Казань"
    assert len(after.stays[0].rooms) == 2 and all(not r.traveler_ids for r in after.stays[0].rooms)
    assert len({r.id for r in after.stays[0].rooms}) == 2
    assert len(after.travelers) == 6 and all(p.group_id is None for p in after.travelers)
    assert len(after.groups) == 2 and all(g.keep_separate and not g.traveler_ids for g in after.groups)
    assert separation["field"] == "group.membership" and separation["question"]
    assert meta["unknowns"] == [] and meta["pending_context"] is None
    assert after.budget.amount_minor == 28_000_000 and after.stays[0].check_in is None
    assert ops == original and not before.stays
    assert meta["notes"][0]["occupants_inferred"] is False


@pytest.mark.parametrize("text,count", [("Два номера", 2), ("Нужны 2 номера.", 2), ("Три комнаты.", 3),
    ("Одна комната", 1), ("Четыре отдельных номера", 4), ("Пять номеров", 5),
    ("Нужны два номера, семьи не смешивай.", 2)])
def test_asserted_bounded_total_room_count_creates_empty_slots_only(text, count):
    before = TripState(stays=[stay()])
    ops, meta = ground_room_count_operations(before, [], text)
    after = apply_operations(before, ops)[0]
    assert len(after.stays[0].rooms) == count and all(not r.traveler_ids for r in after.stays[0].rooms)
    assert not meta["unknowns"] and not after.travelers and not after.groups


@pytest.mark.parametrize("text", [
    "А если два номера?", "Если поедем в Казань, нужны два номера.", "Раньше брали два номера.",
    "Реклама «Два номера».", "Не нужны два номера.", "Проверь два номера.", "Добавь ещё два номера.",
    "Убери два номера.", "Я хотел бы два номера.", "Два номера телефона.", "Два номера заказа.",
    "2", "Детям два и три года.", "Сравни два номера, пожалуйста.", "Взрослых два, номер паспорта не указан.",
])
def test_nonassertions_deltas_and_nonhotel_numbers_do_not_become_a_total_count(text):
    before = TripState(stays=[stay()])
    ops, meta = ground_room_count_operations(before, [], text)
    assert ops == [] and meta["unknowns"] == [] and meta["pending_context"] is None


def test_unaddressed_multiple_stays_ask_exact_target_instead_of_first_stay():
    before = TripState(stays=[stay("first", "Стамбул"), stay("second", "Анталья")])
    ops, meta = ground_room_count_operations(before, [], "Нужны два номера.")
    assert ops == [] and "какой именно остановке" in meta["unknowns"][0]
    assert meta["pending_context"]["field"] == "stay.rooms" and meta["pending_context"]["target_id"] is None
    assert meta["pending_context"]["room_target_selection_required"] is True
    assert meta["pending_context"]["requested_room_count"] == 2


@pytest.mark.parametrize("text,pending", [
    ("В Анталье два номера.", None), ("Для second два номера.", None),
    ("Два номера.", {"field": "stay.rooms", "target_id": "second", "state_version": 0}),
])
def test_unique_explicit_or_owned_target_changes_only_the_addressed_stay(text, pending):
    before = TripState(stays=[stay("first", "Стамбул"), stay("second", "Анталья")])
    ops, _ = ground_room_count_operations(before, [], text, pending)
    after = apply_operations(before, ops)[0]
    assert after.stays[0] == before.stays[0] and len(after.stays[1].rooms) == 2


@pytest.mark.parametrize("pending", [
    {"field": "stay.rooms", "target_id": "first", "state_version": 99},
    {"field": "stay.rooms", "target_id": "first", "state_version": True},
    {"field": "origin.city", "target_id": "first"},
    {"field": "stay.rooms", "target_id": ["first"]},
])
def test_stale_or_wrong_slot_cannot_resolve_ambiguous_room_target(pending):
    before = TripState(stays=[stay("first", "Стамбул"), stay("second", "Анталья")])
    ops, meta = ground_room_count_operations(before, [], "Два номера.", pending)
    assert ops == [] and meta["pending_context"]["target_id"] is None


def test_duplicate_city_is_not_an_address_and_correct_multicity_raw_room_counts_are_untouched():
    before = TripState(stays=[stay("first"), stay("second")])
    ops, meta = ground_room_count_operations(before, [], "В Казани два номера.")
    assert ops == [] and meta["pending_context"]["room_target_selection_required"]
    initial = TripState()
    proposed = [upsert("first", city="Стамбул", rooms=[{"id": "a"}, {"id": "b"}]),
                upsert("second", city="Анталья", rooms=[{"id": "c"}, {"id": "d"}])]
    ops, meta = ground_room_count_operations(initial, proposed, "В каждом городе по два номера.")
    assert ops == proposed and meta["unknowns"] == []


def test_existing_guests_are_not_erased_to_match_a_new_count_and_other_edits_survive():
    before = TripState(travelers=[{"id": "a", "type": "adult"}],
        stays=[stay(rooms=[{"id": "room-old", "traveler_ids": ["a"]}])])
    proposed = [upsert(rooms=[]), {"type": "set_budget", "value": {"amount_minor": 9_000_000}}]
    ops, meta = ground_room_count_operations(before, proposed, "Нужны два номера. Бюджет 90000 рублей.")
    after = apply_operations(before, ops)[0]
    assert after.stays == before.stays and after.budget.amount_minor == 9_000_000
    assert meta["pending_context"]["target_id"] == "stay" and meta["pending_context"]["requested_room_count"] == 2
    assert meta["pending_context"]["room_reallocation_required"] is True
    assert "перераспределить" in meta["unknowns"][0]
    assert "состав каждого из 2" in meta["unknowns"][0]


def test_existing_empty_slot_ids_are_preserved_and_new_slots_do_not_collide():
    before = TripState(stays=[stay(rooms=[{"id": "new-room-1", "traveler_ids": []}])])
    ops, _ = ground_room_count_operations(before, [], "Нужны два номера.")
    after = apply_operations(before, ops)[0]
    assert [r.id for r in after.stays[0].rooms] == ["new-room-1", "new-room-2"]


def test_count_conflict_or_technical_limit_asks_without_changing_rooms():
    before = TripState(stays=[stay()])
    for text in ("Два номера или три номера.", "Нужны 6 номеров."):
        ops, meta = ground_room_count_operations(before, [], text)
        assert ops == [] and meta["unknowns"]


def test_explicit_guest_redistribution_is_left_to_the_existing_interpreter():
    before = TripState(travelers=[{"id": "a", "type": "adult"}], stays=[stay(rooms=[{"id": "old", "traveler_ids": ["a"]}])])
    proposed = [upsert(rooms=[{"id": "old", "traveler_ids": []}, {"id": "new", "traveler_ids": ["a"]}])]
    ops, meta = ground_room_count_operations(before, proposed, "Два номера. Пересели взрослого во второй номер.")
    assert ops == proposed and meta["notes"] == []


def test_proposed_filled_rooms_are_preserved_when_one_more_empty_room_is_requested():
    before = TripState(travelers=[{"id": "a", "type": "adult"}, {"id": "b", "type": "adult"}], stays=[stay()])
    rooms = [{"id": "first", "traveler_ids": ["a"]}, {"id": "second", "traveler_ids": ["b"]}]
    proposed = [upsert(rooms=deepcopy(rooms))]
    ops, _ = ground_room_count_operations(before, proposed, "Нужны три номера.")
    after = apply_operations(before, ops)[0]
    assert [r.model_dump() for r in after.stays[0].rooms[:2]] == rooms
    assert len(after.stays[0].rooms) == 3 and after.stays[0].rooms[2].traveler_ids == []
    assert proposed == [upsert(rooms=rooms)]
