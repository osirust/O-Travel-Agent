"""Whole API/worker/catalogue transitions, with external HTTP controlled."""
import os
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest
from test_api import StubLLM, _headers, _session, _snapshot, _wait
from test_place_directory import CAPITAL, HOMONYM
from test_slot_continuation import _message, _reply

from otravel.api import create_app
from otravel.config import Settings
from otravel.geo_catalog import GeoCatalog
from otravel.place_directory import PlaceDirectory
from otravel.store import Store


class Countries:
    def __init__(self):
        self.calls=0
    async def list_countries(self):
        self.calls+=1
        return [{'country_iso2':'AR','name':'Argentina'},{'country_iso2':'GB','name':'United Kingdom'}]
    async def close(self):
        pass


@asynccontextmanager
async def world_app(monkeypatch,model):
    wires=[]
    def handler(request):
        wires.append(request)
        assert request.url.host=='geocoding-api.open-meteo.com'
        if request.url.path=='/v1/search':
            return httpx.Response(200,json={'results':[CAPITAL,HOMONYM]})
        assert request.url.path=='/v1/get' and request.url.params['id']=='3435910'
        return httpx.Response(200,json={**CAPITAL,'name':'Buenos Aires','country':'Argentina'})
    settings=Settings(database_url=os.environ['TEST_DATABASE_URL'],llm_mode='groq',provider_timeout=1,
        max_message_chars=8000,max_llm_calls_per_day=1000000,max_llm_calls_per_run=1,
        default_search_mode='HYBRID',default_hotel_provider='liteapi',default_flight_provider='duffel',
        dynamic_geography_enabled=True)
    store=Store(settings.database_url)
    catalog=GeoCatalog(store.engine)
    directory=PlaceDirectory(cache=catalog,transport=httpx.MockTransport(handler))
    travel=Countries()
    app=create_app(settings,store=store,llm=model,travel=travel,geo_catalog=catalog,place_directory=directory)
    # Append-only test DB contains unrelated queue fixtures. Exercise scheduling
    # for this session only; recovery/maintenance has its own PG acceptance.
    async def no_recovery():
        pass
    monkeypatch.setattr(app.state.harness,'start',no_recovery)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        yield client,app,wires,travel


@pytest.mark.asyncio
async def test_country_nlu_city_query_homonym_choice_and_persistent_selected_reference(monkeypatch):
    model=StubLLM([{'operations':[{'type':'resolve_place','target_id':None,'value':{
        'role':'destination','kind':'country','query':'Argentina','country_iso2':'AR','stay_id':'new-stay-1'}}],
        'speech_act':'CHANGE','control':{'action':'none'}}])
    async with world_app(monkeypatch,model) as (client,app,wires,travel):
        sid,token,initial=await _session(client)
        accepted=await client.post(f'/api/v1/sessions/{sid}/messages',headers=_headers(token),json={
            'client_message_id':str(uuid4()),'expected_version':0,'text':'Давай начнём собирать поездку: направление Аргентина'})
        checked=await _wait(client,accepted.json()['run_id'],token)
        assert checked['status']=='completed',checked.get('error')
        country=await _snapshot(client,sid,token)
        assert len(model.calls)==1 and travel.calls==1 and not wires
        assert country['trip']==initial['trip'] and country['pending_question']['field']=='place.city_query'
        _,cities=await _message(client,sid,token,'Буэнос-Айрес')
        assert len(model.calls)==1 and len(wires)<=1
        before_choice_wires=len(wires)
        assert cities['pending_question']['field']=='place.resolution'
        assert len(cities['pending_question']['context']['place_resolution']['candidates'])==2
        _,unselected=await _message(client,sid,token,'да')
        assert unselected['trip']==initial['trip'] and len(wires)==before_choice_wires and len(model.calls)==1
        _,selected=await _message(client,sid,token,'первый')
        assert len(model.calls)==1 and before_choice_wires<=len(wires)<=before_choice_wires+1
        assert selected['state_version']==1
        stay=selected['trip']['stays'][0]
        assert stay['city']=='Buenos Aires' and stay['place_id']=='geo:openmeteo:3435910'
        ref=selected['trip']['location_refs'][stay['place_id']]
        assert ref['country_iso2']=='AR'
        persisted=await app.state.geo_catalog.verify_places(selected['trip']['location_refs'])
        assert persisted[stay['place_id']].canonical_name=='Buenos Aires'
        assert selected['pending_question']['field'] in {'origin.city','stay.check_in'}
        if selected['pending_question']['field']=='stay.check_in':
            assert selected['pending_question']['target_id']==stay['id']
        assert await app.state.store.list_orders(sid)==[]


