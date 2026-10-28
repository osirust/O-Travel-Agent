"""Bound raw new-person operations to an explicit current roster prohibition.

No person-ID remapping is performed. A forbidden addition used by membership
setters requires a complete model repair before any operation may be committed.
"""
from __future__ import annotations

import re
from copy import deepcopy

from .budget_intent import _QUOTED
from .route_policy_intent import current_clauses

FINDING_ID = 'HARNESS-004-EXPLICIT-NOADD-DUPLICATE-CHILD-NEMO-001'
_INTRO = re.compile(r'\s*(?:(?:повторно|еще|больше|пока|теперь|сейчас|просто|пожалуйста|прошу|важно|'
    r'и|а|но|я\s+прошу)\s*[,—:-]?\s*)*')
_ADVERBS = r'(?:(?:больше|еще|повторно|пока|сейчас)\s+){0,3}'
_ADD_VERB = r'(?:добавляй(?:те)?|добавь(?:те)?|включай(?:те)?)'
_GLOBAL_NOADD = re.compile(r'\bникого\s+' + _ADVERBS + r'не\s+' + _ADD_VERB + r'\b|'
    r'\bне\s+' + _ADD_VERB + r'\s+' + _ADVERBS
    + r'(?:никого|новых\s+(?:участников|людей|путешественников|пассажиров))\b')
_EXCEPTION = re.compile(r'\b(?:кроме|за\s+исключением)\b|\bникого\s+другого\b')
_PERSON_ADD = re.compile(r'\b(?:добавь(?:те)?|добавляем|добавляю|добавляй(?:те)?|включи(?:те)?)\s+'
    r'(?:(?:еще|нового|новую|новых|одного|одну|один|два|двух|две|троих|трех|третьего|\d{1,2})\s+){0,4}'
    r'(?:ребен\w*|дет\w*|взросл\w*|участник\w*|человек\w*|людей|путешественник\w*|'
    r'сына|дочь|сестру|брата|маму|папу|меня)\b')


def _permissions(text):
    unquoted = _QUOTED.sub(' ', text.casefold().replace('ё', 'е'))
    if any(mark in unquoted for mark in ('"', "'", '`', '«', '»', '“', '”', '„')):
        return False, False
    no_add = positive_add = False
    for sentence in re.finditer(r'[^.!?;\n]+(?:[.!?;\n]|$)', unquoted):
        clauses = current_clauses(sentence.group())
        exception = _EXCEPTION.search(' , '.join(clauses)) is not None
        for clause in clauses:
            no_add |= not exception and any(_INTRO.fullmatch(clause[:match.start()])
                for match in _GLOBAL_NOADD.finditer(clause))
            positive_add |= any(_INTRO.fullmatch(clause[:match.start()])
                for match in _PERSON_ADD.finditer(clause))
    return no_add, positive_add


def _dependent_fields(operation, new_ids):
    kind, value = operation.get('type'), operation.get('value', {})
    fields = []
    if kind in {'update_traveler', 'remove_traveler'} and operation.get('target_id') in new_ids:
        fields.append('target_id')
    if not isinstance(value, dict):
        return fields
    def references(values):
        return isinstance(values, list) and any(isinstance(item, str) and item in new_ids for item in values)
    if kind in {'upsert_group', 'set_room_members'} and references(value.get('traveler_ids')):
        fields.append('value.traveler_ids')
    if kind == 'upsert_stay' and isinstance(value.get('rooms'), list):
        for index, room in enumerate(value['rooms']):
            if isinstance(room, dict) and references(room.get('traveler_ids')):
                fields.append(f'value.rooms[{index}].traveler_ids')
    return fields


def guard_traveler_addition_operations(before_state, operations, text):
    """Return copied ops and safe structured intent feedback; never apply ops."""
    copied = [op.model_dump(mode='json') if hasattr(op, 'model_dump') else deepcopy(op) for op in operations]
    meta = {'notes': [], 'unknowns': [], 'conflicts': [], 'repair_required': False,
            'pending_context': None, 'feedback': None}
    data = before_state.model_dump(mode='json') if hasattr(before_state, 'model_dump') else before_state
    if (not isinstance(data, dict) or not isinstance(data.get('travelers'), list) or not data['travelers']
            or not isinstance(text, str) or not 0 < len(text) <= 20_000):
        return copied, meta
    no_add, positive_add = _permissions(text)
    if not no_add:
        return copied, meta
    if positive_add:
        meta['conflicts'] = ['Вы одновременно просите сохранить состав без новых участников и добавить человека. Нужно добавить участника или сохранить состав?']
        meta['unknowns'] = list(meta['conflicts'])
        meta['notes'] = [{'code': 'traveler_addition_instruction_conflict', 'finding_id': FINDING_ID,
                          'status': 'clarification_required'}]
        meta['pending_context'] = {'field': 'party.addition', 'target_id': None, 'force_clarification': True}
        return copied, meta
    existing = [person['id'] for person in data['travelers'] if isinstance(person, dict) and isinstance(person.get('id'), str)]
    if not existing:
        return copied, meta
    forbidden = [index for index, op in enumerate(copied) if isinstance(op, dict)
        and op.get('type') == 'add_traveler' and isinstance(op.get('value'), dict)
        and op['value'].get('id') not in existing]
    if not forbidden:
        return copied, meta
    new_ids = list(dict.fromkeys(copied[index]['value']['id'] for index in forbidden
        if isinstance(copied[index]['value'].get('id'), str)))
    dependencies = []
    for index, operation in enumerate(copied):
        if not isinstance(operation, dict) or index in forbidden:
            continue
        fields = _dependent_fields(operation, set(new_ids))
        if fields:
            dependencies.append({'index': index, 'type': operation.get('type'), 'fields': fields})
    meta['notes'] = [{'code': 'traveler_addition_prohibited', 'finding_id': FINDING_ID,
        'status': 'repair_required' if dependencies else 'suppressed',
        'forbidden_new_traveler_ids': new_ids, 'suppressed_count': 0 if dependencies else len(forbidden)}]
    if dependencies:
        meta['repair_required'] = True
        meta['feedback'] = {'domain_code': 'traveler_addition_prohibited',
            'source': 'current_global_person_no_add', 'existing_traveler_ids': existing,
            'forbidden_new_traveler_ids': new_ids, 'dependent_operations': dependencies,
            'note': 'Вы просили никого не добавлять. Используй только ID сохранённых участников из existing_traveler_ids. Верни полный исправленный пакет операций; не сопоставляй людей только по возрасту.'}
        return copied, meta
    return [op for index, op in enumerate(copied) if index not in forbidden], meta
