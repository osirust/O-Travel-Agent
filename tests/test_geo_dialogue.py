"""Owned connected geo planner controls; no model, API, Store or fixture DB."""
from copy import deepcopy

import pytest

from otravel.country_intake import literal_country_wish
from otravel.domain import Operation, TripState, apply_operations
from otravel.geo_dialogue import resolve_geo_destination
from otravel.geography import parse_destination
from otravel.intent_grounding import ground_intent_operations


def place(name):
    return parse_destination(name)['requested_destination']


def legacy():
    state = TripState(trip_id='owned-trip', state_version=1, origin='Москва',
        travelers=[{'id': 'adult', 'type': 'adult'}],
        stays=[{'id': 'owned-stay', 'city': 'Белгород', 'check_in': '2026-10-28',
            'check_out': '2026-11-04', 'requested_nights': 7, 'hotel_id': 'old-city-hotel', 'rooms': []}],
        budget={'amount_minor': 8_000_000}, preferences=[{'key': 'price_priority', 'value': 'lowest_total'}])
    return state.model_dump(mode='json')


def pending(state, *, field='destination.city', requested='Белград', proposal=None):
    result = {'state_version': state['state_version'], 'reason': 'clarification', 'field': field,
        'target_id': 'owned-stay' if state['stays'] else None,
        'text': 'Сохраняем это направление без поиска или вы хотите его изменить?',
        'context': {'requested_destination': place(requested)}}
    if proposal:
        result['context'].update(proposed_destination=place(proposal), confirmation_required=True,
            proposal_field='destination.city')
    return result


def ask_packet(state, answer):
    return {'state_version': state['state_version'], 'reason': 'clarification',
        'field': 'destination.city', 'target_id': answer['pending_context']['target_id'],
        'text': answer['question'], 'context': answer['pending_context']}


@pytest.mark.parametrize('text', ['хочу в сербию наверное', 'наверное, хочу в Сербию',
    'в Сербию хочу пожалуй', 'я хочу поехать в Сербию, наверное.'])
def test_soft_wish_is_tentative_country_intake_without_trip_mutation(text):
    state = TripState().model_dump(mode='json')
    before = deepcopy(state)
    answer = literal_country_wish(state, text)
    assert answer['requested_destination']['country_code'] == 'RS'
    assert 'город' in answer['question'] and state == before


@pytest.mark.parametrize('text', ['если хочу в Сербию наверное', 'может быть хочу в Сербию',
    'хочу в Сербию наверное из Москвы', 'хочу в Сербию, наверное, бюджет 50000',
    'не хочу в Сербию наверное', 'хочу в Сербию наверное?', 'хочу в Сербию. Подожди.'])
def test_soft_wish_never_swallows_hypotheses_negation_or_independent_facts(text):
    assert literal_country_wish(TripState(), text) is None


def test_serbian_context_proposes_belgrade_without_inventing_a_russian_stay():
    state = TripState().model_dump(mode='json')
    answer = resolve_geo_destination(state, pending(state, requested='Сербия'), 'Белгород')
    assert answer['handled'] and answer['operations'] == []
    assert answer['pending_context']['proposed_destination'] == place('Белград')
    assert 'Белград' in answer['question'] and 'Сербии' in answer['question']
    assert '?' in answer['question'] and 'или' not in answer['question']


@pytest.mark.parametrize('text', ['Белгород в России', 'из Белгорода', 'Белгород?', 'Белгород и ещё бюджет 50000'])
def test_explicit_russian_origin_or_compound_city_text_is_not_an_alias(text):
    state = TripState().model_dump(mode='json')
    assert not resolve_geo_destination(state, pending(state, requested='Сербия'), text)['handled']


@pytest.mark.parametrize('text', ['сохраняем', 'сохряняем', 'сохраним!', 'да сохраняем'])
def test_legacy_keep_atomically_retargets_city_and_route_preserving_all_other_facts(text):
    state = legacy(); before = deepcopy(state)
    answer = resolve_geo_destination(state, pending(state), text)
    assert answer['action'] == 'retain_unsupported_destination' and answer['search'] is False
    assert answer['clear_pending'] and answer['pending_context'] is None
    after, _ = apply_operations(TripState.model_validate(state), [Operation.model_validate(op) for op in answer['operations']])
    after = after.model_dump(mode='json')
    assert after['stays'][0]['city'] == 'Белград' and after['stays'][0]['hotel_id'] is None
    for field in ('origin', 'travelers', 'groups', 'budget', 'preferences', 'constraints', 'search_policy'):
        assert after[field] == state[field]
    for field in ('id', 'rooms', 'check_in', 'check_out', 'requested_nights'):
        assert after['stays'][0][field] == state['stays'][0][field]
    assert [(l['origin'], l['destination']) for l in after['legs']] == [('Москва', 'Белград'), ('Белград', 'Москва')]
    assert 'поиск предложений' in answer['notice'] and 'Белгород' not in answer['notice']
    assert not answer['postconditions']['supplier_availability_claimed'] and state == before


