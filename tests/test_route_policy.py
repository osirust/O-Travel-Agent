"""Declared route topology and exact preference clearing; no HTTP/model/DB."""
from copy import deepcopy
from datetime import date

import pytest
from jsonschema import Draft202012Validator

from otravel import domain
from otravel.clarifications import derive_pending_field, human_question, resolve_short_answer
from otravel.domain import DomainError, TripState, apply_operations, sample_trip, validate_trip
from otravel.inventory import DemoInventory, reprice_plan, validate_plan
from otravel.llm_contract import build_operation_schema
from otravel.planning import assemble_plan, required_components, validate_quote


def op(kind, value):
    return {"type": kind, "value": value}


def set_route(kind, end_city=None):
    return op("set_route_policy", {"kind": kind, **({"end_city": end_city} if end_city is not None else {})})


def test_one_way_removes_only_implicit_return_without_adding_a_stay():
    before = sample_trip(); original = before.model_dump(mode="json")
    changed, changes = apply_operations(before, [set_route("one_way")])
    assert [(leg.origin, leg.destination) for leg in changed.legs] == [("Казань", "Стамбул"), ("Стамбул", "Анталья")]
    assert [leg.id for leg in changed.legs] == ["leg-out", "leg-between"]
    assert changed.stays == before.stays and changed.travelers == before.travelers
    assert changed.budget == before.budget and changes and validate_trip(changed) == []
    assert before.model_dump(mode="json") == original


def test_open_jaw_final_city_is_a_transport_endpoint_not_a_hotel_stay():
    before = sample_trip()
    changed, _ = apply_operations(before, [set_route("open_jaw", "Москва")])
    assert changed.origin == "Казань" and changed.stays == before.stays
    assert changed.legs[-1].origin == "Анталья" and changed.legs[-1].destination == "Москва"
    assert changed.legs[-1].departure_date == date(2026, 11, 13)
    assert len(changed.stays) == 2 and len(changed.legs) == 3
    assert validate_trip(changed) == []


def test_clear_preference_removes_exact_key_and_keeps_other_preferences():
    state, _ = apply_operations(sample_trip(), [op("set_preference", {"key":"departure_time","value":"early_morning"}),
        op("set_preference", {"key":"price_priority","value":"lowest_total","weight":0.7})])
    before = state.model_dump(mode="json")
    changed, changes = apply_operations(state, [op("clear_preference", {"key":"departure_time"})])
    assert [p.key for p in changed.preferences] == ["price_priority"] and changed.preferences[0].weight == .7
    assert changed.stays == state.stays and changed.legs == state.legs and changes
    assert state.model_dump(mode="json") == before


def test_guest_nationality_slot_sets_only_explicit_search_country():
    state = sample_trip()
    result = resolve_short_answer(state, {"field":"search.guest_nationality"}, "Россия")
    assert result["handled"] and result["operations"] == [op("set_search_policy", {"guest_nationality":"RU"})]
    changed, _ = apply_operations(state, result["operations"])
    assert changed.search_policy.guest_nationality == "RU" and state.search_policy.guest_nationality is None
    assert changed.origin == state.origin and changed.travelers == state.travelers


def envelope(*operations):
    return {"operations":list(operations),"control":{"action":"none"},"reply":"Изменения сохранены.","unknowns":[],"conflicts":[]}


@pytest.mark.parametrize("kind,end", [("round_trip",None),("one_way",None),("undecided",None),("open_jaw",None),("open_jaw","Москва")])
def test_new_policy_schema_accepts_typed_supported_intents_and_applies(kind,end):
    state=sample_trip();operation=set_route(kind,end)
    Draft202012Validator(build_operation_schema(state)).validate(envelope(operation))
    after,_=apply_operations(state,[operation])
    assert after.route_policy.kind==kind and after.route_policy.end_city==end


@pytest.mark.parametrize("value", [{"kind":"unknown"},{"kind":True},{"end_city":"Москва"},
    {"kind":"open_jaw","end_city":"Сербия"},{"kind":"one_way","end_city":"Москва"},
    {"kind":"round_trip","end_city":"Москва"},{"kind":"open_jaw","end_city":""},
    {"kind":"one_way","flight_nonstop":True},{"kind":"one_way","end_city":"Atlantis"}])
