"""Current actual property replay; no network/image fetch or price mutation."""
import json
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from otravel.api import create_app
from otravel.config import Settings
from otravel.media import (
    MAX_PHOTOS,
    MEDIA_CSP_SOURCES,
    MediaResolver,
    safe_media_url,
    safe_serp_media_url,
)
from otravel.planning import _digest, assemble_plan, compare_plans
from otravel.providers import TravelGateway

FIXTURE=Path(__file__).parents[1]/'fixtures/regression/serp-property-photos-current-20261028.json'


@pytest.fixture(autouse=True)
def isolated_captured_redaction_registry(monkeypatch):
    # The R20 golden was captured in a fresh process. A prior test's synthetic
    # short credential must not change its text fields; restore it afterwards.
    monkeypatch.setattr('otravel.providers._SECRETS',set())


def source():
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


async def searched(row=None):
    fixture=source()
    instant=[datetime.fromisoformat(fixture['replay_clock'])]
    calls=[]
    def response(request):
        calls.append(request.url.host)
        assert request.method=='GET'and request.url.host=='serpapi.com'and request.url.path=='/search.json'
        assert len(calls)==1,'No image/content/second supplier request is allowed'
        return httpx.Response(200,json={'search_metadata':{'status':'Success','processed_at':fixture['provenance']['source_as_of']},
            'properties':[deepcopy(row or fixture['property'])]})
    gateway=TravelGateway({'SERPAPI_API_KEY':'controlled-only','SERPAPI_FREE_TIER_CONFIRMED':True,
        'clock':lambda:instant[0],'http_transport':httpx.MockTransport(response)})
    offers=await gateway.search_hotels(fixture['provenance']['request'],'serpapi')
    return gateway,offers,calls,instant


@pytest.mark.asyncio
async def test_exact_captured_property_gains_same_bound_media_dto_without_changing_economic_offer():
    fixture=source()
    gateway,offers,calls,_=await searched()
    try:
        assert offers==[fixture['economic_offer_before']]
        before=deepcopy(offers)
        result=await MediaResolver(gateway).resolve_plan({'offers':offers})
        photo=result['items'][0]['media']
        assert photo['isActualPropertyPhoto']and photo['source']=='SERPAPI'
        assert photo['hotelId']==photo['sourceHotelId']==fixture['property']['property_token']
        assert 0<len(photo['gallery'])<=MAX_PHOTOS
        assert all(safe_serp_media_url(image['url'])for image in photo['gallery'])
        assert offers==before and _digest(offers)==_digest(before)
        assert calls==['serpapi.com']
    finally:
        await gateway.close()


@pytest.mark.parametrize('value',[
    'javascript:alert(1)','http://lh3.googleusercontent.com/grass-cs/abcdefghijk',
    'https://127.0.0.1/grass-cs/abcdefghijk','https://10.0.0.1/grass-cs/abcdefghijk',
    'https://lh3.googleusercontent.com.evil.test/grass-cs/abcdefghijk',
    'https://user:pass@lh3.googleusercontent.com/grass-cs/abcdefghijk',
    'https://lh3.googleusercontent.com:444/grass-cs/abcdefghijk',
    'https://lh3.googleusercontent.com/other/abcdefghijk',
    'https://lh3.googleusercontent.com/grass-cs/abcdefghijk?url=https://evil.test',
    'https://lh3.googleusercontent.com/grass-cs/abcdefghijk#fragment',
    'https://lh3.googleusercontent.com/grass-cs/abcd%0aefghijk',
])
def test_serp_url_policy_rejects_unsafe_or_unobserved_sources(value):
    assert safe_serp_media_url(value)is None


