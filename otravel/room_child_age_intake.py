"""Closed current age replacement by an owned room ordinal, no global ID guess."""
from __future__ import annotations

import re

from .clarifications import _age_answer
from .domain import TripState

FINDING_ID='HARNESS-004-ROOM-SCOPED-AGE-OMISSION-078-001'
_ORDINALS={'первого':1,'второго':2,'третьего':3,'четвертого':4,'пятого':5,
    'шестого':6,'седьмого':7,'восьмого':8,'девятого':9,'десятого':10}
_OLD_FORMS={'нуля':'ноль','одного':'один','двух':'два','трех':'три','четырех':'четыре',
    'пяти':'пять','шести':'шесть','семи':'семь','восьми':'восемь','девяти':'девять',
    'десяти':'десять','одиннадцати':'одиннадцать','двенадцати':'двенадцать','тринадцати':'тринадцать',
    'четырнадцати':'четырнадцать','пятнадцати':'пятнадцать','шестнадцати':'шестнадцать','семнадцати':'семнадцать'}
_ADJECTIVES={'нулевого':0,'однолетнего':1,'двухлетнего':2,'трехлетнего':3,'четырехлетнего':4,
    'пятилетнего':5,'шестилетнего':6,'семилетнего':7,'восьмилетнего':8,'девятилетнего':9,
    'десятилетнего':10,'одиннадцатилетнего':11,'двенадцатилетнего':12,'тринадцатилетнего':13,
    'четырнадцатилетнего':14,'пятнадцатилетнего':15,'шестнадцатилетнего':16,'семнадцатилетнего':17}
_ROOM=r'(?:[1-9][0-9]?(?:-?го)?|'+'|'.join(_ORDINALS)+r')'
_AGE=r'(?:[0-9]{1,2}|[а-я]+)'
_POSITIVE=re.compile(r'(?:(?:новому|этому)\s+)?ребенку\s+из\s+(?P<room>'+_ROOM+r')\s+номера\s+'
    r'теперь\s+(?P<new>'+_AGE+r')\s+(?:лет|год|года)\s+вместо\s+(?P<old>'+_AGE+r')(?:\s+(?:лет|год|года))?')
_NEGATIVE=re.compile(r'(?P<child>ребенка|'+'|'.join(_ADJECTIVES)+r')\s+из\s+(?P<room>'+_ROOM+r')\s+номера'
    r'(?:\s+(?:первой|второй)\s+семьи)?(?:\s+вообще)?\s+не\s+меняй')


def _ordinal(value):
    return _ORDINALS.get(value) if value in _ORDINALS else int(re.sub(r'-?го$','',value))


def _unique_child(room,people):
    if len(room.traveler_ids)!=len(set(room.traveler_ids)):
        return None
    members=[people.get(identifier)for identifier in room.traveler_ids]
    if any(person is None for person in members):
        return None
    children=[person for person in members if person.type=='child']
    return children[0]if len(children)==1 else None


def resolve_room_child_age_replacement(state,text):
    """Resolve only the full closed message; all ambiguity stays with old flow.

    Optional second clause may preserve a child in another owned room. Nothing
    is selected by age alone, family label, newest ID, or implicit first match.
    """
    unhandled={'handled':False,'operations':[]}
    if (not isinstance(text,str)or not 0<len(text)<=2000
            or any(character in text for character in '?;:\"\'«»“”`\x1e')):
        return unhandled
    normalized=' '.join(text.casefold().replace('ё','е').split())
    clauses=[value.strip()for value in re.split(r'[.!]',normalized)if value.strip()]
    if len(clauses)not in(1,2):
        return unhandled
    positive=_POSITIVE.fullmatch(clauses[0])
    negative=_NEGATIVE.fullmatch(clauses[1])if len(clauses)==2 else None
    if positive is None or len(clauses)==2 and negative is None:
        return unhandled
    new=_age_answer(positive['new'])
    old=_age_answer(_OLD_FORMS.get(positive['old'],positive['old']))
    if new is None or old is None:
        return unhandled
    try:
        data=TripState.model_validate(state)
    except (TypeError,ValueError):
        return unhandled
    if len(data.stays)!=1:
        return unhandled
    stay=data.stays[0];number=_ordinal(positive['room'])
    if not 1<=number<=len(stay.rooms):
        return unhandled
    people={person.id:person for person in data.travelers}
    room=stay.rooms[number-1]
    child=_unique_child(room,people)
    if child is None or child.birth_date is not None or type(child.age)is not int or child.age!=old:
        return unhandled
    if sum(person==child.id for item in stay.rooms for person in item.traveler_ids)!=1:
        return unhandled
    if negative is not None:
        keep_number=_ordinal(negative['room'])
        if not 1<=keep_number<=len(stay.rooms)or keep_number==number:
            return unhandled
        kept=_unique_child(stay.rooms[keep_number-1],people)
        adjective_age=_ADJECTIVES.get(negative['child'])
        if kept is None or kept.id==child.id or adjective_age is not None and kept.age!=adjective_age:
            return unhandled
    return {'handled':True,'operations':[{'type':'update_traveler','target_id':child.id,'value':{'age':new}}],
        'reply':f'Возраст ребёнка из номера №{number}: {old} → {new} лет.',
        'note':{'finding_id':FINDING_ID,'code':'explicit_owned_room_child_age_replacement',
            'source':'closed_current_room_ordinal_old_age_new_age','stay_id':stay.id,'room_id':room.id,
            'traveler_id':child.id,'old_age':old,'new_age':new,'other_travelers_changed':False}}
