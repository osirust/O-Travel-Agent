"""Controlled exact geo-role regressions; no remote model/provider calls."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from otravel.domain import TripState, apply_operations
from otravel.intent_grounding import ground_intent_operations

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT/'fixtures/regression/geo-role-server-introduced-20261028.json').read_text())['cases']
@pytest.mark.parametrize('case', CASES, ids=lambda c: c['case_id'])
def test_exact_actual_raw_model_has_no_server_introduced_wrong_geo_role(case):
    before = case['before']
    ops, _meta = ground_intent_operations(before, case['raw_operations'], case['input'])
    if case['case_id'] == 'ZAI100-040':
        assert not any(op['type']=='upsert_stay' and op.get('value',{}).get('city')=='Сочи' for op in ops)
        assert any(op['type']=='set_budget' and op['value']['amount_minor']==7500000 for op in ops)
    else:
        assert not any(op['type']=='set_origin' and op.get('value',{}).get('city') != before['origin'] for op in ops)
        original = [op for op in case['raw_operations'] if op['type']=='upsert_stay']
        assert [op for op in ops if op['type']=='upsert_stay'] == original


def state(city="Стамбул", origin="Казань"):
    return TripState(origin=origin, travelers=[{"id":"adult","type":"adult"}],
        stays=[{"id":"stay","city":city,"check_in":"2026-11-29","check_out":"2026-12-04",
                "rooms":[{"id":"room","traveler_ids":["adult"]}]}],
        budget={"currency":"RUB","amount_minor":8500000})

def apply_geo(before, ops, text):
    checked, meta = ground_intent_operations(before, ops, text)
    after = apply_operations(before, checked)[0]
    return after, checked, meta

@pytest.mark.parametrize("text,expected",[
    ("Не в Сочи, а в Казань.", "Казань"),
    ("Выбираем Сочи вместо Стамбула.", "Сочи"),
    ("Теперь едем в Сочи.", "Сочи"),
    ("Раньше ездили в Сочи. Теперь выбираем Казань.", "Казань"),
    ("Реклама «Не в Сочи, а в Казань». Едем в Стамбул.", "Стамбул"),
])
def test_positive_destination_remains_possible_next_to_negation_or_quotes(text, expected):
    before=state()
    after,_,_=apply_geo(before,[{"type":"upsert_stay","target_id":"stay","value":{"city":"Сочи"}}],text)
    assert after.origin==before.origin
    assert after.stays[0].city==expected
    assert after.stays[0].check_in==before.stays[0].check_in
    assert after.travelers==before.travelers and after.budget==before.budget

@pytest.mark.parametrize("text",[
    "Выезжаю не из Казани, а из Санкт-Петербурга. Стамбул остаётся местом поездки.",
    "Отправление теперь Санкт-Петербург. Стамбул не меняй.",
    "Вылетаем из Санкт-Петербурга, отель в Стамбуле прежний.",
])
def test_explicit_origin_is_not_blocked_by_negative_old_city_or_hotel_reference(text):
    before=state()
    after,_,_=apply_geo(before,[{"type":"set_origin","value":{"city":"Казань"}}],text)
    assert after.origin=="Санкт-Петербург"
    assert after.stays==before.stays and after.travelers==before.travelers

@pytest.mark.parametrize("text",[
    "Выезд из Стамбула 05.12.2026. Город отправления теперь Москва.",
    "Город отправления теперь Москва. Дата выезда из Стамбула 05.12.2026.",
    "Вылетаем из Москвы. Выезд из Стамбула 05.12.2026.",
])
def test_real_origin_edit_survives_independent_owned_hotel_checkout(text):
    before=state()
    proposed=[{"type":"upsert_stay","target_id":"stay","value":{"city":"Стамбул","check_out":"2026-12-05"}},
              {"type":"set_origin","value":{"city":"Москва"}}]
    after,ops,_=apply_geo(before,proposed,text)
    assert after.origin=="Москва"
    assert after.stays[0].check_out.isoformat()=="2026-12-05"
    assert after.travelers==before.travelers and after.budget==before.budget
    assert not any(x["type"]=="set_origin" and x["value"]["city"]=="Стамбул"for x in ops)

def test_true_flight_departure_from_current_hotel_city_is_not_renamed_or_dropped():
    before=state()
    after,_,_=apply_geo(before,[{"type":"set_origin","value":{"city":"Стамбул"}}],
        "Теперь вылетаем из Стамбула. Город отправления — Стамбул.")
    assert after.origin=="Стамбул"
    assert after.stays==before.stays

@pytest.mark.parametrize("text",[
    "Если выезд из Стамбула 05.12.2026, что изменится?",
    "Раньше был выезд из Стамбула 05.12.2026.",
    "Мне прислали «Выезд из Стамбула 05.12.2026».",
    "На какую дату выезд из Стамбула 05.12.2026?",
])
def test_hypothesis_history_quote_and_question_do_not_create_a_new_origin(text):
    before=state()
    raw=[{"type":"upsert_stay","target_id":"stay","value":{"city":"Стамбул","check_out":"2026-12-05"}}]
    ops,_=ground_intent_operations(before,raw,text)
    assert not any(op["type"]=="set_origin" for op in ops)
    # Speech-act handling for dates belongs to Harness; no claim these model
    # date operations are authorized by this geography-only test.

def test_same_named_stays_use_raw_owned_id_without_picking_first():
    before=state()
    data=before.model_dump(mode="json")
    data["stays"].append({"id":"other","city":"Стамбул","check_in":"2026-12-10","check_out":"2026-12-12","rooms":[]})
    proposed=[{"type":"upsert_stay","target_id":"other","value":{"city":"Стамбул","check_out":"2026-12-13"}}]
    checked,_=ground_intent_operations(data,proposed,"Выезд из Стамбула 13.12.2026.")
    assert checked==proposed
    assert data["stays"][0]["check_out"]=="2026-12-04"

def test_hotel_checkout_does_not_rename_another_city_as_origin():
    before=state()
    data=before.model_dump(mode="json")
    data["stays"].append({"id":"dubai","city":"Дубай","check_in":"2026-12-10","check_out":"2026-12-12","rooms":[]})
    proposed=[{"type":"upsert_stay","target_id":"dubai","value":{"city":"Дубай","check_out":"2026-12-13"}}]
    checked,_=ground_intent_operations(data,proposed,"Выезд из Дубая 13.12.2026.")
    assert checked==proposed
    assert data["origin"]=="Казань" and data["stays"][0]["city"]=="Стамбул"

def test_similar_spelling_is_not_matched_as_current_city_identity():
    before=state()
    checked,_=ground_intent_operations(before,[],"Выезд из Стамбулово 05.12.2026.")
    assert not any(op["type"]=="set_origin"for op in checked)

def test_operations_and_before_state_are_not_mutated():
    case=CASES[1]
    before=deepcopy(case["before"]);ops=deepcopy(case["raw_operations"])
    old_before=deepcopy(before);old_ops=deepcopy(ops)
    ground_intent_operations(before,ops,case["input"])
    assert before==old_before and ops==old_ops

def test_negated_country_does_not_block_positive_destination_country():
    before=state()
    checked,meta=ground_intent_operations(before,[],"Не в Турцию, а в Россию.")
    assert not checked
    assert meta["pending_context"]["requested_destination"]["country_code"]=="RU"
