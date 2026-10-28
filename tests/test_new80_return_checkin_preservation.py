"""Exact cached actual005T9; pure local helper, no DB/model/supplier IO."""
import json
from copy import deepcopy
from pathlib import Path
import pytest
from otravel.domain import Operation, TripState, apply_operations
from otravel.return_preservation import guard_preserved_return_operations as guard
FIXTURE=Path(__file__).parents[1]/"fixtures/regression/return-checkin-preserve-005t9.json"

def test_exact_actual_checkout_shift_preserves_checkin_and_makes_seven_nights():
    fixture=json.loads(FIXTURE.read_text())
    before=TripState.model_validate(fixture["before_trip"])
    operations=fixture["raw_contract"]["value"]["operations"]
    copied=deepcopy(operations)
    result,meta=guard(before,operations,fixture["input"])
    assert result==operations and not meta["notes"],meta
    assert operations==copied
    after,_=apply_operations(before,[Operation.model_validate(op)for op in result])
    assert before.stays[-1].check_in.isoformat()=="2026-11-18"
    assert before.stays[-1].check_out.isoformat()=="2026-11-24"
    assert after.stays[-1].check_in==before.stays[-1].check_in
    assert after.stays[-1].check_out.isoformat()=="2026-11-25"
    assert after.legs[-1].departure_date==after.stays[-1].check_out
    assert (after.stays[-1].check_out-after.stays[-1].check_in).days==7
    old,new=before.model_dump(mode="json"),after.model_dump(mode="json")
    for key in ("origin","travelers","groups","budget","constraints","preferences","search_policy","route_policy"):
        assert new[key]==old[key]

@pytest.mark.parametrize("text",[
    "Сделай выезд на один день позже, заезд не трогай. Сколько ночей получится?",
    "Сделай выезд на один день позжэ, заезд не трогай. Сколько ночей получится?",
    "Заезд не трогай, сделай выезд на один день позже.",
])
def test_explicit_make_checkout_edit_in_other_clause_is_date_authority(text):
    fixture=json.loads(FIXTURE.read_text())
    operations=fixture["raw_contract"]["value"]["operations"]
    result,meta=guard(fixture["before_trip"],operations,text)
    assert result==operations and not meta["notes"]

@pytest.mark.parametrize("text",[
    "Сделай второй номер для сестры, даты и возвращение не трогай.",
    "Сделай гостиницу дешевле, даты и возвращение сохрани.",
    "Не меняй дату выезда. Сделай второй номер.",
    "Цитата: «Сделай выезд на один день позже». Заезд и выезд не трогай.",
])
def test_unrelated_make_edit_and_quote_do_not_release_preserved_return(text):
    fixture=json.loads(FIXTURE.read_text())
    result,meta=guard(fixture["before_trip"],fixture["raw_contract"]["value"]["operations"],text)
    assert result==[] and len(meta["notes"])==1
