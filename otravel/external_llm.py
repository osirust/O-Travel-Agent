"""Explicitly configured wire formats for the recipient's own LLM endpoint.

Only server environment selects endpoints/headers/models. User trip messages
cannot select a URL, disable TLS, follow redirects or create a model fallback.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlsplit


class ExternalConfigError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _fail(message: str, *, missing: bool = False):
    raise ExternalConfigError('CONFIG_MISSING' if missing else 'CONFIG_BLOCKED', message)


def _value(settings: dict, name: str, default: str = '') -> str:
    value = settings.get(name, settings.get(name.lower(), os.getenv(name, default)))
    return value if isinstance(value, str) and value else default


@dataclass(frozen=True)
class ExternalProfile:
    api_format: str
    base_url: str
    model: str
    key: str
    json_mode: str
    token_field: str
    auth_style: str
    anthropic_version: str

    @property
    def endpoint(self) -> str:
        if self.api_format == 'openai':
            return self.base_url if self.base_url.endswith('/chat/completions') else self.base_url + '/chat/completions'
        if self.api_format == 'anthropic':
            return self.base_url if self.base_url.endswith('/messages') else self.base_url + '/messages'
        model = self.model.removeprefix('models/')
        return self.base_url + '/models/' + model + ':generateContent'


def external_profile(settings: dict) -> ExternalProfile:
    confirmed = _value(settings, 'LLM_EXTERNAL_API_CONFIRMED').lower()
    if confirmed not in {'true', '1', 'yes'}:
        _fail('Подтвердите в конфиге собственный LLM endpoint: LLM_EXTERNAL_API_CONFIRMED=true.')
    api_format = _value(settings, 'LLM_API_FORMAT', 'openai').lower()
    if api_format not in {'openai', 'gemini', 'anthropic'}:
        _fail('LLM_API_FORMAT должен быть openai, gemini или anthropic.')
    base = _value(settings, 'LLM_BASE_URL').rstrip('/')
    model = _value(settings, 'LLM_MODEL')
    key = _value(settings, 'LLM_API_KEY')
    if not base or not model:
        _fail('Заполните LLM_BASE_URL и LLM_MODEL в серверном конфиге.', missing=True)
    if (not 0 < len(base) <= 2048 or not base.isascii()
            or any(ord(c) <= 32 or ord(c) == 127 or c == '\\' for c in base)):
        _fail('Некорректный LLM_BASE_URL.')
    try:
        parts = urlsplit(base)
        if (parts.scheme not in {'http', 'https'} or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment or parts.port == 0
                or any(part in {'.', '..'} for part in unquote(parts.path).split('/'))):
            raise ValueError
        _ = parts.port
    except ValueError:
        _fail('LLM_BASE_URL требует http(s) адрес без credentials, query и fragment.')
    if (not re.fullmatch(r'[@A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,255}', model)
            or '..' in model or '//' in model or model.endswith('/')):
        _fail('Некорректный идентификатор LLM_MODEL.')
    if api_format == 'gemini' and '/' in model.removeprefix('models/'):
        _fail('Для Gemini укажите короткий model ID либо models/model-ID.')
    auth = _value(settings, 'LLM_AUTH_STYLE', 'auto').lower()
    if auth not in {'auto', 'bearer', 'api-key', 'x-api-key', 'none'}:
        _fail('Некорректный LLM_AUTH_STYLE.')
    if auth != 'none' and not key:
        _fail('Заполните LLM_API_KEY в серверном конфиге.', missing=True)
    if key and (len(key) > 8192 or not key.isascii() or any(ord(c) <= 32 or ord(c) == 127 for c in key)):
        _fail('Некорректный LLM_API_KEY.')
    if key and (key in base or key in model):
        _fail('Ключ LLM не должен находиться в адресе сервера или имени модели.')
    json_mode = _value(settings, 'LLM_JSON_MODE', 'prompt').lower()
    token_field = _value(settings, 'LLM_TOKEN_LIMIT_FIELD', 'max_tokens')
    if json_mode not in {'prompt', 'json_object', 'json_schema'}:
        _fail('Некорректный LLM_JSON_MODE.')
    if token_field not in {'max_tokens', 'max_completion_tokens'}:
        _fail('Некорректный LLM_TOKEN_LIMIT_FIELD.')
    version = _value(settings, 'LLM_ANTHROPIC_VERSION', '2023-06-01')
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', version) is None:
        _fail('Некорректный LLM_ANTHROPIC_VERSION.')
    return ExternalProfile(api_format, base, model, key, json_mode, token_field, auth, version)


def request_payload(profile: ExternalProfile, system: str, prompt: str, schema: dict, max_tokens: int) -> tuple[dict, dict]:
    if type(max_tokens) is not int or not 1 <= max_tokens <= 4096:
        _fail('Некорректный предел ответа модели.')
    auth = profile.auth_style
    if auth == 'auto':
        auth = 'x-goog-api-key' if profile.api_format == 'gemini' else 'x-api-key' if profile.api_format == 'anthropic' else 'bearer'
    headers = {'Content-Type': 'application/json'}
    if auth == 'bearer':
        headers['Authorization'] = 'Bearer ' + profile.key
    elif auth != 'none':
        headers[auth] = profile.key
    if profile.api_format == 'openai':
        body = {'model': profile.model, 'messages': [{'role': 'system', 'content': system},
                {'role': 'user', 'content': prompt}], 'stream': False, profile.token_field: max_tokens}
        if profile.json_mode == 'json_object':
            body['response_format'] = {'type': 'json_object'}
        elif profile.json_mode == 'json_schema':
            body['response_format'] = {'type': 'json_schema', 'json_schema': {
                'name': 'travel_operations', 'strict': True, 'schema': schema}}
        return headers, body
    if profile.api_format == 'gemini':
        generation: dict[str, Any] = {'maxOutputTokens': max_tokens, 'responseMimeType': 'application/json'}
        if profile.json_mode == 'json_schema':
            generation['responseJsonSchema'] = schema
        return headers, {'systemInstruction': {'parts': [{'text': system}]},
                         'contents': [{'role': 'user', 'parts': [{'text': prompt}]}], 'generationConfig': generation}
    headers['anthropic-version'] = profile.anthropic_version
    body = {'model': profile.model, 'system': system, 'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': max_tokens, 'stream': False}
    if profile.json_mode == 'json_schema':
        body['output_config'] = {'format': {'type': 'json_schema', 'schema': schema}}
    return headers, body


def response_text(profile: ExternalProfile, body: dict) -> str:
    try:
        if profile.api_format == 'openai':
            choice = body['choices'][0]
            message = choice['message']
            if choice.get('finish_reason') not in {None, 'stop'} or message.get('refusal') or message.get('tool_calls'):
                raise ValueError
            result = message['content']
        elif profile.api_format == 'gemini':
            candidate = body['candidates'][0]
            if candidate.get('finishReason') not in {None, 'STOP'} or body.get('promptFeedback', {}).get('blockReason'):
                raise ValueError
            parts = candidate['content']['parts']
            if any(part.get('functionCall') for part in parts):
                raise ValueError
            result = ''.join(part['text'] for part in parts if part.get('thought') is not True and 'text' in part)
        else:
            if body.get('stop_reason') not in {None, 'end_turn', 'stop_sequence'}:
                raise ValueError
            parts = body['content']
            if any(part.get('type') not in {'text', 'thinking', 'redacted_thinking'} for part in parts):
                raise ValueError
            result = ''.join(part['text'] for part in parts if part.get('type') == 'text')
        if not isinstance(result, str) or not result.strip():
            raise ValueError
        return result
    except (KeyError, TypeError, IndexError, AttributeError, ValueError):
        raise ExternalConfigError('INVALID_LLM_RESPONSE', 'Внешняя модель не вернула завершённый текстовый JSON-ответ.') from None


def external_status(settings: dict) -> dict:
    try:
        profile = external_profile(settings)
    except ExternalConfigError as error:
        return {'configured': False, 'configuration_error': error.code, 'paid_fallback': False}
    return {'configured': True, 'model': profile.model, 'api_format': profile.api_format,
            'endpoint_confirmed': True, 'paid_fallback': False}