def test_ambiguous_yes_is_reframed_once_and_the_next_purpose_bound_yes_retains_city():
    state = legacy()
    first = resolve_geo_destination(state, pending(state), 'да')
    assert first['action'] == 'ask_destination' and first['operations'] == []
    assert 'Сохранить' in first['question'] and 'или' not in first['question']
    second = resolve_geo_destination(state, ask_packet(state, first), 'да')
    assert second['action'] == 'retain_unsupported_destination'
    assert second['operations'][0]['value']['city'] == 'Белград'


def test_single_purpose_country_city_acceptance_can_retarget_unsupported_city_directly():
    state = legacy()
    p = pending(state, requested='Белгород', proposal='Белград')
    answer = resolve_geo_destination(state, p, 'да')
    assert answer['action'] == 'retain_unsupported_destination'
    assert answer['operations'][0]['target_id'] == 'owned-stay'


def test_refusal_of_explicit_keep_question_asks_a_new_city_without_restoring_russia():
    state = legacy()
    first = resolve_geo_destination(state, pending(state), 'да')
    answer = resolve_geo_destination(state, ask_packet(state, first), 'нет')
    assert answer['operations'] == [] and answer['pending_context']['requested_destination']['country_code'] == 'RS'
    assert 'Какой город' in answer['question']


def test_old_belgorod_room_answer_does_not_mutate_the_wrong_city_while_metadata_disagrees():
    state = legacy()
    answer = resolve_geo_destination(state, pending(state, field='stay.rooms'), '1')
    assert answer['action'] == 'ask_destination' and answer['operations'] == []
    assert answer['pending_context']['requested_destination']['name'] == 'Белград'


@pytest.mark.parametrize('tamper', ['stale', 'foreign_trip', 'foreign_owner'])
def test_owned_question_packet_cannot_be_reused_for_another_state_or_trip(tamper):
    state = legacy(); first = resolve_geo_destination(state, pending(state), 'да')
    p = ask_packet(state, first)
    if tamper == 'stale':
        p['state_version'] = 0
    elif tamper == 'foreign_trip':
        p['context']['geo_destination']['trip_id'] = 'foreign'
    else:
        p['context']['geo_destination']['owner'] = 'model-invented'
    assert not resolve_geo_destination(state, p, 'да')['handled']


def test_multiple_stays_without_owned_target_never_choose_a_stop_implicitly():
    state = legacy()
    state['stays'].append({**deepcopy(state['stays'][0]), 'id': 'second'})
    p = pending(state); p.pop('target_id')
    answer = resolve_geo_destination(state, p, 'сохраняем')
    assert answer['operations'] == [] and 'Какую остановку' in answer['question']


@pytest.mark.parametrize('text', ['сохраняем, бюджет 70000', 'сохряняем и добавь ребёнка',
    'сохраняем?', 'если сохраняем', 'не сохраняем и летим в Москву'])
def test_compound_edits_and_questions_remain_for_the_normal_interpreter(text):
    state = legacy()
    assert not resolve_geo_destination(state, pending(state), text)['handled']


def test_known_search_city_stays_on_ordinary_flow():
    state = legacy()
    assert not resolve_geo_destination(state, pending(state, requested='Стамбул'), 'сохраняем')['handled']


@pytest.mark.parametrize('reply', ['да', 'нет'])
def test_an_active_known_city_proposal_is_not_reinterpreted_as_old_unsupported_city_retention(reply):
    state = legacy()
    p = pending(state, requested='Белград', proposal='Санкт-Петербург')
    assert not resolve_geo_destination(state, p, reply)['handled']


def test_compound_belgorod_in_serbian_context_preserves_independent_operations_and_proposes_belgrade():
    state = TripState().model_dump(mode='json')
    p = pending(state, requested='Сербия')
    operations = [{'type': 'set_origin', 'value': {'city': 'Москва'}},
        {'type': 'add_traveler', 'value': {'id': 'adult', 'type': 'adult'}},
        {'type': 'upsert_stay', 'target_id': 'new-stay-1', 'value': {'city': 'Белгород', 'check_in': '2026-10-28', 'requested_nights': 7}}]
    grounded, meta = ground_intent_operations(state, operations,
        'Из Москвы, один взрослый и хочу в Белгород в отель на неделю с 28 октября.', p)
    assert grounded[:2] == operations[:2]
    assert all(op.get('value', {}).get('city') != 'Белгород' for op in grounded if op['type'] == 'upsert_stay')
    assert meta['pending_context']['proposed_destination'] == place('Белград')
    assert meta['pending_context']['requested_destination']['country_code'] == 'RS'
    assert len(meta['unknowns']) == 1 and 'Выбираем Белград' in meta['unknowns'][0]