def test_unknown_country_endpoint_extra_fields_and_incompatible_policy_are_rejected(value):
    state=sample_trip();operation=op("set_route_policy",value);before=state.model_dump(mode="json")
    assert not Draft202012Validator(build_operation_schema(state)).is_valid(envelope(operation))
    with pytest.raises(DomainError):apply_operations(state,[operation])
    assert state.model_dump(mode="json")==before


def test_legacy_saved_json_defaults_to_existing_round_trip_contract():
    raw=sample_trip().model_dump(mode="json");raw.pop("route_policy")
    state=TripState.model_validate(raw)
    assert state.route_policy.kind=="round_trip" and validate_trip(state)==[]
    assert domain.route_edges(state)==[("Казань","Стамбул",date(2026,11,6)),
        ("Стамбул","Анталья",date(2026,11,9)),("Анталья","Казань",date(2026,11,13))]


@pytest.mark.parametrize("kind,end,last", [("one_way",None,"Анталья"),("open_jaw","Москва","Москва")])
def test_declared_route_reaches_demo_price_coverage_refresh_and_quote_without_phantom_return(kind,end,last):
    state,_=apply_operations(sample_trip(),[set_route(kind,end)])
    result=DemoInventory().search_trip(state);assert result['plans']
    plan=result['plans'][0]
    transport=[o for o in plan['offers'] if o['kind'] in {'flight','train'}]
    assert transport[-1]['destination']==last
    assert [(o['origin'],o['destination']) for o in transport]==[(l.origin,l.destination) for l in state.legs]
    assert [o['component_id'] for o in transport]==[l.id for l in state.legs]
    assert len([o for o in plan['offers'] if o['kind']=='hotel'])==2
    assert all(o.get('city')!='Москва' for o in plan['offers'] if o['kind']=='hotel')
    assert plan['total_minor']==sum(o['total_minor'] for o in plan['offers'])
    assert validate_plan(state,plan)==[]
    refreshed=reprice_plan(plan);assert validate_plan(state,refreshed)==[]
    assembled=assemble_plan(state,refreshed['offers'])
    assert assembled['coverage']['complete'] and not assembled['coverage']['overlapping']
    assert len([c for c in required_components(state) if c.startswith('leg:')])==len(transport)
    assert assembled['ready_for_simulated_checkout'] and not assembled['ready_for_real_booking']
    assert validate_quote(assembled,state)==[]


def test_one_way_does_not_charge_removed_return_and_preserves_stay_prices():
    old=sample_trip();oldplan=DemoInventory().search_trip(old)['plans'][0]
    state,_=apply_operations(old,[set_route('one_way')]);newplan=DemoInventory().search_trip(state)['plans'][0]
    removed=next(o['total_minor'] for o in oldplan['offers'] if o.get('component_id')=='leg-return')
    assert oldplan['total_minor']-newplan['total_minor']==removed
    assert [o['total_minor'] for o in oldplan['offers'] if o['kind']=='hotel']==[o['total_minor'] for o in newplan['offers'] if o['kind']=='hotel']
    assert any(i['code']=='route_mismatch' for i in validate_plan(state,oldplan))


@pytest.mark.parametrize('kind',["undecided","open_jaw"])
def test_unresolved_end_is_a_specific_clarification_and_cannot_be_quoted(kind):
    state,_=apply_operations(sample_trip(),[set_route(kind)])
    assert len(state.legs)==2 and state.legs[-1].destination=='Анталья'
    code='missing_route_end' if kind=='undecided' else 'missing_route_end_city'
    assert code in {i['code'] for i in validate_trip(state)}
    search=DemoInventory().search_trip(state)
    assert search['plans']==[] and any('обратная дорога' in q or 'закончиться' in q for q in search['questions'])
    assembled=assemble_plan(state,[],demo_fallback=True)
    assert not assembled['ready_for_simulated_checkout'] and assembled['total_minor'] is None
    binding=derive_pending_field([{'code':code}],state)
    assert binding['field']==('route.kind' if kind=='undecided' else 'route.end_city')
    assert binding['target_id'] is None


