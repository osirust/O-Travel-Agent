"""Real isolated PG/ASGI with controlled model contracts, no vendor HTTP."""
from copy import deepcopy
from uuid import uuid4

import pytest
from test_api import StubLLM,_headers,_session,_snapshot,_wait,_plans
from test_api import api_factory as original_api_factory

from otravel.domain import Constraint,sample_trip
from otravel.financial_requirements import TEMPORAL_KEY

api_factory=original_api_factory


async def message(client,sid,token,text):
    before=await _snapshot(client,sid,token)
    response=await client.post(f'/api/v1/sessions/{sid}/messages',headers=_headers(token),json={
        'text':text,'client_message_id':str(uuid4()),'expected_version':before['state_version']})
    assert response.status_code==202,response.text
    run=await _wait(client,response.json()['run_id'],token)
    assert run['status']=='completed',run.get('error')
    return run,await _snapshot(client,sid,token)


@pytest.mark.asyncio
async def test_literal_refund_deadline_is_persistent_revision_and_invalidates_old_quote(api_factory):
    model=StubLLM([{'speech_act':'CHANGE','control':{'action':'continue'},'operations':[],
        'reply':'Условие принято.','unknowns':[],'conflicts':[]}])
    async with api_factory(model) as (client,_):
        sid,token,initial=await _session(client,sample_trip().model_dump(mode='json'))
        response=await client.post(f'/api/v1/sessions/{sid}/commands',headers=_headers(token),json={
            'client_message_id':str(uuid4()),'expected_version':0,'operations':[],'search':True})
        assert response.status_code==202
        initial_run=await _wait(client,response.json()['run_id'],token)
        plans=_plans(initial_run);assert plans
        old=await _snapshot(client,sid,token)
        preview=await client.post(f'/api/v1/sessions/{sid}/checkout/preview',headers=_headers(token),json={
            'plan_id':plans[0]['plan_id'],'state_version':old['state_version']})
        assert preview.status_code==200,preview.text
        quote=preview.json()
        run,after=await message(client,sid,token,'Мне нужен именно refundable до дня заезда, а не до недели до него.')
        assert after['state_version']==old['state_version']+1
        assert any(c['key']==TEMPORAL_KEY and c['value'] is True and c['hard'] is True for c in after['trip']['constraints'])
        assert not _plans(run)
        assert (await client._transport.app.state.store.get_session(sid))['state']==after['trip']
        denied=await client.post(f'/api/v1/sessions/{sid}/checkout/confirm',headers=_headers(token),json={
            'quote_id':quote['quote_id'],'quote_version':quote['quote_version'],'state_version':old['state_version'],
            'confirmed':True,'idempotency_key':'old-refund-quote-must-fail'})
        assert denied.status_code==409,denied.text
        denied_preview=await client.post(f'/api/v1/sessions/{sid}/checkout/preview',headers=_headers(token),json={
            'plan_id':plans[0]['plan_id'],'state_version':after['state_version']})
        assert denied_preview.status_code in {409,422},denied_preview.text
        assert await client._transport.app.state.store.list_orders(sid)==[]
        assert len(model.calls)==1


@pytest.mark.asyncio
@pytest.mark.parametrize('text,operations',[
    ('Отель с бесплатной отменой в любое время, но невозвратный самый дешёвый.',[
        {'type':'set_constraint','value':{'key':'refundable','value':False,'hard':True}}]),
    ('Только прямой рейс. Предложи пересадку, если дешевле.',[
        {'type':'set_constraint','value':{'key':'nonstop','value':True,'hard':False}}]),
])
async def test_captured_current_conflicts_preserve_every_field_and_do_not_search(api_factory,text,operations):
    model=StubLLM([{'speech_act':'CHANGE','control':{'action':'continue'},'operations':operations,
        'reply':'Буду искать невозвратный или с пересадкой.','unknowns':[],'conflicts':[]}])
    async with api_factory(model) as (client,_):
        sid,token,before=await _session(client,sample_trip().model_dump(mode='json'))
        run,after=await message(client,sid,token,text)
        assert after['trip']==before['trip'] and after['state_version']==before['state_version']
        assert after['waiting_reason']=='clarification'
        assert '?' in after['pending_question']['text']
        assert not _plans(run) and run['result'].get('changes',[])==[]
        assert await client._transport.app.state.store.list_orders(sid)==[]
        assert len(model.calls)==1


@pytest.mark.asyncio
async def test_model_clear_is_suppressed_but_current_waiver_removes_unknown_key(api_factory):
    state=sample_trip();state.constraints.append(Constraint(key=TEMPORAL_KEY,value=True))
    raw={'type':'clear_constraint','value':{'key':TEMPORAL_KEY}}
    model=StubLLM([
        {'speech_act':'CHANGE','control':{'action':'continue'},'operations':[deepcopy(raw)],'reply':'Продолжаем.'},
        {'speech_act':'CHANGE','control':{'action':'continue'},'operations':[
            {'type':'set_constraint','value':{'key':TEMPORAL_KEY,'value':False,'hard':True}}],'reply':'Снимаю условие.'},
    ])
    async with api_factory(model) as (client,_):
        sid,token,before=await _session(client,state.model_dump(mode='json'))
        _,protected=await message(client,sid,token,'Продолжим собирать поездку.')
        assert protected['trip']==before['trip'] and protected['state_version']==before['state_version']
        _,waived=await message(client,sid,token,'Мне больше не нужна бесплатная отмена до дня заезда, сними это условие.')
        assert waived['state_version']==before['state_version']+1
        assert not any(c['key']==TEMPORAL_KEY for c in waived['trip']['constraints'])
        assert await client._transport.app.state.store.list_orders(sid)==[]


@pytest.mark.asyncio
async def test_current_named_stay_refuses_model_wrong_stay_then_accepts_exact_current_scope(api_factory):
    state=sample_trip();first,second=state.stays
    state.constraints.extend(Constraint(key=TEMPORAL_KEY,value=True,stay_id=s.id) for s in state.stays)
    def contract(sid):
        return {'speech_act':'CHANGE','control':{'action':'continue'},'operations':[
            {'type':'clear_constraint','value':{'key':TEMPORAL_KEY,'stay_id':sid}}],'reply':'Условие снято.'}
    model=StubLLM([contract(second.id),contract(first.id)])
    async with api_factory(model) as (client,_):
        sid,token,before=await _session(client,state.model_dump(mode='json'))
        text='Сними условие бесплатной отмены до заезда в Стамбуле.'
        _,protected=await message(client,sid,token,text)
        assert protected['trip']==before['trip'] and protected['state_version']==before['state_version']
        assert protected['waiting_reason']=='clarification'
        _,correct=await message(client,sid,token,text)
        assert correct['state_version']==before['state_version']+1
        assert not any(c['key']==TEMPORAL_KEY and c.get('stay_id')==first.id for c in correct['trip']['constraints'])
        assert any(c['key']==TEMPORAL_KEY and c.get('stay_id')==second.id for c in correct['trip']['constraints'])
        assert await client._transport.app.state.store.list_orders(sid)==[]

