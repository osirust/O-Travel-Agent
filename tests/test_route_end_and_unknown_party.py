"""Retained route-end and unknown-party regressions; pure, no external calls."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from otravel import intent_grounding as geo
from otravel import route_policy_intent as policy
from otravel import unknown_party_categories as party

FIXTURE = json.loads((Path(__file__).parents[1] / 'fixtures/regression/route-end035-unknown-party025-20261028.json').read_text(encoding='utf-8'))


def actual(case_id):
    return deepcopy(next(row for row in FIXTURE['records'] if row['id'] == case_id))


def route(city='Москва'):
    return {'type': 'set_route_policy', 'value': {'kind': 'open_jaw', 'end_city': city}, 'target_id': None}


def test_exact035_correct_model_endpoint_survives_without_touching_hotel():
    row = actual('ZAI100-035')
    before, text = row['before']['trip'], row['input']
    raw = row['events'][0]['final_json']['operations']
    guarded, meta = policy.guard_route_policy_operations(before, raw, text)
    assert guarded == raw and not meta['unknowns']
    final, geo_meta = geo.ground_intent_operations(before, guarded, text)
    assert final == raw
    assert not any(note['code'] == 'destination_grounded_from_current_literal' for note in geo_meta['notes'])


@pytest.mark.parametrize('cue', ['Подтверждаю завершение', 'Окончание поездки', 'Конец маршрута'])
@pytest.mark.parametrize('raw', [[], [{'type': 'upsert_stay', 'target_id': 'new-stay-1', 'value': {'city': 'Москва'}}]])
def test_endpoint_role_protects_lodging_even_without_a_route_operation(cue, raw):
    before = actual('ZAI100-035')['before']['trip']
    final, _ = geo.ground_intent_operations(before, raw, cue + ' в Москве после Дубая.')
    assert not any(op['type'] == 'upsert_stay' and op['value'].get('city') == 'Москва' for op in final)


@pytest.mark.parametrize('text', [
    'Подтверждаю завершение в Москве или Казани после Дубая.',
    'Окончание поездки в Москве либо Казани после Дубая.',
    'Подтверждаю завершение в Москве, или Казани после Дубая.',
])
def test_two_endpoint_alternatives_before_context_require_a_choice(text):
    guarded, meta = policy.guard_route_policy_operations(actual('ZAI100-035')['before']['trip'], [route()], text)
    assert not guarded and meta['unknowns']


@pytest.mark.parametrize('text', [
    'Если подтверждаю завершение в Москве после Дубая.',
    '«Подтверждаю завершение в Москве после Дубая».',
    'Раньше подтверждаю завершение в Москве после Дубая.',
    'Подтверждаю завершение в Москве после Дубая?',
    'Не подтверждаю завершение в Москве после Дубая.',
])
def test_noncurrent_ending_is_not_route_authority(text):
    guarded, _ = policy.guard_route_policy_operations(actual('ZAI100-035')['before']['trip'], [route()], text)
    assert not guarded


def test_explicit_hotel_city_still_authorizes_lodging_change():
    before = actual('ZAI100-035')['before']['trip']
    final, _ = geo.ground_intent_operations(before, [], 'Теперь нужен отель в Москве.')
    assert final == [{'type': 'upsert_stay', 'target_id': 'new-stay-1', 'value': {'city': 'Москва'}}]


def test_exact025_total_does_not_authorize_four_adult_categories():
    row = actual('ZAI100-025')
    raw = row['events'][0]['final_json']['operations']
    frozen = deepcopy(raw)
    kept, meta = party.guard_unknown_party_categories(row['before']['trip'], raw, row['input'])
    assert [op['type'] for op in kept] == ['set_origin', 'upsert_stay']
    assert meta['pending_context']['field'] == 'party.adults'
    assert meta['pending_context']['declared_total'] == 4
    assert meta['unknowns'] and 'взрослых' in meta['unknowns'][0] and 'детей' in meta['unknowns'][0]
    assert not meta['conflicts']
    assert raw == frozen


def test_unknown_categories_remove_dependent_membership_but_keep_city_dates_money():
    row = actual('ZAI100-025')
    raw = row['events'][0]['final_json']['operations']
    raw.extend([
        {'type': 'upsert_group', 'target_id': 'new-group-1', 'value': {'traveler_ids': ['new-traveler-1'], 'keep_separate': True}},
        {'type': 'set_room_members', 'target_id': 'new-room-1', 'value': {'stay_id': 'new-stay-1', 'traveler_ids': ['new-traveler-1']}},
        {'type': 'upsert_stay', 'target_id': 'new-stay-1', 'value': {'city': 'Сочи', 'check_in': '2026-11-10', 'rooms': [{'id': 'new-room-1', 'traveler_ids': ['new-traveler-1']}] }},
        {'type': 'set_budget', 'value': {'amount_minor': 10000000, 'currency': 'RUB'}},
    ])
    kept, meta = party.guard_unknown_party_categories(row['before']['trip'], raw, row['input'])
    assert not any(op['type'] in {'add_traveler', 'upsert_group', 'set_room_members'} for op in kept)
    assert kept[-1] == raw[-1]
    assert kept[-2]['value'] == {'city': 'Сочи', 'check_in': '2026-11-10'}
    assert meta['pending_context']['declared_total'] == 4


@pytest.mark.parametrize('text', [
    'Нас четверо взрослых, хотим из Санкт-Петербурга в Сочи. Возраст ещё не перечислил, не угадывай.',
    'Нас четверо, два взрослых и двое детей. Возраст ещё не перечислил.',
    'Нас четверо: я, муж и дети. Возраст пока не назвал.',
    'Нас четверо. Возраст я ещё не перечислил, не угадывай.',
    'Если нас четверо, состав я ещё не перечислил, не угадывай.',
    '«Нас четверо, состав я ещё не перечислил, не угадывай».',
    'Раньше нас четверо, состав я ещё не перечислил, не угадывай.',
    'Нас четверо, состав я ещё не перечислил, не угадывай?',
    'Четыре номера, состав я ещё не перечислил, не угадывай.',
    'У нас четыре номера, состав я ещё не перечислил, не угадывай.',
])
def test_only_proved_current_unknown_composition_is_in_scope(text):
    row = actual('ZAI100-025')
    raw = row['events'][0]['final_json']['operations']
    kept, meta = party.guard_unknown_party_categories(row['before']['trip'], raw, text)
    assert kept == raw and not meta['unknowns']


def test_existing_party_is_not_replaced_by_initial_unknown_category_guard():
    row = actual('ZAI100-025')
    before = row['before']['trip']
    before['travelers'] = [{'id': 'existing-adult', 'type': 'adult', 'age': None}]
    raw = row['events'][0]['final_json']['operations']
    kept, meta = party.guard_unknown_party_categories(before, raw, row['input'])
    assert kept == raw and not meta['unknowns']


def test_unknown_categories_with_no_model_additions_still_ask_without_inventing_people():
    row = actual('ZAI100-025')
    raw = [op for op in row['events'][0]['final_json']['operations'] if op['type'] != 'add_traveler']
    kept, meta = party.guard_unknown_party_categories(row['before']['trip'], raw, row['input'])
    assert kept == raw and meta['pending_context']['declared_total'] == 4
