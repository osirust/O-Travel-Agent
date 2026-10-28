import json
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator
from test_groq_transport_provider import completion, gateway

from otravel.answering import READONLY_MODE
from otravel.domain import TripState
from otravel.llm_contract import build_operation_schema
from otravel.providers import ProviderError


def context():
    state=TripState().model_dump(mode='json')
    return {'trip':state,'current_message':'Хочу в Аргентину',
        '__response_schema':build_operation_schema(state,dynamic_places=True)}


def result():
    return {'operations':[{'type':'resolve_place','target_id':None,'value':{
        'role':'destination','kind':'country','query':'Argentina','stay_id':'new-stay-1','country_iso2':'AR'}}],
        'control':{'action':'none'},'reply':'Уточним город в Аргентине.','unknowns':[],
        'conflicts':[],'speech_act':'CHANGE','information_subject':None}


def wire(value):
    value=deepcopy(value)
    value['operations']=[{'op':op['type']+('@'+op['target_id'] if op['target_id'] else ''),
        'value_json':json.dumps(op['value'],ensure_ascii=False)} for op in value['operations']]
    return value


@pytest.mark.asyncio
async def test_optin_v3_admits_only_canonical_nonmutating_place_request():
    calls=[]
    def handler(request):
        calls.append(request)
        payload=json.loads(request.content)
        schema=payload['response_format']['json_schema']['schema']
        assert Draft202012Validator(schema).is_valid(wire(result()))
        return completion(wire(result()))
    model=gateway(handler,GROQ_TRANSPORT_VERSION=3,DYNAMIC_GEOGRAPHY_ENABLED=True)
    try:
        output=await model.extract_operations(context())
        assert output==result() and len(calls)==1
        meta=output.call_metadata['structured_transport']
        assert meta['format']=='groq_strict_operations_v3' and meta['transport_version']==3
        assert meta['semantic_schema_delivery']=='trusted_place_hints_full_server_validation'
        assert meta['wire_request_body_bytes']==len(calls[0].content)
    finally:
        await model.close()


@pytest.mark.asyncio
async def test_v3_free_city_without_trusted_ref_cannot_enter_regular_stay_operation():
    wrong=result()
    wrong['operations']=[{'type':'upsert_stay','target_id':'new-stay-1','value':{'city':'Buenos Aires'}}]
    model=gateway(lambda _request:completion(wire(wrong)),GROQ_TRANSPORT_VERSION=3,DYNAMIC_GEOGRAPHY_ENABLED=True)
    try:
        with pytest.raises(ProviderError) as error:
            await model.extract_operations(context())
        assert error.value.code=='INVALID_LLM_OPERATION'
    finally:
        await model.close()


@pytest.mark.asyncio
async def test_v3_schema_is_compared_to_trusted_trip_before_any_http():
    given=context()
    given['__response_schema']['properties']['reply']['maxLength']=1001
    calls=[]
    model=gateway(lambda request:calls.append(request),GROQ_TRANSPORT_VERSION=3,DYNAMIC_GEOGRAPHY_ENABLED=True)
    try:
        with pytest.raises(ProviderError) as error:
            await model.extract_operations(given)
        assert error.value.code=='CONFIG_BLOCKED' and not calls
    finally:
        await model.close()


@pytest.mark.asyncio
async def test_v3_flag_off_is_blocked_without_implicit_profile_switch():
    calls=[]
    model=gateway(lambda request:calls.append(request),GROQ_TRANSPORT_VERSION=3,DYNAMIC_GEOGRAPHY_ENABLED=False)
    try:
        with pytest.raises(ProviderError) as error:
            await model.extract_operations(context())
        assert error.value.code=='CONFIG_BLOCKED' and not calls
    finally:
        await model.close()


@pytest.mark.asyncio
async def test_v3_readonly_remains_server_owned_noop_without_geo_queries():
    model=gateway(lambda _request:completion({'sections':['party']}),GROQ_TRANSPORT_VERSION=3,DYNAMIC_GEOGRAPHY_ENABLED=True)
    try:
        output=await model.extract_operations({'__response_mode':READONLY_MODE,'current_message':'Кто едет?'})
        assert output['operations']==[] and output['reply']=='party'
        assert output.call_metadata['structured_transport']['format']=='groq_strict_readonly_v3'
    finally:
        await model.close()
