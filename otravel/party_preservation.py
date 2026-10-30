"""Current party facts checked independently of model omissions and defaults.

The helpers copy operations and never store state, choose an ambiguous person,
or call a model. Typed commands are outside this model-output guard.
"""
from __future__ import annotations

import re
from copy import deepcopy

from .domain import MVP_LIMITS, DomainError, TripState, apply_operations
from .operation_aliases import _without_quoted_spans
from .traveler_addition_grounding import _dependent_fields

CHILD_REMOVAL = 'HARNESS-004-F40-CHILD-REMOVAL-MISREAD-024-001'
UNREQUESTED_PARTY = 'HARNESS-004-F40-UNREQUESTED-PARTY-029-001'
INCLUSIVE_CENSUS = 'HARNESS-004-F40-INCLUSIVE-PARTY-CENSUS-026-001'
_NONCURRENT = re.compile(r'\b(?:если|бы|допустим|предположим|может|возможно|наверное|'
    r'раньше|ранее|прежде|вчера|цитата|пример|if|would|maybe|previously|suppose)\b')
_NO_CHANGE = re.compile(r'\b(?:не\s+(?:удаля\w*|убира\w*|исключа\w*|меня\w*|применя\w*|добавля\w*)|'
    r'никого\s+не|пока\s+не\s+(?:надо|нужно))\b')
_NUMBERS = {'один': 1, 'одна': 1, 'два': 2, 'две': 2, 'двое': 2, 'три': 3,
    'трое': 3, 'четыре': 4, 'четверо': 4, 'пять': 5, 'пятеро': 5, 'шесть': 6,
    'шестеро': 6, 'семь': 7, 'семеро': 7, 'восемь': 8, 'девять': 9, 'десять': 10}
_NUMBER = r'(?:[0-9]{1,2}|' + '|'.join(sorted(_NUMBERS, key=len, reverse=True)) + r')'
_INCLUSIVE = re.compile(r'\s*(?:(?:я\s+)?имел[аи]?\s+в\s+виду\s+|нас\s+|едем\s+|едут\s+|'
    r'в\s+поездке\s+)?(?P<total>' + _NUMBER + r')\s+(?:человек\w*\s+)?всего\s*[,—:-]?\s*'
    r'(?:включая|в\s+том\s+числе)\s+(?:одного\s+)?ребенка\s*')
_CHILD_NONTRIP = re.compile(r'\s*(?:(?:нет|теперь|пожалуйста)\s*[,—:-]?\s*)*'
    r'(?P<subject>(?:(?:мой|этот|единственный)\s+)?ребенок|все\s+дети|дети|оба\s+ребенка)'
    r'(?:\s+(?P<binding>[a-z0-9_-]+|[0-9]{1,2}\s+(?:лет|года|год)))?\s+'
    r'не\s+(?:едет|едут|поедет|поедут|летит|летят|участвует|участвуют)'
    r'(?:\s+(?:в\s+(?:этой|нашей|всей)\s+поездке|с\s+нами))?\s*')
_PERSON_ASSERTION = re.compile(r'\b(?:[0-9]{1,2}|' + '|'.join(_NUMBERS) + r')\s+'
    r'(?:взросл\w*|дет\w*|ребен\w*|участник\w*|человек\w*|путешественник\w*|пассажир\w*)\b|'
    r'\b(?:я\s+(?:один|одна|взрослый|взрослая)|еду\s+(?:один|одна)|'
    r'едем\s+(?:вдвоем|втроем|вчетвером|впятером)|нас\s+' + _NUMBER + r'|'
    r'(?:добавь|добавьте|включи|включите|добавляем|присоединится)\b[^.!?;]{0,80}'
    r'(?:ребен\w*|дет\w*|взросл\w*|участник\w*|человек\w*|сына|дочь|сестру|брата|маму|папу|меня))\b')
