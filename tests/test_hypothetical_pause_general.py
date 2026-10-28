"""Questions and hypotheses cannot grant edits merely by requesting a pause."""
from copy import deepcopy

import pytest
from test_api import StubLLM, _session, _snapshot
from test_api import api_factory as api_factory
from test_slot_continuation import _message

from otravel.domain import sample_trip
from otravel.harness import Harness, ModelResult


@pytest.mark.asyncio
@pytest.mark.parametrize('speech_act',['ASK','PROPOSE'])
@pytest.mark.parametrize('boundary',['controlled_extraction','dispatcher'])
async def test_non_place_hypothesis_with_pause_preserves_trip(monkeypatch,api_factory,speech_act,boundary):
    async def no_global_recovery(_self):
        pass
    monkeypatch.setattr(Harness,'start',no_global_recovery)
    proposed=[{'type':'set_budget','value':{'amount_minor':7000000,'currency':'RUB'}},
        {'type':'update_traveler','target_id':'child-1','value':{'age':7}}]
    answer={'operations':proposed,'speech_act':speech_act,'control':{'action':'wait'},'unknowns':[],'conflicts':[],
        'reply':'Это гипотетический вариант; жду дополнения.'}
    model=StubLLM([answer] if boundary=='controlled_extraction' else [])
    async with api_factory(model) as (client,_model):
        sid,token,_=await _session(client,sample_trip().model_dump(mode='json'))
        before=await _snapshot(client,sid,token)
        if boundary=='dispatcher':
            async def interpreted(_self,session,run):
                return ModelResult.model_validate(deepcopy(answer)),{'provider':'controlled','model':'no-http'}
            monkeypatch.setattr(Harness,'_interpret',interpreted)
        _,after=await _message(client,sid,token,
            'А если сделать бюджет 70 тысяч и возраст ребёнка 7 лет? Подожди, я ещё дополню.')
        assert after['waiting_reason']=='user_pause'
        assert after['state_version']==before['state_version']
        expected=deepcopy(before['trip'])
        expected['state_version']=after['state_version']
        assert after['trip']==expected
        assert len(model.calls)==(1 if boundary=='controlled_extraction' else 0)
