"""Exact actual T4 extraction must refresh plans, beyond mutating the state."""
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from test_api import StubLLM, _headers, _session, _wait
from test_api import api_factory as city_scope_factory
from test_existing_city_scope import SOURCE
from test_slot_continuation import _message

api_factory = city_scope_factory


@pytest.mark.asyncio
async def test_cached_actual_t4_is_options_ready_with_plans_and_preserves_every_wow_fact(api_factory):
    model = StubLLM([deepcopy(SOURCE['raw_validated'][0]['value'])])
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, SOURCE['before_trip'])
        route = f'/api/v1/sessions/{sid}'
        accepted = await client.post(route + '/search', headers=_headers(token), json={})
        searched = await _wait(client, accepted.json()['run_id'], token)
        assert searched['status'] == 'completed'
        before_plans = searched['result']['result']['plans']
        assert len(before_plans) >= 2
        selected = next(offer['hotel_id'] for offer in before_plans[1]['offers']
                        if offer.get('kind') == 'hotel' and offer.get('city') == 'Анталья')
        assert selected == SOURCE['captures']['hotel_b_id']
        run, after = await _message(client, sid, token, SOURCE['text'])
        assert run['status'] == 'completed' and not run.get('error')
        assert after['status'] == 'OPTIONS_READY'
        assert after['pending_question'] is None
        plans = run['result']['result']['plans']
        assert len(plans) >= 1 and all(plan['source_type'] == 'DEMO' for plan in plans)
        trip, before = after['trip'], created['trip']
        assert sorted(p['age'] for p in trip['travelers'] if p['type'] == 'child') == [7, 8]
        assert trip['budget']['amount_minor'] == 38_000_000 and trip['origin'] == before['origin']
        assert trip['constraints'] == before['constraints']
        assert trip['groups'][0] == before['groups'][0]
        assert trip['groups'][1]['traveler_ids'] == ['new-traveler-2', 'new-traveler-4']
        for prior, stay in zip(before['stays'], trip['stays'], strict=True):
            assert stay['rooms'][0] == prior['rooms'][0]
            assert [room['id'] for room in stay['rooms']] == [room['id'] for room in prior['rooms']]
            assert stay['rooms'][1]['traveler_ids'] == ['new-traveler-2', 'new-traveler-4']
        assert trip['stays'][0]['hotel_id'] == before['stays'][0]['hotel_id']
        assert trip['stays'][0]['check_out'] == before['stays'][0]['check_out']
        assert trip['stays'][1]['hotel_id'] == selected
        assert trip['stays'][1]['check_out'] == trip['legs'][-1]['departure_date'] == '2026-11-19'
        assert len(model.calls) == 1
        # Preserve a successful controlled API/PG projection for oracle review.
        # It is labelled separately from the original real-provider epoch.
        record = {'format': 'otravel.cached-pg-flow-pass.v1', 'case_id': 'PF-GROUP-04', 'step_id': '04',
            'scope': 'Cached actual T4 extraction injected into the API/isolated PG on current working source; no new remote NLU.',
            'source_projection_sha256': SOURCE['projection_sha256_without_self_digest'],
            'status': after['status'], 'waiting_reason': after.get('waiting_reason'), 'pending_question': after['pending_question'],
            'trip': trip, 'plans': plans, 'selected_hotel_id': selected, 'model_adapter_calls': len(model.calls),
            'actual_model_http': 0, 'actual_supplier_http': 0}
        raw = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        record['projection_sha256_without_self_digest'] = hashlib.sha256(raw).hexdigest()
        folder = Path(__file__).parents[1] / '.local/evaluation/geo-20261027'
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / f'existing-city-cached-flow-pass-{uuid4().hex}.json').open('x', encoding='utf-8') as output:
            json.dump(record, output, ensure_ascii=False, indent=2)
            output.write('\n')
