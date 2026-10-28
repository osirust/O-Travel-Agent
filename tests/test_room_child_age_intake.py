"""Adversarial scope of room-owned literal child age replacement."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from otravel.domain import TripState, apply_operations
from otravel.room_child_age_intake import resolve_room_child_age_replacement

FIXTURE=Path(__file__).parents[1]/'fixtures/regression/room4-age-current-078-20261028.json'


def source():
    value=json.loads(FIXTURE.read_text(encoding='utf-8'))
    return value['before_trip'],value['input']


def test_exact_captured_explicit_room4_age_uses_owned_id_not_the_other_nine_year_old():
    before,text=source();original=deepcopy(before)
    result=resolve_room_child_age_replacement(before,text)
    assert result['handled']is True
    assert result['operations']==[{'type':'update_traveler','target_id':'new-traveler-10','value':{'age':10}}]
    after,_=apply_operations(TripState.model_validate(before),result['operations'])
    people={person.id:person for person in after.travelers}
    assert people['new-traveler-10'].age==10 and people['new-traveler-8'].age==9
    assert before==original
    expected=deepcopy(before)
    next(person for person in expected['travelers']if person['id']=='new-traveler-10')['age']=10
    assert after.model_dump(mode='json')==TripState.model_validate(expected).model_dump(mode='json')


@pytest.mark.parametrize('text',[
    'Ребёнку из 4-го номера теперь 10 лет вместо 9.',
    'Этому ребенку из четвертого номера теперь десять лет вместо девяти!',
    'Новому ребёнку из четвертого номера теперь 0 лет вместо 9.',
    'Новому ребёнку из четвертого номера теперь 17 лет вместо девяти.',
])
def test_bounded_declension_digits_and_child_age_limits(text):
    before,_=source()
    assert resolve_room_child_age_replacement(before,text)['handled']is True


@pytest.mark.parametrize('text',[
    'Если ребёнку из четвертого номера теперь десять лет вместо девяти, что будет?',
    'Ребёнку из четвертого номера теперь десять лет вместо девяти?',
    'Раньше ребёнку из четвертого номера теперь десять лет вместо девяти.',
    '«Ребёнку из четвертого номера теперь десять лет вместо девяти».',
    'Не меняй: ребёнку из четвертого номера теперь десять лет вместо девяти.',
    'Ребёнку из четвертого номера теперь десять лет вместо девяти. Бюджет 300000 рублей.',
    'Ребёнку из четвертого номера теперь десять лет вместо девяти и добавь взрослого.',
    'Ребёнку из четвертого номера теперь десять лет вместо девяти. Подожди, я ещё дополню.',
    'Ребёнку из четвертого номера теперь десять лет вместо девяти. Ребёнку из второго номера теперь десять лет вместо девяти.',
    'Ребёнку из четвертого номера теперь 18 лет вместо 9.',
    'Ребёнку из четвертого номера теперь -1 год вместо 9.',
    'Ребёнку из четвертого номера теперь десять лет вместо восьми.',
    'Ребёнку теперь десять лет вместо девяти.',
    'Новому ребёнку теперь десять лет вместо девяти.',
    'Ребёнку из пятого номера теперь десять лет вместо девяти.',
    'Ребёнку из четвертого номера теперь десять лет вместо девяти. Девятилетнего из четвертого номера вообще не меняй.',
    'Ребёнку из четвертого номера теперь десять лет вместо девяти. Девятилетнего из пятого номера не меняй.',
    'Ребёнку из четвертого номера теперь десять лет вместо девяти. Восьмилетнего из второго номера не меняй.',
    '\x1eOTRAVEL_TYPED_V1:{"kind":"commands"}',
])
def test_whole_message_unasserted_extra_edit_wrong_age_or_unbound_room_is_unhandled(text):
    before,_=source();original=deepcopy(before)
    assert resolve_room_child_age_replacement(before,text)=={'handled':False,'operations':[]}
    assert before==original


@pytest.mark.parametrize('change',['two_stays','two_children','no_child','birthdate','unknown_age','duplicate_member','missing_room'])
def test_ambiguous_or_non_age_authoritative_state_never_selects_a_first_person(change):
    before,text=source()
    if change=='two_stays':
        extra=deepcopy(before['stays'][0]);extra['id']='other-stay';before['stays'].append(extra)
    elif change=='two_children':before['stays'][0]['rooms'][3]['traveler_ids'].append('new-traveler-8')
    elif change=='no_child':before['stays'][0]['rooms'][3]['traveler_ids']=['new-traveler-1']
    elif change=='birthdate':next(person for person in before['travelers']if person['id']=='new-traveler-10')['birth_date']='2017-01-01'
    elif change=='unknown_age':next(person for person in before['travelers']if person['id']=='new-traveler-10')['age']=None
    elif change=='duplicate_member':before['stays'][0]['rooms'][3]['traveler_ids'].append('new-traveler-10')
    else:before['stays'][0]['rooms']=before['stays'][0]['rooms'][:3]
    assert resolve_room_child_age_replacement(before,text)['handled']is False
