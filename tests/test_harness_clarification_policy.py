"""Exact natural extraction on own PG with recorded in-process travel only."""
import json
import os
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from test_api import StubLLM, _headers, _session, _snapshot, _wait
from test_orchestrator import FakeTravel

from otravel.api import create_app
from otravel.config import Settings
from otravel.store import Store, quotes

SOURCE = json.loads((Path(__file__).parents[1] / 'fixtures/regression/natural-hybrid-optional-unknowns-20261027.json').read_text(encoding='utf-8'))


class RecordedTravel(FakeTravel):
    """Fresh controlled flags/prices for contract tests, never supplier evidence."""
    def offer(self, request, provider, kind):
        offer = super().offer(request, provider, kind)
        now = datetime.now(UTC)
        offer.update(retrieved_at=now.isoformat(), expires_at=(now + timedelta(minutes=5)).isoformat())
        return offer

    async def close(self):
        return None


@pytest_asyncio.fixture
async def optional_api():
    url = os.environ.get('TEST_DATABASE_URL')
    if not url:
        pytest.skip('An explicit isolated PostgreSQL test database is required')
    assert make_url(url).database == 'otravel_test'
    opened = []

    async def open_api(answer):
        store, model, travel = Store(url), StubLLM([deepcopy(answer)]), RecordedTravel()
        app = create_app(Settings(url, 'gemini', 1, 8000, 1000000, 1), store=store, llm=model, travel=travel)
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
        await client.__aenter__()
        opened.append((client, lifespan, store))
        return client, model, travel, store

    try:
        yield open_api
    except Exception as exc:
        if isinstance(exc, AssertionError):
            raise
        pytest.fail('Controlled optional-question integration failed (' + type(exc).__name__ + '); private DB details omitted', pytrace=False)
    finally:
        for client, lifespan, store in reversed(opened):
            await client.__aexit__(None, None, None)
            await lifespan.__aexit__(None, None, None)
            await store.close()


async def send_source(factory, answer, text=None):
    client, model, travel, store = await factory(answer)
    sid, token, initial = await _session(client)
    assert initial['trip']['travelers'] == [] and initial['trip']['stays'] == []
    accepted = await client.post('/api/v1/sessions/' + sid + '/messages', headers=_headers(token),
        json={'client_message_id': 'natural-optional-' + sid, 'text': SOURCE['input_text'] if text is None else text})
    run = await _wait(client, accepted.json()['run_id'], token)
    snapshot = await _snapshot(client, sid, token)
    assert len(model.calls) == 1
    assert (await client.get('/api/v1/sessions/' + sid + '/orders', headers=_headers(token))).json()['orders'] == []
    async with store.engine.connect() as conn:
        assert await conn.scalar(select(func.count()).select_from(quotes).where(quotes.c.session_id == sid)) == 0
    return run, snapshot, travel


@pytest.mark.parametrize('model_control', ['continue', 'wait'])
@pytest.mark.asyncio
async def test_exact_complete_natural_contract_reaches_trip_search_and_keeps_optional_advice_nonblocking(optional_api, model_control):
    answer = deepcopy(SOURCE['raw_source_validated_contract'])
    answer['control']['action'] = model_control
    run, snapshot, travel = await send_source(optional_api, answer)
    assert run['status'] == 'completed' and not run.get('error')
    assert snapshot['status'] == 'OPTIONS_READY' and snapshot['waiting_reason'] is None and snapshot['pending_question'] is None
    result = run['result']['result']
    assert result['plans'] and len([call for call in travel.calls if call[0] == 'search_hotels']) == 2
    assert len([call for call in travel.calls if call[0] == 'search_flights']) == 3
    optional = result['optional_clarifications']
    assert {item['topic'] for item in optional} == {'room_type_preference', 'transport_booking_permission'}
    assert all(item['blocking'] is False for item in optional)
    trip = snapshot['trip']
    assert trip['search_policy']['guest_nationality'] == 'RU' and trip['search_policy']['mode'] == 'HYBRID'
    assert trip['budget']['amount_minor'] == 200000000
    assert sorted(person['age'] for person in trip['travelers'] if person['type'] == 'child') == [7, 12]
    assert len(trip['stays']) == 2 and all(len(stay['rooms']) == 2 for stay in trip['stays'])
    assert trip['constraints'] == [] and trip['preferences'] == []