_ADULT_FACT = re.compile(r'\b(?:взросл\w*|adult\w*|я|меня|муж|жена|женой|мама|папа|мамой|папой|'
    r'едем\s+(?:вдвоем|втроем|вчетвером|впятером)|нас\s+' + _NUMBER + r')\b')
_ENGLISH_PERSON = re.compile(r'\b(?:[0-9]{1,2}|one|two|three|four|five)\s+(?:adults?|children|kids?|people|travelers?)\b')


def _data(value):
    return value.model_dump(mode='json') if hasattr(value, 'model_dump') else deepcopy(value)


def _sentences(text):
    if not isinstance(text, str) or not 0 < len(text) <= 20_000 or text.startswith('\x1eOTRAVEL_TYPED_V1:'):
        return []
    key = ' '.join(text.casefold().replace('ё', 'е').split())
    clean = _without_quoted_spans(re.sub(r'`[^`]*`', ' ', key))
    if clean is None or '`' in clean:
        return []
    return [piece.strip(' .!;') for piece in re.findall(r'[^.!?;]+[.!?;]?', clean)
        if not piece.rstrip().endswith('?') and not piece.lstrip().startswith('>')
        and not _NONCURRENT.search(piece) and not _NO_CHANGE.search(piece)]


def _child_targets(data, sentences):
    children = [p for p in data.get('travelers', []) if p.get('type') == 'child']
    requests = []
    for sentence in sentences:
        matched = _CHILD_NONTRIP.fullmatch(sentence)
        if not matched:
            continue
        binding = matched['binding']
        if binding:
            age = re.fullmatch(r'([0-9]{1,2})\s+(?:лет|года|год)', binding)
            matches = [p['id'] for p in children if (p.get('age') == int(age[1]) if age else p['id'].casefold() == binding)]
            requests.append(matches if len(matches) == 1 else None)
        elif matched['subject'] in {'дети', 'все дети', 'оба ребенка'}:
            requests.append([p['id'] for p in children] if matched['subject'] != 'оба ребенка' or len(children) == 2 else None)
        else:
            requests.append([children[0]['id']] if len(children) == 1 else None)
    if not requests:
        return None, False
    if any(request is None for request in requests) or len({tuple(r) for r in requests}) != 1:
        return None, True
    return requests[0], False


def _owned_party_answer(data, pending, sentences):
    if (not isinstance(pending, dict) or pending.get('field') not in {'party.adults', 'party.children'}
            or pending.get('target_id') is not None
            or type(pending.get('state_version')) is not int
            or pending['state_version'] != data.get('state_version')):
        return False
    return any(re.fullmatch(r'(?:' + _NUMBER + r'|нет|детей нет|дети не едут)', s) for s in sentences)


