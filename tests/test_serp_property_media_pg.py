"""Owner-bound asynchronous standalone live result, real PG/cached HTTP only."""
import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from test_api import StubLLM, _headers, _session, _snapshot, _wait
from test_return_preservation_pg import return_api  # noqa: F401

from otravel.planning import _digest
from otravel.providers import TravelGateway

FIXTURE=Path(__file__).parents[1]/'fixtures/regression/serp-property-photos-current-20261028.json'


@pytest.fixture(autouse=True)
def isolated_captured_redaction_registry(monkeypatch):
    # Match the fresh R20 golden process, with automatic prior-set restoration.
    monkeypatch.setattr('otravel.providers._SECRETS',set())


@pytest.mark.asyncio
async def test_standalone_serp_result_exposes_live_media_sidecar_outside_unchanged_economic_offers_and_owner_run(return_api):  # noqa: F811
    fixture=json.loads(FIXTURE.read_text(encoding='utf-8'))
    calls=[]
    def response(request):
        calls.append(request.url.host)
        assert len(calls)==1 and request.method=='GET'and request.url.host=='serpapi.com'
        return httpx.Response(200,json={'search_metadata':{'status':'Success','processed_at':fixture['provenance']['source_as_of']},'properties':[fixture['property']]})
    gateway=TravelGateway({'SERPAPI_API_KEY':'controlled-only','SERPAPI_FREE_TIER_CONFIRMED':True,
        'clock':lambda:datetime.fromisoformat(fixture['replay_clock']),'http_transport':httpx.MockTransport(response)})
    model=StubLLM([])
    client,store=await return_api(model,travel=gateway)
    sid,token,before=await _session(client,{})
    accepted=await client.post(f'/api/v1/sessions/{sid}/live/search',headers=_headers(token),json={
        'kind':'hotels','provider':'serpapi','request':fixture['provenance']['request']})
    assert accepted.status_code==202
    run=await _wait(client,accepted.json()['run_id'],token)
    assert run['status']=='completed'and not run.get('error'),run.get('error')
    result=run['result']['result']
    assert result['live_offers']==[fixture['economic_offer_before']]
    assert _digest(result['live_offers'])==_digest([fixture['economic_offer_before']])
    item=result['live_media']['items'][0]
    assert item['offer_id']==result['live_offers'][0]['offer_id']
    assert item['media']['isActualPropertyPhoto']and item['media']['source']=='SERPAPI'
    assert item['media']['sourceHotelId']==fixture['property']['property_token']
    assert result['live_offers'][0]['source_type']=='LIVE_INDICATIVE'
    assert result['live_offers'][0]['details']['availability_verified']is False
    assert result['plans']==[] and not model.calls and calls==['serpapi.com']
    after=await _snapshot(client,sid,token)
    assert after['trip']==before['trip']and after['state_version']==before['state_version']
    path=f'/api/v1/runs/{run["run_id"]}'
    assert(await client.get(path)).status_code==401
    assert(await client.get(path,headers=_headers('other-owner'))).status_code==403
    assert await store.list_orders(sid)==[]
    events=await store.list_events(sid)
    assert sum(event['event_type']=='TOOL_CALL_STARTED'for event in events)==1
    assert not any(event['event_type']=='MODEL_CALL_STARTED'for event in events)
