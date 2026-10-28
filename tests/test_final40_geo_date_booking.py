"""FINAL40 date/country/booking regressions; controlled pure boundaries only.

Original R26 RED results and source-file hashes are retained separately.
These tests do not claim a live-model or full-dialogue acceptance run.
"""
import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from otravel.answering import canonical_order_status_answer, order_status_question
from otravel.country_lexicon import DATA_SHA256, _lexicon
from otravel.date_slots import resolve_date_answer
from otravel.intent_grounding import current_city_country_hint
from otravel.llm_contract import build_operation_schema
from otravel.llm_schema_feedback import operation_schema_feedback
from otravel.place_resolution import PlaceResolutionCoordinator, PlaceResolutionError

F=json.loads((Path(__file__).parents[1]/'fixtures/regression/final40-geo-date-booking-20261028.json').read_text(encoding='utf-8'))
NOW=datetime(2026,10,28,15,0,tzinfo=UTC)

@pytest.mark.parametrize('row',F['date_cases'],ids=lambda x:x['case_id'])
def test_exact_checkout_suffix_owns_only_fresh_target(row):
    before=copy.deepcopy(row['before']['trip']);pending=copy.deepcopy(row['before']['pending_question'])
    original=copy.deepcopy(before)
    got=resolve_date_answer(before,pending,row['user'],NOW)
    assert got['handled'] is True, 'closed owned checkout answer must not invoke model/update_stay'
    assert got['operations']==[{'type':'upsert_stay','target_id':pending['target_id'],'value':{'check_out':'2026-11-19'}}]
    assert before==original,'resolver must be pure'

@pytest.mark.parametrize('kind',['stale','bool-version','foreign-trip','foreign-target','foreign-context-target','missing-question','user-pause','other-field','null-target'])
def test_new_suffix_cannot_claim_a_foreign_or_stale_slot(kind):
    row=F['date_cases'][0];s=copy.deepcopy(row['before']['trip']);p=copy.deepcopy(row['before']['pending_question'])
    if kind=='stale':p['state_version']-=1
    elif kind=='bool-version':p['state_version']=True
    elif kind=='foreign-trip':p['trip_id']='another-trip'
    elif kind=='foreign-target':p['target_id']='another-stay'
    elif kind=='foreign-context-target':p.setdefault('context',{})['stay_id']='another-stay'
    elif kind=='missing-question':p.pop('question_id',None)
    elif kind=='user-pause':p['reason']='user_pause'
    elif kind=='other-field':p['field']='stay.check_in'
    elif kind=='null-target':p['target_id']=None
    got=resolve_date_answer(s,p,row['user'],NOW)
    assert got.get('handled') is False and not got['operations']

@pytest.mark.parametrize('text',[
 'Если выезд 2026-11-19. Заезд не меняем.',
 '«Выезд 2026-11-19. Заезд не меняем.»',
 'Выезд 2026-11-19. Заезд не меняем. Добавь ребёнка 7 лет.',
 'Выезд 2026-11-19. Заезд не меняем. Бюджет 50000 рублей.',
 'Выезд 2026-11-19. Заезд не меняем?',
 'Выезд 2026-11-19. Заезд не меняем; удали номер.',
 'Выезд 2026-11-19. Заезд не меняем\nПоменяй город.',
 'Не меняй выезд 2026-11-19. Заезд не меняем.',
 'Напомни, выезд 2026-11-19. Заезд не меняем.',
])
def test_checkout_suffix_does_not_swallow_independent_edits_or_questions(text):
    row=F['date_cases'][0]
    got=resolve_date_answer(copy.deepcopy(row['before']['trip']),copy.deepcopy(row['before']['pending_question']),text,NOW)
    assert got.get('handled') is False and not got['operations']

def test_plain_iso_calendar_answer_remains_supported():
    row=F['date_cases'][0];s=copy.deepcopy(row['before']['trip']);p=copy.deepcopy(row['before']['pending_question'])
    got=resolve_date_answer(s,p,'2026-11-19',NOW)
    assert got['handled'] and got['operations'][0]['value']=={'check_out':'2026-11-19'}

def test_kazan_exact_date_patch_passes_original_schema_without_name_coercion():
    row=F['date_cases'][-1];s=copy.deepcopy(row['before']['trip']);p=row['before']['pending_question']
    value={'operations':[{'type':'upsert_stay','target_id':p['target_id'],'value':{'check_out':'2026-11-19'}}],
        'control':{'action':'none'},'reply':'Дата сохранена.','unknowns':[],'conflicts':[],'speech_act':'CHANGE','information_subject':None}
    assert Draft202012Validator(build_operation_schema(s,dynamic_places=True)).is_valid(value)

def test_nullable_verified_city_misspelling_gets_precise_schema_repair_hint():
    row=F['date_cases'][-1];s=copy.deepcopy(row['before']['trip']);p=row['before']['pending_question']
    stay=next(stay for stay in s['stays'] if stay['id']==p['target_id'])
    assert stay['city']=='Kazan’' and stay['place_id']=='geo:openmeteo:551487'
    value={'operations':[{'type':'upsert_stay','target_id':p['target_id'],'value':{'city':"Kazan'",'place_id':stay['place_id'],'check_out':'2026-11-19'}}],
        'control':{'action':'continue'},'reply':'','unknowns':[],'conflicts':[]}
    schema=build_operation_schema(s,dynamic_places=True)
    assert not Draft202012Validator(schema).is_valid(value)
    hints=operation_schema_feedback(value,schema)
    assert any(h.get('path')==['operations',0,'value','city'] and h.get('rule')=='enum' for h in hints),hints
    assert not Draft202012Validator(schema).is_valid(value),'diagnostics never accept invalid output'