def guard_current_party_operations(state, operations, text, pending=None):
    """Return copied operations and precise server-owned clarification metadata."""
    data = _data(state)
    copied = [_data(operation) for operation in operations]
    meta = {'notes': [], 'unknowns': [], 'conflicts': [], 'pending_context': None, 'block_batch': False}
    if not isinstance(data, dict):
        return copied, meta
    sentences = _sentences(text)

    def ask(message, field, finding):
        meta.update(unknowns=[message], conflicts=[message], block_batch=True,
            pending_context={'field': field, 'target_id': None, 'force_clarification': True})
        meta['notes'].append({'finding_id': finding, 'code': 'party_identity_clarification_required',
            'status': 'clarification_required', 'question': message})
        return [], meta

    targets, ambiguous = _child_targets(data, sentences)
    if ambiguous:
        return ask('Какой ребёнок не едет? Укажите сохранённого участника или его возраст; состав пока сохраняю.',
            'party.removal', CHILD_REMOVAL)
    if targets:
        selected = set(targets)
        # Canonical removal cleans existing room/group references. Apply last,
        # so unrelated setters cannot restore a removed participant afterwards.
        copied = [op for op in copied if not (op.get('type') in {'remove_traveler', 'update_traveler'}
            and op.get('target_id') in selected)]
        copied.extend({'type': 'remove_traveler', 'target_id': identifier, 'value': {}} for identifier in targets)
        meta['pending_context'] = {'children_known': True,
            'children_count': sum(p.get('type') == 'child' and p.get('id') not in selected
                for p in data.get('travelers', []))}
        meta['notes'].append({'finding_id': CHILD_REMOVAL, 'code': 'current_child_nonparticipation_grounded',
            'status': 'grounded', 'target_ids': targets, 'source': 'current_whole_trip_literal'})
    elif isinstance(text, str) and re.search(r'\bреб[её]нок\b.*\bне\s+(?:едет|поедет|летит)\b', text, re.I):
        # An unqualified model removal must not turn a question, quotation,
        # hypothetical or a hotel/transport-local refusal into trip removal.
        explicit_remove = any(re.search(r'\b(?:удали|убери|исключи)\b', sentence)
            and re.search(r'\b(?:поездк\w*|состав\w*)\b', sentence) for sentence in sentences)
        if not explicit_remove:
            child_ids = {p['id'] for p in data.get('travelers', []) if p.get('type') == 'child'}
            kept = [op for op in copied if not (op.get('target_id') in child_ids and
                (op.get('type') == 'remove_traveler' or op.get('type') == 'update_traveler'
                 and isinstance(op.get('value'), dict) and op['value'].get('age', 'unchanged') is None))]
            if len(kept) != len(copied):
                meta['notes'].append({'finding_id': CHILD_REMOVAL, 'code': 'noncurrent_or_local_child_removal_suppressed',
                    'status': 'suppressed', 'source': 'no_current_whole_trip_nonparticipation'})
                copied = kept

    inclusive = [_INCLUSIVE.fullmatch(sentence) for sentence in sentences]
    totals = {int(m['total']) if m['total'].isdigit() else _NUMBERS[m['total']] for m in inclusive if m}
    if totals:
        try:
            candidate, _ = apply_operations(TripState.model_validate(data), copied)
        except (DomainError, ValueError, TypeError):
            candidate = None
        if (len(totals) != 1 or candidate is None or len(candidate.travelers) != next(iter(totals))
                or not any(p.type == 'child' for p in candidate.travelers)):
            return ask('Вы указали общее число участников, включая ребёнка. Сколько взрослых и детей едет, '
                'и сколько лет ребёнку? Сохранённые личности пока не меняю.', 'party.composition', INCLUSIVE_CENSUS)

    owned_answer = _owned_party_answer(data, pending, sentences)
    asserted = bool(totals) or any(_PERSON_ASSERTION.search(s) or _ENGLISH_PERSON.search(s) for s in sentences)
    existing = {p.get('id') for p in data.get('travelers', [])}
    def authorized_addition(operation):
        kind = operation['value'].get('type')
        if owned_answer and kind == ('adult' if pending['field'] == 'party.adults' else 'child'):
            return True
        return asserted and (kind != 'adult' or bool(totals) or any(_ADULT_FACT.search(s) for s in sentences))
    forbidden = [i for i, op in enumerate(copied) if op.get('type') == 'add_traveler'
        and isinstance(op.get('value'), dict) and op['value'].get('id') not in existing
        and not authorized_addition(op)]
    if forbidden:
        new_ids = {copied[i]['value'].get('id') for i in forbidden} - {None}
        if any(_dependent_fields(op, new_ids) for i, op in enumerate(copied) if i not in forbidden):
            return ask('Состав участников в текущем сообщении не указан. Сколько взрослых и детей едет? '
                'Не добавляю участников и не распределяю их по номерам автоматически.', 'party.composition', UNREQUESTED_PARTY)
        copied = [op for i, op in enumerate(copied) if i not in forbidden]
        meta['notes'].append({'finding_id': UNREQUESTED_PARTY, 'code': 'unrequested_party_addition_suppressed',
            'status': 'suppressed', 'new_ids': sorted(new_ids), 'source': 'no_current_party_fact_or_owned_answer'})
    return copied, meta


