"""Controlled one-attempt refusal and bounded intent repair through real PG.

Recorded first model JSON; the second response is explicitly controlled test
data. No test helper or product guard remaps people by matching their ages.
"""
import hashlib
import json
import os
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from test_api import StubLLM, _snapshot
from test_duration_grounding_pg import message
from test_geo_catalog import admit, key
from test_mixed_checkout import CheckoutTravel
from test_slot_continuation import _reply

from otravel.api import create_app
from otravel.config import Settings
from otravel.geo_catalog import GeoCatalog
from otravel.place_directory import PlaceDirectory
from otravel.providers import ProviderError
from otravel.store import Store

HERE = Path(__file__).parent
SOURCE = json.loads((ROOT / 'fixtures/regression/nemo-explicit-noadd-20261028.json').read_text(encoding='utf-8'))['recorded_case']


def controlled_second_answer():
    """An authored response names the already known child ID, not a heuristic."""
    result = deepcopy(SOURCE['recorded_final_contract'])
    result['operations'] = [operation for operation in result['operations'] if operation['type'] != 'add_traveler']
    for operation in result['operations']:
        value = operation['value']
        if isinstance(value.get('traveler_ids'), list):
            value['traveler_ids'] = ['new-traveler-4' if identifier == 'new-traveler-5' else identifier
                                    for identifier in value['traveler_ids']]
        if operation['type'] == 'upsert_stay':
            for room in value.get('rooms', []):
                room['traveler_ids'] = ['new-traveler-4' if identifier == 'new-traveler-5' else identifier
                                       for identifier in room['traveler_ids']]
    # Schema-valid replay from the earlier correction: the existing return
    # preservation guard must still suppress it on the successful repair pass.
    result['operations'].insert(0, {'type': 'shift_return', 'value': {'days': 2}, 'target_id': None})
    result['reply'] = 'Распределяю только четырёх сохранённых участников; нового ребёнка не создаю.'
    return result


class ClosedRecordedLLM(StubLLM):
    def __init__(self, responses):
        super().__init__(responses)
        self.unavailable_repairs = 0

    async def extract_operations(self, context):
        if not self.answers:
            self.unavailable_repairs += 1
            raise ProviderError('RECORDED_REPAIR_UNAVAILABLE', 'No unrecorded model response is allowed.')
        return await super().extract_operations(context)


@pytest_asyncio.fixture
async def no_add_api(monkeypatch):
    url = os.environ.get('TEST_DATABASE_URL')
    if not url:
        pytest.skip('Only explicitly selected isolated test PostgreSQL is authorized')
    if make_url(url).database != 'otravel_test':
        pytest.fail('Only otravel_test is authorized', pytrace=False)
    opened = []

    async def create(model, run_limit):
        repository = Store(url)
        catalog = GeoCatalog(repository.engine)
        await catalog.initialize()
        for ref in SOURCE['before_trip']['location_refs'].values():
            fact = {field: value for field, value in ref.items()
                    if field not in {'place_id', 'ref_version', 'provider_bindings'}}
            fact['language'] = 'en'
            admitted, _ = await admit(catalog, key(operation='get_place', role='city', language='en',
                query=str(fact['geographic_source']['external_id']), country_iso2=None, count=1), fact,
                now=datetime.now(UTC))
            assert admitted.model_dump(mode='json') == ref

        def no_directory_http(_request):
            raise AssertionError('No directory HTTP is authorized for cached known CityRefs')

        directory = PlaceDirectory(cache=catalog, transport=httpx.MockTransport(no_directory_http))
        settings = Settings(url, 'gemini', 1, 8000, 1000000, run_limit, max_local_llm_calls_per_day=0,
            default_search_mode='HYBRID', default_hotel_provider='liteapi', default_flight_provider='duffel',
            dynamic_geography_enabled=True)
        app = create_app(settings, store=repository, llm=model, travel=CheckoutTravel(),
                         geo_catalog=catalog, place_directory=directory)

        async def no_foreign_recovery():
            pass

        monkeypatch.setattr(app.state.harness, 'start', no_foreign_recovery)
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://no-add-controlled')
        opened.append((client, lifespan, repository))
        token = str(uuid4())
        own = await repository.create_session(SOURCE['before_trip'], hashlib.sha256(token.encode()).hexdigest())
        return client, repository, own['session_id'], token

    yield create
    for client, lifespan, repository in reversed(opened):
        await client.aclose()
        await lifespan.__aexit__(None, None, None)
        await repository.close()


