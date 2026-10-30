import pytest
from test_api import StubLLM, _session
from test_api import api_factory as nonanswer_api_factory
from test_harness_saved_answers import seed_pending
from test_slot_continuation import _message, _reply

from otravel.conversation_followup import nonanswer_pending_question
from otravel.domain import sample_trip

api_factory=nonanswer_api_factory


@pytest.mark.parametrize('field,text', [('stay.rooms','да'),('stay.check_in','давай'),
    ('search.guest_nationality','сохряняем'),('budget.amount_minor','да'),('traveler.age','да')])
def test_assent_to_required_fact_reasks_without_guessing(field,text):
    state=sample_trip().model_dump(mode='json')
    pending={'field':field,'reason':'clarification','context':{}}
    assert nonanswer_pending_question(pending,text,state)


@pytest.mark.parametrize('context', [{'confirmation_required':True}, {'date_proposal':{'field':'stay.check_in'}},
    {'room_reallocation_required':True}, {'budget_scope_ambiguous':True}])
def test_existing_confirmation_handlers_keep_their_yes(context):
    assert nonanswer_pending_question({'field':'stay.check_in','reason':'clarification','context':context},'да',{}) is None


@pytest.mark.parametrize('text',['да, один номер','давай 28 октября','не сохраняем','подожди','продолжай','сохряняем и добавь ребёнка'])
def test_compound_facts_and_controls_are_not_swallowed(text):
    assert nonanswer_pending_question({'field':'stay.rooms','reason':'clarification'},text,{}) is None


@pytest.mark.asyncio
async def test_three_unfilled_room_assents_then_number_uses_no_model_and_preserves_dates(api_factory):
    model=StubLLM()
    trip=sample_trip().model_dump(mode='json')
    trip['stays']=trip['stays'][:1]
    trip['stays'][0]['rooms']=[]
    trip['legs']=[]
    async with api_factory(model) as (client,_):
        sid,token,initial=await _session(client,trip)
        store=client._transport.app.state.store
        target=initial['trip']['stays'][0]['id']
        await seed_pending(store,sid,'stay.rooms',target=target)
        for text in ('да','да','сохряняем'):
            run,after=await _message(client,sid,token,text)
            assert after['trip']==initial['trip'] and not model.calls
            assert after['pending_question']['field']=='stay.rooms' and after['pending_question']['target_id']==target
            assert 'число номеров' in await _reply(client,sid,token,run)
        _,filled=await _message(client,sid,token,'1')
        assert not model.calls and len(filled['trip']['stays'][0]['rooms'])==1
        for field in ('city','check_in','check_out'):
            assert filled['trip']['stays'][0][field]==initial['trip']['stays'][0][field]
