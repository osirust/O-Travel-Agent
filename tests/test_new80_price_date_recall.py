import json
from copy import deepcopy
from pathlib import Path
import pytest
from otravel.domain import Operation,TripState,apply_operations
from datetime import UTC,datetime
from otravel.preference_grounding import guard_preference_operations
from otravel.date_intake import ground_explicit_stay_dates
from otravel.duration_grounding import guard_unasked_duration_operations

def checked_operations(state,operations,text,pending=None):
    operations,meta=guard_preference_operations(state,operations,text)
    notes=list(meta["notes"])
    operations,date_meta=ground_explicit_stay_dates(state,text,operations,pending=pending,now=datetime(2026,10,28,tzinfo=UTC))
    notes.extend(date_meta["notes"])
    operations,duration_meta=guard_unasked_duration_operations(state,operations,text,model_generated=True)
    notes.extend(duration_meta["notes"])
    from otravel.price_date_recall import guard_price_date_recall_operations
    operations,recall_meta=guard_price_date_recall_operations(state,operations,text,model_generated=True)
    notes.extend(recall_meta["notes"])
    return operations,notes

from otravel.preference_grounding import guard_preference_operations
FIXTURE=Path(__file__).parents[1]/"fixtures/regression/price-date-recall-007t7.json"

def fixture():
    return json.loads(FIXTURE.read_text())

def test_exact_007t7_keeps_canonical_dates_and_accepts_lowest_total():
    data=fixture();before=TripState.model_validate(data["before_trip"])
    raw=data["raw_contract"]["value"]["operations"];old=deepcopy(raw)
    operations,notes=checked_operations(data["before_trip"],raw,data["input"],data["pending_question"])
    after,_=apply_operations(before,[Operation.model_validate(op)for op in operations])
    assert raw==old
    assert after.stays[-1].check_out.isoformat()=="2026-11-14",notes
    assert after.stays[-1].check_in.isoformat()=="2026-11-08"
    assert after.legs[-1].departure_date==before.legs[-1].departure_date
    assert (after.stays[-1].check_out-after.stays[-1].check_in).days==6
    assert [(p.key,p.value)for p in after.preferences]==[("price_priority","lowest_total")]
    old,new=before.model_dump(mode="json"),after.model_dump(mode="json")
    assert {k:v for k,v in new.items()if k!="preferences"}=={k:v for k,v in old.items()if k!="preferences"}

def test_actual_typo_raw_price_preference_is_authorized():
    data=fixture();raw=data["raw_contract"]["value"]["operations"][:1]
    result,meta=guard_preference_operations(data["before_trip"],raw,data["input"])
    assert result==raw and not meta["notes"],meta

@pytest.mark.parametrize("text",[
    "Не ищи подишевле. Напомни даты.",
    "Если искать подишевле, напомни даты.",
    "Цитата: «Ищи подишевле». Напомни даты.",
])
def test_typo_anchor_does_not_grant_negative_hypothetical_or_quoted_preference(text):
    data=fixture();raw=data["raw_contract"]["value"]["operations"][:1]
    result,_=guard_preference_operations(data["before_trip"],raw,text)
    assert result==[]

from otravel.price_date_recall import guard_price_date_recall_operations as recall_guard

@pytest.mark.parametrize("text",[
    "Ищи подешевле. Напомни даты.",
    "И всё-таки ищи подишевле. Напомни даты, а то я уже запутался.",
    "Напомни текущие даты. Найди подешевле.",
])
def test_closed_price_and_date_recall_removes_only_existing_stay_date_fields(text):
    data=fixture();raw=data["raw_contract"]["value"]["operations"];copied=deepcopy(raw)
    result,meta=recall_guard(data["before_trip"],raw,text)
    assert raw==copied
    assert result[0]==raw[0]
    assert result[1]["value"]=={"city":"Анталья"}
    assert len(meta["notes"])==1

@pytest.mark.parametrize("text",[
    "Ищи подешевле. Напомни даты. Перенеси выезд на два дня позже.",
    "Ищи подешевле. Заезд 8 ноября2026, выезд12 ноября2026.",
    "Ищи подешевле и сократи поездку на две ночи. Напомни даты.",
    "Ищи подешевле. Напомни даты и сократи поездку на две ночи.",
    "Если искать подешевле. Напомни даты.",
    "Не ищи подешевле. Напомни даты.",
    "Цитата: «Ищи подешевле». Напомни даты.",
    "Ищи подешевле? Напомни даты.",
])
def test_open_or_explicit_date_edit_clauses_are_outside_narrow_scope(text):
    data=fixture();raw=data["raw_contract"]["value"]["operations"]
    result,meta=recall_guard(data["before_trip"],raw,text)
    assert result==raw and not meta["notes"]

def test_typed_new_initial_unbound_and_missing_canonical_dates_are_not_rewritten():
    data=fixture();raw=data["raw_contract"]["value"]["operations"];text=data["input"]
    assert recall_guard(data["before_trip"],raw,text,model_generated=False)[0]==raw
    assert recall_guard(data["before_trip"],raw,"\x1eOTRAVEL_TYPED_V1:"+text)[0]==raw
    assert recall_guard(TripState(trip_id="fresh"),raw,text)[0]==raw
    before=deepcopy(data["before_trip"]);before["stays"][0]["check_out"]=None
    assert recall_guard(before,raw,text)[0]==raw
    unknown=deepcopy(raw);unknown[1]["target_id"]="other-stay"
    assert recall_guard(data["before_trip"],unknown,text)[0]==unknown
    mismatch=deepcopy(raw);mismatch[1]["value"]["id"]="other-stay"
    assert recall_guard(data["before_trip"],mismatch,text)[0]==mismatch

@pytest.mark.parametrize("values",[
    {"check_out":"tomorrow"},{"check_out":None},{"requested_nights":True},{"requested_nights":"4"},
])
def test_invalid_temporal_field_shapes_remain_for_normal_schema_validation(values):
    data=fixture()
    raw=[{"type":"upsert_stay","target_id":"new-stay-1","value":{"city":"Анталья",**values}}]
    assert recall_guard(data["before_trip"],raw,data["input"])[0]==raw

def test_explicit_legal_new_dates_and_original_typed_operations_remain_available():
    data=fixture();state=TripState.model_validate(data["before_trip"])
    raw=[{"type":"upsert_stay","target_id":"new-stay-1","value":{"city":"Анталья","check_in":"2026-11-08","check_out":"2026-11-12"}}]
    text="Ищи подешевле. Напомни даты. Но выезд поставь12 ноября2026."
    result,meta=recall_guard(state,raw,text)
    assert result==raw and not meta["notes"]
    after,_=apply_operations(state,[Operation.model_validate(op)for op in result])
    assert after.stays[0].check_out.isoformat()=="2026-11-12"

def test_existing_stay_without_transport_legs_is_still_protected():
    data=fixture();before=deepcopy(data["before_trip"]);before["legs"]=[]
    raw=data["raw_contract"]["value"]["operations"]
    result,meta=recall_guard(before,raw,data["input"])
    assert result[1]["value"]=={"city":"Анталья"}and meta["notes"]