def dictionaries(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from dictionaries(child)
    elif isinstance(value, list):
        for child in value:
            yield from dictionaries(child)


@pytest.mark.asyncio
async def test_recorded_nemo_single_attempt_cannot_commit_forbidden_duplicate(no_add_api):
    model = ClosedRecordedLLM([SOURCE['recorded_final_contract']])
    client, repository, sid, token = await no_add_api(model, 1)
    before = await _snapshot(client, sid, token)
    run, after = await message(client, sid, token, SOURCE['input'])
    assert run['status'] == 'failed' and run['error']['code'] == 'INVALID_LLM_OPERATION'
    assert after['trip'] == before['trip'] and after['state_version'] == before['state_version']
    assert len(after['trip']['travelers']) == 4 and len(model.calls) == 1
    assert model.unavailable_repairs == 0
    assert await repository.list_orders(sid) == []
    assert not any(event['event_type'] == 'TOOL_CALL_STARTED' for event in await repository.list_events(sid))


@pytest.mark.asyncio
async def test_valid_controlled_second_answer_uses_feedback_and_keeps_original_return(no_add_api):
    model = ClosedRecordedLLM([SOURCE['recorded_final_contract'], controlled_second_answer()])
    client, repository, sid, token = await no_add_api(model, 2)
    before = await _snapshot(client, sid, token)
    run, after = await message(client, sid, token, SOURCE['input'])
    assert run['status'] == 'completed' and not run.get('error'), run.get('error')
    assert len(model.calls) == 2 and model.unavailable_repairs == 0
    feedback = model.calls[1]['validation_feedback']
    guard_feedback = next(item for item in dictionaries(feedback)
                          if item.get('forbidden_new_traveler_ids') == ['new-traveler-5'])
    assert guard_feedback['existing_traveler_ids'] == [person['id'] for person in before['trip']['travelers']]
    assert {person['id'] for person in after['trip']['travelers']} == {person['id'] for person in before['trip']['travelers']}
    assert sum(person['type'] == 'child' and person['age'] == 4 for person in after['trip']['travelers']) == 1
    assert [len(stay['rooms']) for stay in after['trip']['stays']] == [2, 2]
    assert after['trip']['stays'][-1]['check_out'] == after['trip']['legs'][-1]['departure_date'] == '2026-11-19'
    assert after['trip']['budget'] == before['trip']['budget']
    notes = run['result']['result']['operation_normalizations']
    assert any(note.get('code') == 'unrequested_return_shift_suppressed' for note in notes)
    assert await repository.list_orders(sid) == []


@pytest.mark.asyncio
async def test_current_add_and_noadd_conflict_asks_human_without_repair_or_state_edit(no_add_api):
    raw = {'operations': [deepcopy(SOURCE['recorded_final_contract']['operations'][0])],
           'speech_act': 'CHANGE', 'control': {'action': 'continue'}, 'reply': 'Ребёнок добавлен.'}
    model = ClosedRecordedLLM([raw])
    client, repository, sid, token = await no_add_api(model, 2)
    before = await _snapshot(client, sid, token)
    run, after = await message(client, sid, token, 'Никого не добавляй. Добавь ребёнка четырёх лет.')
    assert run['status'] == 'completed' and not run.get('error')
    assert after['trip'] == before['trip'] and after['state_version'] == before['state_version']
    assert len(model.calls) == 1 and model.unavailable_repairs == 0
    assert after['pending_question']['field'] == 'party.addition'
    reply = await _reply(client, sid, token, run)
    diagnostic = {'pending_question': after['pending_question'], 'reply': reply,
        'result': run['result']['result'], 'trip_unchanged': after['trip'] == before['trip'],
        'version_unchanged': after['state_version'] == before['state_version'],
        'model_adapter_calls': len(model.calls), 'external_model_calls': 0, 'external_travel_calls': 0}
    path = ROOT / '.local/evaluation/after80-20261028/no-add-r16-conflict-diagnostic.json'
    path.write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding='utf-8')
    assert 'добав' in reply.lower() and 'состав' in reply.lower()
    assert run['result']['result']['model_reply'] is None
    assert await repository.list_orders(sid) == []
    assert not any(event['event_type'] == 'TOOL_CALL_STARTED' for event in await repository.list_events(sid))
