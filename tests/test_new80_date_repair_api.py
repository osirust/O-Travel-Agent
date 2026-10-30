"""Captured NLU outputs through the real harness/API/PG; zero remote inference."""
import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from test_api import StubLLM, _headers, _session, _snapshot, _wait
from test_api import api_factory as original_api_factory

api_factory = original_api_factory
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
@pytest.mark.parametrize('fixture_name,expected_checkout,price', [
    ('return-checkin-preserve-005t9.json', '2026-11-25', False),
    ('price-date-recall-007t7.json', '2026-11-14', True),
])
async def test_captured_date_failure_is_repaired_through_harness(
        api_factory, fixture_name, expected_checkout, price):
    data = json.loads((ROOT / 'fixtures/regression' / fixture_name).read_text())
    model = StubLLM([deepcopy(data['raw_contract']['value'])])
    initial = deepcopy(data['before_trip'])
    # Remote search is independently qualified; this checks interpretation,
    # persistence and real HTTP wrapper with an explicit DEMO source policy.
    initial['search_policy']['mode'] = 'DEMO'
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, initial)
        before = await _snapshot(client, sid, token)
        accepted = await client.post(f'/api/v1/sessions/{sid}/messages', headers=_headers(token),
            json={'client_message_id': str(uuid4()), 'text': data['input'], 'expected_version': 0})
        assert accepted.status_code == 202
        run = await _wait(client, accepted.json()['run_id'], token)
        assert run['status'] == 'completed', run.get('error')
        after = await _snapshot(client, sid, token)
        assert after['trip']['stays'][-1]['check_out'] == expected_checkout
        assert after['trip']['stays'][-1]['check_in'] == before['trip']['stays'][-1]['check_in']
        assert after['trip']['travelers'] == before['trip']['travelers']
        assert after['trip']['budget'] == before['trip']['budget']
        assert after['state_version'] == before['state_version'] + 1
        if price:
            assert any(p['key'] == 'price_priority' and p['value'] == 'lowest_total'
                       for p in after['trip']['preferences'])
        assert len(model.calls) == 1
        store = client._transport.app.state.store
        assert not await store.list_orders(sid)
