"""Do not turn an explicitly unknown initial composition into typed people.

An overall head count is not an adult count. This bounded model-only guard
handles fresh rosters with a current explicit statement that composition has
not been named; ordinary NLU with known categories remains outside its scope.
"""
from __future__ import annotations

import re
from copy import deepcopy

from .country_intake import _NUMBERS
from .route_policy_intent import current_clauses

FINDING_ID = 'HARNESS-004-PARTY-CATEGORY-GUESS-025-001'
_COUNT = r'(?:\d{1,2}|' + '|'.join(re.escape(word) for word in sorted(_NUMBERS, key=len, reverse=True)) + r')'
_TOTAL = re.compile(r'\bнас\s+(?P<count>' + _COUNT + r')(?!\w)|\bвсего\s+(?P<people>' + _COUNT
    + r')\s+(?:человек\w*|участник\w*|путешественник\w*)\b')
_UNKNOWN = re.compile(r'\bсостав\s+(?:(?:я|мы|еще|пока)\s+){0,3}не\s+'
    r'(?:перечислил\w*|назвал\w*|указал\w*|уточнил\w*|определил\w*)\b|'
    r'\bсостав\s+(?:(?:еще|пока)\s+)?(?:неизвестен|не\s+(?:указан|назван|уточнен|определен))\b')
_KNOWN_CATEGORIES = re.compile(r'\b(?:взросл\w*|ребен\w*|дет\w*|adult\w*|child\w*|kid\w*|'
    r'муж|жена|супруг\w*|мама|папа|сын\w*|доч\w*)\b')


def guard_unknown_party_categories(before_state, operations, text):
    """Suppress guessed additions/dependencies, preserving independent facts."""
    copied = [operation.model_dump(mode='json') if hasattr(operation, 'model_dump') else deepcopy(operation)
              for operation in operations]
    meta = {'notes': [], 'unknowns': [], 'conflicts': [], 'pending_context': None}
    data = before_state.model_dump(mode='json') if hasattr(before_state, 'model_dump') else before_state
    if (not isinstance(data, dict) or data.get('travelers') or not isinstance(text, str)
            or not 0 < len(text) <= 20_000):
        return copied, meta
    clauses = current_clauses(text)
    current = ' '.join(clauses)
    if not _UNKNOWN.search(current) or _KNOWN_CATEGORIES.search(current):
        return copied, meta
    totals = set()
    for match in _TOTAL.finditer(current):
        if match['count'] and re.search(r'\bу\s+$', current[:match.start()]):
            continue  # 'У нас четыре номера' is not a head count.
        token = match['count'] or match['people']
        totals.add(int(token) if token.isascii() and token.isdigit() else _NUMBERS[token])
    if len(totals) != 1 or not 1 <= next(iter(totals)) <= 10:
        return copied, meta
    total = next(iter(totals))
    guessed = {op['value'].get('id') for op in copied if isinstance(op, dict)
        and op.get('type') == 'add_traveler' and isinstance(op.get('value'), dict)
        and isinstance(op['value'].get('id'), str)}
    result, suppressed = [], []
    for op in copied:
        if not isinstance(op, dict) or not isinstance(op.get('value', {}), dict):
            result.append(op)
            continue
        kind, value = op.get('type'), op.get('value', {})
        dependent = (kind in {'upsert_group', 'set_room_members'}
            and isinstance(value.get('traveler_ids'), list) and bool(guessed.intersection(value['traveler_ids'])))
        if (kind == 'add_traveler' or dependent
                or kind in {'update_traveler', 'remove_traveler'} and op.get('target_id') in guessed):
            suppressed.append(kind)
            continue
        if kind == 'upsert_stay' and isinstance(value.get('rooms'), list) and any(
                isinstance(room, dict) and isinstance(room.get('traveler_ids'), list)
                and guessed.intersection(room['traveler_ids']) for room in value['rooms']):
            remainder = {key: val for key, val in value.items() if key != 'rooms'}
            suppressed.append('upsert_stay.rooms')
            if remainder:
                result.append({**op, 'value': remainder})
            continue
        result.append(op)
    question = f'Вы указали {total} участников, но состав ещё не назван. Сколько взрослых и сколько детей поедет?'
    meta['notes'] = [{'code': 'unknown_initial_party_categories_preserved', 'finding_id': FINDING_ID,
        'declared_total': total, 'suppressed_operations': suppressed}]
    meta['unknowns'] = [question]
    meta['pending_context'] = {'field': 'party.adults', 'target_id': None, 'declared_total': total, 'force': True}
    return result, meta
