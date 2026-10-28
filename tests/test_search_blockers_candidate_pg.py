"""Captured010/012 through ASGI + its own isolated real PG; no vendor/model HTTP."""
import asyncio,json,os
from contextlib import asynccontextmanager
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
import httpx,pytest
from otravel.api import create_app
from otravel.config import Settings
from otravel.harness import ModelResult
from otravel.store import Store
FIXTURES=Path(__file__).parents[1]/"fixtures/regression"

class NeverExternal:
    async def extract_operations(self,*args,**kwargs):raise AssertionError("Actual model forbidden")
    async def search_hotels(self,*args,**kwargs):raise AssertionError("Actual supplier forbidden")
    async def search_flights(self,*args,**kwargs):raise AssertionError("Actual supplier forbidden")
    async def weather(self,*args,**kwargs):raise AssertionError("Actual supplier forbidden")
    async def close(self):pass

def fixture(cid):
    return json.loads((FIXTURES/("actual"+cid+"-search-blocker.json")).read_text())

def rebind(value,trip_id,version):
    if isinstance(value,list):return [rebind(v,trip_id,version)for v in value]
    if isinstance(value,dict):
        return {k:trip_id if k=="trip_id"else version if k=="state_version"else rebind(v,trip_id,version)for k,v in value.items()}
    return value

@asynccontextmanager
async def controlled(cid):
    data=fixture(cid);url=os.environ["TEST_DATABASE_URL"]
    repository=Store(url)
    settings=Settings(url,"gemini",1,8000,0,1,max_local_llm_calls_per_day=0)
    app=create_app(settings,store=repository,llm=NeverExternal(),travel=NeverExternal())
    captured=deepcopy(data["checked_contract"]["value"])
    async def interpretation(session,run):
        await repository.save_event(session["session_id"],run["run_id"],"CACHED_INTERPRETATION_PROPOSED",
            {"case":data["case"],"turn":data["turn"],"captured_contract_sha256":data["checked_contract"]["sha256"],
             "actual_model_http":0,"qualification":"controlled_orchestration_not_NLU"})
        return ModelResult.model_validate(captured),None
    async def search(state,**kwargs):
        value=rebind(deepcopy(data["search_result"]),state.trip_id,state.state_version)
        for key in("llm","model_reply","operation_normalizations","conversational_response","answer_sections","search_explanation"):
            value.pop(key,None)
        value["controlled_search_fixture"]={"case":data["case"],"actual_supplier_http":0}
        return value
    original_interpretation=app.state.harness._interpret
    app.state.harness._interpret=interpretation
    app.state.harness._search_trip=search
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://peer-blockers")as client:
            token=uuid4().hex
            own=await repository.create_session(deepcopy(data["before"]["trip"]),sha256(token.encode()).hexdigest())
            headers={"Authorization":"Bearer "+token};sid=own["session_id"];route="/api/v1/sessions/"+sid
            before=(await client.get(route,headers=headers)).json()
            accepted=await client.post(route+"/messages",headers=headers,json={"client_message_id":uuid4().hex,
                "expected_version":before["state_version"],"text":data["user"]})
            assert accepted.status_code==202,accepted.json()
            rid=accepted.json()["run_id"]
            for _ in range(300):
                run=(await client.get("/api/v1/runs/"+rid,headers=headers)).json()
                if run["status"]in{"completed","failed","conflict","cancelled"}:break
                await asyncio.sleep(.01)
            assert run["status"]=="completed",run.get("error")
            after=(await client.get(route,headers=headers)).json()
            messages=(await client.get(route+"/messages",headers=headers)).json()["messages"]
            replies=[m["text"]for m in messages if m.get("run_id")==rid and m["role"]=="assistant"]
            assert len(replies)==1
            assert await repository.list_orders(sid)==[]
            events=await repository.list_events(sid)
            assert not any(e["event_type"]in{"MODEL_CALL_STARTED","TOOL_CALL_STARTED"}for e in events)
            yield {"before":before,"after":after,"run":run,"reply":replies[0],"repository":repository,"sid":sid,
                "client":client,"headers":headers,"data":data,"events":events,"app":app,"original_interpretation":original_interpretation}
    await repository.close()