def resolve_owned_party_census_answer(state, pending, text):
    """Consume only a closed adult-count + zero-child answer to a fresh slot.

    No earlier model preference/operation is reused. Extra edits deliberately
    return unhandled so an otherwise valid budget/date clause is never lost.
    """
    no = {'handled': False, 'operations': []}
    data = _data(state)
    if (not isinstance(data, dict) or not isinstance(pending, dict)
            or pending.get('field') not in {'party.adults', 'party.children', 'party.composition'}
            or pending.get('target_id') is not None
            or type(pending.get('state_version')) is not int
            or pending['state_version'] != data.get('state_version')
            or not isinstance(text, str) or not 0 < len(text) <= 200):
        return no
    raw = ' '.join(text.casefold().replace('ё', 'е').split()).strip(' .!')
    clean = _without_quoted_spans(raw)
    if clean != raw or _NONCURRENT.search(raw) or _NO_CHANGE.search(raw):
        return no
    match = re.fullmatch(r'(?:(?:едут|едем|поедут|поедем|в\s+поездке)\s+)?(?P<adults>' + _NUMBER
        + r')\s+взросл(?:ый|ая|ых|ые|ого)\s*[,;.]?\s*(?:и\s+)?(?:детей\s+нет|дети\s+не\s+едут|без\s+детей)'
        + r'(?:\s*[.;]\s*(?:нужен|нужна|нужно)\s+(?P<rooms>' + _NUMBER + r')\s+номер(?:а|ов)?)?', raw)
    if not match:
        return no
    requested = int(match['adults']) if match['adults'].isdigit() else _NUMBERS[match['adults']]
    adults = [p for p in data.get('travelers', []) if p.get('type') == 'adult']
    if match['rooms']:
        room_count = int(match['rooms']) if match['rooms'].isdigit() else _NUMBERS[match['rooms']]
        stays = data.get('stays', [])
        if (requested != 1 or room_count != 1 or len(stays) != 1 or len(stays[0].get('rooms', [])) > 1):
            return no
    if not 1 <= requested <= MVP_LIMITS['travelers'] or len(adults) > requested:
        question = 'Состав взрослых уже сохранён. Кого именно нужно оставить или убрать? Число не определяет личность участника.'
        return {'handled': True, 'operations': [], 'question': question,
            'pending_context': {'field': 'party.composition', 'target_id': None, 'force_clarification': True}}
    used = {p.get('id') for collection in ('travelers', 'groups', 'stays', 'legs')
        for p in data.get(collection, [])}
    used.update(room.get('id') for stay in data.get('stays', []) for room in stay.get('rooms', []))
    operations = []
    number = 1
    for _ in range(requested - len(adults)):
        while f'traveler-adult-{number}' in used:
            number += 1
        identifier = f'traveler-adult-{number}'
        used.add(identifier)
        operations.append({'type': 'add_traveler', 'value': {'id': identifier, 'type': 'adult'}})
    operations.extend({'type': 'remove_traveler', 'target_id': p['id'], 'value': {}}
        for p in data.get('travelers', []) if p.get('type') == 'child')
    if match['rooms']:
        # Reuse the canonical owned room-count resolver after an explicitly
        # projected party; never choose a stay, participant or room split.
        try:
            projected, _ = apply_operations(TripState.model_validate(data), operations)
        except (DomainError, TypeError, ValueError):
            return no
        from .room_intent import resolve_room_count_answer
        candidate = projected.model_dump(mode='json')
        owned_room = {'field': 'stay.rooms', 'target_id': candidate['stays'][0]['id'],
            'state_version': candidate['state_version']}
        room_answer = resolve_room_count_answer(candidate, owned_room, '1')
        if not room_answer.get('handled') or room_answer.get('question'):
            return no
        operations.extend(room_answer['operations'])
    return {'handled': True, 'operations': operations,
        'pending_context': {'children_known': True, 'children_count': 0}}
