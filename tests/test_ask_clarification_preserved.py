import pytest
from test_api import StubLLM, _session
from test_api import api_factory as clarification_api_factory
from test_harness_saved_answers import seed_pending
from test_slot_continuation import _message, _reply

api_factory=clarification_api_factory


@pytest.mark.asyncio
async def test_model_missing_date_question_is_not_replaced_with_faq(api_factory):
    model=StubLLM([{'speech_act':'ASK','reply':'Уточните дату заезда в Белград',
        'unknowns':['Дата заезда в Белград'],'operations':[],'control':{'action':'none'}}])
    async with api_factory(model) as (client,_):
        sid,token,initial=await _session(client,{'stays':[{'id':'stay','city':'Белград'}]})
        await seed_pending(client._transport.app.state.store,sid,'stay.check_in',target='stay')
        run,after=await _message(client,sid,token,'россии')
        reply=await _reply(client,sid,token,run)
        assert after['trip']==initial['trip'] and len(model.calls)==1
        assert after['pending_question']['field']=='stay.check_in' and after['pending_question']['target_id']=='stay'
        assert 'дату' in reply.casefold() and 'заезд' in reply.casefold() and 'Могу помочь с направлением' not in reply


@pytest.mark.asyncio
async def test_actual_information_question_does_not_turn_into_intake_edit(api_factory):
    model=StubLLM([{'speech_act':'ASK','information_subject':'Сербия','reply':'Сербия — страна.',
        'unknowns':[],'operations':[]}])
    async with api_factory(model) as (client,_):
        sid,token,initial=await _session(client)
        await seed_pending(client._transport.app.state.store,sid,'destination.city')
        _,after=await _message(client,sid,token,'Сербия — это страна?')
        assert after['trip']==initial['trip']
