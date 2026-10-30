"""Lean model guidance never replaces canonical validation or business context."""
import json
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator
from test_groq_transport_provider import ANSWER, completion, gateway

from otravel.answering import READONLY_MODE
from otravel.domain import sample_trip
from otravel.llm_contract import build_operation_schema
from otravel.providers import ProviderError


def v2(answer):
    result = deepcopy(answer)
    result['operations'] = [{'op': op['type'] + ('@' + op['target_id'] if op.get('target_id') is not None else ''),
        'value_json': json.dumps(op['value'], ensure_ascii=False)} for op in answer['operations']]
    return result


@pytest.mark.asyncio
async def test_v2_sends_lean_hints_and_compact_selectors_without_dropping_state_or_prices():
    state = sample_trip().model_dump(mode='json')
    schema = build_operation_schema(state)
    context = {'current_message': 'Поменяй возраст ребёнка на4.', 'trip': state,
        'shown_plans': [{'run_id': 'own', 'plans': [{'plan_id': 'p', 'total_minor': 73102, 'currency': 'EUR'}]}],
        'pending_question': {'field': 'traveler.age', 'target_id': 'child-1'}, '__response_schema': schema}
    before, calls = deepcopy(context), []
    def handler(request):
        calls.append(request)
        body = json.loads(request.content)
        system = body['messages'][0]['content']
        assert 'Operation value hints' in system and 'Required output JSON Schema' not in system
        assert 'Each operation contains op' in system
        incoming = json.loads(body['messages'][1]['content'])
        assert incoming['trip'] == state and incoming['shown_plans'] == context['shown_plans']
        assert incoming['pending_question'] == context['pending_question']
        grammar = body['response_format']['json_schema']['schema']
        assert set(grammar['properties']['operations']['items']['properties']) == {'op', 'value_json'}
        assert Draft202012Validator(grammar).is_valid(v2(ANSWER))
        return completion(v2(ANSWER))
    g = gateway(handler, GROQ_TRANSPORT_VERSION='2')
    try:
        actual = await g.extract_operations(context)
        assert actual == ANSWER and context == before and len(calls) == 1
        meta = actual.call_metadata['structured_transport']
        assert meta['transport_version'] == 2 and meta['format'] == 'groq_strict_operations_v2'
        assert meta['semantic_schema_delivery'] == 'compact_hints_full_server_validation'
        assert meta['wire_request_body_bytes'] == len(calls[0].content)
    finally:
        await g.close()


@pytest.mark.asyncio
async def test_v2_canonical_value_violation_cannot_hide_inside_valid_wire():
    wrong = deepcopy(ANSWER)
    wrong['operations'][0]['value'] = {'age': 4, 'invented_clear_all': True}
    calls = []
    def handler(request):
        calls.append(request)
        return completion(v2(wrong))
    g = gateway(handler, GROQ_TRANSPORT_VERSION=2)
    try:
        with pytest.raises(ProviderError) as failed:
            await g.extract_operations({'__response_schema': build_operation_schema(sample_trip())})
        assert failed.value.code == 'INVALID_LLM_OPERATION' and len(calls) == 1
        assert failed.value.details['validation_stage'] == 'canonical_schema'
    finally:
        await g.close()


@pytest.mark.asyncio
async def test_custom_system_rules_are_preserved_with_full_schema_fallback_in_v2():
    def handler(request):
        text = json.loads(request.content)['messages'][0]['content']
        assert text.startswith('CUSTOM COMPANY RULES')
        assert 'Required output JSON Schema' in text and 'Operation value hints' not in text
        return completion(v2(ANSWER))
    g = gateway(handler, GROQ_TRANSPORT_VERSION=2, LLM_SYSTEM_PROMPT='CUSTOM COMPANY RULES')
    try:
        actual = await g.extract_operations({'__response_schema': build_operation_schema(sample_trip())})
        assert actual == ANSWER
        assert actual.call_metadata['structured_transport']['semantic_schema_delivery'] == 'full_schema_in_system'
    finally:
        await g.close()


@pytest.mark.asyncio
async def test_readonly_v2_only_selects_sections_and_retains_fixed_server_noop():
    def handler(request):
        system = json.loads(request.content)['messages'][0]['content']
        assert 'Required output JSON Schema' not in system
        return completion({'sections': ['changes', 'party', 'budget']})
    g = gateway(handler, GROQ_TRANSPORT_VERSION=2)
    try:
        actual = await g.extract_operations({'__response_mode': READONLY_MODE, 'current_message': 'Что изменилось?'})
        assert actual['operations'] == [] and actual['control'] == {'action': 'none'}
        assert actual['reply'] == 'changes,party,budget'
        assert actual.call_metadata['structured_transport']['semantic_schema_delivery'] == 'server_constructed_readonly_noop'
    finally:
        await g.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('version', [True, 2.0, [], {}, '3', '2.0'])
async def test_unknown_version_is_rejected_before_any_model_request(version):
    def forbidden(_request):
        pytest.fail('Unknown wire version must not dispatch HTTP')
    g = gateway(forbidden, GROQ_TRANSPORT_VERSION=version)
    try:
        with pytest.raises(ProviderError) as error:
            await g.extract_operations({'__response_schema': build_operation_schema(sample_trip())})
        assert error.value.code == 'CONFIG_BLOCKED'
    finally:
        await g.close()