@pytest.mark.asyncio
async def test_actual010_unassigned_added_adult_has_durable_actionable_room_question_without_auto_allocation():
    async with controlled("010")as row:
        before,after=row["before"],row["after"]
        assert len(after["trip"]["travelers"])==2
        assert after["trip"]["stays"][0]["rooms"]==before["trip"]["stays"][0]["rooms"]
        assert after["trip"]["budget"]==before["trip"]["budget"]
        assert after["trip"]["origin"]==before["trip"]["origin"]
        assert after["trip"]["legs"]==before["trip"]["legs"]
        assert after["waiting_reason"]=="clarification"
        pending=after["pending_question"];assert pending and pending["field"]=="stay.rooms"
        assert pending["target_id"]==after["trip"]["stays"][0]["id"]
        assert pending["context"]["room_reallocation_required"]is True
        assert "распредел"in row["reply"].casefold()and"номер"in row["reply"].casefold()
        result=row["run"]["result"]["result"]
        assert result["plans"]==[]
        assert any(v["code"]=="changed_allocation"for v in result["violations"])
        saved=await row["repository"].get_session(row["sid"])
        assert saved["pending_question"]==after["pending_question"]

@pytest.mark.asyncio
async def test_actual012_budget_violation_survives_canonical_party_budget_receipt():
    async with controlled("012")as row:
        after=row["after"];result=row["run"]["result"]["result"]
        assert after["trip"]["budget"]["amount_minor"]==14500000
        assert len(after["trip"]["travelers"])==3
        assert after["trip"]["stays"]==row["before"]["trip"]["stays"]
        assert result["plans"]==[]
        assert any(v["code"]=="budget_exceeded"for v in result["violations"])
        assert "превышает"in row["reply"].casefold()
        assert "148 993,78 RUB"in row["reply"]
        assert "145 000,00 RUB"in row["reply"]
        assert "3 993,78 RUB"in row["reply"]
        assert "SANDBOX"in row["reply"]and"DEMO"in row["reply"]

@pytest.mark.asyncio
async def test010_pending_survives_a_new_database_connection():
    async with controlled("010")as row:
        mirror=Store(os.environ["TEST_DATABASE_URL"])
        try:
            persisted=await mirror.get_session(row["sid"])
            assert persisted["waiting_reason"]=="clarification"
            assert persisted["pending_question"]==row["after"]["pending_question"]
            assert persisted["state"]["stays"][0]["rooms"]==row["before"]["trip"]["stays"][0]["rooms"]
        finally:
            await mirror.close()

@pytest.mark.asyncio
async def test010_numeric_room_count_followup_still_requires_explicit_people_allocation():
    async with controlled("010")as row:
        row["app"].state.harness._interpret=row["original_interpretation"]
        route="/api/v1/sessions/"+row["sid"]
        accepted=await row["client"].post(route+"/messages",headers=row["headers"],json={
            "client_message_id":uuid4().hex,"expected_version":row["after"]["state_version"],"text":"1"})
        assert accepted.status_code==202
        for _ in range(300):
            run=(await row["client"].get("/api/v1/runs/"+accepted.json()["run_id"],headers=row["headers"])).json()
            if run["status"]in{"completed","failed"}:break
            await asyncio.sleep(.01)
        assert run["status"]=="completed"
        after=(await row["client"].get(route,headers=row["headers"])).json()
        assert after["trip"]==row["after"]["trip"]
        assert after["waiting_reason"]=="clarification"
        assert after["pending_question"]["field"]=="stay.rooms"
        assert "кто"in after["pending_question"]["text"].casefold()
        assert await row["repository"].list_orders(row["sid"])==[]