@pytest.mark.asyncio
async def test_explicit_user_pause_is_not_removed_when_all_model_questions_are_optional(optional_api):
    answer = deepcopy(SOURCE['raw_source_validated_contract'])
    answer['control']['action'] = 'wait'
    run, snapshot, travel = await send_source(optional_api, answer, SOURCE['input_text'] + ' Подожди, я ещё дополню.')
    assert run['status'] == 'completed' and snapshot['waiting_reason'] == 'user_pause'
    assert travel.calls == []


@pytest.mark.parametrize('addition,topic', [('Обязательна двуспальная кровать.', 'типу номера'),
    ('Перелёты не нужны, только отели.', 'перел')])
@pytest.mark.asyncio
async def test_real_user_room_requirement_or_transport_veto_still_blocks_and_question_keeps_its_meaning(optional_api, addition, topic):
    run, snapshot, travel = await send_source(optional_api, SOURCE['raw_source_validated_contract'], SOURCE['input_text'] + ' ' + addition)
    assert run['status'] == 'completed' and snapshot['waiting_reason'] == 'clarification'
    assert travel.calls == []
    question = snapshot['pending_question']['text']
    assert question != 'Что нужно уточнить о поездке, чтобы продолжить?'
    assert topic in question.casefold().replace('ё', 'е')


@pytest.mark.parametrize('missing', ['nationality', 'child_age', 'family'])
@pytest.mark.asyncio
async def test_real_required_search_fact_is_still_a_specific_blocking_question(optional_api, missing):
    answer = deepcopy(SOURCE['raw_source_validated_contract'])
    text = SOURCE['input_text']
    if missing == 'nationality':
        next(op for op in answer['operations'] if op['type'] == 'set_search_policy')['value'].pop('guest_nationality')
        text = text.replace('Гражданство Россия.', '')
        answer['unknowns'] = ['Гражданство для поиска отеля?']
        expected_field = 'search.guest_nationality'
    elif missing == 'child_age':
        next(op for op in answer['operations'] if op['type'] == 'add_traveler' and op['value'].get('age') == 7)['value']['age'] = None
        text = text.replace('7 и 12 лет', 'неизвестного возраста и 12 лет').replace('ребёнком 7 лет', 'младшим ребёнком неизвестного возраста')
        answer['unknowns'] = ['Сколько лет младшему ребёнку?']
        expected_field = 'traveler.age'
    else:
        answer['operations'] = [op for op in answer['operations'] if not (op['type'] == 'upsert_group' and op['target_id'] == 'new-group-1')]
        answer['unknowns'] = ['Кто входит в первую отдельную семью?']
        expected_field = 'group.membership'
    run, snapshot, travel = await send_source(optional_api, answer, text)
    assert run['status'] == 'completed' and snapshot['waiting_reason'] == 'clarification'
    assert snapshot['pending_question']['field'] == expected_field
    assert travel.calls == []
    if missing == 'nationality':
        assert snapshot['trip']['search_policy']['guest_nationality'] is None
    elif missing == 'child_age':
        assert any(person['type'] == 'child' and person['age'] is None for person in snapshot['trip']['travelers'])
    else:
        assert any(group['keep_separate'] and not group['traveler_ids'] for group in snapshot['trip']['groups'])


@pytest.mark.asyncio
async def test_model_broad_room_unknown_over_explicit_unsupported_seaview_remains_blocking(optional_api):
    run, snapshot, travel = await send_source(optional_api, SOURCE['raw_source_validated_contract'],
        SOURCE['input_text'] + ' Обязателен вид на море.')
    assert run['status'] == 'completed' and snapshot['waiting_reason'] == 'clarification'
    assert travel.calls == []
    assert 'типу номера' in snapshot['pending_question']['text'].casefold()
