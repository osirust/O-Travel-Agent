"""Captured country compound regression; controlled model, isolated PG only.

The saved actual failure remains immutable. Cached success is not actual NLU.
"""
import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_api import StubLLM, _headers, _session, _snapshot, _wait
from test_api import api_factory as country_api_factory

from otravel.country_intake import normalize_country_intake_clarification

api_factory = country_api_factory
FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures/regression/country-compound-intake-groq-20261027.json'


def captured():
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


def normalize(fixture=None, *, text=None, operations=None, conflicts=None, state=None, speech_act='CHANGE'):
    fixture = fixture or captured()
    raw = fixture['raw_validated']
    return normalize_country_intake_clarification(
        fixture['before_state'] if state is None else state,
        raw['operations'] if operations is None else operations,
        fixture['text'] if text is None else text,
        raw['conflicts'] if conflicts is None else conflicts, speech_act=speech_act)


def test_exact_raw_is_preserved_and_country_as_city_is_only_a_missing_slot():
    fixture = captured()
    original = deepcopy(fixture)
    operations, meta = normalize(fixture)
    assert fixture == original
    assert operations == fixture['raw_validated']['operations']
    assert meta['handled'] and meta['conflicts'] == []
    assert meta['authorizes_retain_independent_ops']
    assert meta['pending_context']['field'] == 'destination.city'
    assert meta['destination_request']['country_code'] == 'RS'
    assert meta['notes'][0]['reclassified_model_conflict_count'] == 1
    assert meta['unknowns'] == meta['server_questions']
    assert 'Сербия' in meta['unknowns'][0] and 'город' in meta['unknowns'][0].casefold()
    assert 'destination_city' not in meta['unknowns'][0]


@pytest.mark.parametrize(('destination', 'code'), [('Турцию', 'TR'), ('Россию', 'RU'), ('ОАЭ', 'AE'), ('Грузию', 'GE')])
def test_country_intake_is_not_a_serbia_special_case(destination, code):
    fixture = captured()
    operations, meta = normalize(fixture, text=fixture['text'].replace('Сербию', destination))
    assert meta['handled'] and meta['destination_request']['country_code'] == code
    assert operations == fixture['raw_validated']['operations']


def test_missing_city_without_model_conflict_has_the_same_literal_guard():
    operations, meta = normalize(conflicts=[])
    assert meta['handled'] and len(operations) == 3


def test_pure_country_can_only_save_the_country_question_without_inferred_facts():
    operations, meta = normalize(text='Хочу в Сербию.', operations=[])
    assert meta['handled'] and operations == []
    assert meta['destination_request']['country_code'] == 'RS'
    operations, meta = normalize(text='Хочу в Сербию.')
    assert not meta['handled']  # Unanchored Moscow/budget/adult may not be retained.


@pytest.mark.parametrize('text', [
    'Не хочу в Сербию. Отправление из Москвы. Один взрослый, бюджет 80000 рублей.',
    'Если поехать в Сербию. Отправление из Москвы. Один взрослый, бюджет 80000 рублей.',
    'Раньше хотел в Сербию. Отправление из Москвы. Один взрослый, бюджет 80000 рублей.',
    '«Хочу в Сербию». Отправление из Москвы. Один взрослый, бюджет 80000 рублей.',
    'Хочу в Сербию или Турцию. Отправление из Москвы. Один взрослый, бюджет 80000 рублей.',
    'Хочу в Сербию. Стамбул или Анталья. Отправление из Москвы. Один взрослый, бюджет 80000 рублей.',
    'Хочу в Сербию. Белград. Отправление из Москвы. Один взрослый, бюджет 80000 рублей.',
    'Хочу в Сербию. Отправление из Москвы. Один или два взрослых, бюджет 80000 рублей.',
    'Хочу в Сербию. Отправление из Москвы. Один взрослый. Два взрослых. Бюджет 80000 рублей.',
    'Хочу в Сербию. Отправление из Москвы. Один взрослый, бюджет 80000 или 90000 рублей.',
    'Хочу в Сербию. Отправление из Москвы. Один взрослый, бюджет 80000 рублей и 90000 рублей.',
    'Хочу в Сербию. Отправление из Москвы. Один взрослый, бюджет 80000 рублей. Бюджет 90000 рублей.',
])
def test_rival_negated_quoted_historical_and_contradictory_facts_keep_batch_blocked(text):
    fixture = captured()
    ops, meta = normalize(fixture, text=text)
    assert not meta['handled'] and meta['conflicts'] == fixture['raw_validated']['conflicts']
    assert ops == fixture['raw_validated']['operations']


