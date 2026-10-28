"""Exact captured room4 age change; real isolated PG, no real model/travel."""
import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from test_api import StubLLM, _headers, _session, _snapshot, _wait
from test_duration_grounding_pg import message
from test_return_preservation_pg import return_api  # noqa: F401

FIXTURE=Path(__file__).parents[1]/'fixtures/regression/room4-age-current-078-20261028.json'


@pytest.mark.asyncio
async def test_room4_explicit_age_replacement_bypasses_wrong_raw_ask_and_preserves_other_child_and_room_refs(return_api):  # noqa: F811
    fixture=json.loads(FIXTURE.read_text(encoding='utf-8'))
    model=StubLLM([fixture['raw_contract']])
    client,store=await return_api(model)
    sid,token,initial=await _session(client,fixture['before_trip'])
    before=deepcopy(initial['trip'])
    run,after=await message(client,sid,token,fixture['input'])
    assert run['status']=='completed' and not run.get('error'),run.get('error')
    people={person['id']:person for person in after['trip']['travelers']}
    assert people['new-traveler-10']['age']==10
    assert people['new-traveler-8']['age']==9
    expected=deepcopy(before)
    next(person for person in expected['travelers']if person['id']=='new-traveler-10')['age']=10
    expected['state_version']=after['state_version']
    assert after['trip']==expected
    assert after['state_version']==initial['state_version']+1
    assert model.calls==[]
    assert await store.list_orders(sid)==[]
    events=await store.list_events(sid)
    assert not any(event['event_type']=='MODEL_CALL_STARTED'for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize('typed',[False,True])
async def test_valid_existing_model_and_typed_age_updates_keep_their_original_path(return_api,typed):  # noqa: F811
    fixture=json.loads(FIXTURE.read_text(encoding='utf-8'))
    operation={'type':'update_traveler','target_id':'new-traveler-10','value':{'age':10}}
    model=StubLLM([]if typed else[{'operations':[operation],'control':{'action':'continue'},'speech_act':'CHANGE'}])
    client,store=await return_api(model)
    sid,token,initial=await _session(client,fixture['before_trip'])
    if typed:
        response=await client.post(f'/api/v1/sessions/{sid}/commands',headers=_headers(token),json={
            'client_message_id':str(uuid4()),'expected_version':initial['state_version'],'search':False,'operations':[operation]})
        assert response.status_code==202
        run=await _wait(client,response.json()['run_id'],token)
        after=await _snapshot(client,sid,token)
    else:
        run,after=await message(client,sid,token,'Исправь возраст нового ребёнка на десять лет; используй уже сохранённого участника.')
    assert run['status']=='completed'and not run.get('error'),run.get('error')
    assert next(p for p in after['trip']['travelers']if p['id']=='new-traveler-10')['age']==10
    assert next(p for p in after['trip']['travelers']if p['id']=='new-traveler-8')['age']==9
    assert len(model.calls)==(0 if typed else 1)
    assert after['state_version']==initial['state_version']+1
    assert await store.list_orders(sid)==[]
