"""One isolated Store/Catalog revision admission, zero provider or model HTTP."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from test_geo_catalog import admit, fact, key
from test_place_run_pg import PGHarness, claimed, revision_count, shown_pending
from test_place_run_pg import pg as isolated_pg

from otravel.domain import Operation, TripState, apply_operations, sample_trip
from otravel.place_resolution import OWNER, _binding, _choice_id, _revision_scope, _sha
from otravel.place_run import finish_place_run
from otravel.store import StaleQuote

pg=isolated_pg


@pytest.mark.asyncio
async def test_confirmed_catalog_revision_commits_once_and_revokes_old_quote_and_consent(pg):
    repository,catalog=pg
    now=datetime.now(UTC)
    identifier=1_000_000_000+uuid4().int%1_000_000_000
    old_fact=fact(identifier,canonical_name="Previous declared city")
    directory_key=key(operation="get_place",role="origin",query=str(identifier),country_iso2=None,count=1)
    old,_=await admit(catalog,directory_key,old_fact,now=now)
    initial=apply_operations(sample_trip(),[Operation(type="set_origin",value={"city":old.canonical_name,
        "place_id":old.place_id})],resolved_places={old.place_id:old})[0]
    made=await repository.create_session(initial.model_dump(mode="json"),"a"*64)
    session=await repository.get_session(made["session_id"])
    initial=TripState.model_validate(session["state"])
    # The controlled Store-level quote tests lifecycle and CAS only; this is
    # not an external fare, accepted travel plan, booking or payment.
    quote=await repository.create_quote(session["session_id"],0,{"plan_id":"declared-revision-lifecycle",
        "trip_id":initial.trip_id,"state_version":0,"source_type":"DEMO","source_snapshots":[]})
    new_fact=fact(identifier,canonical_name="Current declared city")
    new,_=await admit(catalog,directory_key,new_fact,now=now+timedelta(milliseconds=1))
    assert (old.ref_version,new.ref_version)==(1,2)
    packet={"owner":OWNER,"trip_id":initial.trip_id,"state_version":0,"resolution_id":"pr-"+uuid4().hex,
        "role":"origin","stay_id":None,"query":"Current declared city","country_ref":None,
        "stage":"ref_revision_confirm","expires_at":(now+timedelta(minutes=15)).isoformat()}
    candidate={"kind":"ref_revision","source_id":identifier,"label":new.canonical_name,"country_iso2":new.country_iso2,
        "fact_sha256":new.source_fact_sha256,"response_sha256":"b"*64,"key":directory_key,
        "revision":{"place_id":new.place_id,"previous_ref_version":1,"new_ref_version":2,
            "previous_snapshot_sha256":old.snapshot_sha256(),"new_snapshot_sha256":new.snapshot_sha256(),
            "new_snapshot":new.model_dump(mode="json"),"scope_sha256":_sha(_revision_scope(initial.model_dump(mode="json"),"origin",None))}}
    candidate["candidate_id"]=_choice_id(packet,candidate)
    packet["candidates"]=[candidate]
    pending={"field":"place.resolution","text":"Confirm the declared source revision?","context":_binding(packet)}
    session=await shown_pending(repository,session,pending)
    previous_count=await revision_count(repository,session["session_id"])
    session,run,message_key=await claimed(repository,session["session_id"])
    selected={"action":"selected","operations":[{"type":"set_origin","value":{"city":new.canonical_name,"place_id":new.place_id}}],
        "resolved_places":{new.place_id:new.model_dump(mode="json")},"replace_place_versions":{old.place_id:1},
        "notice":"Declared source revision confirmed."}
    harness=PGHarness(repository,catalog,[selected],now+timedelta(milliseconds=2))
    await finish_place_run(harness,session,run,TripState.model_validate(session["state"]),selection={
        "resolution_id":packet["resolution_id"],"candidate_id":candidate["candidate_id"]})
    after=await repository.get_session(session["session_id"])
    assert after["state_version"]==1 and after["state"]["location_refs"][new.place_id]["ref_version"]==2
    assert after["state"]["origin"]==new.canonical_name
    assert after["state"]["legs"][0]["origin"]==after["state"]["legs"][-1]["destination"]==new.canonical_name
    assert await revision_count(repository,session["session_id"])==previous_count+1
    assert (await repository.get_quote(quote["quote_id"]))["status"]=="stale"
    with pytest.raises(StaleQuote):
        await repository.confirm_quote(session["session_id"],quote["quote_id"],quote["quote_version"],str(uuid4()),True,0)
    replay,reused=await repository.intake_message(session["session_id"],message_key,run["input_text"],0)
    assert reused and replay["run_id"]==run["run_id"] and replay["status"]=="completed"
    assert await revision_count(repository,session["session_id"])==previous_count+1
    assert await repository.list_orders(session["session_id"])==[]
    assert await catalog.get_place(old.place_id,1)==old and await catalog.get_place(new.place_id,2)==new
