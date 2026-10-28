"""Current date-preservation authority; pure cached model regression."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from otravel.domain import Operation, TripState, apply_operations, sample_trip
from otravel.duration_grounding import guard_unasked_duration_operations
from otravel.harness import operation_conflicts
from otravel.return_preservation import guard_preserved_return_operations as guard

FIXTURE = Path(__file__).parents[1] / "fixtures/regression/return-preservation-complex-r5-20261028.json"


def shifted():
    return [{"type": "shift_return", "target_id": None, "value": {"days": 2}}]


@pytest.mark.parametrize("text", [
    "Добавь ребёнка. Даты и возвращение оставь прежними.",
    "Поменяй второй отель, а даты сохрани.", "Не меняй даты и время возвращения.",
    "Возвращение 19.11.2026 сохрани.", "Даты, маршрут, возвращение 19 ноября 2026 и бюджет оставь прежними.",
    "Keep travel dates unchanged.", "Do not change return date.", "Don't change return date.",
])
def test_current_explicit_preservation_suppresses_only_model_return_shift(text):
    state = sample_trip()
    operations = [*shifted(), {"type": "set_budget", "value": {"amount_minor": 7000000, "currency": "RUB"}}]
    before = deepcopy(operations)
    result, meta = guard(state, operations, text)
    assert result == before[1:] and operations == before
    assert len(meta["notes"]) == 1 and meta["unknowns"] == meta["conflicts"] == []


@pytest.mark.parametrize("text", [
    "Даты Стамбула сохрани, но возвращение перенеси на два дня позже.",
    "Сохрани заезд. Возвращение перенеси на 20.11.2026.",
    "Даты Стамбула сохрани. Продли проживание на два дня и следом возвращение.",
    "Сохрани заезд, выезд измени на 20 ноября 2026.",
    "Keep hotel dates, move return by 2 days.",
    "Даты сохрани, возвращение +2 дня.",
    "Сохрани заезд. Возвращение перенеси на два дня раньше.",
    "Сохрани заезд, возвращение 20.11.2026.",
    "Даты Стамбула оставь прежними. Возвращение завтра.",
    "Keep hotel dates. Return 20 November 2026.",
])
def test_true_or_ambiguous_positive_current_date_edit_remains_for_existing_validation(text):
    operations = shifted()
    result, meta = guard(sample_trip(), operations, text)
    assert result == operations and not meta["notes"]


@pytest.mark.parametrize("text", [
    'Цитата: «Даты сохрани». Добавь ребёнка.', "Если даты сохраним, сможешь найти отель?",
    "Раньше я просил сохранить даты. Сейчас поменяй отель.", "Даты не сохраняй.",
    "Не оставляй даты прежними.", "Даты сохрани?", "Напомни текущие даты.",
    "Датчик оставь прежним.", '`Keep dates unchanged`. Find a hotel.',
])
def test_quotes_hypotheses_history_negated_keep_and_other_subjects_grant_no_preservation_authority(text):
    operations = shifted()
    assert guard(sample_trip(), operations, text)[0] == operations


def test_typed_and_missing_stable_return_are_not_repaired():
    operations = shifted()
    assert guard(sample_trip(), operations, "Даты сохрани.", model_generated=False)[0] == operations
    assert guard(sample_trip(), operations, '\x1eOTRAVEL_TYPED_V1:{"kind":"commands"}')[0] == operations
    assert guard(TripState(trip_id="unknown-return"), operations, "Даты сохрани.")[0] == operations
    state = sample_trip()
    state.legs[-1].departure_date = None
    assert guard(state, operations, "Даты сохрани.")[0] == operations


@pytest.mark.parametrize("value", [{"days": "2"}, {"days": True}, {"days": 2, "date": "2026-11-19"}])
def test_invalid_shift_shape_is_not_silently_removed(value):
    operations = [{"type": "shift_return", "value": value}]
    assert guard(sample_trip(), operations, "Даты сохрани.")[0] == operations


def test_exact_cached_correction_raw_keeps_valid_room_group_changes_and_date19_without_replay():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    state = TripState.model_validate(fixture["before_trip"])
    raw = fixture["raw_contract"]["value"]["operations"]
    assert any("trip.return_edit_kind" in value for value in operation_conflicts(state, [Operation.model_validate(op) for op in raw]))
    result, meta = guard(state, raw, fixture["input"])
    assert result == [op for op in raw if op["type"] != "shift_return"] and len(meta["notes"]) == 1
    assert operation_conflicts(state, [Operation.model_validate(op) for op in result]) == []
    grounded, duration_meta = guard_unasked_duration_operations(state, result, fixture["input"])
    assert len(duration_meta["notes"]) == 2
    after, _changes = apply_operations(state, grounded)
    assert after.stays[-1].check_out.isoformat() == after.legs[-1].departure_date.isoformat() == "2026-11-19"
    assert [len(stay.rooms) for stay in after.stays] == [2, 2]
    assert all(stay.rooms[0].traveler_ids == ["new-traveler-1", "new-traveler-3", "new-traveler-4"]
        and stay.rooms[1].traveler_ids == ["new-traveler-2"] for stay in after.stays)
    assert next(person for person in after.travelers if person.id == "new-traveler-4").group_id == "new-group-1"
    assert len(after.groups[0].traveler_ids) == 4 and after.budget.amount_minor == 118000000
    assert [len(stay.rooms) for stay in state.stays] == [1, 1]


def test_nonreturn_conflicts_are_preserved_after_shift_suppression():
    operations = [*shifted(), {"type": "set_budget", "value": {"amount_minor": 1000000}},
        {"type": "set_budget", "value": {"amount_minor": 2000000}}]
    state = sample_trip()
    result, _meta = guard(state, operations, "Даты сохрани.")
    assert result == operations[1:]
    assert operation_conflicts(state, [Operation.model_validate(op) for op in result])