@pytest.mark.parametrize('conflict', [
    'Бюджет указан 80000 или 90000.',
    'Сербия — страна, не город. Бюджеты противоречат друг другу.',
    'country not a city; one adult versus two adults',
    'country not a city; dates are incompatible',
    'Сербия и Турция — разные страны, надо выбрать одну.',
    'Состав группы указан противоречиво.',
    'Уточните что-нибудь.',
])
def test_country_clause_does_not_clear_other_model_conflicts(conflict):
    _, meta = normalize(conflicts=captured()['raw_validated']['conflicts'] + [conflict])
    assert not meta['handled']


@pytest.mark.parametrize('mutation', ['budget', 'adults', 'origin', 'child', 'membership', 'inferred_age', 'stay'])
def test_unanchored_model_operations_do_not_gain_authority_from_country(mutation):
    ops = deepcopy(captured()['raw_validated']['operations'])
    if mutation == 'budget':
        ops[1]['value']['amount_minor'] = 9_000_000
    elif mutation == 'adults':
        ops.append({'type': 'add_traveler', 'value': {'id': 'other', 'type': 'adult'}})
    elif mutation == 'origin':
        ops[0]['value']['city'] = 'Казань'
    elif mutation == 'child':
        ops[2]['value'].update(type='child', age=7)
    elif mutation == 'membership':
        ops[2]['value']['group_id'] = 'invented-family'
    elif mutation == 'inferred_age':
        ops[2]['value']['age'] = 35
    else:
        ops.append({'type': 'upsert_stay', 'target_id': 'guess', 'value': {'city': 'Белград'}})
    returned, meta = normalize(operations=ops)
    assert returned == ops and not meta['handled']


@pytest.mark.parametrize('field', ['origin', 'budget', 'travelers', 'stays', 'legs', 'groups', 'preferences', 'constraints'])
def test_existing_trip_is_outside_fresh_intake_scope(field):
    state = deepcopy(captured()['before_state'])
    state[field] = 'already-saved' if field == 'origin' else {'amount_minor': 1} if field == 'budget' else [{'id': 'saved'}]
    assert not normalize(state=state)[1]['handled']


@pytest.mark.parametrize('speech_act', ['ASK', 'PROPOSE'])
def test_questions_and_hypothetical_proposals_do_not_become_changes(speech_act):
    assert not normalize(speech_act=speech_act)[1]['handled']


async def post(client, sid, token, text, message_id):
    before = await _snapshot(client, sid, token)
    response = await client.post(f'/api/v1/sessions/{sid}/messages', headers=_headers(token),
        json={'client_message_id': message_id, 'expected_version': before['state_version'], 'text': text})
    assert response.status_code == 202
    run = await _wait(client, response.json()['run_id'], token)
    assert run['status'] == 'completed', run.get('error')
    return run, await _snapshot(client, sid, token)


@pytest.mark.asyncio
async def test_exact_captured_country_compound_retains_independent_facts_and_owned_city_question(api_factory):
    fixture = captured()
    model = StubLLM([fixture['raw_validated']])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client)
        run, after = await post(client, sid, token, fixture['text'], 'country-compound-captured')
        assert run['status'] == 'completed'
        assert after['trip']['origin'] == 'Москва'
        assert after['trip']['budget']['amount_minor'] == 8_000_000
        assert [(p['type'], p.get('age')) for p in after['trip']['travelers']] == [('adult', None)]
        assert after['trip']['stays'] == [] and after['trip']['legs'] == []
        assert after['pending_question']['field'] == 'destination.city'
        assert after['pending_question']['context']['requested_destination']['country_code'] == 'RS'
        assert 'город' in after['pending_question']['text'].casefold()
        assert 'Серби' in after['pending_question']['text']
        assert 'Что нужно уточнить о поездке' not in after['pending_question']['text']
        assert len(model.calls) == 1
        store = client._transport.app.state.store
        assert not any(e['event_type'] == 'TOOL_CALL_STARTED' for e in await store.list_events(sid))
        assert await store.list_orders(sid) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('contradiction', ['budget', 'adults', 'city_alternative'])
