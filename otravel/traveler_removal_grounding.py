"""Current-message permission for model-proposed traveler removals.

This narrow guard does not choose a person or prove pronoun/ID attribution.
Positive requests continue to the existing addressed/group/domain resolvers.
Typed commands and canonical short-answer operations bypass this model guard.
"""
from __future__ import annotations

import re
from copy import deepcopy

from .budget_intent import _FRESH, _NON_CURRENT
from .domain import MVP_LIMITS, DomainError, Operation, TripState, apply_operations
from .operation_aliases import _without_quoted_spans
from .party_literals import explicit_no_children
from .preference_grounding import _clauses

FINDING_ID = "HARNESS-004-UNREQUESTED-TRAVELER-REMOVAL-001"
_REMOVE = re.compile(r"\b(?:убери|уберите|убрать|удали|удалите|удалить|исключи|исключите|исключить|"
    r"вычеркни|вычеркните|remove|exclude|drop|delete)\b")
_PERSON = re.compile(r"\b(?:участник\w*|путешественник\w*|гост(?:ь|я|ю|ем|е|и|ей|ям|ями|ях)|взросл\w*|ребен\w*|дет(?:и|ей|ям|ьми|ях)|младен\w*|"
    r"сын\w*|доч\w*|сестр\w*|брат\w*|муж\w*|жен[ауые]\w*|мам\w*|пап\w*|родител\w*|отец|отца|"
    r"он|она|они|я|меня|его|ее|их|нас|всех|adult\w*|child\w*|infant\w*|travell?er\w*|guest\w*|"
    r"sister|brother|mother|father|wife|husband|him|her|them|me)\b")
_NONTRIP = re.compile(r"\bне\s+(?:(?:будет|будут|буду|будем)\s+(?:ехать|лететь|участвовать)|"
    r"ед(?:ет|ут|у|ем|ете)|поед(?:ет|ут|у|ем|ете)|летит|летят|лечу|летим|полетит|полетят|полечу|полетим)\b|"
    r"\b(?:is|are|am)\s+not\s+(?:coming|going|travelling|traveling)\b")
_NONCURRENT = re.compile(r"\b(?:думал\w*|хотел\w*|планировал\w*|советуют|пишут|говорят|"
    r"if|would|hypothetically|previously|earlier|yesterday|suppose)\b")
_NEGATED = re.compile(r"\b(?:не\s+(?:(?:надо|нужно)\s+)?(?:убира\w*|удаля\w*|исключа\w*|вычеркива\w*)|"
    r"(?:do\s+not|don't|never)\s+(?:remove|exclude|drop|delete))\b")
_OTHER_OBJECT = re.compile(r"\s*(?:это\s+|этот\s+|данное\s+)?(?:пожелан\w*|предпочтен\w*|ограничен\w*|"
    r"услови\w*|фильтр\w*|бюджет\w*|детал\w*|отел\w*|гостиниц\w*|номер\w*|проживан\w*|"
    r"рейс\w*|перелет\w*|тариф\w*|preference|constraint|filter|budget|hotel|flight|fare)\b")
_LOCAL_SCOPE = re.compile(r"\b(?:из|с)\s+(?:групп\w*|номер\w*|комнат\w*|семь\w*)\b")
_TRIP_SCOPE = re.compile(r"\b(?:поездк\w*|состав\w*|маршрут\w*|trip|travel\s+party)\b")
_COUNTS = {'один': 1, 'одна': 1, 'два': 2, 'две': 2, 'двое': 2, 'три': 3, 'трое': 3,
    'четыре': 4, 'четверо': 4, 'пять': 5, 'пятеро': 5, 'шесть': 6, 'семь': 7,
    'восемь': 8, 'девять': 9, 'десять': 10}
_AGES = {**_COUNTS, 'ноль': 0, 'нуль': 0, 'одного': 1, 'двух': 2, 'трех': 3, 'четырех': 4,
    'пяти': 5, 'шести': 6, 'семи': 7, 'восьми': 8, 'девяти': 9, 'десяти': 10,
    'одиннадцать': 11, 'одиннадцати': 11, 'двенадцать': 12, 'двенадцати': 12,
    'тринадцать': 13, 'тринадцати': 13, 'четырнадцать': 14, 'четырнадцати': 14,
    'пятнадцать': 15, 'пятнадцати': 15, 'шестнадцать': 16, 'шестнадцати': 16,
    'семнадцать': 17, 'семнадцати': 17}
