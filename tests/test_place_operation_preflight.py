"""Conflict preflight must understand an already verified non-MVP city."""
import pytest
from test_place_ref_intent_grounding import KYOTO, MOSCOW_US, _place, _state

from otravel.domain import DomainError, Operation
from otravel.harness import operation_conflicts


@pytest.mark.parametrize('with_id',[False,True])
def test_existing_world_stay_echo_is_valid_preflight(with_id):
    state=_state()
    value={'city':KYOTO.canonical_name,'check_in':'2026-11-02'}
    if with_id:
        value['place_id']=KYOTO.place_id
    assert operation_conflicts(state,[Operation(type='upsert_stay',target_id='stop-1',value=value)])==[]


def test_same_city_label_with_distinct_verified_identities_is_a_conflict():
    state=_state()
    other=_place(524901,'Москва','RU')
    state.location_refs[other.place_id]=other
    ops=[Operation(type='set_origin',value={'city':MOSCOW_US.canonical_name,'place_id':ref.place_id})
        for ref in (MOSCOW_US,other)]
    assert operation_conflicts(state,ops)


def test_conflict_preflight_cannot_admit_wrong_ref_name_or_unverified_id():
    state=_state()
    for value in ({'city':'Неизвестный город','place_id':KYOTO.place_id},
                  {'city':KYOTO.canonical_name,'place_id':'geo:openmeteo:99999999'}):
        with pytest.raises(DomainError):
            operation_conflicts(state,[Operation(type='upsert_stay',target_id='stop-1',value=value)])


def test_legacy_city_aliases_stay_equivalent():
    assert operation_conflicts(_state(),[Operation(type='set_origin',value={'city':'Казань'}),
        Operation(type='set_origin',value={'city':'Kazan'})])==[]


@pytest.mark.parametrize('name',['Москва','Moscow','Москвы'])
def test_known_alias_keeps_the_selected_country_identity(name):
    state=_state()
    assert operation_conflicts(state,[Operation(type='set_origin',value={'city':name,'place_id':MOSCOW_US.place_id}),
        Operation(type='set_origin',value={'city':'Москва','place_id':MOSCOW_US.place_id})])==[]
