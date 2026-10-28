from copy import deepcopy

import pytest
from test_api import StubLLM, _session
from test_api import api_factory as retention_api_factory
from test_harness_saved_answers import seed_pending
from test_slot_continuation import _message

from otravel.domain import TripState, apply_operations
from otravel.geography import parse_destination
from otravel.intent_grounding import ground_intent_operations

api_factory=retention_api_factory
TEXT='из москвы, я один взрослый и хочу в белгород в отель на неделю с 28 октября'
OPS=[{'type':'set_origin','value':{'city':'Москва'}},
    {'type':'add_traveler','value':{'id':'new-traveler-1','type':'adult'}},
    {'type':'upsert_stay','target_id':'new-stay-1','value':{'city':'Белград','check_in':'2026-10-28','requested_nights':7,'rooms':[]}}]


def pending(state):
    return {'field':'destination.city','state_version':state['state_version'],'reason':'clarification',
        'context':{'requested_destination':parse_destination('Сербия')['requested_destination']}}


def test_uncertain_city_keeps_independent_dates_for_exact_new_stop():
    before=TripState().model_dump(mode='json')
    original=deepcopy(before)
    operations,meta=ground_intent_operations(before,OPS,TEXT,pending(before))
    city_op=next(op for op in operations if op['type']=='upsert_stay')
    assert city_op['target_id']=='new-stay-1' and 'city' not in city_op['value']
    after,_=apply_operations(TripState.model_validate(before),operations)
    stay=after.stays[0]
    assert stay.city is None and stay.check_in.isoformat()=='2026-10-28'
    assert stay.check_out.isoformat()=='2026-11-04' and stay.requested_nights==7
    assert meta['pending_context']['confirmation_required'] is True
    assert before==original


@pytest.mark.asyncio
@pytest.mark.parametrize('empty_model',[False,True])
async def test_compound_dates_survive_city_confirmation_without_reasking_checkin(api_factory,empty_model):
    answer=({'operations':[],'speech_act':'ASK','information_subject':'Сербия',
        'reply':'Помогу, но нужно уточнить, какой город в Сербии вы хотите выбрать.',
        'control':{'action':'none'},'unknowns':[]} if empty_model else
        {'operations':OPS,'speech_act':'CHANGE','control':{'action':'continue'}})
    model=StubLLM([answer])
    async with api_factory(model) as (client,_):
        sid,token,_=await _session(client,{'search_policy':{'mode':'HYBRID','hotel_provider':'liteapi','flight_provider':'duffel'}})
        store=client._transport.app.state.store
        await seed_pending(store,sid,'destination.city',context=pending({'state_version':0})['context'])
        _,asked=await _message(client,sid,token,TEXT)
        assert len(model.calls)==1 and asked['pending_question']['field']=='destination.city'
        assert asked['trip']['stays'][0]['city'] is None
        assert asked['trip']['stays'][0]['check_in']=='2026-10-28'
        _,selected=await _message(client,sid,token,'в белград хочу')
        assert len(model.calls)==1
        stay=selected['trip']['stays'][0]
        assert stay['city']=='Белград' and stay['check_in']=='2026-10-28' and stay['check_out']=='2026-11-04'
        assert stay['requested_nights']==7
        assert selected['pending_question']['field']=='stay.rooms'