_COUNT_TOKEN = r'(?:[0-9]{1,2}|' + '|'.join(sorted(_COUNTS, key=len, reverse=True)) + r')'
_AGE_TOKEN = r'(?:[0-9]{1,2}|' + '|'.join(sorted(_AGES, key=len, reverse=True)) + r')'
_AGE_SEQUENCE = rf'{_AGE_TOKEN}(?:\s*(?:,|и|/)\s*{_AGE_TOKEN})*'
_COMPLETE_ROSTER = re.compile(
    r'\s*(?:(?:окончательный|полный|итоговый)\s+состав(?:\s+(?:всей\s+)?поездки)?\s*[:—-]\s*|'
    r'(?:теперь\s+)?(?:едем|едут|поедем|поедут|остаемся|остаются)\s+только\s+|'
    r'в\s+поездке\s+(?:теперь\s+)?(?:остаются\s+)?только\s+)'
    rf'(?P<adults>{_COUNT_TOKEN})\s+взросл\w*\s*(?:и|,)\s*'
    rf'(?P<children>{_COUNT_TOKEN})\s+(?:детей|дети|ребенок|ребенка)\s*[,—:-]?\s*'
    rf'(?P<ages>{_AGE_SEQUENCE})\s+(?:лет|года|год)\s*[.!]?\s*')
_ROSTER_VETO = re.compile(r'\b(?:гипотез\w*|предложени\w*|пример\w*|только\s+обсуждаем|'
    r'пока\s+не\s+примен\w*|не\s+меняй\s+состав|никого\s+не\s+(?:удаляй|убирай))\b')
_ROSTER_QUESTION = 'Уточните, каких участников нужно сохранить или исключить: окончательные числа сами по себе не определяют личность разных участников.'


def _number(token, values):
    return int(token) if token.isascii() and token.isdigit() else values[token]


def _complete_current_roster(text):
    """Closed whole-trip declaration, never a room/group census or ID picker."""
    unquoted = _without_quoted_spans(re.sub(r'`[^`]*`', ' ', text.casefold().replace('ё', 'е')))
    if unquoted is None or '`' in unquoted:
        return None, False
    found = []
    for sentence in re.finditer(r'[^.!?;\n]+(?:[.!?;\n]|$)', unquoted):
        piece = sentence.group()
        if piece.rstrip().endswith('?') or piece.lstrip().startswith('>'):
            continue
        for branch in _FRESH.split(piece):
            if _NON_CURRENT.search(branch) or _NONCURRENT.search(branch):
                continue
            match = _COMPLETE_ROSTER.fullmatch(branch)
            if not match:
                continue
            adults = _number(match['adults'], _COUNTS)
            ages = tuple(sorted(_number(token, _AGES) for token in re.findall(_AGE_TOKEN, match['ages'])))
            if (0 < adults <= MVP_LIMITS['travelers'] and len(ages) == _number(match['children'], _COUNTS)
                    and adults + len(ages) <= MVP_LIMITS['travelers'] and all(0 <= age < 18 for age in ages)):
                found.append((adults, ages))
    if not found:
        return None, False
    return found[0], len(set(found)) != 1 or bool(_ROSTER_VETO.search(unquoted))


def _anonymous_unbound_adults(data, adults):
    ids = {person.get('id') for person in adults}
    for person in adults:
        if (person.get('age') is not None or person.get('birth_date') is not None or person.get('group_id') is not None
                or any(key not in {'id', 'type', 'age', 'birth_date', 'group_id'} and value not in (None, '')
                    for key, value in person.items())):
            return False
    if any(ids.intersection(group.get('traveler_ids', [])) for group in data.get('groups', [])):
        return False
    if any(ids.intersection(room.get('traveler_ids', [])) for stay in data.get('stays', []) for room in stay.get('rooms', [])):
        return False
    def mentions_person(value):
        if isinstance(value, str):
            return value in ids
        if isinstance(value, dict):
            return any(mentions_person(item) for item in value.values())
        if isinstance(value, (list, tuple)):
            return any(mentions_person(item) for item in value)
        return False
    return not mentions_person([data.get('constraints', []), data.get('preferences', [])])