async def test_country_missing_city_does_not_relax_genuine_atomic_conflict(contradiction, api_factory):
    fixture = captured()
    raw, text = deepcopy(fixture['raw_validated']), fixture['text']
    if contradiction == 'budget':
        text += ' Общий бюджет 90000 рублей.'
        raw['operations'].append({'type': 'set_budget', 'value': {'amount_minor': 9_000_000, 'currency': 'RUB'}})
    elif contradiction == 'adults':
        text += ' Или два взрослых.'
        raw['operations'].append({'type': 'add_traveler', 'value': {'id': 'second', 'type': 'adult'}})
    else:
        text += ' Выбираю Стамбул или Анталью, пока не решил.'
    model = StubLLM([raw])
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client)
        run, after = await post(client, sid, token, text, 'country-true-conflict')
        assert run['status'] == 'completed'
        assert after['trip'] == before['trip'] and after['waiting_reason'] == 'clarification'
        assert len(model.calls) == 1
        assert await client._transport.app.state.store.list_orders(sid) == []


@pytest.mark.asyncio
async def test_ten_turn_country_intake_typos_refusal_and_independent_edits_preserve_owned_facts(api_factory):
    fixture = captured()
    model = StubLLM([fixture['raw_validated'], {
        'operations': [], 'control': {'action': 'none'}, 'speech_act': 'ASK',
        'reply': 'geography', 'information_subject': None,
    }, {
        'operations': [
            {'type': 'add_traveler', 'value': {'id': 'child7', 'type': 'child', 'age': 7}},
            {'type': 'set_budget', 'value': {'amount_minor': 7_000_000, 'currency': 'RUB'}},
        ], 'reply': 'Добавлен ребёнок, общий бюджет 70000 рублей.', 'speech_act': 'CHANGE',
    }])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client)
        run, first = await post(client, sid, token, fixture['text'], 'country-series-01')
        assert run['status'] == 'completed'
        assert first['trip']['origin'] == 'Москва' and first['trip']['budget']['amount_minor'] == 8_000_000
        assert first['pending_question']['context']['requested_destination']['country_code'] == 'RS'
        _, second = await post(client, sid, token, 'Где находится Сербия? Ничего не меняй.', 'country-series-02')
        assert second['trip'] == first['trip']
        assert len(model.calls) == 2  # Country-only free QA uses a read-only model contract.
        _, third = await post(client, sid, token, 'Белгород', 'country-series-03')
        assert third['trip'] == first['trip']
        assert third['pending_question']['context']['requested_destination']['name'] == 'Белград'
        _, fourth = await post(client, sid, token, 'Стамбул в Сербии', 'country-series-04')
        assert fourth['trip'] == first['trip']
        assert fourth['pending_question']['context']['proposed_destination']['country_code'] == 'TR'
        _, fifth = await post(client, sid, token,
            'Нет, добавь одного ребёнка семи лет и установи общий бюджет 70000 рублей.', 'country-series-05')
        assert fifth['trip']['origin'] == 'Москва' and fifth['trip']['budget']['amount_minor'] == 7_000_000
        assert [(p['type'], p.get('age')) for p in fifth['trip']['travelers']] == [('adult', None), ('child', 7)]
        assert fifth['pending_question']['context']['requested_destination']['name'] == 'Белград'
        assert 'proposed_destination' not in fifth['pending_question']['context']
        _, sixth = await post(client, sid, token, 'да', 'country-series-06')
        assert sixth['trip'] == fifth['trip'] and sixth['trip']['stays'] == []
        _, seventh = await post(client, sid, token, 'Анталья', 'country-series-07')
        assert seventh['trip'] == fifth['trip']
        assert seventh['pending_question']['context']['proposed_destination']['country_code'] == 'TR'
        _, eighth = await post(client, sid, token, 'да', 'country-series-08')
        assert len(eighth['trip']['stays']) == 1 and eighth['trip']['stays'][0]['city'] == 'Анталья'
        _, ninth = await post(client, sid, token, '14 ноября 2026', 'country-series-09')
        assert ninth['trip']['stays'][0]['check_in'] == '2026-11-14'
        _, tenth = await post(client, sid, token, '19 ноября 2026', 'country-series-10')
        assert tenth['trip']['stays'][0]['check_out'] == '2026-11-19'
        assert tenth['trip']['origin'] == 'Москва' and tenth['trip']['budget']['amount_minor'] == 7_000_000
        assert [(p['type'], p.get('age')) for p in tenth['trip']['travelers']] == [('adult', None), ('child', 7)]
        for snap in (first, second, third, fourth, fifth, sixth, seventh, eighth, ninth, tenth):
            if snap.get('pending_question'):
                assert 'Что нужно уточнить о поездке' not in snap['pending_question']['text']
                assert snap['pending_question']['field']
        assert len(model.calls) == 3
        store = client._transport.app.state.store
        assert not any(e['event_type'] == 'TOOL_CALL_STARTED' for e in await store.list_events(sid))
        assert await store.list_orders(sid) == []
