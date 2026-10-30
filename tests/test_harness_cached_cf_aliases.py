"""Actual CF06/10 payloads replayed through controlled ASGI + isolated PG.

The recorded model output is data. No actual model or supplier HTTP occurs.
"""
import json
from pathlib import Path

import pytest
from test_api import StubLLM, _plans, _session
from test_api import api_factory as cached_api_factory
from test_slot_continuation import _message

api_factory = cached_api_factory
FIXTURE = json.loads((Path(__file__).resolve().parents[1] / 'fixtures/regression/after80-cf-native-6009644.json').read_text())


def cached(identifier):
    return next(c for c in FIXTURE['cases'] if c['id'] == identifier)


@pytest.mark.asyncio
async def test_cached_cf06_nullable_new_group_default_then_membership_is_not_a_conflict(api_factory):
    source = cached('CF10-06')
    model = StubLLM([source['raw']])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, source['before_state'])
        run, after = await _message(client, sid, token, source['user'])
        state = after['trip']
        assert len(model.calls) == 1
        assert state['origin'] == 'Москва' and len(state['travelers']) == 6
        assert sum(p['type'] == 'adult' for p in state['travelers']) == 4
        assert sorted(p['age'] for p in state['travelers'] if p['type'] == 'child') == [6, 10]
        assert len(state['groups']) == 2 and all(g['keep_separate'] and len(g['traveler_ids']) == 3 for g in state['groups'])
        assert [s['city'] for s in state['stays']] == ['Стамбул', 'Анталья'], json.dumps({
            'stays': state['stays'], 'normalizations': run['result']['result'].get('operation_normalizations'),
            'pending': after.get('pending_question')}, ensure_ascii=True)
        assert all(len(s['rooms']) == 2 and not s['check_in'] and not s['check_out'] for s in state['stays'])
        assert state['budget']['amount_minor'] == 50000000
        assert after['pending_question']['field'] in {'stay.check_in', 'stay.check_out'}
        assert after['pending_question']['target_id'] == state['stays'][0]['id']
        assert not _plans(run) and after['waiting_reason'] == 'clarification'
        assert await client._transport.app.state.store.list_orders(sid) == []


@pytest.mark.asyncio
async def test_cached_cf10_duplicate_return_alias_and_budget_apply_exactly_once(api_factory):
    source = cached('CF10-10')
    model = StubLLM([source['raw']])
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client, source['before_state'])
        run, after = await _message(client, sid, token, source['user'])
        state = after['trip']
        assert len(model.calls) == 1 and len(state['travelers']) == 10
        assert state['travelers'][-1]['age'] == 5 and state['travelers'][-1]['group_id'] == 'family-1'
        assert state['budget']['amount_minor'] == 78000000
        assert state['stays'][0] == before['trip']['stays'][0]
        assert state['stays'][-1]['check_out'] == '2026-11-15'
        assert state['legs'][-1]['departure_date'] == '2026-11-15'
        assert state['constraints'] == before['trip']['constraints']
        assert state['travelers'][:-1] == before['trip']['travelers']
        normalizations = run['result']['result'].get('operation_normalizations', [])
        assert any(n.get('code') == 'equivalent_return_checkout_alias_removed' for n in normalizations)
        assert await client._transport.app.state.store.list_orders(sid) == []