def _complete_roster_permission(data, operations, text):
    declared, conflict = _complete_current_roster(text)
    if declared is None:
        return None
    if conflict:
        return False
    adults = [person for person in data.get('travelers', []) if person.get('type') == 'adult']
    ids = {person.get('id') for person in adults}
    targets = {operation.get('target_id') for operation in operations
        if isinstance(operation, dict) and operation.get('type') == 'remove_traveler'}
    if not targets or not targets <= ids or not _anonymous_unbound_adults(data, adults):
        return False  # No child removal or distinguishing adult identity is inferred.
    try:
        projected, _ = apply_operations(TripState.model_validate(data),
            [Operation.model_validate(operation) for operation in operations])
    except (DomainError, ValueError, TypeError):
        return False
    after = projected.model_dump(mode='json')
    kept_adults = [person for person in after['travelers'] if person['type'] == 'adult']
    child_ages = tuple(sorted(person['age'] for person in after['travelers']
        if person['type'] == 'child' and type(person.get('age')) is int))
    all_children = sum(person['type'] == 'child' for person in after['travelers'])
    return (len(kept_adults) == declared[0] and child_ages == declared[1] and all_children == len(declared[1])
        and {person['id'] for person in kept_adults} == ids - targets
        and _anonymous_unbound_adults({**after, 'stays': [], 'groups': []}, kept_adults))


def _has_current_removal(state: dict, text: str) -> bool:
    owned_ids = [person.get("id") for person in state.get("travelers", [])
        if isinstance(person, dict) and isinstance(person.get("id"), str)]
    for clause in _clauses(text):
        if _NONCURRENT.search(clause) or _NEGATED.search(clause):
            continue
        if _LOCAL_SCOPE.search(clause) and not _TRIP_SCOPE.search(clause):
            continue
        if explicit_no_children(clause) or _NONTRIP.search(clause) and _PERSON.search(clause):
            return True
        for verb in _REMOVE.finditer(clause):
            tail = clause[verb.end():].strip()[:160]
            if _OTHER_OBJECT.match(tail):
                continue
            if (_PERSON.search(tail) or re.search(r"\bиз\s+(?:поездк\w*|состав\w*)\b", tail)
                    or any(re.search(r"(?<!\w)" + re.escape(identifier.casefold()) + r"(?!\w)", tail)
                        for identifier in owned_ids)):
                return True
    return False


def guard_traveler_removal_operations(before_state, operations, text):
    data = before_state.model_dump(mode="json") if hasattr(before_state, "model_dump") else deepcopy(before_state)
    copied = [op.model_dump(mode="json") if hasattr(op, "model_dump") else deepcopy(op) for op in operations]
    meta = {"notes": [], "unknowns": [], "conflicts": []}
    if not isinstance(data, dict) or not isinstance(text, str) or not 0 < len(text) <= 20_000:
        return copied, meta
    if _has_current_removal(data, text):
        return copied, meta
    if any(isinstance(operation, dict) and operation.get('type') == 'remove_traveler' for operation in copied):
        complete = _complete_roster_permission(data, copied, text)
        if complete is True:
            meta['notes'].append({'code': 'complete_current_roster_removal_authorized', 'finding_id': FINDING_ID,
                'status': 'authorized', 'source': 'exact_projected_census_of_anonymous_unbound_adults'})
            return copied, meta
        if complete is False:
            meta['conflicts'].append(_ROSTER_QUESTION)
            meta['unknowns'].append(_ROSTER_QUESTION)
            meta['pending_context'] = {'field': 'party.removal', 'target_id': None, 'force_clarification': True}
    result = []
    for operation in copied:
        if isinstance(operation, dict) and operation.get("type") == "remove_traveler":
            meta["notes"].append({"code": "traveler_removal_not_requested", "finding_id": FINDING_ID,
                "field": "travelers", "target_id": operation.get("target_id"), "status": "suppressed"})
        else:
            result.append(operation)
    return result, meta
