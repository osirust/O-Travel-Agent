"""Cached actual-CF regressions and conservative pure return-alias boundaries."""
import json
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pytest

from otravel.domain import Operation, TripState, apply_operations, sample_trip
from otravel.harness import operation_conflicts
from otravel.money import ground_budget_operations
from otravel.operation_aliases import normalize_equivalent_return_ops

FIXTURE = json.loads((Path(__file__).resolve().parents[1] / 'fixtures/regression/after80-cf-native-6009644.json').read_text())


def cached(identifier):
    row = next(c for c in FIXTURE['cases'] if c['id'] == identifier)
    return TripState.model_validate(row['before_state']), [Operation.model_validate(op) for op in row['raw']['operations']], row['user']


def test_actual_cf06_new_null_group_defaults_do_not_conflict_with_later_assignments():
    state, ops, _ = cached('CF10-06')
    before = deepcopy(ops)
    assert operation_conflicts(state, ops) == []
    updated, _ = apply_operations(state, ops)
    assert ops == before and len(updated.travelers) == 6 and len(updated.groups) == 2
    assert [s.city for s in updated.stays] == ['Стамбул', 'Анталья']
    assert all(len(s.rooms) == 2 for s in updated.stays)
    assert all(g.keep_separate and len(g.traveler_ids) == 3 for g in updated.groups)
    assert all(p.group_id is not None for p in updated.travelers)
    assert state.travelers == [] and state.stays == []


@pytest.mark.parametrize('kind,target,value', [
    ('update_traveler', 'child-1', {'group_id': None}),
    ('add_traveler', None, {'id': 'adult-1', 'type': 'adult', 'group_id': None}),
])
def test_existing_explicit_group_clear_or_duplicate_creation_still_conflicts(kind, target, value):
    state = sample_trip()
    ops = [Operation(type=kind, target_id=target, value=value), Operation(type='upsert_group', target_id='family-1',
        value={'traveler_ids': state.groups[0].traveler_ids, 'keep_separate': True})]
    assert any('group_membership' in c for c in operation_conflicts(state, ops))


def test_new_nonnull_group_and_different_setter_still_conflict():
    state = sample_trip()
    ops = [Operation(type='add_traveler', value={'id': 'new-child', 'type': 'child', 'age': 5, 'group_id': 'family-1'}),
           Operation(type='upsert_group', target_id='family-2', value={'traveler_ids': ['new-child'], 'keep_separate': True})]
    assert any('group_membership.new-child' in c for c in operation_conflicts(state, ops))


def test_actual_cf10_equivalent_date_alias_then_budget_grounding_applies_once():
    state, ops, text = cached('CF10-10')
    original = deepcopy(ops)
    assert operation_conflicts(state, ops)
    normalized, notes = normalize_equivalent_return_ops(state, ops, text)
    assert notes[0]['code'] == 'equivalent_return_checkout_alias_removed'
    assert notes[0]['removed_absolute_check_out'] == '2026-11-15'
    assert operation_conflicts(state, normalized) == [] and ops == original
    assert next(o for o in normalized if o.type == 'upsert_stay').value == {
        k: v for k, v in next(o for o in ops if o.type == 'upsert_stay').value.items() if k != 'check_out'}
    grounded, money_notes = ground_budget_operations(state, normalized, text)
    assert next(o.value['amount_minor'] for o in grounded if o.type == 'set_budget') == 78000000
    result, _ = apply_operations(state, grounded)
    assert result.stays[-1].check_out.isoformat() == '2026-11-15'
    assert result.legs[-1].departure_date.isoformat() == '2026-11-15'
    assert result.stays[0] == state.stays[0]
    assert result.budget.amount_minor == 78000000
    assert len(result.travelers) == 10 and result.travelers[-1].age == 5 and result.travelers[-1].group_id == 'family-1'
    assert state.stays[-1].check_out.isoformat() == '2026-11-13' and money_notes


def alias_ops(state=None, *, days=2, absolute=None, extra=None, raw=False, reverse=False):
    state = state or sample_trip()
    absolute = absolute or (state.stays[-1].check_out + timedelta(days=days)).isoformat()
    ops = [{'type': 'upsert_stay', 'target_id': state.stays[-1].id, 'value': {'check_out': absolute, **(extra or {})}},
           {'type': 'shift_return', 'value': {'days': days}}]
    if reverse:
        ops.reverse()
    return ops if raw else [Operation.model_validate(o) for o in ops]


