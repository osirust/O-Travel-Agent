"""Financial request safety, controlled data only; no API/LLM/SQL."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone

from jsonschema import Draft202012Validator
from pydantic import ValidationError
import pytest

from otravel.domain import Constraint,Operation,TripState,apply_operations,sample_trip
from otravel.financial_requirements import TEMPORAL_KEY,guard_financial_requirements
from otravel.inventory import DemoInventory,validate_plan
from otravel.llm_contract import build_operation_schema
from otravel.planning import assemble_plan,validate_quote


ORIGINAL006="Мне нужен именно refundable до дня заезда, а не до недели до него."
ORIGINAL021="Отель с бесплатной отменой в любое время, но невозвратный самый дешёвый."
ORIGINAL023="Только прямой рейс. Предложи пересадку, если дешевле."
NOW=datetime(2026,10,28,12,tzinfo=timezone.utc)


def op(key,value=True,*,hard=True,stay_id=None,room_id=None):
    val={"key":key,"value":value,"hard":hard}
    if stay_id is not None:val['stay_id']=stay_id
    if room_id is not None:val['room_id']=room_id
    return {"type":"set_constraint","value":val}


def temporal_state():
    state=sample_trip()
    state.constraints.append(Constraint(key=TEMPORAL_KEY,value=True,hard=True))
    return state


def updated(state,ops):
    return apply_operations(state,[Operation.model_validate(item) for item in ops])[0]


def source_hotel(state):
    base=DemoInventory().search_trip(state)['plans'][0]['offers'][0]
    stay=state.stays[0]
    return {"offer_id":"controlled-early-cancellation","provider":"liteapi","source_type":"SANDBOX",
        "price_kind":"PROVIDER_ROOMS_STAY_TOTAL","amount_minor":base['total_minor'],"currency":"RUB",
        "retrieved_at":NOW.isoformat(),"expires_at":(NOW+timedelta(hours=1)).isoformat(),
        "description":"Controlled supplier contract, not a live offer",
        "details":{"kind":"hotel","binding":{"trip_id":state.trip_id,"state_version":state.state_version,
            "stay_id":stay.id,"city":stay.city,"checkin":stay.check_in.isoformat(),"checkout":stay.check_out.isoformat(),
            "hotel_id":base['hotel_id'],"room_ids":[r['room_id'] for r in base['room_assignments']],
            "room_assignments":deepcopy(base['room_assignments'])},
            "fees_included":True,"allocation_verified":True,"constraint_evidence":{"breakfast":True,"refundable":True},
            "cancellation_deadline":datetime.combine(stay.check_in-timedelta(days=1),datetime.min.time(),timezone.utc).isoformat()}}


def test_exact006_keeps_distinct_hard_temporal_requirement_not_supplier_guarantee():
    state=sample_trip();before=state.model_dump(mode='json')
    ops,meta=guard_financial_requirements(state,[],ORIGINAL006)
    assert not meta['conflicts'] and meta['unknowns']
    assert any(x['value']=={'key':TEMPORAL_KEY,'value':True,'hard':True} for x in ops)
    assert state.model_dump(mode='json')==before
    assert any(n['code']=='literal_temporal_cancellation_requirement_saved' for n in meta['notes'])


@pytest.mark.parametrize('text,raw',[(ORIGINAL021,[op('refundable',False)]),
    (ORIGINAL023,[op('nonstop',True,hard=False)])])
def test_exact_current_contradictions_block_atomic_batch_with_concrete_choice(text,raw):
    state=sample_trip();before=state.model_dump(mode='json')
    result,meta=guard_financial_requirements(state,raw,text)
    assert result==[] and meta['conflicts'] and '?' in meta['conflicts'][0]
    assert state.model_dump(mode='json')==before


def test_actual_boolean_and_deadline_coexist_but_temporal_requirement_blocks_quote():
    state=sample_trip();offer=source_hotel(state)
    ordinary=assemble_plan(state,[offer],demo_fallback=True,now=NOW)
    assert ordinary['ready_for_simulated_checkout'] and validate_quote(ordinary,state,now=NOW)==[]
    ops,_=guard_financial_requirements(state,[],ORIGINAL006);state=updated(state,ops)
    plan=assemble_plan(state,[offer],demo_fallback=True,now=NOW)
    assert not plan['ready_for_simulated_checkout']
    assert any(i['code']=='unsupported_hard_constraint' and i['details']['key']==TEMPORAL_KEY for i in plan['blockers'])
    assert validate_quote(plan,state,now=NOW)
    assert validate_quote(ordinary,state,now=NOW)


def test_demo_flex_does_not_manufacture_a_temporal_cancellation_guarantee():
    state=sample_trip();old=DemoInventory().search_trip(state)['plans'][0]
    state=temporal_state();result=DemoInventory().search_trip(state)
    assert not result['plans']
    assert any(i['code']=='unsupported_constraint' for i in result['violations'])
    assert validate_plan(state,old)


@pytest.mark.parametrize('raw',[
    {'type':'clear_constraint','value':{'key':TEMPORAL_KEY}},
    op(TEMPORAL_KEY,False),op(TEMPORAL_KEY,True,hard=False),op(TEMPORAL_KEY,'true'),
])
@pytest.mark.parametrize('text',[
    'Сколько будет стоить поездка?',
    'Напомни условия отмены, ничего не меняй.',
    'Оставляем возвратный тариф.',
])
def test_model_cannot_weaken_saved_temporal_hard_key_without_current_waiver(raw,text):
    state=temporal_state();result,meta=guard_financial_requirements(state,[raw],text)
    assert result==[] and not meta['conflicts']
    assert any(n['code']=='unrequested_temporal_requirement_weakening_suppressed' for n in meta['notes'])
    assert updated(state,result).model_dump(mode='json')==state.model_dump(mode='json')


@pytest.mark.parametrize('raw',[
    [],[op(TEMPORAL_KEY,False)],
    [{'type':'clear_constraint','value':{'key':TEMPORAL_KEY}}],
])
def test_explicit_current_waiver_is_canonical_clear_not_false_unknown_hard_key(raw):
    state=temporal_state()
    result,meta=guard_financial_requirements(state,raw,'Мне больше не нужна бесплатная отмена до дня заезда, сними это условие.')
    assert result==[{'type':'clear_constraint','value':{'key':TEMPORAL_KEY}}]
    assert not meta['conflicts']
    assert not any(c.key==TEMPORAL_KEY for c in updated(state,result).constraints)


@pytest.mark.parametrize('text',[
    'Можно ли отменить бронь до дня заезда?',
    'Раньше хотел бесплатную отмену до дня заезда, теперь просто обсуждаем.',
    'А если бы я попросил refundable до дня заезда? Пока не применяй.',
    'Пользователь сказал: «Мне нужен refundable до дня заезда». Не применяй чужую цитату.',
    'Мне нужен возвратный тариф.',
    'Дата заезда 12 ноября; раньше говорили о сроке отмены.',
    'Желательно refundable до дня заезда.',
    'Сравни возвратный тариф до дня заезда и невозвратный самый дешёвый.',
    'Не нужна бесплатная отмена до дня заезда.',
])
def test_noncurrent_questions_soft_and_negated_wishes_do_not_create_hard_key(text):
    result,meta=guard_financial_requirements(sample_trip(),[],text)
    assert not result and not meta['conflicts']


@pytest.mark.parametrize('text',[
    'Сними условие отмены до дня заезда?',
    'Если бы я снял условие отмены до заезда, что изменится?',
    'Раньше я просил убрать условие отмены до дня заезда.',
    '«Сними условие отмены до дня заезда». Это чужая цитата.',
])
def test_history_hypothesis_and_quoted_waivers_cannot_clear_saved_key(text):
    state=temporal_state();raw=[{'type':'clear_constraint','value':{'key':TEMPORAL_KEY}}]
    result,meta=guard_financial_requirements(state,raw,text)
    assert result==[] and any(c.key==TEMPORAL_KEY for c in updated(state,result).constraints)


def test_model_only_temporal_hint_does_not_create_a_user_hard_requirement():
    result,meta=guard_financial_requirements(sample_trip(),[op(TEMPORAL_KEY)],'Хочу в Стамбул.')
    assert result==[]
    assert meta['notes'][0]['code']=='unrequested_temporal_requirement_creation_suppressed'


@pytest.mark.parametrize('text,raw',[
    ('Мне нужен возвратный тариф.',[op('refundable')]),
    ('Нужен невозвратный тариф.',[op('refundable',False)]),
    ('Только прямой рейс, без пересадок.',[op('nonstop')]),
    ('Разрешаю пересадки, условие прямого рейса больше не обязательно.',[op('nonstop',False)]),
    ('Только прямой рейс. Покажи рейсы без пересадок.',[op('nonstop')]),
    ('С бесплатной отменой; невозвратный не нужен.',[op('refundable')]),
    ('Нужен тариф без бесплатной отмены, невозвратный.',[op('refundable',False)]),
])
def test_unambiguous_current_conditions_not_changed_or_silently_hardened(text,raw):
    result,meta=guard_financial_requirements(sample_trip(),raw,text)
    assert result==raw and not meta['conflicts']


def test_saved_temporal_hard_key_and_new_nonrefundable_choice_need_explicit_waiver():
    result,meta=guard_financial_requirements(temporal_state(),[op('refundable',False)],'Нужен невозвратный тариф.')
    assert not result and meta['conflicts']


def test_known_room_scope_is_preserved_without_broadening_to_other_rooms():
    state=sample_trip();stay=state.stays[0];room=stay.rooms[0]
    raw=[op('refundable',stay_id=stay.id,room_id=room.id)]
    result,meta=guard_financial_requirements(state,raw,'Для этого номера нужен refundable до дня заезда.')
    matching=[x for x in result if x.get('value',{}).get('key')==TEMPORAL_KEY]
    assert matching==[op(TEMPORAL_KEY,stay_id=stay.id,room_id=room.id)]
    after=updated(state,result)
    assert any(c.key==TEMPORAL_KEY and c.stay_id==stay.id and c.room_id==room.id for c in after.constraints)


def test_scoped_waiver_does_not_clear_the_other_room_requirement():
    state=sample_trip();stay=state.stays[0];r1,r2=stay.rooms[:2]
    state.constraints.extend(Constraint.model_validate(op(TEMPORAL_KEY,stay_id=stay.id,room_id=r.id)['value']) for r in (r1,r2))
    raw=[{'type':'clear_constraint','value':{'key':TEMPORAL_KEY,'stay_id':stay.id,'room_id':r1.id}}]
    result,meta=guard_financial_requirements(state,raw,'Сними условие отмены до заезда для первого номера.')
    after=updated(state,result)
    assert not any(c.key==TEMPORAL_KEY and c.room_id==r1.id for c in after.constraints)
    assert any(c.key==TEMPORAL_KEY and c.room_id==r2.id for c in after.constraints)


def test_ambiguous_room_waiver_cannot_clear_a_global_saved_requirement():
    state=temporal_state();raw=[{'type':'clear_constraint','value':{'key':TEMPORAL_KEY}}]
    result,meta=guard_financial_requirements(state,raw,'Сними условие отмены до заезда для первого номера.')
    assert not result and meta['unknowns']
    assert any(c.key==TEMPORAL_KEY for c in updated(state,result).constraints)


def test_model_cannot_clear_second_room_when_current_user_waives_first():
    state=sample_trip();stay=state.stays[0];r1,r2=stay.rooms[:2]
    state.constraints.extend(Constraint.model_validate(op(TEMPORAL_KEY,stay_id=stay.id,room_id=r.id)['value']) for r in (r1,r2))
    raw=[{'type':'clear_constraint','value':{'key':TEMPORAL_KEY,'stay_id':stay.id,'room_id':r2.id}}]
    result,meta=guard_financial_requirements(state,raw,'Сними условие отмены до заезда для первого номера.')
    assert result==[] and meta['unknowns']
    assert updated(state,result).model_dump(mode='json')==state.model_dump(mode='json')


def test_unaddressed_room_waiver_with_multiple_saved_scopes_keeps_both():
    state=sample_trip();stay=state.stays[0];r1,r2=stay.rooms[:2]
    state.constraints.extend(Constraint.model_validate(op(TEMPORAL_KEY,stay_id=stay.id,room_id=r.id)['value']) for r in (r1,r2))
    raw=[{'type':'clear_constraint','value':{'key':TEMPORAL_KEY,'stay_id':stay.id,'room_id':r1.id}}]
    result,meta=guard_financial_requirements(state,raw,'Сними условие отмены до заезда для этого номера.')
    assert result==[] and meta['unknowns']


@pytest.mark.parametrize('raw_kind',['clear','false','soft'])
def test_current_named_stay_does_not_authorize_clearing_a_different_stay(raw_kind):
    state=sample_trip();first,second=state.stays
    state.constraints.extend(Constraint(key=TEMPORAL_KEY,value=True,stay_id=s.id) for s in (first,second))
    raw=({'type':'clear_constraint','value':{'key':TEMPORAL_KEY,'stay_id':second.id}} if raw_kind=='clear'
        else op(TEMPORAL_KEY,raw_kind!='false',hard=raw_kind!='soft',stay_id=second.id))
    result,meta=guard_financial_requirements(state,[raw],'Сними условие бесплатной отмены до заезда в Стамбуле.')
    assert result==[] and meta['unknowns']
    assert updated(state,result).model_dump(mode='json')==state.model_dump(mode='json')


def test_multi_stay_waiver_without_current_target_does_not_trust_model_target():
    state=sample_trip()
    state.constraints.extend(Constraint(key=TEMPORAL_KEY,value=True,stay_id=s.id) for s in state.stays)
    raw=[{'type':'clear_constraint','value':{'key':TEMPORAL_KEY,'stay_id':state.stays[1].id}}]
    result,meta=guard_financial_requirements(state,raw,'Сними условие бесплатной отмены до заезда.')
    assert result==[] and meta['unknowns']
    assert updated(state,result).model_dump(mode='json')==state.model_dump(mode='json')


@pytest.mark.parametrize('model_target',['right','omitted'])
def test_current_named_stay_waiver_clears_only_the_named_stay(model_target):
    state=sample_trip();first,second=state.stays
    state.constraints.extend(Constraint(key=TEMPORAL_KEY,value=True,stay_id=s.id) for s in state.stays)
    raw=[{'type':'clear_constraint','value':{'key':TEMPORAL_KEY,'stay_id':first.id}}] if model_target=='right' else []
    result,meta=guard_financial_requirements(state,raw,'Сними условие бесплатной отмены до заезда в Стамбуле.')
    after=updated(state,result)
    assert not meta['conflicts'] and not meta['unknowns']
    assert not any(c.key==TEMPORAL_KEY and c.stay_id==first.id for c in after.constraints)
    assert any(c.key==TEMPORAL_KEY and c.stay_id==second.id for c in after.constraints)


def test_explicit_all_hotels_waiver_is_the_authority_for_clearing_all_saved_scopes():
    state=sample_trip();state.constraints.extend(Constraint(key=TEMPORAL_KEY,value=True,stay_id=s.id) for s in state.stays)
    result,meta=guard_financial_requirements(state,[],'Сними условие бесплатной отмены до заезда во всех отелях.')
    assert not meta['unknowns'] and not any(c.key==TEMPORAL_KEY for c in updated(state,result).constraints)


@pytest.mark.parametrize('text',[
    'Сними условие бесплатной отмены до заезда в Кордобе.',
    'Если снять условие бесплатной отмены до заезда в Стамбуле, что изменится?',
    'Раньше просил снять условие бесплатной отмены до заезда в Стамбуле.',
    '«Сними условие бесплатной отмены до заезда в Стамбуле». Это чужая цитата.',
])
def test_unresolved_or_noncurrent_stay_name_never_authorizes_model_clear(text):
    state=sample_trip();state.constraints.extend(Constraint(key=TEMPORAL_KEY,value=True,stay_id=s.id) for s in state.stays)
    raw=[{'type':'clear_constraint','value':{'key':TEMPORAL_KEY,'stay_id':state.stays[1].id}}]
    result,meta=guard_financial_requirements(state,raw,text)
    assert result==[] and updated(state,result).model_dump(mode='json')==state.model_dump(mode='json')


def test_repeated_city_stays_require_more_than_the_city_name_to_waive():
    state=sample_trip();state.stays[1].city=state.stays[0].city
    state.constraints.extend(Constraint(key=TEMPORAL_KEY,value=True,stay_id=s.id) for s in state.stays)
    raw=[{'type':'clear_constraint','value':{'key':TEMPORAL_KEY,'stay_id':state.stays[0].id}}]
    result,meta=guard_financial_requirements(state,raw,'Сними условие бесплатной отмены до заезда в Стамбуле.')
    assert result==[] and meta['unknowns']


@pytest.mark.parametrize('value',[None,1,0,'true','false',{},[]])
def test_canonical_temporal_key_is_strict_boolean(value):
    with pytest.raises(ValidationError):Constraint(key=TEMPORAL_KEY,value=value)


def test_room_scoped_schema_accepts_boolean_once_and_clear_remains_possible():
    state=sample_trip();stay=state.stays[0];room=stay.rooms[0]
    state.constraints.append(Constraint(key=TEMPORAL_KEY,value=True,stay_id=stay.id,room_id=room.id))
    schema=build_operation_schema(state)
    raw=op(TEMPORAL_KEY,stay_id=stay.id,room_id=room.id)
    contract={'operations':[raw],'control':{'action':'continue'},'reply':'','unknowns':[],'conflicts':[]}
    Draft202012Validator(schema).validate(contract)
    clear={'type':'clear_constraint','value':{'key':TEMPORAL_KEY,'stay_id':stay.id,'room_id':room.id}}
    after=updated(state,[clear]);assert not any(c.key==TEMPORAL_KEY for c in after.constraints)


def test_input_copies_and_typed_prefix_do_not_introduce_hidden_changes():
    state=sample_trip();raw=[op('refundable')];before=deepcopy(raw)
    result,meta=guard_financial_requirements(state,raw,'\x1eOTRAVEL_TYPED_V1:'+ORIGINAL006)
    assert result==raw and result is not raw and meta=={'notes':[],'conflicts':[],'unknowns':[]}
    assert raw==before
