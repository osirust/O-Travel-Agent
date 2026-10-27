"""Completeness of explicit per-city counts; no allocator or actual NLU claim."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from otravel.domain import TripState, apply_operations, sample_trip
from otravel.initial_room_layout import verify_universal_room_count

FIXTURE = Path(__file__).parents[1] / "fixtures/regression/universal-room-count-complex-r5-20261028.json"


def cached():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_exact_cached_original_compound_exposes_missing_per_city_count_without_inventing_rooms_or_group():
    fixture = cached()
    operations = fixture["checked_contract"]["value"]["operations"]
    original = deepcopy(operations)
    result, meta = verify_universal_room_count(fixture["before_trip"], operations, fixture["input"])
    assert result == operations == original
    assert meta["block_batch"] is True and len(meta["unknowns"]) == 1
    assert meta["notes"][0]["finding_id"] == "HARNESS-004-UNIVERSAL-ROOM-COUNT-COVERAGE-001"
    assert meta["notes"][0]["requested_room_count"] == 2
    assert meta["pending_context"]["field"] == "stay.rooms"
    assert meta["pending_context"]["force_clarification"] is True
    assert set(meta["notes"][0]["affected_stay_ids"]) == {stay["id"] for stay in fixture["before_trip"]["stays"]}
    assert "2" in meta["unknowns"][0] and "Istanbul" in meta["unknowns"][0] and "Antalya" in meta["unknowns"][0]
    candidate, _ = apply_operations(TripState.model_validate(fixture["before_trip"]), result)
    assert [len(stay.rooms) for stay in candidate.stays] == [1, 1]
    assert next(person for person in candidate.travelers if person.age == 4).group_id is None


@pytest.mark.parametrize("text", [
    "В каждом городе сделай два номера.", "В каждом отеле нужны два гостиничных номера.",
    "В обоих отелях два номера.", "В каждом из двух отелей два номера.",
])
def test_asserted_universal_count_is_checked_even_when_model_omits_all_room_operations(text):
    state = sample_trip()
    for stay in state.stays:
        stay.rooms = stay.rooms[:2]
    assert [len(stay.rooms) for stay in state.stays] == [2, 2]
    state.stays[1].rooms = state.stays[1].rooms[:1]
    result, meta = verify_universal_room_count(state, [], text)
    assert result == [] and meta["block_batch"] is True
    assert meta["notes"][0]["affected_stay_ids"] == [state.stays[1].id]


@pytest.mark.parametrize("text", [
    "В каждом городе два номера.", "В каждом отеле два номера: распределение прежнее.",
])
def test_correct_explicit_count_is_never_rewritten_or_asked_again(text):
    state = sample_trip()
    for stay in state.stays:
        stay.rooms = stay.rooms[:2]
    operations = [{"type": "set_room_members", "target_id": state.stays[0].rooms[0].id,
        "value": {"stay_id": state.stays[0].id, "traveler_ids": state.stays[0].rooms[0].traveler_ids}}]
    result, meta = verify_universal_room_count(state, operations, text)
    assert result == operations and not meta["block_batch"] and meta["unknowns"] == meta["notes"] == []


@pytest.mark.parametrize("text", [
    "Если в каждом городе два номера, сколько это стоит?", "В каждом городе два номера?",
    'Он сказал «в каждом городе два номера».', "Раньше в каждом городе было два номера.",
    "Не в каждом городе нужны два номера.", "В каждом городе не два номера.",
    "В каждом городе не делай два номера.", "Напомни, в каждом городе два номера.",
    "В каждом городе два номера, кроме Стамбула.", "В каждом городе два номера. В Стамбуле один номер.",
    "В первом городе два номера.", "Два номера на всю поездку.",
    "В каждом городе два или три номера.", "В каждом городе99 номеров.",
])
def test_quoted_hypothetical_negated_ambiguous_nonuniversal_counts_are_outside_scope(text):
    state = sample_trip()
    state.stays[0].rooms = state.stays[0].rooms[:1]
    operations = []
    result, meta = verify_universal_room_count(state, operations, text)
    assert result == [] and not meta["block_batch"] and not meta["unknowns"]


def test_typing_one_city_and_domain_errors_are_not_overridden():
    state = sample_trip()
    assert not verify_universal_room_count(state, [], "В каждом городе три номера.", model_generated=False)[1]["block_batch"]
    state.stays = state.stays[:1]
    # A non-round-trip projection is intentionally irrelevant to room coverage.
    assert not verify_universal_room_count(state, [], "В каждом городе три номера.")[1]["block_batch"]
    malformed = [{"type": "set_room_members", "target_id": "missing-room", "value": {"traveler_ids": []}}]
    assert not verify_universal_room_count(sample_trip(), malformed, "В каждом городе три номера.")[1]["block_batch"]
