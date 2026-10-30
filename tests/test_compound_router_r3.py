"""Captured seven-op WOW batch; normalized representations are not new NLU."""
import json
from pathlib import Path

import pytest

from otravel.domain import Operation, TripState, apply_operations
from otravel.harness import operation_conflicts
from otravel.operation_aliases import normalize_equivalent_return_ops
from otravel.room_identity import normalize_room_identities

SOURCE = json.loads((Path(__file__).parents[1] / 'fixtures/regression/compound-openrouter-r3-20261027.json').read_text(encoding='utf-8'))


def captured():
    row = SOURCE['observation']
    return row, TripState.model_validate(row['before_state']), [Operation.model_validate(op) for op in row['validated']['value']['operations']]


def test_captured_actual_compound_batch_is_blocked_by_equivalent_return_representation():
    row, state, operations = captured()
    assert len(operations) == 7
    assert any('return_edit_kind' in conflict for conflict in operation_conflicts(state, operations))
    assert any('return_edit_kind' in conflict for conflict in row['checked']['value']['conflicts'])
    assert row['actual_after_state'] == row['before_state']


def test_quoted_hotel_name_does_not_disable_an_independent_literal_return_shift():
    row, state, operations = captured()
    normalized, notes = normalize_equivalent_return_ops(state, operations, row['text'])
    assert any(note['code'] == 'equivalent_return_checkout_alias_removed' for note in notes)
    assert operation_conflicts(state, normalized) == []
    antalya = next(op for op in normalized if op.type == 'upsert_stay' and op.target_id == 'new-stay-2')
    assert 'check_out' not in antalya.value
    assert next(op for op in normalized if op.type == 'shift_return').value == {'days': 2}


@pytest.mark.parametrize('name', ['«Тихий сад»', '"Тихий сад"', "'Тихий сад'", '“Тихий сад”',
    '«Вернись на три дня раньше»'])
def test_independent_quoted_name_cannot_veto_current_unquoted_return_instruction(name):
    _, state, operations = captured()
    normalized, notes = normalize_equivalent_return_ops(state, operations,
        f'Замени отель на {name}. Перенеси возвращение на два дня позже.')
    assert operation_conflicts(state, normalized) == []
    assert any(note['code'] == 'equivalent_return_checkout_alias_removed' for note in notes)


@pytest.mark.parametrize('text', [
    'Название «Тихий сад». «Перенеси возвращение на два дня позже».',
    'Название «Тихий сад». Если перенести возвращение на два дня позже, сколько будет стоить?',
    'Название «Тихий сад». Не переноси возвращение на два дня позже.',
    'Название «Тихий сад». Ранее просил перенести возвращение на два дня позже.',
    'Название «Тихий сад». Перенеси возвращение на два дня позже?',
    'Название «Тихий сад». Перенеси возвращение на два дня позже. Возвращение сдвинь на три дня позже.',
    'Название «Тихий сад. Перенеси возвращение на два дня позже.',
    'Название "Тихий сад. Перенеси возвращение на два дня позже.',
])
def test_quote_hypothesis_veto_history_question_ambiguity_and_unclosed_quote_remain_conflicts(text):
    _, state, operations = captured()
    normalized, notes = normalize_equivalent_return_ops(state, operations, text)
    assert normalized == operations and notes == []
    assert any('return_edit_kind' in conflict for conflict in operation_conflicts(state, normalized))


def test_captured_compound_representation_preserves_room_identity_breakfast_and_all_requested_changes():
    row, state, operations = captured()
    normalized, _ = normalize_equivalent_return_ops(state, operations, row['text'])
    normalized, _ = normalize_room_identities(state, normalized)
    changed, _ = apply_operations(state, normalized)
    assert len(changed.travelers) == 4 and sorted(p.age for p in changed.travelers if p.type == 'child') == [7, 8]
    assert changed.budget.amount_minor == 38_000_000
    assert changed.stays[-1].check_out.isoformat() == changed.legs[-1].departure_date.isoformat() == '2026-11-19'
    assert changed.stays[0].check_out == state.stays[0].check_out
    assert changed.constraints == state.constraints
    assert [room.id for room in changed.stays[0].rooms] == [room.id for room in state.stays[0].rooms]
    assert [room.id for room in changed.stays[1].rooms] == [room.id for room in state.stays[1].rooms]
    assert changed.groups[1].traveler_ids == ['new-traveler-2', 'new-traveler-4']
    for stay in changed.stays:
        assert stay.rooms[0].traveler_ids == ['new-traveler-1', 'new-traveler-3']
        assert stay.rooms[1].traveler_ids == ['new-traveler-2', 'new-traveler-4']
