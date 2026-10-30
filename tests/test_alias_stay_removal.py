"""Controlled cached038 and alias-removal boundaries; no network/DB/NLU."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from otravel.alias_stay_removal import guard_alias_stay_removal_operations as guard
from otravel.domain import DomainError, Operation, TripState, apply_operations
from otravel.intent_grounding import ground_intent_operations

F=json.loads((Path(__file__).parents[1]/"fixtures/regression/alias-question-stay-removal-20261028.json").read_text())

def test_actual038_alias_question_must_not_remove_only_stay():
    before=TripState.model_validate(F["before"])
    operations,_=ground_intent_operations(before,F["raw_contract"]["value"]["operations"],F["input"])
    operations,meta=guard(before,operations,F["input"])
    after=apply_operations(before,operations)[0]
    assert after.stays==before.stays
    assert after.budget.amount_minor==6500000
    assert meta["notes"][0]["finding_id"]=="HARNESS-004-ALIAS-QUESTION-STAY-REMOVAL-001"


def data():
    return deepcopy(F["before"])

def ops():
    return deepcopy(F["raw_contract"]["value"]["operations"])

@pytest.mark.parametrize("tail",[
    " Удали единственную остановку в Санкт-Петербурге.",
    " Убери проживание в Санкт-Петербурге.",
    " Замени Санкт-Петербург на Казань.",
])
def test_separate_current_explicit_delete_or_replacement_is_not_blocked(tail):
    before,raw=data(),ops()
    checked,meta=guard(before,raw,F["input"]+tail)
    assert checked==raw and meta["notes"]==[]

@pytest.mark.parametrize("tail",[
    " Мне прислали «Удали единственную остановку».",
    " Если понадобится, удали единственную остановку.",
    " Не удали единственную остановку.",
    " Не замени Санкт-Петербург на Казань.",
])
def test_quote_hypothesis_and_negation_cannot_authorize_deleting_only_stay(tail):
    before=TripState.model_validate(data())
    checked,meta=guard(before,ops(),F["input"]+tail)
    after=apply_operations(before,checked)[0]
    assert after.stays==before.stays and after.budget.amount_minor==6500000
    assert after.origin==before.origin and after.travelers==before.travelers
    assert meta["notes"]

def test_two_real_stays_are_not_silently_selected_or_filtered_by_new_guard():
    before=data()
    other=deepcopy(before["stays"][0]);other["id"]="second-real-stay"
    other["check_in"]="2026-12-10";other["check_out"]="2026-12-13"
    other["rooms"]=[{"id":"other-room","traveler_ids":[]}]
    before["stays"].append(other)
    checked,meta=guard(before,ops(),F["input"])
    assert checked==ops() and meta["notes"]==[]
    after=apply_operations(TripState.model_validate(before),checked)[0]
    assert [s.id for s in after.stays]==["second-real-stay"]

def test_foreign_remove_target_does_not_gain_ownership_from_alias_question():
    before=data();raw=ops()
    raw[-1]["target_id"]="foreign-stay"
    checked,meta=guard(before,raw,F["input"])
    assert checked==raw and meta["notes"]==[]
    with pytest.raises(DomainError):
        apply_operations(TripState.model_validate(before),checked)

def test_only_room_change_and_budget_are_preserved_verbatim():
    before=TripState.model_validate(data())
    raw=[ops()[0],{"type":"upsert_stay","target_id":before.stays[0].id,
        "value":{"rooms":[{"id":"room-single","traveler_ids":["new-traveler-1"]}]}}]
    checked,meta=guard(before,raw,F["input"])
    assert checked==raw and meta["notes"]==[]
    after=apply_operations(before,checked)[0]
    assert after.stays[0].city==before.stays[0].city
    assert after.stays[0].rooms[0].traveler_ids==["new-traveler-1"]

def test_unrelated_room_removal_sentence_does_not_authorize_stay_delete():
    before=TripState.model_validate(data())
    checked,meta=guard(before,ops(),F["input"]+" Удали номер, а остановку оставь.")
    after=apply_operations(before,checked)[0]
    assert after.stays==before.stays and meta["notes"]

def test_other_city_pair_and_same_token_are_outside_this_alias_guard():
    for text in ("Москва и Казань один город? Мне две остановки не нужны.",
                 "Питер и Питер один город? Мне две остановки не нужны.",
                 "Питер и Санкт-Петербург разные города? Мне две остановки не нужны."):
        checked,meta=guard(data(),ops(),text)
        assert checked==ops() and not meta["notes"]

def test_no_stay_and_no_duplicate_veto_do_not_create_a_target_or_alias_authority():
    for before,text in (({**data(),"stays":[]},F["input"]),
                        (data(),"Питер и Санкт-Петербург один город?")):
        checked,meta=guard(before,ops(),text)
        assert checked==ops() and not meta["notes"]

def test_pydantic_operations_and_input_dictionaries_not_mutated():
    before=data();raw=ops();oldbefore=deepcopy(before);oldraw=deepcopy(raw)
    typed=[Operation.model_validate(op)for op in raw]
    checked,meta=guard(before,typed,F["input"])
    assert before==oldbefore and raw==oldraw
    assert checked==raw[:1] and meta["notes"]

def test_explicit_unquoted_delete_still_works_when_the_alias_question_is_a_quote():
    text="Цитата «Питер и Санкт-Петербург один город? Мне две остановки не нужны». Удали единственную остановку."
    checked,meta=guard(data(),ops(),text)
    assert checked==ops() and not meta["notes"]

def test_current_russian_physical_ref_is_preserved_without_alias_reselection():
    before=data()
    ref={"place_id":"geo:openmeteo:498817","ref_version":1,"canonical_name":"Saint Petersburg",
        "country_iso2":"RU","geographic_source":{"provider":"openmeteo","external_id":498817},
        "latitude":59.93,"longitude":30.34,"timezone":"Europe/Moscow","feature_code":"PPLA",
        "administrative_areas":[],"source_fact_sha256":"a"*64}
    before["stays"][0].update(city="Saint Petersburg",place_id=ref["place_id"])
    before["location_refs"]={ref["place_id"]:ref}
    original=deepcopy(before)
    checked,meta=guard(before,ops(),F["input"])
    assert checked==ops()[:1] and meta["notes"]
    assert before==original and before["location_refs"]==original["location_refs"]

def test_same_name_in_another_country_is_not_asserted_to_be_piter_ru():
    before=data()
    ref={"place_id":"geo:openmeteo:4171563","ref_version":1,"canonical_name":"Saint Petersburg",
        "country_iso2":"US","geographic_source":{"provider":"openmeteo","external_id":4171563},
        "latitude":27.77,"longitude":-82.64,"timezone":"America/New_York","feature_code":"PPL",
        "administrative_areas":[],"source_fact_sha256":"b"*64}
    before["stays"][0].update(city="Saint Petersburg",place_id=ref["place_id"])
    before["location_refs"]={ref["place_id"]:ref}
    checked,meta=guard(before,ops(),F["input"])
    assert checked==ops() and meta["notes"]==[]
    # Outside this narrow alias guard; public provenance/country and removal
    # permission remain separately enforced. Not a claim deletion is authorized.

def test_already_equal_budget_does_not_make_fake_changes_after_removal_veto():
    before=data();before["budget"]={"scope":"whole_trip","currency":"RUB","amount_minor":6500000}
    old=TripState.model_validate(before)
    checked,meta=guard(old,ops(),F["input"])
    after,changes=apply_operations(old,checked)
    assert after==old and changes==[]
    assert meta["notes"] and checked==ops()[:1]


@pytest.mark.parametrize("tail,change_origin", [
    (" Поменяй только бюджет, маршрут не меняй.", False),
    (" Замени Москву на Казань в городе отправления.", True),
])
def test_unrelated_replacement_marker_does_not_authorize_deleting_only_stay(tail, change_origin):
    before = TripState.model_validate(data())
    raw = ops()
    if change_origin:
        raw.append({"type": "set_origin", "value": {"city": "Казань"}})
    checked, meta = guard(before, raw, F["input"] + tail)
    after = apply_operations(before, checked)[0]
    assert after.stays == before.stays
    assert after.budget.amount_minor == 6500000
    assert after.origin == ("Казань" if change_origin else before.origin)
    assert meta["notes"]
