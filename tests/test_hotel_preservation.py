"""Explicit hotel preservation binds identity, never price or purchase consent."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from otravel.hotel_preservation import ground_preserved_hotel_selection

SOURCE = json.loads((Path(__file__).parents[1] / 'fixtures/regression/preserved-hotel-one-wow-20261027.json').read_text(encoding='utf-8'))


def reference(state, first='demo-selected-plan3-first'):
    return {'source': 'ui_preview_selection', 'selection_status': 'current_reference_only',
        'trip_id': state['trip_id'], 'plan_id': 'synthetic-selected-plan3', 'state_version': state['state_version'],
        'source_type': 'DEMO', 'reference_only': True, 'prices_included': False,
        'hotels': [{'stay_id': stay['id'], 'hotel_id': first if index == 0 else 'demo-selected-second',
            'city': stay['city'], 'name': 'Synthetic selected identity', 'source_type': 'DEMO'}
            for index, stay in enumerate(state['stays'])]}


def test_actual_raw_omitted_a_pin_for_the_explicit_first_hotel_preservation_wish():
    before, after = SOURCE['before_state'], SOURCE['after_state']
    assert before['stays'][0]['hotel_id'] is after['stays'][0]['hotel_id'] is None
    assert SOURCE['report_checks']['first_selected_hotel_kept'] is False
    assert SOURCE['run_status'] == 'completed' and SOURCE['report_checks']['ready_status']
    assert not any(op['type'] == 'replace_hotel' and op.get('target_id') == before['stays'][0]['id']
        for op in SOURCE['raw_validated'][0]['value']['operations'])


def test_captured_raw_is_completed_with_the_explicit_current_selected_first_hotel_identity():
    state = deepcopy(SOURCE['before_state'])
    raw = deepcopy(SOURCE['raw_validated'][0]['value']['operations'])
    selection = reference(state)
    result, meta = ground_preserved_hotel_selection(state, raw, SOURCE['text'], selection)
    assert {'type': 'replace_hotel', 'target_id': state['stays'][0]['id'],
            'value': {'hotel_id': 'demo-selected-plan3-first'}} in result
    assert not meta['unknowns'] and not meta['conflicts']
    assert raw == SOURCE['raw_validated'][0]['value']['operations']


@pytest.mark.parametrize('text', ['Первый отель сохрани.', 'Сохрани отель в Стамбуле.', 'Отель в Стамбуле оставь.'])
def test_positive_owned_ordinal_or_unique_city_uses_the_actual_selected_plan_not_first_shown(text):
    state = deepcopy(SOURCE['before_state'])
    selection = reference(state, first='plan3-chosen-first-hotel')
    result, meta = ground_preserved_hotel_selection(state, [], text, selection)
    assert result == [{'type': 'replace_hotel', 'target_id': state['stays'][0]['id'],
                       'value': {'hotel_id': 'plan3-chosen-first-hotel'}}]
    assert meta['notes'][0]['plan_id'] == 'synthetic-selected-plan3'


@pytest.mark.parametrize('text', ['Первый отель сохрани?', 'Если первый отель сохранить, что получится?',
    'Ранее просил: сохрани первый отель.', '«Сохрани первый отель».', 'Первый отель не оставь.',
    'Первый отель не сохраняй.', 'Покажи первый отель.'])
def test_questions_hypotheses_history_quotes_negated_keep_and_nonrequests_never_pin(text):
    state = deepcopy(SOURCE['before_state'])
    result, meta = ground_preserved_hotel_selection(state, [], text, reference(state))
    assert result == [] and not meta['notes'] and not meta['conflicts']


@pytest.mark.parametrize('mutation', ['version', 'foreign_trip', 'source', 'reference', 'price', 'duplicate_stay', 'foreign_stay', 'city'])
def test_stale_or_untrusted_selection_does_not_become_an_implicit_default_pin(mutation):
    state = deepcopy(SOURCE['before_state'])
    selection = reference(state)
    if mutation == 'version': selection['state_version'] += 1
    elif mutation == 'foreign_trip': selection['trip_id'] = 'foreign'
    elif mutation == 'source': selection['source'] = 'first_shown_plan'
    elif mutation == 'reference': selection['reference_only'] = False
    elif mutation == 'price': selection['prices_included'] = True
    elif mutation == 'duplicate_stay': selection['hotels'].append(deepcopy(selection['hotels'][0]))
    elif mutation == 'foreign_stay': selection['hotels'][0]['stay_id'] = 'foreign'
    else: selection['hotels'][0]['city'] = 'Белград'
    result, meta = ground_preserved_hotel_selection(state, [], 'Первый отель сохрани.', selection)
    assert result == [] and meta['unknowns'] and not meta['notes']


def test_no_selection_asks_instead_of_pinning_first_shown_but_a_saved_pin_needs_no_quote():
    state = deepcopy(SOURCE['before_state'])
    assert ground_preserved_hotel_selection(state, [], 'Добавь ребёнка.', reference(state)) == (
        [], {'notes': [], 'unknowns': [], 'conflicts': [], 'pending_context': None})
    result, meta = ground_preserved_hotel_selection(state, [], 'Первый отель сохрани.', None)
    assert result == [] and meta['unknowns']
    state['stays'][0]['hotel_id'] = 'already-saved-canonical-hotel'
    result, meta = ground_preserved_hotel_selection(state, [], 'Первый отель сохрани.', None)
    assert result == [] and not meta['unknowns'] and not meta['conflicts']


def test_same_model_assignment_is_not_duplicated_or_claimed_as_a_guard_repair():
    state = deepcopy(SOURCE['before_state'])
    operation = {'type': 'replace_hotel', 'target_id': state['stays'][0]['id'], 'value': {'hotel_id': 'selected'}}
    result, meta = ground_preserved_hotel_selection(state, [operation], 'Первый отель сохрани.', reference(state, 'selected'))
    assert result == [operation] and not meta['notes'] and not meta['unknowns'] and not meta['conflicts']


@pytest.mark.parametrize('operation', [
    {'type': 'replace_hotel', 'target_id': 'new-stay-1', 'value': {'hotel_id': 'different'}},
    {'type': 'upsert_stay', 'target_id': 'new-stay-1', 'value': {'hotel_id': None}},
    {'type': 'upsert_stay', 'target_id': 'new-stay-1', 'value': {'city': 'Москва'}},
    {'type': 'upsert_stay', 'target_id': 'new-stay-3', 'value': {'city': 'Москва'}},
    {'type': 'remove_stay', 'target_id': 'new-stay-2', 'value': {}},
    {'type': 'set_search_policy', 'value': {'hotel_provider': 'serpapi'}},
    {'type': 'set_search_policy', 'value': {'mode': 'HYBRID'}},
])
def test_conflicting_model_assignment_or_topology_provider_change_does_not_pin(operation):
    state = deepcopy(SOURCE['before_state'])
    result, meta = ground_preserved_hotel_selection(state, [operation], 'Первый отель сохрани.', reference(state))
    assert result == [operation] and meta['conflicts'] and not meta['notes']


def test_literal_same_stay_replace_and_keep_conflict_even_if_model_tried_the_selected_id():
    state = deepcopy(SOURCE['before_state'])
    result, meta = ground_preserved_hotel_selection(state, [], 'Замени первый отель. Первый отель сохрани.', reference(state))
    assert result == [] and meta['conflicts']


def test_ambiguous_hotel_or_city_ordinal_disagreement_asks_without_selecting_a_default():
    state = deepcopy(SOURCE['before_state'])
    for text in ['Сохрани отель.', 'Первый отель в Анталье сохрани.']:
        result, meta = ground_preserved_hotel_selection(state, [], text, reference(state))
        assert result == [] and meta['unknowns']
