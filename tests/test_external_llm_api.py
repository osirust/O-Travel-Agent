"""Real API/harness/PostgreSQL with recipient-format synthetic HTTP servers."""
import asyncio
import json
import os
from uuid import uuid4

import httpx
import pytest
from test_api import NoTravelHTTP, _assert_no_private_keys, _headers, _run, _session, _snapshot
from test_external_llm import GOOD, config, response

from otravel.api import create_app
from otravel.config import Settings
from otravel.providers import LLMGateway
from otravel.store import Store


async def _wait(client, run_id, token):
    # This exercises real PostgreSQL and a background worker, rather than a
    # CPU-latency benchmark. Keep completion bounded under full-suite DB load.
    async with asyncio.timeout(10):
        while True:
            response = await client.get(f'/api/v1/runs/{run_id}', headers=_headers(token))
            assert response.status_code == 200, response.text
            body = response.json()
            run = _run(body)
            if run.get('status') in {'completed', 'failed', 'conflict', 'interrupted', 'cancelled'}:
                _assert_no_private_keys(body)
                return run
            await asyncio.sleep(0.05)


@pytest.mark.asyncio
@pytest.mark.parametrize('api_format', ['openai', 'gemini', 'anthropic'])
async def test_recipient_native_format_persists_change_and_readonly_answer_without_key_leak(api_format, monkeypatch):
    url = os.environ.get('TEST_DATABASE_URL')
    if not url:
        pytest.skip('Explicit PostgreSQL test URL required')
    calls = []
    change = {**GOOD, 'operations': [{'type': 'set_origin', 'value': {'city': 'Москва'}}]}
    readonly = {**GOOD, 'operations': [], 'control': {'action': 'none'}, 'reply': 'route',
                'unknowns': [], 'speech_act': 'ASK'}
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=response(api_format, change if len(calls) == 1 else readonly))
    settings = Settings(url, 'external', 1.0, 8000, 1_000_000, 2)
    gateway = LLMGateway(config(LLM_API_FORMAT=api_format, http_transport=httpx.MockTransport(handler)))
    repository = Store(url)
    app = create_app(settings, store=repository, llm=gateway, travel=NoTravelHTTP())
    try:
        async with app.router.lifespan_context(app), \
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                sid, token, _ = await _session(client)
                path = f'/api/v1/sessions/{sid}/messages'
                accepted = await client.post(path, headers=_headers(token), json={
                    'text': 'Я отправляюсь из Москвы.', 'client_message_id': str(uuid4()), 'expected_version': 0})
                assert accepted.status_code == 202
                run = await _wait(client, accepted.json()['run_id'], token)
                assert run['status'] == 'completed', run.get('error')
                assert run['result']['result']['llm']['provider'] == 'external'
                before = await _snapshot(client, sid, token)
                assert before['trip']['origin'] == 'Москва' and before['state_version'] == 1
                accepted = await client.post(path, headers=_headers(token), json={
                    'text': 'Напомни город отправления и ничего не меняй.', 'client_message_id': str(uuid4()), 'expected_version': 1})
                run = await _wait(client, accepted.json()['run_id'], token)
                assert run['status'] == 'completed', run.get('error')
                after = await _snapshot(client, sid, token)
                assert after['trip'] == before['trip'] and after['state_version'] == before['state_version']
                assert 'Москва' in run['result']['reply']
                assert len(calls) == 2 and not await repository.list_orders(sid)
                events = await repository.list_events(sid)
                assert sum(event['event_type'] == 'MODEL_CALL_STARTED' for event in events) == 2
                assert not any(event['event_type'] == 'TOOL_CALL_STARTED' for event in events)
    finally:
        await repository.close()


@pytest.mark.asyncio
async def test_missing_recipient_key_is_recoverable_and_does_not_make_http_or_mutate_trip():
    url = os.environ.get('TEST_DATABASE_URL')
    if not url:
        pytest.skip('Explicit PostgreSQL test URL required')
    calls = []
    def forbidden(request):
        calls.append(True)
        raise AssertionError('No external HTTP for missing key')
    settings = Settings(url, 'external', 1.0, 8000, 1_000_000, 1)
    gateway = LLMGateway(config(LLM_API_KEY='', http_transport=httpx.MockTransport(forbidden)))
    repository = Store(url)
    app = create_app(settings, store=repository, llm=gateway, travel=NoTravelHTTP())
    try:
        async with app.router.lifespan_context(app), \
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                sid, token, _ = await _session(client)
                before = await _snapshot(client, sid, token)
                accepted = await client.post(f'/api/v1/sessions/{sid}/messages', headers=_headers(token), json={
                    'text': 'Я отправляюсь из Москвы.', 'client_message_id': str(uuid4())})
                run = await _wait(client, accepted.json()['run_id'], token)
                after = await _snapshot(client, sid, token)
                assert run['status'] == 'failed' and run['error']['code'] == 'CONFIG_MISSING'
                assert after['trip'] == before['trip'] and after['state_version'] == before['state_version']
                assert after['status'] == 'RECOVERABLE_ERROR' and calls == []
    finally:
        await repository.close()


@pytest.mark.asyncio
async def test_recipient_provider_remains_in_durable_remote_bucket_and_repairs_at_most_twice():
    url = os.environ.get('TEST_DATABASE_URL')
    if not url:
        pytest.skip('Explicit PostgreSQL test URL required')
    calls = []
    invalid = {**GOOD, 'operations': [{'type': 'set_route_policy',
                                    'value': {'kind': 'round_trip', 'end_city': 'Стамбул'}}]}
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=response('openai', invalid))
    settings = Settings(url, 'external', 1.0, 8000, 1_000_000, 5)
    gateway = LLMGateway(config(http_transport=httpx.MockTransport(handler)))
    repository = Store(url)
    app = create_app(settings, store=repository, llm=gateway, travel=NoTravelHTTP())
    try:
        async with app.router.lifespan_context(app), \
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            sid, token, _ = await _session(client)
            before = await _snapshot(client, sid, token)
            accepted = await client.post(f'/api/v1/sessions/{sid}/messages', headers=_headers(token), json={
                'text': 'Хочу из Москвы в Стамбул.', 'client_message_id': str(uuid4())})
            run = await _wait(client, accepted.json()['run_id'], token)
            assert run['status'] == 'failed' and run['error']['code'] == 'INVALID_LLM_OPERATION'
            assert (await _snapshot(client, sid, token))['trip'] == before['trip']
            assert len(calls) == 2
            repair_context = json.loads(calls[1]['messages'][1]['content'])
            assert repair_context['validation_feedback']['errors'][0] == {
                'path': ['operations', 0, 'value', 'end_city'], 'rule': 'type', 'expected_type': 'null'}
            assert repair_context['validation_feedback']['rejected_contract']['operations'] == invalid['operations']
            events = await repository.list_events(sid)
            actual = [event for event in events if event['event_type'] == 'MODEL_CALL_STARTED']
            assert len(actual) == 2 and all(event['payload']['provider'] == 'external' for event in actual)
            assert not await repository.reserve_model_call(sid, run['run_id'], 2000, 'external')
            assert not await repository.list_orders(sid)
    finally:
        await repository.close()
