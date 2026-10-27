"""Cached final-roster causal regression with controlled external adapters.

The fixture is actual a36 before/raw. An API seed recreates that canonical trip;
an owned finish_run recreates the real date question with fresh test version.
No application source patch, real model, provider HTTP, old row cleanup or reset.
"""
import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from test_api import StubLLM, _session
from test_api import api_factory as shadow_api_factory
from test_slot_continuation import _message, _reply

api_factory = shadow_api_factory


@pytest.mark.asyncio
async def test_exact025_final_roster_through_whole_harness_not_only_remove_filter(api_factory):
    fixture = json.loads((Path(__file__).parents[1] / 'fixtures/regression/traveler-final-census025-20261028.json').read_text(encoding='utf-8'))
    model = StubLLM([deepcopy(fixture['validated_raw_contract']['value'])])
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, deepcopy(fixture['before_trip']))
        store = client._transport.app.state.store
        own = await store.get_session(sid)
        pending = deepcopy(fixture['before_pending'])
        pending['question_id'] = 'shadow025-' + str(uuid4())
        pending['state_version'] = own['state_version']
        seed, _ = await store.intake_message(sid, str(uuid4()), 'Controlled bootstrap of the actual025 pending question', own['state_version'])
        assert await store.claim_run(seed['run_id'])
        await store.finish_run(seed['run_id'], own['state_version'], None, 'WAITING_USER', pending['text'],
            pending_question=pending, waiting_reason=fixture['before_waiting_reason'])
        run, after = await _message(client, sid, token, fixture['input'])
        reply = await _reply(client, sid, token, run)
        events = await store.list_events(sid)
        checked = [event['payload'] for event in events if event['event_type'] == 'MODEL_OPERATIONS_CHECKED']
        people = after['trip']['travelers']
        actual_adults = [person['id'] for person in people if person['type'] == 'adult']
        actual_child_ages = sorted(person['age'] for person in people if person['type'] == 'child')
        diagnostic = {'status': run['status'], 'model_adapter_calls': len(model.calls),
            'adult_ids': actual_adults, 'child_ages': actual_child_ages,
            'before_trip': created['trip'], 'after_trip': after['trip'],
            'pending_question': after.get('pending_question'), 'reply': reply,
            'checked_contracts': checked, 'source_fixture_sha256': fixture['source_sha256'],
            'real_model_or_provider_http': 0}
        evidence = Path(__file__).parents[1] / '.local/peer_reviews/budget-intake-20261028/shadow025-wholeharness-green-diagnostic.json'
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding='utf-8')
        assert run['status'] == 'completed'
        assert len(model.calls) == 1
        assert len(actual_adults) == 2 and actual_child_ages == [4, 12], (
            'Whole harness final census mismatch: ' + str((len(actual_adults), actual_child_ages)))
        assert actual_adults == ['new-traveler-1', 'new-traveler-2']
        assert any(note.get('code') == 'complete_current_roster_removal_authorized'
            for contract in checked for note in contract.get('normalizations', []))
        assert not any(event['event_type'] == 'TOOL_CALL_STARTED' for event in events)
        assert await store.list_orders(sid) == []
        for field in ('origin', 'stays', 'legs', 'budget', 'constraints', 'preferences', 'route_policy', 'search_policy'):
            assert after['trip'][field] == created['trip'][field], field


@pytest.mark.asyncio
async def test_final_roster_does_not_pick_which_distinguishable_adults_to_remove(api_factory):
    fixture = json.loads((Path(__file__).parents[1] / 'fixtures/regression/traveler-final-census025-20261028.json')
        .read_text(encoding='utf-8'))
    before = deepcopy(fixture['before_trip'])
    before['travelers'][2]['age'] = 45
    model = StubLLM([deepcopy(fixture['validated_raw_contract']['value'])])
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, before)
        run, after = await _message(client, sid, token, fixture['input'])
        assert run['status'] == 'completed' and len(model.calls) == 1
        assert after['trip'] == created['trip']
        assert after['state_version'] == created['state_version']
        reply = await _reply(client, sid, token, run)
        assert after['pending_question']['field'] == 'party.removal'
        assert any(term in reply.casefold() for term in ('кого', 'каких', 'участник'))
        assert 'что нужно уточнить о поездке' not in reply.casefold()
        store = client._transport.app.state.store
        assert await store.list_orders(sid) == []
        assert not any(event['event_type'] == 'TOOL_CALL_STARTED' for event in await store.list_events(sid))
