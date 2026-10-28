"""Cached final model JSON through API+PG, with external model/travel doubles."""
import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_api import StubLLM, _session
from test_api import api_factory as semantic_api_factory
from test_slot_continuation import _message, _reply

api_factory = semantic_api_factory
FIXTURE = json.loads((Path(__file__).parents[1]
    / 'fixtures/regression/route-end035-unknown-party025-20261028.json').read_text(encoding='utf-8'))


def actual(case_id):
    return deepcopy(next(row for row in FIXTURE['records'] if row['id'] == case_id))


@pytest.mark.asyncio
async def test_exact035_endpoint_does_not_replace_selected_lodging_through_api(api_factory):
    row = actual('ZAI100-035')
    model = StubLLM([row['events'][0]['final_json']])
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client, row['before']['trip'])
        run, after = await _message(client, sid, token, row['input'])
        assert run['status'] == 'completed' and len(model.calls) == 1
        assert after['trip']['route_policy']['kind'] == 'open_jaw'
        assert after['trip']['route_policy']['end_city'] == 'Москва'
        assert after['trip']['stays'] == before['trip']['stays']
        assert after['trip']['stays'][0]['city'] == 'Дубай'
        assert after['trip']['origin'] == before['trip']['origin'] == 'Санкт-Петербург'
        assert after['trip']['travelers'] == before['trip']['travelers']
        assert after['trip']['budget'] == before['trip']['budget']
        store = client._transport.app.state.store
        assert await store.list_orders(sid) == []
        checked = [event['payload'] for event in await store.list_events(sid)
                   if event['event_type'] == 'MODEL_OPERATIONS_CHECKED']
        assert not any(note['code'] == 'destination_grounded_from_current_literal'
                       for payload in checked for note in payload.get('normalizations', []))


@pytest.mark.asyncio
async def test_exact025_unknown_categories_ask_and_keep_independent_initial_facts(api_factory):
    row = actual('ZAI100-025')
    model = StubLLM([row['events'][0]['final_json']])
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client, row['before']['trip'])
        run, after = await _message(client, sid, token, row['input'])
        assert run['status'] == 'completed' and len(model.calls) == 1
        assert before['trip']['travelers'] == after['trip']['travelers'] == []
        assert after['trip']['origin'] == 'Санкт-Петербург'
        assert after['trip']['stays'][0]['city'] == 'Сочи'
        question = after['pending_question']
        assert question['field'] == 'party.adults'
        assert question['context']['declared_total'] == 4
        reply = await _reply(client, sid, token, run)
        assert 'взрослых' in reply and 'детей' in reply
        assert '4 взрослых' not in reply and 'четверо взрослых' not in reply
        assert run['result']['result']['model_reply'] is None
        assert run['result']['result']['model_reply_suppressed']
        store = client._transport.app.state.store
        assert await store.list_orders(sid) == []
        events = await store.list_events(sid)
        raw_audit = [event['payload']['structured_contract']['value'] for event in events
                     if event['event_type'] == 'MODEL_EXTRACTION_VALIDATED']
        assert raw_audit[0]['reply'] == row['events'][0]['final_json']['reply']
        assert 'четверо взрослых' in raw_audit[0]['reply']
        assert not any(event['event_type'] == 'TOOL_CALL_STARTED' for event in events)
        assert any(note.get('code') == 'unknown_initial_party_categories_preserved'
            for event in events if event['event_type'] == 'MODEL_OPERATIONS_CHECKED'
            for note in event['payload'].get('normalizations', []))
