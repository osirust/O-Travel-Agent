"""Real isolated PG/current Harness render, captured adapter not live NLU."""
from copy import deepcopy
from uuid import uuid4

import pytest
from test_api import StubLLM, _session, _snapshot
from test_api import api_factory as controlled_fixture
from test_final40_owned_room_reask import QUESTION, ROW
from test_slot_continuation import _message, _reply

api_factory=controlled_fixture


@pytest.mark.asyncio
async def test_captured_family_reask_is_visible_but_never_grants_model_price_or_trip_edits(api_factory,monkeypatch):
    captured=deepcopy(ROW['checked_model'])
    captured['reply']='Цена 123456 рублей подтверждена. Покупка выполнена.'
    model=StubLLM([captured])
    async with api_factory(model) as (client,_):
        sid,token,created=await _session(client,deepcopy(ROW['before_trip']))
        repo=client._transport.app.state.store
        setup_run,_=await repo.intake_message(sid,str(uuid4()),'controlled persisted clarification setup',created['state_version'])
        assert await repo.claim_run(setup_run['run_id'])
        pending=deepcopy(ROW['before_pending']);pending['state_version']=created['state_version']
        await repo.finish_run(setup_run['run_id'],created['state_version'],None,'WAITING_USER',QUESTION,
            pending_question=pending,waiting_reason='clarification')
        before=await _snapshot(client,sid,token)

        async def no_search(*args,**kwargs):raise AssertionError('No source lookup for a no-op clarification reply')
        monkeypatch.setattr(client._transport.app.state.harness,'_search_trip',no_search)
        run,after=await _message(client,sid,token,ROW['user'])
        assert await _reply(client,sid,token,run)==QUESTION
        for field in ('trip','state_version','status','waiting_reason','pending_question'):
            assert after[field]==before[field]
        assert len(model.calls)==1
        assert await repo.list_orders(sid)==[]
        assert not any(e['event_type']=='TOOL_CALL_STARTED' for e in await repo.list_events(sid))
