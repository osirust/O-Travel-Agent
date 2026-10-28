"""Exact 079/T5 saved state and hostile cached final JSON, isolated PG only."""
import json
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import text
from test_answering import answer
from test_api import StubLLM, _session
from test_duration_grounding_pg import message
from test_return_preservation_pg import return_api  # noqa: F401
from test_slot_continuation import _reply

FIXTURE=Path(__file__).parents[1]/'fixtures/regression/readonly-now-prefix-079-20261028.json'


async def quote_count(store,sid):
    async with store.engine.connect()as connection:
        return (await connection.execute(text('SELECT COUNT(*) FROM quotes WHERE session_id=:sid'),{'sid':sid})).scalar_one()


@pytest.mark.asyncio
async def test_exact079_now_fact_query_rejects_mutating_raw_readonly_contract_without_state_or_room_changes(return_api):  # noqa: F811
    fixture=json.loads(FIXTURE.read_text(encoding='utf-8'))
    model=StubLLM([fixture['raw_contract']])
    client,store=await return_api(model)
    sid,token,before=await _session(client,fixture['before_trip'])
    before=deepcopy(before)
    before_quotes=await quote_count(store,sid)
    run,after=await message(client,sid,token,fixture['input'])
    assert run['status']=='failed'and run['error']['code']=='INVALID_LLM_OPERATION'
    assert len(model.calls)==1 and model.calls[0]['__response_mode']=='readonly_answer_v1'
    for key in ('trip','state_version','status','waiting_reason','pending_question'):
        assert after[key]==before[key]
    assert await quote_count(store,sid)==before_quotes==0
    assert await store.list_orders(sid)==[]
    assert not any(event['event_type']=='TOOL_CALL_STARTED'for event in await store.list_events(sid))


@pytest.mark.asyncio
async def test_exact079_valid_party_rooms_selector_answers_saved_three_adults_two_children_without_mutation(return_api):  # noqa: F811
    fixture=json.loads(FIXTURE.read_text(encoding='utf-8'))
    model=StubLLM([answer('party,rooms')])
    client,store=await return_api(model)
    sid,token,before=await _session(client,fixture['before_trip'])
    run,after=await message(client,sid,token,fixture['input'])
    assert run['status']=='completed'and not run.get('error'),run.get('error')
    assert len(model.calls)==1 and model.calls[0]['__response_mode']=='readonly_answer_v1'
    assert run['result']['result']['response_source']=='readonly_answer'
    for key in ('trip','state_version','status','waiting_reason','pending_question'):
        assert after[key]==before[key]
    reply=(await _reply(client,sid,token,run)).casefold()
    assert '3 взрослых'in reply and '2 ребёнка'in reply
    assert 'номер №1: взрослый №1, взрослый №2, ребёнок №1'in reply
    assert await quote_count(store,sid)==0 and await store.list_orders(sid)==[]
    assert not any(event['event_type']=='TOOL_CALL_STARTED'for event in await store.list_events(sid))
