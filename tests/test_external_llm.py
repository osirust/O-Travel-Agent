"""Recipient-owned LLM transports: exact wire format and canonical safeguards."""
import asyncio
import json
from copy import deepcopy

import httpx
import pytest

from otravel.answering import READONLY_SCHEMA
from otravel.config import Settings, provider_status
from otravel.domain import TripState
from otravel.external_llm import external_profile, request_payload
from otravel.llm_contract import build_operation_schema
from otravel.providers import LLMGateway, ProviderError

KEY = 'synthetic-external-private-key-2700'
GOOD = {'operations': [], 'control': {'action': 'none'}, 'reply': 'Уточните город.',
        'unknowns': ['В какой город?'], 'conflicts': [], 'speech_act': 'CHANGE', 'information_subject': None}


def config(**overrides):
    return {'LLM_PROVIDER': 'external', 'LLM_API_FORMAT': 'openai', 'LLM_BASE_URL': 'https://judge.example/v1',
            'LLM_MODEL': 'their-own-model', 'LLM_API_KEY': KEY, 'LLM_EXTERNAL_API_CONFIRMED': 'true', **overrides}


def response(api_format, output=GOOD, **extra):
    content = json.dumps(output, ensure_ascii=False)
    if api_format == 'openai':
        return {'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}], **extra}
    if api_format == 'gemini':
        return {'candidates': [{'content': {'parts': [{'thought': True, 'text': 'hidden reasoning'}, {'text': content}]},
                                'finishReason': 'STOP'}], **extra}
    return {'content': [{'type': 'thinking', 'thinking': 'not user-visible'}, {'type': 'text', 'text': content}],
            'stop_reason': 'end_turn', **extra}


def extract(settings, context=None):
    async def run():
        gateway = LLMGateway(settings)
        try:
            return await gateway.extract_operations(context or {})
        finally:
            await gateway.close()
    return asyncio.run(run())


@pytest.mark.parametrize('api_format', ['openai', 'gemini', 'anthropic'])
def test_three_native_request_shapes_share_the_same_validated_domain_contract(api_format):
    seen = []
    def handler(request):
        seen.append(request)
        assert request.method == 'POST' and not request.url.query
        body = json.loads(request.content)
        assert request.headers['content-type'].startswith('application/json')
        if api_format == 'openai':
            assert request.url.path == '/v1/chat/completions'
            assert request.headers['authorization'] == 'Bearer ' + KEY
            assert body['model'] == 'their-own-model' and body['stream'] is False
            assert body['max_tokens'] == 4096 and 'response_format' not in body
            assert [m['role'] for m in body['messages']] == ['system', 'user']
            assert 'upsert_stay.value.city' in body['messages'][0]['content']
        elif api_format == 'gemini':
            assert request.url.path == '/v1/models/their-own-model:generateContent'
            assert request.headers['x-goog-api-key'] == KEY and 'authorization' not in request.headers
            assert body['contents'][0]['role'] == 'user' and body['systemInstruction']['parts'][0]['text']
            assert body['generationConfig'] == {'maxOutputTokens': 4096, 'responseMimeType': 'application/json'}
        else:
            assert request.url.path == '/v1/messages' and request.headers['x-api-key'] == KEY
            assert request.headers['anthropic-version'] == '2023-06-01'
            assert body['model'] == 'their-own-model' and body['max_tokens'] == 4096
            assert body['system'] and body['messages'][0]['role'] == 'user'
        assert KEY not in request.content.decode()
        return httpx.Response(200, json=response(api_format))
    result = extract(config(LLM_API_FORMAT=api_format, http_transport=httpx.MockTransport(handler)))
    assert result == GOOD and len(seen) == 1
    assert result.call_metadata['provider'] == 'external' and result.call_metadata['model'] == 'their-own-model'


@pytest.mark.parametrize('api_format', ['openai', 'gemini', 'anthropic'])
def test_readonly_role_does_not_gain_operation_authority_in_any_external_format(api_format):
    readonly = {**GOOD, 'reply': 'party,route', 'speech_act': 'ASK', 'unknowns': []}
    handler = lambda request: httpx.Response(200, json=response(api_format, readonly))
    settings = config(LLM_API_FORMAT=api_format, http_transport=httpx.MockTransport(handler))
    result = extract(settings, {'__response_mode': 'readonly_answer_v1', '__response_schema': READONLY_SCHEMA})
    assert result == readonly
    malicious = {**readonly, 'operations': [{'type': 'set_origin', 'value': {'city': 'Москва'}}]}
    settings['http_transport'] = httpx.MockTransport(lambda request: httpx.Response(200, json=response(api_format, malicious)))
    with pytest.raises(ProviderError) as error:
        extract(settings, {'__response_mode': 'readonly_answer_v1', '__response_schema': READONLY_SCHEMA})
    assert error.value.code == 'INVALID_LLM_OPERATION'


@pytest.mark.parametrize('overrides', [
    {'LLM_EXTERNAL_API_CONFIRMED': 'false'}, {'LLM_API_FORMAT': 'invented'}, {'LLM_BASE_URL': ''}, {'LLM_MODEL': ''},
    {'LLM_API_KEY': ''}, {'LLM_BASE_URL': 'https://key:password@judge.example/v1'},
    {'LLM_BASE_URL': 'https://judge.example/v1?api_key=secret'}, {'LLM_BASE_URL': 'https://judge.example/v1#secret'},
    {'LLM_BASE_URL': 'file:///etc/passwd'}, {'LLM_BASE_URL': 'https://judge.example/v1\nHeader:value'},
    {'LLM_BASE_URL': 'https://judge.example\\@evil.test/v1'}, {'LLM_BASE_URL': 'https://judge.example:bad/v1'},
    {'LLM_BASE_URL': 'https://judge.example/v1/%2e%2e/x'}, {'LLM_MODEL': '../model'}, {'LLM_MODEL': 'model?key=x'},
    {'LLM_API_KEY': 'secret\nX-Header: bad'}, {'LLM_JSON_MODE': 'invalid'}, {'LLM_TOKEN_LIMIT_FIELD': 'unsupported'},
    {'LLM_AUTH_STYLE': 'arbitrary-header'}, {'LLM_ANTHROPIC_VERSION': 'header\nvalue'},
    {'LLM_BASE_URL': 'https://judge.example/' + KEY}, {'LLM_MODEL': KEY},
])
def test_invalid_or_unconfirmed_configuration_is_rejected_before_any_http(overrides):
    calls = []
    def forbidden(request):
        calls.append(True)
        raise AssertionError('Configuration must fail before HTTP')
    with pytest.raises(ProviderError) as error:
        extract(config(**overrides, http_transport=httpx.MockTransport(forbidden)))
    assert error.value.code in {'CONFIG_MISSING', 'CONFIG_BLOCKED'} and not calls
    assert KEY not in str(error.value.as_dict())


@pytest.mark.parametrize('base', ['http://127.0.0.1:8000/v1', 'http://vllm:8000/v1', 'https://internal.company/v1'])
def test_explicit_server_confirmation_supports_private_company_endpoint(base):
    profile = external_profile(config(LLM_BASE_URL=base))
    assert profile.endpoint == base + '/chat/completions'


def test_full_chat_endpoint_is_not_appended_twice_and_advanced_token_field_is_explicit():
    profile = external_profile(config(LLM_BASE_URL='https://judge.example/v1/chat/completions',
        LLM_TOKEN_LIMIT_FIELD='max_completion_tokens', LLM_JSON_MODE='json_schema'))
    headers, body = request_payload(profile, 'system', 'prompt', {'type': 'object'}, 512)
    assert profile.endpoint.endswith('/v1/chat/completions')
    assert body['max_completion_tokens'] == 512 and 'max_tokens' not in body
    assert body['response_format']['json_schema']['strict'] is True
    assert headers['Authorization'].startswith('Bearer ')


@pytest.mark.parametrize('api_format', ['openai', 'gemini', 'anthropic'])
def test_credential_echo_is_rejected_before_diagnostic_snapshot(api_format):
    echoed = {**GOOD, 'reply': 'key=' + KEY}
    with pytest.raises(ProviderError) as error:
        extract(config(LLM_API_FORMAT=api_format,
            http_transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response(api_format, echoed)))))
    assert error.value.code == 'INVALID_LLM_RESPONSE'
    assert KEY not in str(error.value.as_dict())


