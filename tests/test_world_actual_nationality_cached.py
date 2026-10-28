"""Exact actual compound contract plus explicit nationality omission recovery.

The five original model operations are not edited. Source search is explicitly
unavailable through controlled ProviderError doubles; this is not ready travel
inventory or a new actual inference.
"""
import hashlib
import json
import os
from datetime import date

import pytest
from sqlalchemy.engine import make_url
from test_api import StubLLM, _session
from test_dynamic_place_conversation import world_app
from test_slot_continuation import _message, _reply
from test_world_actual_compound_cached import RAW_ACTUAL, RAW_CONTRACT_CANONICAL_SHA256, TEXT

from otravel.providers import ProviderError


@pytest.mark.asyncio
async def test_exact_cached_world_compound_recovers_explicit_RU_without_repeating_nationality(monkeypatch):
    value = os.environ.get('TEST_DATABASE_URL')
    if not value:
        pytest.skip('Only isolated TEST_DATABASE_URL is used')
    if make_url(value).database != 'otravel_test':
        pytest.fail('Only otravel_test is authorized', pytrace=False)
    assert hashlib.sha256(json.dumps(RAW_ACTUAL, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest() == RAW_CONTRACT_CANONICAL_SHA256
    assert all(not (op['type'] == 'set_search_policy' and 'guest_nationality' in op['value'])
        for op in RAW_ACTUAL['operations'])
    model = StubLLM([RAW_ACTUAL])
    async with world_app(monkeypatch, model) as (client, app, wires, travel):
        source_attempts = []
        for method in ('resolve_hotels', 'resolve_air_place', 'search_hotels', 'search_flights'):
            async def unavailable(*args, _method=method, **kwargs):
                source_attempts.append(_method)
                raise ProviderError('CONFIG_MISSING', 'Controlled unavailable travel source; no HTTP.', provider='controlled')
            monkeypatch.setattr(travel, method, unavailable, raising=False)
        sid, token, _ = await _session(client)
        await _message(client, sid, token, 'хочу в аргентину наверное')
        await _message(client, sid, token, 'Буэнос-Айрес')
        _, before = await _message(client, sid, token, 'первый')
        assert before['trip']['search_policy']['guest_nationality'] is None
        assert before['trip']['stays'][0]['place_id'] == 'geo:openmeteo:3435910'
        before_wires = len(wires)
        run, after = await _message(client, sid, token, TEXT)
        assert run['status'] == 'completed' and not run.get('error')
        state = after['trip']
        assert state['search_policy']['guest_nationality'] == 'RU'
        expected_policy = dict(before['trip']['search_policy'], guest_nationality='RU')
        assert state['search_policy'] == expected_policy
        assert state['origin'] == 'Москва' and state['budget']['amount_minor'] == 30000000
        assert len(state['travelers']) == 1 and state['travelers'][0]['type'] == 'adult'
        assert state['travelers'][0]['group_id'] == 'new-group-1'
        stay, = state['stays']
        assert (stay['city'], stay['place_id']) == ('Buenos Aires', 'geo:openmeteo:3435910')
        assert state['location_refs'] == before['trip']['location_refs']
        assert (stay['check_in'], stay['check_out']) == ('2026-11-10', '2026-11-17')
        assert (date.fromisoformat(stay['check_out']) - date.fromisoformat(stay['check_in'])).days == 7
        assert stay.get('requested_nights') is None, 'The source names dates, not a separate hard duration.'
        assert stay['rooms'] == [{'id': 'new-room-1', 'traveler_ids': ['new-traveler-1']}]
        assert (after.get('pending_question') or {}).get('field') != 'search.guest_nationality'
        reply = await _reply(client, sid, token, run)
        assert 'Гражданство какой страны' not in reply and 'не распознан' not in reply
        assert len(model.calls) == 1 and len(wires) == before_wires
        assert source_attempts and travel.calls == 1
        result = run['result']['result']
        assert result['plans'] == [] and result['component_errors']
        assert all(error['code'] == 'CONFIG_MISSING' for error in result['component_errors'])
        assert not result['partial_plan']['ready_for_simulated_checkout']
        assert not result['ready_for_real_booking'] and await app.state.store.list_orders(sid) == []
        events = await app.state.store.list_events(sid)
        checked = [e['payload'] for e in events if e['run_id'] == run['run_id']
            and e['event_type'] == 'MODEL_OPERATIONS_CHECKED']
        assert len(checked) == 1
        assert any(note['code'] == 'explicit_hotel_guest_nationality_grounded'
            for note in checked[0]['normalizations'])
        assert any(op['type'] == 'set_search_policy' and op['value'] == {'guest_nationality': 'RU'}
            for op in checked[0]['structured_contract']['value']['operations'])