@pytest.mark.parametrize('row',F['geo_cases'],ids=lambda x:x['case_id'])
def test_current_literal_paired_country_filters_query_without_city_admission(row):
    assert current_city_country_hint(row['before']['trip'],row['user'],row['role'],row['query'])==row['expected_country']

@pytest.mark.parametrize('text,query,role,expected',[
 ('Выбираем город Сочи, Россия.','Сочи','destination','RU'),
 ('Выбираем город Казань, Россия.','Казань','destination','RU'),
 ('Выбираем London, Канада.','London','destination','CA'),
])
def test_generic_current_choice_city_country_grammar(text,query,role,expected):
    state=copy.deepcopy(F['geo_cases'][0]['before']['trip'])
    assert current_city_country_hint(state,text,role,query)==expected

@pytest.mark.parametrize('text,query,role',[
 ('Раньше выбирали London, Канада.','London','destination'),
 ('Если выбираем London, Канада.','London','destination'),
 ('Не выбираем London, Канада.','London','destination'),
 ('«Выбираем London, Канада».','London','destination'),
 ('Выбираем Париж, Канада.','London','destination'),
 ('Выбираем London, Канада или Великобритания.','London','destination'),
 ('Выбираем London, Канада. Бюджет 50000 рублей.','London','destination'),
 ('Выбираем London, Канада.','London','origin'),
 ('Паспорт Канады, выбираем London.','London','destination'),
 ('Из London, Канада, едем в Стамбул.','London','destination'),
 ('Выбираем London, неизвестная страна.','London','destination'),
 ('London, Канада.','London','destination'),
 ('Выбираем London, Канада, Россия.','London, Канада','destination'),
])
def test_unowned_or_noncurrent_country_pair_does_not_grant_authority(text,query,role):
    assert current_city_country_hint(F['geo_cases'][0]['before']['trip'],text,role,query) is None

@pytest.mark.parametrize('change',['trip','version','role','stay','hash','expiry'])
def test_country_ref_ownership_source_and_ttl_remain_strict(change):
    row=F['geo_cases'][0];s=copy.deepcopy(row['before']['trip']);request={'kind':'city','role':'destination','stay_id':'new-stay-1','query':'Сочи','country_iso2':'RU'}
    co=PlaceResolutionCoordinator(None,None,None,clock=lambda:NOW,offline_countries=True)
    countries,_,_=_lexicon()
    ref=co._country_ref(s,request,{'country_iso2':'RU','name':countries['RU']['ru']},{'source_provider':'unicode-cldr','source_sha256':DATA_SHA256,'retrieved_at':NOW.isoformat(),'expires_at':(NOW+timedelta(hours=24)).isoformat()})
    assert co._checked_country_ref(ref,s,request)=='RU'
    if change=='trip':ref['trip_id']='foreign'
    elif change=='version':ref['state_version']=s['state_version']+1
    elif change=='role':ref['role']='origin'
    elif change=='stay':ref['stay_id']='foreign'
    elif change=='hash':ref['source_sha256']='0'*64
    elif change=='expiry':ref['expires_at']=(NOW-timedelta(seconds=1)).isoformat()
    with pytest.raises(PlaceResolutionError):co._checked_country_ref(ref,s,request)

@pytest.mark.parametrize('text',[
 'Моё бронирование отменилось, можно вернуть его как было?',
 'Можно восстановить моё бронирование?',
 'Как вернуть отменённую бронь?',
])
def test_booking_restore_subject_never_means_undo_trip_age(text):
    assert order_status_question(text) is True
    before=copy.deepcopy(F['booking_cases'][0]['before']['trip']);original=copy.deepcopy(before)
    answer=canonical_order_status_answer(text,before,[])
    assert answer and 'брон' in answer.casefold()
    assert before==original

@pytest.mark.parametrize('text',[
 'Верни последнюю правку.',
 'Верни как было.',
 'Отмени изменение возраста ребёнка.',
 'Верни мой бюджет как было, а бронирование пока не трогай.',
 'Бронирование отменилось. Верни дату заезда на 2026-11-12.',
 'Забронируй первый вариант.',
 'Оплати существующий заказ.',
])
def test_unrelated_trip_undo_and_new_order_intents_stay_outside_booking_readonly(text):
    assert order_status_question(text) is False

def test_actual_fixture_proves_booking_undo_changed_age_without_any_order():
    row=F['booking_cases'][0]
    assert row['orders_before']==row['orders_after']==[]
    assert row['before']['trip']['travelers'][2]['age']==7
    assert row['after']['trip']['travelers'][2]['age'] is None
    assert row['after']['state_version']==row['before']['state_version']+1
    assert any(c['value']['operations']==[{'type':'undo','value':{},'target_id':None}] for c in row['contracts'] if c['value'])