@pytest.mark.parametrize('api_format', ['openai', 'gemini', 'anthropic'])
def test_truncated_or_tool_call_output_never_becomes_domain_operations(api_format):
    bad = response(api_format)
    if api_format == 'openai':
        bad['choices'][0]['finish_reason'] = 'length'
    elif api_format == 'gemini':
        bad['candidates'][0]['finishReason'] = 'MAX_TOKENS'
    else:
        bad['stop_reason'] = 'max_tokens'
    with pytest.raises(ProviderError) as error:
        extract(config(LLM_API_FORMAT=api_format,
            http_transport=httpx.MockTransport(lambda request: httpx.Response(200, json=bad))))
    assert error.value.code == 'INVALID_LLM_RESPONSE'


def test_redirect_is_not_followed_and_http429_retains_bounded_retry_hint():
    calls = []
    def redirect(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={'Location': 'https://other.example/leak'})
    with pytest.raises(ProviderError) as error:
        extract(config(http_transport=httpx.MockTransport(redirect)))
    assert error.value.code == 'HTTP_ERROR' and len(calls) == 1
    with pytest.raises(ProviderError) as error:
        extract(config(http_transport=httpx.MockTransport(lambda request: httpx.Response(429, headers={'retry-after': '17'}))))
    assert error.value.code == 'RATE_LIMITED' and error.value.details['retry_after_seconds'] == 17


