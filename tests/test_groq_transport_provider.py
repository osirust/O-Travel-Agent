"""Strict Groq wire roles retain the canonical contract and free-provider fence."""
import hashlib
import json
from copy import deepcopy

import httpx
import pytest
from jsonschema import Draft202012Validator

from otravel.answering import READONLY_MODE, READONLY_SCHEMA
from otravel.config import Settings, provider_status
from otravel.domain import sample_trip
from otravel.llm_contract import build_operation_schema
from otravel.providers import LLMGateway, ProviderError

MODEL = 'qwen/qwen3.8-27b'
ANSWER = {'operations': [{'type': 'update_traveler', 'target_id': 'child-1', 'value': {'age': 4}}],
    'control': {'action': 'continue'}, 'reply': 'Возраст ребёнка изменён.', 'unknowns': [], 'conflicts': [],
    'speech_act': 'CHANGE', 'information_subject': None}


def encode(answer):
    payload = deepcopy(answer)
    payload['operations'] = [{**{k: v for k, v in op.items() if k != 'value'},
        'value_json': json.dumps(op['value'], ensure_ascii=False)} for op in answer['operations']]
    return payload


def gateway(handler, **overrides):
    return LLMGateway({'LLM_PROVIDER': 'groq', 'GROQ_API_KEY': 'synthetic-groq-strict-key',
        'GROQ_MODEL': MODEL, 'GROQ_FREE_TIER_CONFIRMED': True, 'GROQ_REASONING_EFFORT': 'none',
        'GROQ_STRUCTURED_TRANSPORT_ENABLED': True, 'http_transport': httpx.MockTransport(handler), **overrides})


def completion(value):
    return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
        'message': {'content': json.dumps(value, ensure_ascii=False)}}],
        'usage': {'prompt_tokens': 100, 'completion_tokens': 30, 'total_tokens': 130}})


@pytest.mark.asyncio
async def test_operation_wire_targets_and_values_are_canonically_validated_with_request_local_provenance():
    schema = build_operation_schema(sample_trip())
    pristine, calls = deepcopy(schema), []
    wire = encode(ANSWER)
    def handler(request):
        calls.append(request)
        body = json.loads(request.content)
        assert str(request.url) == 'https://api.groq.com/openai/v1/chat/completions'
        assert body['model'] == MODEL and body['reasoning_effort'] == 'none'
        assert body['response_format']['type'] == 'json_schema'
        grammar = body['response_format']['json_schema']
        assert grammar['strict'] is True and Draft202012Validator(grammar['schema']).is_valid(wire)
        bad = encode({**ANSWER, 'operations': [{'type': 'upsert_stay', 'target_id': None, 'value': {'city': 'Белград'}}]})
        assert not Draft202012Validator(grammar['schema']).is_valid(bad)
        assert 'Wire transport override' in body['messages'][0]['content']
        assert 'synthetic-groq-strict-key' not in request.content.decode()
        assert 'models' not in body and 'provider' not in body
        return completion(wire)
    g = gateway(handler)
    try:
        actual = await g.extract_operations({'current_message': 'Ребёнку четыре года.', '__response_schema': schema})
        assert actual == ANSWER and schema == pristine and len(calls) == 1
        metadata = actual.call_metadata['structured_transport']
        assert metadata['format'] == 'groq_strict_operations_v1'
        assert metadata['wire_final_sha256'] == hashlib.sha256(json.dumps(wire, ensure_ascii=False).encode()).hexdigest()
        assert metadata['validated_output_representation'] == 'decoded_canonical_contract'
        assert actual.call_metadata['usage']['total_tokens'] == 130
    finally:
        await g.close()


@pytest.mark.asyncio
async def test_readonly_model_selects_topics_and_server_constructs_the_fixed_noop_envelope():
    calls = []
    def handler(request):
        calls.append(request)
        body = json.loads(request.content)
        grammar = body['response_format']['json_schema']['schema']
        assert set(grammar['properties']) == {'sections'} and body['max_tokens'] <= 512
        return completion({'sections': ['party', 'budget', 'dates']})
    g = gateway(handler)
    try:
        actual = await g.extract_operations({'__response_mode': READONLY_MODE, 'current_message': 'Кто едет, бюджет и даты?'})
        assert actual['operations'] == [] and actual['control'] == {'action': 'none'}
        assert actual['reply'] == 'party,budget,dates' and not actual['unknowns'] and not actual['conflicts']
        assert actual['speech_act'] == 'ASK' and actual['information_subject'] is None
        assert Draft202012Validator(READONLY_SCHEMA).is_valid(actual) and len(calls) == 1
        assert actual.call_metadata['structured_transport']['format'] == 'groq_strict_readonly_v1'
    finally:
        await g.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('wire', [{'': None}, {'operations': []}, {'sections': []},
    {'sections': ['party'], 'operations': [{'type': 'undo'}]}])
async def test_malformed_final_wire_is_rejected_once_without_hidden_fallback(wire):
    calls = []
    def handler(request):
        calls.append(request)
        return completion(wire)
    g = gateway(handler)
    try:
        with pytest.raises(ProviderError) as failed:
            await g.extract_operations({'current_message': 'Изменить поездку.', '__response_schema': build_operation_schema(sample_trip())})
        assert failed.value.code == 'INVALID_LLM_RESPONSE' and len(calls) == 1
        assert g.call_metadata_for_current_call()['usage']['total_tokens'] == 130
        assert 'wire_final_sha256' in g.call_metadata_for_current_call()['structured_transport']
    finally:
        await g.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('key,value', [('GROQ_REASONING_EFFORT', True), ('GROQ_REASONING_EFFORT', 'paid/default'),
    ('GROQ_STRUCTURED_TRANSPORT_ENABLED', []), ('GROQ_STRUCTURED_TRANSPORT_ENABLED', 1)])
async def test_invalid_optin_settings_stop_before_http(key, value):
    def forbidden(_request):
        pytest.fail('Invalid profile must not dispatch HTTP')
    g = gateway(forbidden, **{key: value})
    try:
        with pytest.raises(ProviderError) as error:
            await g.extract_operations({'__response_schema': build_operation_schema(sample_trip())})
        assert error.value.code == 'CONFIG_BLOCKED'
    finally:
        await g.close()


def test_explicit_config_forwarding_and_public_profile_do_not_expose_arbitrary_effort_text(monkeypatch):
    monkeypatch.setenv('GROQ_STRUCTURED_TRANSPORT_ENABLED', 'true')
    monkeypatch.setenv('GROQ_REASONING_EFFORT', 'none')
    config = Settings('unused', 'groq', 1, 8000, 40, 1).provider_settings()
    assert config['GROQ_STRUCTURED_TRANSPORT_ENABLED'] == 'true' and config['GROQ_REASONING_EFFORT'] == 'none'
    assert provider_status()['groq']['structured_transport'] is True
    assert provider_status()['groq']['reasoning_effort'] == 'none'
    monkeypatch.setenv('GROQ_REASONING_EFFORT', 'SYNTHETIC_PRIVATE_ACCOUNT_TEXT')
    assert provider_status()['groq']['reasoning_effort'] is None
    assert 'SYNTHETIC_PRIVATE_ACCOUNT_TEXT' not in json.dumps(provider_status())
