"""Actual R4 NULL-date contract through current API/Harness/real test PG.

The pause row is an explicitly labelled controlled variation. Source reads
return controlled errors; no live API availability or new inference is claimed.
"""
import json
import os
from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url
from test_api import StubLLM, _session
from test_dynamic_place_conversation import world_app
from test_slot_continuation import _message, _reply

from otravel.providers import ProviderError

FIXTURE = Path(__file__).parents[1] / 'fixtures/regression/world-r4-date-omission-20261027.json'


@pytest.mark.asyncio
@pytest.mark.parametrize('explicit_pause', [False, True])
async def test_cached_R4_dates_are_saved_without_repeating_the_wish_or_overriding_selected_city(monkeypatch, explicit_pause, _hard_duration=False):
    url = os.environ.get('TEST_DATABASE_URL')
    if not url:
        pytest.skip('Only isolated TEST_DATABASE_URL is used')
    if make_url(url).database != 'otravel_test':
        pytest.fail('Only otravel_test is authorized', pytrace=False)
    fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
    original = fixture['raw']
    raw = deepcopy(original)
    text = fixture['input']
    if _hard_duration:
        text += ' На7ночей.'  # Controlled additional requirement; original source input stays frozen.
    if explicit_pause:
        raw['control'] = {'action': 'wait'}
        text += ' Подожди, я ещё дополню.'
    assert raw['operations'][3]['value']['check_in'] is raw['operations'][3]['value']['check_out'] is None
    model = StubLLM([raw])
    async with world_app(monkeypatch, model) as (client, app, wires, travel):
        source_attempts = []
        for name in ('resolve_hotels', 'resolve_air_place', 'search_hotels', 'search_flights'):
            async def unavailable(*args, _name=name, **kwargs):
                source_attempts.append(_name)
                raise ProviderError('CONFIG_MISSING', 'Controlled source unavailable; no HTTP.', provider='controlled')
            monkeypatch.setattr(travel, name, unavailable, raising=False)
        sid, token, _ = await _session(client)
        await _message(client, sid, token, 'хочу в аргентину наверное')
        await _message(client, sid, token, 'Буэнос-Айрес')
        _, before = await _message(client, sid, token, 'первый')
        assert before['state_version'] == 1
        assert before['pending_question']['field'] == 'stay.check_in'
        assert before['pending_question']['target_id'] == 'new-stay-1'
        assert before['trip']['stays'][0]['check_in'] is before['trip']['stays'][0]['check_out'] is None
        old_wires = len(wires)
        run, after = await _message(client, sid, token, text)
        assert run['status'] == 'completed' and not run.get('error')
        state = after['trip']
        stay, = state['stays']
        assert stay['id'] == 'new-stay-1' and stay['city'] == 'Buenos Aires'
        assert stay['place_id'] == 'geo:openmeteo:3435910' and stay['hotel_id'] is None
        assert state['location_refs'] == before['trip']['location_refs']
        assert (stay['check_in'], stay['check_out']) == ('2026-11-10', '2026-11-17')
        assert (date.fromisoformat(stay['check_out']) - date.fromisoformat(stay['check_in'])).days == 7
        if _hard_duration:
            assert stay['requested_nights'] == 7
        else:
            assert stay.get('requested_nights') is None, 'Explicit dates alone must not invent a hard duration constraint'
        assert stay['rooms'] == [{'id': 'new-room-1', 'traveler_ids': ['new-traveler-1']}]
        assert state['origin'] == 'Москва' and len(state['travelers']) == 1
        assert state['travelers'][0]['type'] == 'adult' and state['travelers'][0]['group_id'] == 'new-group-1'
        assert state['budget'] == {'currency': 'RUB', 'scope': 'whole_trip', 'amount_minor': 30000000}
        assert state['search_policy']['guest_nationality'] == 'RU'
        assert state['preferences'] == [], 'The input did not request model-generated early-flight/price preferences'
        assert [leg['departure_date'] for leg in state['legs']] == ['2026-11-10', '2026-11-17']
        assert (after.get('pending_question') or {}).get('field') not in {'stay.check_in', 'stay.check_out', 'search.guest_nationality'}
        assert len(model.calls) == 1 and len(wires) == old_wires and travel.calls == 1
        reply = await _reply(client, sid, token, run)
        assert 'На какую дату' not in reply and 'Гражданство какой страны' not in reply and 'не распознан' not in reply
        if explicit_pause:
            assert after['waiting_reason'] == 'user_pause'
            assert source_attempts == []
        else:
            assert source_attempts and run['result']['result']['plans'] == []
            assert not run['result']['result']['ready_for_real_booking']
        assert await app.state.store.list_orders(sid) == []
        events = await app.state.store.list_events(sid)
        checked = next(e['payload'] for e in events if e['run_id'] == run['run_id']
            and e['event_type'] == 'MODEL_OPERATIONS_CHECKED')
        assert any(note['code'] == 'explicit_initial_stay_dates_grounded' for note in checked['normalizations'])
        original_trace = next(e['payload']['structured_contract']['value'] for e in events
            if e['run_id'] == run['run_id'] and e['event_type'] == 'MODEL_EXTRACTION_VALIDATED')
        assert original_trace['operations'] == original['operations']
        assert original_trace['operations'][3]['value']['check_in'] is None


@pytest.mark.asyncio
async def test_controlled_R4_with_explicit_seven_nights_retains_the_hard_duration(monkeypatch):
    await test_cached_R4_dates_are_saved_without_repeating_the_wish_or_overriding_selected_city(
        monkeypatch, False, _hard_duration=True)