def test_change_origin_of_open_jaw_keeps_explicit_end_city_independent():
    state,_=apply_operations(sample_trip(),[set_route('open_jaw','Москва')])
    changed,_=apply_operations(state,[op('set_origin',{'city':'Санкт-Петербург'})])
    assert changed.legs[0].origin=='Санкт-Петербург' and changed.legs[-1].destination=='Москва'
    assert [l.id for l in changed.legs]==[l.id for l in state.legs]
    assert changed.stays==state.stays and validate_trip(changed)==[]


def test_dates_and_hotel_edits_keep_one_way_topology_and_stable_modes():
    state=sample_trip();state.legs[1].mode='train'
    state,_=apply_operations(state,[set_route('one_way')])
    changed,_=apply_operations(state,[{'type':'upsert_stay','target_id':'stay-istanbul','value':{'check_out':'2026-11-10'}},
        {'type':'upsert_stay','target_id':'stay-antalya','value':{'check_in':'2026-11-10','hotel_id':'explicit-hotel'}}])
    assert len(changed.legs)==2 and changed.legs[1].departure_date==date(2026,11,10)
    assert changed.legs[1].mode=='train' and [l.id for l in changed.legs]==[l.id for l in state.legs]
    assert changed.legs[-1].destination=='Анталья' and changed.stays[-1].hotel_id=='explicit-hotel'
    assert validate_trip(changed)==[]


def test_partial_date_edit_prunes_forbidden_return_without_inventing_dates():
    state,_=apply_operations(sample_trip(),[set_route('one_way'),
        {'type':'upsert_stay','target_id':'stay-antalya','value':{'check_out':None}}])
    assert len(state.legs)==2 and all(l.destination!='Казань' for l in state.legs)
    assert state.stays[-1].check_out is None and 'missing_check_out' in {i['code'] for i in validate_trip(state)}


def test_removing_a_stay_or_redeclaring_roundtrip_does_not_resurrect_historical_edges():
    state,_=apply_operations(sample_trip(),[set_route('one_way')])
    shortened,_=apply_operations(state,[{'type':'remove_stay','target_id':'stay-antalya','value':{}}])
    assert len(shortened.legs)==1 and shortened.legs[0].id=='leg-out'
    restored,_=apply_operations(shortened,[set_route('round_trip')])
    assert len(restored.legs)==2 and restored.legs[-1].destination=='Казань'
    assert restored.legs[-1].id not in {'leg-between','leg-return'}


def test_switching_mode_clears_old_terminal_city_not_other_trip_fields():
    state,_=apply_operations(sample_trip(),[set_route('open_jaw','Москва')])
    changed,_=apply_operations(state,[set_route('one_way')])
    assert changed.route_policy.end_city is None and changed.stays==state.stays
    assert changed.origin=='Казань' and len(changed.legs)==2


@pytest.mark.parametrize('kind',['one_way','undecided'])
def test_shift_return_does_not_edit_inbound_for_a_route_without_return(kind):
    state,_=apply_operations(sample_trip(),[set_route(kind)]);before=state.model_dump(mode='json')
    with pytest.raises(DomainError) as err:apply_operations(state,[op('shift_return',{'days':2})])
    assert err.value.code=='missing_return_leg' and state.model_dump(mode='json')==before


def test_clear_known_absent_preference_is_idempotent_and_unknown_is_not_ignored():
    state=sample_trip();changed,changes=apply_operations(state,[op('clear_preference',{'key':'departure_time'})])
    assert changed==state and changes==[]
    with pytest.raises(DomainError) as err:apply_operations(state,[op('clear_preference',{'key':'not_saved_unknown'})])
    assert err.value.code=='unknown_preference'


def test_clear_existing_legacy_preference_key_is_allowed_without_replaying_other_preferences():
    raw=sample_trip().model_dump(mode='json');raw['preferences']=[{'key':'quiet','value':True,'weight':.5},
        {'key':'departure_time','value':'early_morning','weight':.7}]
    state=TripState.model_validate(raw);operation=op('clear_preference',{'key':'quiet'})
    Draft202012Validator(build_operation_schema(state)).validate(envelope(operation))
    after,_=apply_operations(state,[operation]);assert [p.key for p in after.preferences]==['departure_time']
    assert after.preferences[0].weight==.7


