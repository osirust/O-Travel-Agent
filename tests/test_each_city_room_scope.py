from copy import deepcopy

import pytest
from test_api import StubLLM, _session
from test_api import api_factory as each_city_api_factory
from test_slot_continuation import _message

from otravel.room_intent import _explicit_allocation, ground_room_count_operations

api_factory = each_city_api_factory
TEXT = ("Теперь добавь ребёнка четырёх лет. В КАЖДОМ городе нужны два номера: "
    "в первом — первый взрослый и оба ребёнка, во втором — второй взрослый.")


def before():
    return {"origin": "Казань", "travelers": [{"id": "adult-a", "type": "adult"},
        {"id": "adult-b", "type": "adult"}, {"id": "child-old", "type": "child", "age": 7}],
        "stays": [{"id": "stay-first", "city": "Стамбул", "check_in": "2026-11-10", "check_out": "2026-11-13",
            "rooms": [{"id": "old-first", "traveler_ids": ["adult-a", "adult-b", "child-old"]}]},
            {"id": "stay-second", "city": "Анталья", "check_in": "2026-11-13", "check_out": "2026-11-17",
            "rooms": [{"id": "old-second", "traveler_ids": ["adult-a", "adult-b", "child-old"]}]}],
        "budget": {"amount_minor": 120000000, "currency": "RUB", "scope": "whole_trip"}}


def operations():
    return [{"type": "add_traveler", "value": {"id": "child-new", "type": "child", "age": 4}},
        *[{"type": "upsert_stay", "target_id": stay, "value": {"rooms": [
            {"id": old, "traveler_ids": ["adult-a", "child-old", "child-new"]},
            {"id": new, "traveler_ids": ["adult-b"]}]}}
          for stay, old, new in [("stay-first", "old-first", "new-room-first"),
              ("stay-second", "old-second", "new-room-second")]]]


def test_observed_valid_each_city_assignment_is_not_replaced_with_old_rooms():
    state, ops = before(), operations()
    original = deepcopy((state, ops))
    grounded, meta = ground_room_count_operations(state, ops, TEXT)
    assert grounded == ops and meta["unknowns"] == []
    assert (state, ops) == original and not meta["notes"]


@pytest.mark.parametrize("text", ["В каждом городе два номера.",
    'Нужны два номера. Цитата: «в первом взрослый, во втором ребёнок».',
    "Если нужны два номера, в первом взрослый, во втором ребёнок.",
    "В первом городе два номера, во втором городе завтрак для ребёнка.",
    "Не переселяй взрослых. Нужны два номера."])
def test_counts_quotes_hypotheses_or_city_ordinals_are_not_reallocation_authority(text):
    assert not _explicit_allocation(text)


@pytest.mark.asyncio
async def test_actual_compound_shape_persists_both_complete_room_partitions(api_factory):
    model = StubLLM([{"operations": operations(), "speech_act": "CHANGE", "control": {"action": "continue"},
        "unknowns": [], "conflicts": [], "reply": "Предлагаю расселение в обоих городах."}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, before())
        run, after = await _message(client, sid, token, TEXT)
        assert len(model.calls) == 1
        assert [person["id"] for person in after["trip"]["travelers"]] == ["adult-a", "adult-b", "child-old", "child-new"]
        for stay in after["trip"]["stays"]:
            assert [room["traveler_ids"] for room in stay["rooms"]] == [["adult-a", "child-old", "child-new"], ["adult-b"]]
        assert not after.get("pending_question") or after["pending_question"].get("field") != "stay.rooms"
        assert not any(note.get("code") == "count_only_room_membership_preserved"
            for note in run["result"]["result"].get("operation_normalizations", []))