@pytest.mark.parametrize('text,days', [
    ('Перенеси возвращение на два дня позже.', 2), ('Сдвинь обратный рейс на 2 дня.', 2),
    ('Возвращение перенеси на один день раньше.', -1), ('Отложи обратный вылет на три дня.', 3),
    ('Продли последнее проживание на два дня. Возвращение сдвигается вслед за новым выездом.', 2),
])
@pytest.mark.parametrize('raw,reverse', [(False, False), (True, False), (False, True)])
def test_exact_equivalent_alias_is_order_independent_and_does_not_double_shift(text, days, raw, reverse):
    state = sample_trip()
    ops = alias_ops(state, days=days, raw=raw, reverse=reverse)
    original = deepcopy(ops)
    result, notes = normalize_equivalent_return_ops(state, ops, text)
    assert len(result) == 1 and (result[0]['type'] if raw else result[0].type) == 'shift_return'
    assert notes and ops == original
    changed, _ = apply_operations(state, result)
    assert changed.stays[-1].check_out == state.stays[-1].check_out + timedelta(days=days)
    assert changed.legs[-1].departure_date == changed.stays[-1].check_out
    assert normalize_equivalent_return_ops(state, result, text) == (result, [])


@pytest.mark.parametrize('text', [
    'Продли последнее проживание на два дня.',
    'Выезд из последнего отеля сдвинь на два дня, дату обратного рейса не меняй.',
    'Если перенести возвращение на два дня позже, сколько будет стоить?',
    'Ранее просил перенести возвращение на два дня позже.',
    'Модель ответила: «Сдвинь возвращение на два дня».',
    'Не сдвигай возвращение на два дня.', 'Сдвинь возвращение на два дня?',
    'Добавь ребёнка и уменьши бюджет на 20000 рублей.',
    'Сдвинь возвращение на три дня.',
])
def test_unsupported_hotel_only_history_hypothesis_quotes_and_inconsistent_count_are_unchanged(text):
    ops = alias_ops()
    result, notes = normalize_equivalent_return_ops(sample_trip(), ops, text)
    assert result == ops and notes == []


def test_mismatched_absolute_is_not_removed_and_yields_precise_question():
    ops = alias_ops(absolute='2026-11-16')
    result, notes = normalize_equivalent_return_ops(sample_trip(), ops, 'Сдвинь возвращение на два дня позже.')
    assert result == ops and operation_conflicts(sample_trip(), result)
    assert notes[0]['status'] == 'clarification_required'
    assert '15.11.2026' in notes[0]['question'] and '16.11.2026' in notes[0]['question']


@pytest.mark.parametrize('kind', ['one_way', 'undecided'])
def test_no_return_route_is_never_given_a_return_by_alias_normalization(kind):
    state = sample_trip()
    state.route_policy.kind = kind
    ops = alias_ops()
    assert normalize_equivalent_return_ops(state, ops, 'Сдвинь возвращение на два дня.') == (ops, [])


def test_other_last_stay_fields_are_preserved_without_touching_first_stay():
    state = sample_trip()
    ops = alias_ops(extra={'hotel_id': 'explicit-choice', 'rooms': [{'id': 'kept-room', 'traveler_ids': []}]})
    result, _ = normalize_equivalent_return_ops(state, ops, 'Перенеси возвращение на два дня.')
    assert result[0].value == {'hotel_id': 'explicit-choice', 'rooms': [{'id': 'kept-room', 'traveler_ids': []}]}


def test_missing_before_date_and_changed_topology_cannot_prove_alias():
    state = sample_trip()
    ops = alias_ops()
    state.stays[-1].check_out = None
    assert normalize_equivalent_return_ops(state, ops, 'Сдвинь возвращение на два дня.') == (ops, [])
    state = sample_trip()
    ops.append(Operation(type='set_route_policy', value={'kind': 'one_way'}))
    assert normalize_equivalent_return_ops(state, ops, 'Сдвинь возвращение на два дня.') == (ops, [])
