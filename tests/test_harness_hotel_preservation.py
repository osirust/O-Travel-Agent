"""Fresh Store selections pin explicit hotel wishes, never first shown defaults."""
from copy import deepcopy
from uuid import uuid4

import pytest
from test_api import StubLLM, _headers, _session, _wait
from test_api import api_factory as preserve_factory
from test_hotel_preservation import SOURCE
from test_slot_continuation import _message, _reply

api_factory = preserve_factory


async def setup_selection(client, position):
    initial = deepcopy(SOURCE['before_state'])
    if position == 3:
        # Separate selection-identity fixture: the actual breakfast constraint
        # admits only two plans. No relabelling of plan2 as plan3 is permitted.
        initial['constraints'] = []
    sid, token, created = await _session(client, initial)
    route = f'/api/v1/sessions/{sid}'
    accepted = await client.post(route + '/search', headers=_headers(token), json={})
    searched = await _wait(client, accepted.json()['run_id'], token)
    assert searched['status'] == 'completed'
    plans = searched['result']['result']['plans']
    assert len(plans) >= position
    preview = await client.post(route + '/checkout/preview', headers=_headers(token), json={
        'plan_id': plans[position - 1]['plan_id'], 'state_version': created['state_version']})
    assert preview.status_code == 200
    store = client._transport.app.state.store
    selection = await store.current_selected_plan(sid, created['state_version'])
    assert selection and selection['trip_id'] == created['trip_id']
    wanted = next(hotel['hotel_id'] for hotel in selection['hotels'] if hotel['city'] == 'Стамбул')
    return sid, token, created, plans, preview.json(), wanted


@pytest.mark.asyncio
@pytest.mark.parametrize('selected_position', [1, 3])
async def test_explicit_preserve_pins_first_stay_from_actual_selected_preview_including_third_plan(api_factory, selected_position):
    model = StubLLM([{'operations': [], 'control': {'action': 'continue'}}])
    async with api_factory(model) as (client, _):
        sid, token, created, plans, quote, wanted = await setup_selection(client, selected_position)
        if selected_position == 3:
            first_shown = next(offer['hotel_id'] for offer in plans[0]['offers'] if offer.get('city') == 'Стамбул' and offer.get('kind') == 'hotel')
            assert wanted != first_shown
        run, after = await _message(client, sid, token, 'Первый отель в Стамбуле сохрани.')
        assert run['status'] == 'completed' and not run.get('error')
        assert after['trip']['stays'][0]['hotel_id'] == wanted
        assert after['trip']['stays'][1] == created['trip']['stays'][1]
        assert after['trip']['constraints'] == created['trip']['constraints']
        assert after['trip']['budget'] == created['trip']['budget']
        assert after['trip']['travelers'] == created['trip']['travelers']
        assert after['trip']['state_version'] == created['state_version'] + 1
        assert after['status'] == 'OPTIONS_READY' and after['pending_question'] is None
        store = client._transport.app.state.store
        old = await client.post(f'/api/v1/sessions/{sid}/checkout/confirm', headers=_headers(token), json={
            'quote_id': quote['quote_id'], 'quote_version': quote['quote_version'], 'state_version': quote['state_version'],
            'confirmed': True, 'idempotency_key': str(uuid4())})
        assert old.status_code == 409 and await store.list_orders(sid) == []
        notes = run['result']['result']['operation_normalizations']
        assert any(note['code'] == 'explicit_selected_hotel_preserved' and note['hotel_id'] == wanted for note in notes)
        assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_exact_actual_omitted_pin_wow_batch_keeps_the_selected_first_hotel_and_all_other_constraints(api_factory):
    model = StubLLM([deepcopy(SOURCE['raw_validated'][0]['value'])])
    async with api_factory(model) as (client, _):
        sid, token, created, _, quote, wanted = await setup_selection(client, 1)
        run, after = await _message(client, sid, token, SOURCE['text'])
        assert run['status'] == 'completed' and not run.get('error')
        trip = after['trip']
        assert trip['stays'][0]['hotel_id'] == wanted
        assert trip['stays'][0]['check_in'] == created['trip']['stays'][0]['check_in']
        assert trip['stays'][0]['check_out'] == created['trip']['stays'][0]['check_out']
        assert trip['budget']['amount_minor'] == 38_000_000
        assert sorted(person['age'] for person in trip['travelers'] if person['type'] == 'child') == [7, 8]
        assert trip['constraints'] == created['trip']['constraints']
        assert trip['groups'][0] == created['trip']['groups'][0] and trip['groups'][1]['keep_separate'] is True
        for before, stay in zip(created['trip']['stays'], trip['stays'], strict=True):
            assert stay['rooms'][0] == before['rooms'][0]
            assert stay['rooms'][1]['id'] == before['rooms'][1]['id']
            assert stay['rooms'][1]['traveler_ids'] == ['new-traveler-2', 'new-traveler-4']
        assert trip['stays'][1]['hotel_id'] == 'demo-hotel-antalya-family'
        assert trip['stays'][1]['check_out'] == trip['legs'][-1]['departure_date'] == '2026-11-19'
        assert after['status'] == 'OPTIONS_READY' and after['pending_question'] is None
        assert len(run['result']['result']['plans']) >= 1 and len(model.calls) == 1
        old = await client.post(f'/api/v1/sessions/{sid}/checkout/confirm', headers=_headers(token), json={
            'quote_id': quote['quote_id'], 'quote_version': quote['quote_version'], 'state_version': quote['state_version'],
            'confirmed': True, 'idempotency_key': str(uuid4())})
        assert old.status_code == 409
        assert await client._transport.app.state.store.list_orders(sid) == []


@pytest.mark.asyncio
async def test_no_preview_and_no_pin_asks_for_selection_instead_of_first_shown_default(api_factory):
    model = StubLLM([{'operations': [], 'control': {'action': 'continue'}}])
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, SOURCE['before_state'])
        run, after = await _message(client, sid, token, 'Первый отель сохрани.')
        assert run['status'] == 'completed' and after['trip'] == created['trip']
        assert after['waiting_reason'] == 'clarification'
        assert 'выберите' in (await _reply(client, sid, token, run)).casefold()
        assert after['trip']['stays'][0]['hotel_id'] is None


@pytest.mark.asyncio
async def test_explicit_keep_authority_does_not_depend_on_the_model_misclassifying_it_as_a_question(api_factory):
    model = StubLLM([{'operations': [], 'control': {'action': 'none'}, 'speech_act': 'ASK'}])
    async with api_factory(model) as (client, _):
        sid, token, _, _, _, wanted = await setup_selection(client, 1)
        run, after = await _message(client, sid, token, 'Первый отель сохрани.')
        assert run['status'] == 'completed' and not run.get('error')
        assert after['trip']['stays'][0]['hotel_id'] == wanted
        assert after['status'] == 'OPTIONS_READY' and after['pending_question'] is None
        assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_already_persisted_hotel_identity_survives_keep_without_a_fresh_preview(api_factory):
    model = StubLLM([{'operations': [], 'control': {'action': 'continue'}}])
    state = deepcopy(SOURCE['before_state'])
    state['stays'][0]['hotel_id'] = 'demo-hotel-istanbul-garden'
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, state)
        run, after = await _message(client, sid, token, 'Первый отель сохрани.')
        assert run['status'] == 'completed' and after['trip'] == created['trip']
        assert after['pending_question'] is None and len(model.calls) == 1