@pytest.mark.parametrize('value',[{'key':'new_unknown'},{'key':True},{'key':'departure_time','value':'any'},{}, {'key':'departure_time','weight':0}])
def test_preference_clear_schema_and_domain_reject_unknown_or_extra_value(value):
    state=sample_trip();operation=op('clear_preference',value)
    assert not Draft202012Validator(build_operation_schema(state)).is_valid(envelope(operation))
    with pytest.raises(DomainError):apply_operations(state,[operation])


@pytest.mark.parametrize('text,code',[('Россия','RU'),('RU','RU'),('РФ','RU'),('гражданство России','RU'),('Турция','TR'),('Serbia','RS')])
def test_bound_guest_country_answer_is_not_origin_or_destination(text,code):
    state=sample_trip();result=resolve_short_answer(state,{'field':'search.guest_nationality'},text)
    assert result['operations']==[op('set_search_policy',{'guest_nationality':code})]
    after,_=apply_operations(state,result['operations']);assert after.origin==state.origin and after.stays==state.stays
    assert after.travelers==state.travelers and after.search_policy.guest_nationality==code


@pytest.mark.parametrize('text',['Москва','ZZ','Россия или Турция','Не знаю','Georgia'])
def test_unknown_ambiguous_or_city_nationality_never_guesses_a_code(text):
    result=resolve_short_answer(sample_trip(),{'field':'search.guest_nationality'},text)
    assert result['handled'] and result['operations']==[] and 'Гражданство' in result['question']


@pytest.mark.parametrize('text',['Россия и добавь ребёнка','RU, бюджет100000','Если Россия','Россия?','продолжай',
    'В Сербию хочу','To Russia','Хочу в Россию','RU, сними завтрак'])
def test_mixed_question_or_control_answer_not_swallowed_by_country_slot(text):
    result=resolve_short_answer(sample_trip(),{'field':'search.guest_nationality'},text)
    assert not result['handled'] and result['operations']==[]


def test_known_citizenship_does_not_become_a_stale_slot_overwrite():
    state=sample_trip();state.search_policy.guest_nationality='RU'
    assert derive_pending_field(['missing_nationality'],state)['field'] is None
    assert not resolve_short_answer(state,{'field':'search.guest_nationality'},'Турция')['handled']
    result=resolve_short_answer(state,{'field':'search.guest_nationality','context':{'force_clarification':True}},'Турция')
    assert result['operations']==[op('set_search_policy',{'guest_nationality':'TR'})]


def test_human_nationality_question_and_derived_slot_do_not_default_from_origin():
    state=sample_trip()
    question=human_question('search.guest_nationality',state)
    assert question=='Гражданство какой страны использовать для поиска отеля?'
    assert derive_pending_field([question],state)['field']=='search.guest_nationality'
    assert state.search_policy.guest_nationality is None


@pytest.mark.parametrize('answer,kind',[('нет','one_way'),('да','round_trip'),('в одну сторону','one_way'),('в другой город','open_jaw')])
def test_route_kind_short_answers_only_follow_the_bound_route_question(answer,kind):
    state,_=apply_operations(sample_trip(),[set_route('undecided')])
    result=resolve_short_answer(state,{'field':'route.kind'},answer)
    assert result['operations']==[set_route(kind)]
    after,_=apply_operations(state,result['operations']);assert after.route_policy.kind==kind


@pytest.mark.parametrize('city,kind,end',[('Москва','open_jaw','Москва'),('Казань','round_trip',None),('Анталья','one_way',None)])
def test_terminal_city_slot_changes_endpoint_only_and_never_creates_a_hotel(city,kind,end):
    state,_=apply_operations(sample_trip(),[set_route('open_jaw')])
    result=resolve_short_answer(state,{'field':'route.end_city'},city)
    after,_=apply_operations(state,result['operations'])
    assert after.origin=='Казань' and after.stays==state.stays
    assert after.route_policy.kind==kind and after.route_policy.end_city==end


def test_departure_question_without_leg_is_replaced_before_asking_with_addressable_hotel_checkin():
    state=TripState(origin='Казань',stays=[{'id':'s','city':'Стамбул'}])
    question=human_question('leg.departure_date',state)
    assert 'заезд' in question and 'Стамбул' in question and 'отправление' not in question
    binding=derive_pending_field(['На какую дату планируете отправление?'],state)
    assert binding['field']=='stay.check_in' and binding['target_id']=='s'