def test_unknown_recipient_model_still_obeys_round_trip_schema():
    bad = {**GOOD, 'operations': [{'type': 'set_route_policy', 'value': {'kind': 'round_trip', 'end_city': 'Стамбул'}}]}
    schema = build_operation_schema(TripState())
    with pytest.raises(ProviderError) as error:
        extract(config(http_transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response('openai', bad)))),
                {'__response_schema': schema})
    assert error.value.code == 'INVALID_LLM_OPERATION'
    assert error.value.details['rejected_structured_contract']['value']['operations'] == bad['operations']


def test_settings_and_status_select_recipient_format_without_echoing_endpoint_or_key(monkeypatch):
    for name, value in config(LLM_API_FORMAT='gemini', LLM_MODEL='gemini-owned-by-jury').items():
        monkeypatch.setenv(name, value)
    settings = Settings.from_env()
    assert settings.llm_mode == 'external' and settings.provider_settings()['LLM_API_KEY'] == KEY
    status = provider_status()
    assert status['_runtime'] == {'llm_provider': 'external', 'model': 'gemini-owned-by-jury'}
    assert status['external']['configured'] and status['external']['api_format'] == 'gemini'
    assert KEY not in json.dumps(status) and 'judge.example' not in json.dumps(status)
    monkeypatch.setenv('LLM_MODEL', KEY)
    assert provider_status()['_runtime']['model'] is None
    assert KEY not in json.dumps(provider_status())


def test_empty_optional_settings_use_documented_defaults(monkeypatch):
    monkeypatch.setenv('LLM_JSON_MODE', '')
    settings = config(LLM_JSON_MODE='', LLM_TOKEN_LIMIT_FIELD='', LLM_AUTH_STYLE='', LLM_ANTHROPIC_VERSION='')
    profile = external_profile(settings)
    assert profile.json_mode == 'prompt' and profile.auth_style == 'auto'
    assert profile.token_field == 'max_tokens' and profile.anthropic_version == '2023-06-01'


def test_schema_repair_explains_round_trip_null_from_server_schema_without_accepting_or_echoing_input():
    from otravel.llm_schema_feedback import operation_schema_feedback
    schema = build_operation_schema(TripState())
    before = deepcopy(schema)
    value = {'operations': [{'type': 'set_route_policy', 'value': {'kind': 'round_trip', 'end_city': 'Стамбул'}}]}
    copied = deepcopy(value)
    details = operation_schema_feedback(value, schema)
    assert details == [{'path': ['operations', 0, 'value', 'end_city'], 'rule': 'type', 'expected_type': 'null'}]
    assert value == copied and schema == before and 'Стамбул' not in json.dumps(details)
    value['operations'][0]['value'] = {'kind': 'open_jaw', 'end_city': 'Стамбул'}
    assert operation_schema_feedback(value, schema) == []


def test_expected_identifier_feedback_is_bounded_to_server_owned_enum_and_three_errors():
    from otravel.llm_schema_feedback import operation_schema_feedback
    schema = build_operation_schema(TripState())
    value = {'operations': [{'type': 'add_traveler', 'value': {'id': 'UNTRUSTED-ID', 'type': 'adult', 'age': 18}}] * 20}
    details = operation_schema_feedback(value, schema)
    assert 0 < len(details) <= 3
    assert 'UNTRUSTED-ID' not in json.dumps(details)
    assert all(len(item.get('allowed_values', [])) <= 8 for item in details)
