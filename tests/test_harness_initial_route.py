"""Cached owner example, real isolated PG, no model or supplier HTTP."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from otravel.store import events
from test_api import StubLLM, _session
from test_api import api_factory as route_api_factory
from test_slot_continuation import _message

api_factory = route_api_factory
SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/evaluation/owner-zai009-extractions-20261026.json'


def owner_first():
    return json.loads(SOURCE.read_text())['turns'][0]


@pytest.mark.asyncio
async def test_known_literal_destination_is_saved_before_asking_for_dates(api_factory):
    case = owner_first()
    raw = deepcopy(case['cached_extraction'])
    assert not any(op['type'] == 'upsert_stay' for op in raw['operations'])
    model = StubLLM([raw])
    async with api_factory(model) as (client, _):
        repository = client._transport.app.state.store
        sid, token, before = await _session(client, {})
        run, after = await _message(client, sid, token, case['input_text'])
        trip = after['trip']
        assert trip['origin'] == 'Москва'
        assert len(trip['stays']) == 1 and trip['stays'][0]['city'] == 'Казань'
        assert trip['stays'][0]['check_in'] is None and trip['stays'][0]['check_out'] is None
        assert trip['stays'][0]['rooms'] == [] and trip['legs'] == [] and trip['budget'] is None
        assert len(trip['travelers']) == 1 and trip['travelers'][0]['type'] == 'adult'
        assert after['state_version'] == before['state_version'] + 1
        assert after['pending_question']['field'] == 'stay.check_in'
        assert after['pending_question']['target_id'] == 'new-stay-1'
        assert after['pending_question']['context']['children_known'] is True
        assert after['pending_question']['context']['children_count'] == 0
        assert len(model.calls) == 1
        async with repository.engine.connect() as conn:
            rows = (await conn.execute(select(events.c.event_type, events.c.payload).where(
                events.c.run_id == run['run_id']))).mappings().all()
        extracted = next(row['payload'] for row in rows if row['event_type'] == 'MODEL_EXTRACTION_VALIDATED')
        checked = next(row['payload'] for row in rows if row['event_type'] == 'MODEL_OPERATIONS_CHECKED')
        assert not any(op['type'] == 'upsert_stay' for op in extracted['structured_contract']['value']['operations'])
        assert any(op['type'] == 'upsert_stay' and op['value']['city'] == 'Казань'
                   for op in checked['structured_contract']['value']['operations'])
        assert checked['normalizations'][-1]['source'] == 'explicit_initial_route'


@pytest.mark.asyncio
@pytest.mark.parametrize('act', ['ASK', 'PROPOSE'])
async def test_route_complement_never_overrides_non_mutating_model_act(api_factory, act):
    case = owner_first()
    raw = deepcopy(case['cached_extraction'])
    raw.update(speech_act=act, control={'action': 'none'}, reply='Обсудим этот маршрут.')
    async with api_factory(StubLLM([raw])) as (client, _):
        sid, token, before = await _session(client, {})
        _, after = await _message(client, sid, token, case['input_text'])
        assert after['trip'] == before['trip']
        assert after['state_version'] == before['state_version']


@pytest.mark.asyncio
async def test_route_complement_never_applies_a_conflicting_batch(api_factory):
    case = owner_first()
    raw = deepcopy(case['cached_extraction'])
    raw['conflicts'] = ['Нужно уточнить противоречивый маршрут.']
    async with api_factory(StubLLM([raw])) as (client, _):
        sid, token, before = await _session(client, {})
        _, after = await _message(client, sid, token, case['input_text'])
        assert after['trip'] == before['trip']
        assert after['waiting_reason'] == 'clarification'
