"""Real PG worker with intercepted strict-wire Groq, never external inference."""
import json
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from test_api import _headers, _session, _snapshot, _wait
from test_api import api_factory as strict_factory
from test_slot_continuation import _message

from otravel.domain import sample_trip
from otravel.providers import LLMGateway

api_factory = strict_factory


def model(answer, requests):
    def handler(request):
        body = json.loads(request.content)
        assert body['response_format']['json_schema']['strict'] is True
        assert body['reasoning_effort'] == 'none' and body['model'] == 'qwen/qwen3.8-27b'
        requests.append(body)
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
            'message': {'content': json.dumps(answer, ensure_ascii=False)}}]})
    return LLMGateway({'LLM_PROVIDER': 'groq', 'GROQ_API_KEY': 'synthetic-strict-PG-key',
        'GROQ_FREE_TIER_CONFIRMED': True, 'GROQ_STRUCTURED_TRANSPORT_ENABLED': True,
        'GROQ_REASONING_EFFORT': 'none', 'http_transport': httpx.MockTransport(handler)})


def answer(operation):
    return {'operations': [operation], 'control': {'action': 'continue'},
        'reply': 'Возраст изменён.', 'unknowns': [], 'conflicts': [],
        'speech_act': 'CHANGE', 'information_subject': None}


@pytest.mark.asyncio
async def test_decoded_operation_crosses_real_worker_cas_once_and_retains_wire_provenance(api_factory):
    requests = []
    wire = answer({'type': 'update_traveler', 'target_id': 'child-1', 'value_json': '{"age":4}'})
    async with api_factory(model(wire, requests)) as (client, _):
        app = client._transport.app
        # Explicit application-only ceiling for this controlled test pool;
        # never reset shared counters and never claim a vendor allowance.
        app.state.harness.settings = replace(app.state.harness.settings, max_groq_llm_calls_per_day=1000)
        sid, token, before = await _session(client, sample_trip().model_dump(mode='json'))
        run, after = await _message(client, sid, token, 'Измени возраст нашего ребёнка на четыре года, остальных оставь.')
        assert run['status'] == 'completed' and len(requests) == 1
        assert after['state_version'] == before['state_version'] + 1
        prior = {p['id']: p for p in before['trip']['travelers']}
        current = {p['id']: p for p in after['trip']['travelers']}
        assert current['child-1']['age'] == 4
        assert all(current[i] == p for i, p in prior.items() if i != 'child-1')
        assert after['trip']['budget'] == before['trip']['budget']
        events = await app.state.store.list_events(sid)
        completed = [e for e in events if e['event_type'] == 'MODEL_RESPONSE_RECEIVED']
        assert len(completed) == 1 and completed[0]['payload']['structured_transport']['format'] == 'groq_strict_operations_v1'
        checked = [e for e in events if e['event_type'] == 'MODEL_OPERATIONS_CHECKED']
        assert len(checked) == 1 and checked[0]['payload']['structured_contract']['value']['operations'][0]['value'] == {'age': 4}
        assert await app.state.store.list_orders(sid) == []


@pytest.mark.asyncio
async def test_invalid_null_target_from_old_probe_is_terminal_without_mutation_or_search(api_factory):
    requests = []
    wire = answer({'type': 'upsert_stay', 'target_id': None, 'value_json': '{"city":"Белград"}'})
    async with api_factory(model(wire, requests)) as (client, _):
        app = client._transport.app
        app.state.harness.settings = replace(app.state.harness.settings, max_groq_llm_calls_per_day=1000)
        sid, token, before = await _session(client, sample_trip().model_dump(mode='json'))
        accepted = await client.post(f'/api/v1/sessions/{sid}/messages', headers=_headers(token), json={
            'client_message_id': str(uuid4()), 'expected_version': before['state_version'],
            'text': 'Измени возраст ребёнка на четыре года.'})
        assert accepted.status_code == 202
        run = await _wait(client, accepted.json()['run_id'], token)
        after = await _snapshot(client, sid, token)
        assert run['status'] == 'failed' and len(requests) == 1
        assert run['error']['code'] == 'INVALID_LLM_RESPONSE'
        assert run['error']['details']['validation_stage'] == 'transport_schema'
        assert after['trip'] == before['trip'] and after['state_version'] == before['state_version']
        events = await app.state.store.list_events(sid)
        assert not any(e['event_type'] == 'TOOL_CALL_STARTED' for e in events)
        assert await app.state.store.list_orders(sid) == []