@pytest.mark.asyncio
async def test_typed_resolve_and_select_queue_execute_with_real_worker(monkeypatch):
    model=StubLLM()
    async with world_app(monkeypatch,model) as (client,_app,wires,travel):
        sid,token,_=await _session(client)
        body={'role':'destination','kind':'city','query':'Buenos Aires','stay_id':'new-stay-1',
            'country_iso2':'AR','expected_version':0,'client_message_id':str(uuid4())}
        response=await client.post(f'/api/v1/sessions/{sid}/places/resolve',headers=_headers(token),json=body)
        assert response.status_code==202
        run=await _wait(client,response.json()['run_id'],token)
        assert run['status']=='completed'
        pending=(await _snapshot(client,sid,token))['pending_question']['context']['place_resolution']
        choice={'resolution_id':pending['resolution_id'],'candidate_id':pending['candidates'][0]['candidate_id'],
            'expected_version':0,'client_message_id':str(uuid4())}
        response=await client.post(f'/api/v1/sessions/{sid}/places/select',headers=_headers(token),json=choice)
        assert response.status_code==202
        run=await _wait(client,response.json()['run_id'],token)
        assert run['status']=='completed'
        state=await _snapshot(client,sid,token)
        assert state['trip']['stays'][0]['place_id']=='geo:openmeteo:3435910'
        assert not model.calls and travel.calls==0 and len(wires)<=2


@pytest.mark.asyncio
async def test_complete_literal_country_wish_needs_no_model_and_never_picks_capital(monkeypatch):
    model=StubLLM([])
    async with world_app(monkeypatch,model) as (client,_app,wires,travel):
        sid,token,initial=await _session(client)
        run,state=await _message(client,sid,token,'хочу в аргентину наверное')
        assert run['status']=='completed'
        assert state['trip']==initial['trip'] and state['state_version']==0
        assert state['pending_question']['field']=='place.city_query'
        assert not model.calls and not wires and travel.calls==1


@pytest.mark.asyncio
@pytest.mark.parametrize('speech_act',['ASK','PROPOSE'])
async def test_informational_place_operation_cannot_select_or_change_trip(monkeypatch,speech_act):
    model=StubLLM([{'operations':[{'type':'resolve_place','target_id':None,'value':{
        'role':'destination','kind':'country','query':'Argentina','country_iso2':'AR','stay_id':'new-stay-1'}}],
        'speech_act':speech_act,'control':{'action':'none'},'reply':'Расскажу об этом месте.'}])
    async with world_app(monkeypatch,model) as (client,_app,wires,travel):
        sid,token,initial=await _session(client)
        run,state=await _message(client,sid,token,'Аргентина')
        assert run['status']=='completed',run.get('error')
        assert state['trip']==initial['trip'] and state['state_version']==0
        assert not wires and not travel.calls and len(model.calls)==1
        assert 'выбрать это направление' in await _reply(client,sid,token,run)


@pytest.mark.asyncio
async def test_informational_place_wait_keeps_explicit_pause_and_does_no_lookup(monkeypatch):
    model=StubLLM([{'operations':[{'type':'resolve_place','target_id':None,'value':{
        'role':'destination','kind':'country','query':'Argentina','country_iso2':'AR','stay_id':'new-stay-1'}}],
        'speech_act':'ASK','control':{'action':'wait'}}])
    async with world_app(monkeypatch,model) as (client,_app,wires,travel):
        sid,token,_initial=await _session(client)
        _,state=await _message(client,sid,token,'Аргентина, подожди, я ещё дополню')
        assert state['waiting_reason']=='user_pause' and not wires and not travel.calls
        assert not state['trip']['stays']


@pytest.mark.asyncio
async def test_informational_place_cancel_stops_processing_without_lookup(monkeypatch):
    model=StubLLM([{'operations':[{'type':'resolve_place','target_id':None,'value':{
        'role':'destination','kind':'country','query':'Argentina','country_iso2':'AR','stay_id':'new-stay-1'}}],
        'speech_act':'ASK','control':{'action':'cancel'}}])
    async with world_app(monkeypatch,model) as (client,_app,wires,travel):
        sid,token,initial=await _session(client)
        accepted=await client.post(f'/api/v1/sessions/{sid}/messages',headers=_headers(token),json={
            'client_message_id':str(uuid4()),'expected_version':0,'text':'Аргентина, больше не обрабатывай этот запрос'})
        run=await _wait(client,accepted.json()['run_id'],token)
        state=await _snapshot(client,sid,token)
        assert run['status']=='cancelled' and not wires and not travel.calls
        assert state['trip']['stays']==initial['trip']['stays']
