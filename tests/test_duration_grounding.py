"""Conservative unasked-duration suppression; no HTTP, model or database."""
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from otravel.domain import DomainError, Operation, TripState, apply_operations
from otravel.duration_grounding import FINDING_ID, guard_unasked_duration_operations

FIXTURE = Path(__file__).parents[1] / "fixtures/regression/duration-initial-complex-r3-20261027.json"


def setter(**values):
    return {"type": "upsert_stay", "target_id": "stay", "value": {
        "city": "Стамбул", "check_in": "2026-11-10", "check_out": "2026-11-13", "requested_nights": 3, **values}}


@pytest.mark.parametrize("text", [
    "Стамбул, заезд 10 ноября 2026, выезд 13 ноября 2026.",
    "Два взрослых и ребёнок 7 лет, Стамбул 10–13 ноября 2026.",
    "Из Казани в Стамбул 10.11.2026–13.11.2026. Нужен один номер.",
    "Istanbul, check-in 2026-11-10, check-out 2026-11-13.",
])
def test_only_new_unasked_nights_removed_from_complete_dates(text):
    before, operations = TripState(trip_id="duration-owned"), [setter(), {"type": "set_origin", "value": {"city": "Казань"}}]
    saved = deepcopy(operations)
    result, meta = guard_unasked_duration_operations(before, operations, text)
    expected = deepcopy(saved)
    del expected[0]["value"]["requested_nights"]
    assert result == expected and operations == saved and before.stays == []
    assert meta["notes"][0]["finding_id"] == FINDING_ID
    assert meta["unknowns"] == meta["conflicts"] == []


@pytest.mark.parametrize("duration", [
    "на 3 ночи", "на3ночи", "на семь ночей", "на один день", "на 10 дней", "на 2 недели",
    "на неделю", "на одну неделю", "на пару суток", "на 1,5 недели", "7 дн.", "2 нед.",
    "две ночи", "три ночи", "длительность пока не знаю", "на выходные", "на год", "на1год",
    "for 3 nights", "for a week", "for seven days", "for 2weeks", "duration=3", "requested_nights=3",
    'Цитирую: «на 3 ночи»', "Если поедем на неделю", "не на три ночи", "В другом городе 3 ночи",
])
def test_any_explicit_or_ambiguous_duration_language_vetoes_this_narrow_guard(duration):
    operations = [setter()]
    result, meta = guard_unasked_duration_operations(TripState(trip_id="duration-owned"), operations,
        duration + ". Заезд 10.11.2026, выезд 13.11.2026.")
    assert result == operations and meta == {"notes": [], "unknowns": [], "conflicts": []}


@pytest.mark.parametrize("change", [
    {"check_in": None}, {"check_out": None}, {"check_out": "2026-11-09"}, {"check_in": "10.11.2026"},
    {"check_out": "2026-02-31"}, {"requested_nights": None}, {"requested_nights": True},
    {"requested_nights": 3.0}, {"requested_nights": "3"}, {"requested_nights": 0}, {"requested_nights": 100000},
])
def test_partial_or_invalid_shapes_are_not_repaired_by_suppression(change):
    operations = [setter(**change)]
    result, meta = guard_unasked_duration_operations(TripState(trip_id="duration-owned"), operations, "Хочу в Стамбул.")
    assert result == operations and not meta["notes"]


@pytest.mark.parametrize("nights", [3, 7])
def test_existing_nonnull_duration_is_never_stripped_even_if_model_changes_it(nights):
    before = TripState(trip_id="duration-owned", stays=[{"id": "stay", "city": "Стамбул",
        "check_in": "2026-11-10", "check_out": "2026-11-13", "requested_nights": 3}])
    operations = [setter(requested_nights=nights)]
    result, meta = guard_unasked_duration_operations(before, operations, "Выезд 17.11.2026.")
    assert result == operations and not meta["notes"] and before.stays[0].requested_nights == 3


def test_existing_null_duration_can_suppress_new_model_number_without_changing_other_fields():
    before = TripState(trip_id="duration-owned", stays=[{"id": "stay", "city": "Стамбул"}])
    result, meta = guard_unasked_duration_operations(before, [setter(requested_nights=7)], "Заезд 10.11.2026, выезд 13.11.2026.")
    assert "requested_nights" not in result[0]["value"] and result[0]["value"]["check_out"] == "2026-11-13"
    assert len(meta["notes"]) == 1


@pytest.mark.parametrize("model_generated,text", [(False, "В Стамбул"), (1, "В Стамбул"),
    (True, '\x1eOTRAVEL_TYPED_V1:{"kind":"patch"}')])
def test_typed_or_nonmodel_operations_are_untouched(model_generated, text):
    operations = [Operation.model_validate(setter())]
    result, meta = guard_unasked_duration_operations(TripState(trip_id="duration-owned"), operations, text,
        model_generated=model_generated)
    assert result == [operation.model_dump(mode="json") for operation in operations] and not meta["notes"]


def test_cached_actual_initial_batch_does_not_create_new_hard_durations_then_followup_date_edit_succeeds():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw = fixture["raw_contract"]["value"]
    digest = lambda value: hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    assert digest(raw) == fixture["source_projection_digests"]["initial_contract_value_canonical_sha256"]
    assert digest(fixture["followup"]["raw_contract"]["value"]) == fixture["source_projection_digests"]["followup_contract_value_canonical_sha256"]
    before = TripState.model_validate(fixture["before_trip"])
    unguarded, _ = apply_operations(before, raw["operations"])
    assert [stay.requested_nights for stay in unguarded.stays] == [3, 4]
    followup = fixture["followup"]["raw_contract"]["value"]["operations"]
    with pytest.raises(DomainError) as error:
        apply_operations(unguarded, followup)
    assert error.value.code == "duration_mismatch"
    guarded, meta = guard_unasked_duration_operations(before, raw["operations"], fixture["input"])
    assert len(meta["notes"]) == 2
    expected = deepcopy(raw["operations"])
    for operation in expected:
        if operation["type"] == "upsert_stay":
            del operation["value"]["requested_nights"]
    assert guarded == expected
    after, _ = apply_operations(before, guarded)
    assert [stay.requested_nights for stay in after.stays] == [None, None]
    checked_followup, follow_meta = guard_unasked_duration_operations(after, followup, fixture["followup"]["input"])
    assert checked_followup == followup and not follow_meta["notes"]
    final, _ = apply_operations(after, checked_followup)
    assert final.stays[0].check_out.isoformat() == "2026-11-13"
    assert final.stays[1].check_out.isoformat() == "2026-11-19"
    assert final.legs[-1].departure_date.isoformat() == "2026-11-19"
    assert [stay.requested_nights for stay in final.stays] == [None, None]


def test_old_existing_derived_duration_provenance_remains_open_and_unchanged():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    old, _ = apply_operations(TripState.model_validate(fixture["before_trip"]), fixture["raw_contract"]["value"]["operations"])
    followup = fixture["followup"]["raw_contract"]["value"]["operations"]
    checked, meta = guard_unasked_duration_operations(old, followup, fixture["followup"]["input"])
    assert checked == followup and not meta["notes"]
    with pytest.raises(DomainError) as error:
        apply_operations(old, checked)
    assert error.value.code == "duration_mismatch" and old.stays[1].requested_nights == 4