def test_google_policy_does_not_broaden_liteapi_urls_or_add_wildcard_csp():
    fixture=source()
    google=next(image['thumbnail']for image in fixture['property']['images']if safe_serp_media_url(image.get('thumbnail')))
    assert safe_serp_media_url(google)==google and safe_media_url(google)is None
    assert 'https://lh3.googleusercontent.com'in MEDIA_CSP_SOURCES
    assert all('*'not in value for value in MEDIA_CSP_SOURCES)


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation',['foreign_token','foreign_offer','conflicting_binding','wrong_kind','expired','restart','cache_copy'])
async def test_foreign_missing_or_stale_evidence_has_no_photo_and_never_looks_up_or_fetches(mutation):
    gateway,offers,calls,instant=await searched()
    try:
        selected=deepcopy(offers[0])
        if mutation=='foreign_token':selected['details']['provider_data']['property_token']='other-property'
        elif mutation=='foreign_offer':selected['offer_id']='serpapi:foreign-offer'
        elif mutation=='conflicting_binding':selected['details']['binding']={'hotel_id':'other-property'}
        elif mutation=='wrong_kind':selected['source_type']='DEMO'
        elif mutation=='expired':instant[0]+=timedelta(hours=7)
        elif mutation=='restart':gateway._serp_media_evidence.clear()
        else:
            content=gateway.serpapi_property_media(selected['offer_id'],selected['details']['provider_data']['property_token'])
            content['id']='other-property'
            assert gateway.serpapi_property_media(selected['offer_id'],selected['details']['provider_data']['property_token'])['id']!='other-property'
            return
        result=await MediaResolver(gateway).resolve_plan({'offers':[selected]})
        assert result['items'][0]['media']['isActualPropertyPhoto']is False
        assert result['items'][0]['media']['gallery']==[]and calls==['serpapi.com']
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_dedup_bounds_and_explicit_foreign_image_identity_are_enforced(monkeypatch):
    row=deepcopy(source()['property'])
    image=next(x for x in row['images']if safe_serp_media_url(x.get('thumbnail')))
    row['images']=[deepcopy(image)]*30+[{'original_image':image['thumbnail'],'sourceHotelId':'other-property'}]
    gateway,offers,calls,_=await searched(row)
    try:
        result=await MediaResolver(gateway).resolve_plan({'offers':offers})
        assert len(result['items'][0]['media']['gallery'])==1
        monkeypatch.setattr('otravel.media.MAX_SERP_CACHED_HOTELS',3)
        for i in range(6):
            gateway._remember_serpapi_media({'offer_id':f'offer-{i}'},row)
        assert len(gateway._serp_media_evidence)==3
        assert gateway.serpapi_property_media(offers[0]['offer_id'],row['property_token'])is None
        assert calls==['serpapi.com']
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_default_cache_retains_two_twenty_offer_searches_including_the_first_selected_property():
    from otravel.media import MAX_SERP_CACHED_HOTELS
    row=deepcopy(source()['property'])
    gateway,offers,calls,_=await searched(row)
    try:
        assert MAX_SERP_CACHED_HOTELS==256
        for i in range(40):
            gateway._remember_serpapi_media({'offer_id':f'controlled-search-offer-{i}'},row)
        assert len(gateway._serp_media_evidence)==41
        assert gateway.serpapi_property_media(offers[0]['offer_id'],row['property_token'])is not None
        assert all(len(entry['content']['hotelImages'])<=MAX_PHOTOS for entry in gateway._serp_media_evidence.values())
        assert calls==['serpapi.com']
    finally:
        await gateway.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('field',['sourceHotelId','property_token','hotel_id'])
async def test_explicit_foreign_property_identity_on_all_images_never_becomes_actual_property_photo(field):
    row=deepcopy(source()['property'])
    for image in row['images']:
        image[field]='other-property'
    gateway,offers,calls,_=await searched(row)
    try:
        result=await MediaResolver(gateway).resolve_plan({'offers':offers})
        assert result['items'][0]['media']['isActualPropertyPhoto']is False
        assert result['items'][0]['media']['gallery']==[]and calls==['serpapi.com']
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_saved_partial_plan_media_api_is_owner_bound_and_photo_changes_leave_all_price_snapshots_unchanged():
    from test_media_api import MediaStore
    gateway,offers,calls,_=await searched()
    try:
        store=MediaStore()
        store.partial=True
        bound=deepcopy(offers)
        bound[0]['details']['binding']={'trip_id':'trip','state_version':3,'stay_id':'s1','hotel_id':source()['property']['property_token'],
            'city':'Стамбул','checkin':'2026-11-10','checkout':'2026-11-13','room_ids':['r1'],
            'room_assignments':[{'room_id':'r1','traveler_ids':['a1','a2']}]}
        store.plan['offers']=bound
        state={'trip_id':'trip','state_version':3,'travelers':[{'id':'a1','type':'adult'},{'id':'a2','type':'adult'}],
            'stays':[{'id':'s1','city':'Стамбул','check_in':'2026-11-10','check_out':'2026-11-13','rooms':[{'id':'r1','traveler_ids':['a1','a2']}]}],
            'search_policy':{'mode':'HYBRID','guest_nationality':'RU'}}
        plan_before=assemble_plan(state,deepcopy(bound))
        assert len(plan_before['components'])==1 and len(plan_before['source_snapshots'])==1
        frozen=deepcopy(store.plan)
        app=create_app(Settings('unused','groq',1,8000,0,1),store=store,llm=SimpleNamespace(),travel=gateway,media=MediaResolver(gateway))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://controlled')as client:
            path='/api/v1/sessions/own/media?plan_id=priced&state_version=3'
            assert(await client.get(path)).status_code==401
            assert(await client.get(path,headers={'Authorization':'Bearer foreign'})).status_code==403
            response=await client.get(path,headers={'Authorization':'Bearer private-owner'})
        assert response.status_code==200 and response.json()['items'][0]['media']['isActualPropertyPhoto']
        assert store.plan==frozen and offers==[source()['economic_offer_before']]
        plan_after=assemble_plan(state,deepcopy(bound))
        assert plan_before==plan_after
        assert plan_before.get('snapshot_hash')==plan_after.get('snapshot_hash')
        assert plan_before.get('source_snapshots')==plan_after.get('source_snapshots')
        assert compare_plans(plan_before,plan_after)['requires_new_confirmation']is False
        assert calls==['serpapi.com']
    finally:
        await gateway.close()
