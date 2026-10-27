"""Bounded compatibility for new-traveller additions to known room IDs."""
import re
from collections import Counter
from copy import deepcopy

from .domain import Operation, TripState
from .operation_aliases import _without_quoted_spans

_ADD = re.compile(r"\b(?:добавь(?:те)?|добавить|включи(?:те)?|включить|add|include)\s+"
                  r"(?:(?:еще|пожалуйста|нам|нового|новую|одного|одну|двух|трех|четырех|[0-9]+|a|an|another|new)\s+){0,5}"
                  r"(?:ребен\w*|дет\w*|взросл\w*|участник\w*|путешественник\w*|travell?er|child|adult|passenger)\b")
_ROOM = re.compile(r"\b(?:номер\w*|комнат\w*|rooms?)\b")
_MEMBERSHIP = re.compile(r"\b(?:жив\w*|жить|размест\w*|размещ\w*|посели\w*|селить|прожива\w*)\b"
                         r"|\b(?:в|во)\s+(?:(?:перв\w*|втор\w*|трет\w*|четверт\w*|[0-9]+)\s+)?(?:номер\w*|комнат\w*)\b")
_UNASSERTED = re.compile(r"\b(?:если|допустим|предположим|возможно|наверное|может|гипотетически|раньше|ранее|говорил\w*|просил\w*|цитата)\b")
_VETO = re.compile(r"\bне\s+(?:добавля\w*|добавь\w*|включа\w*|применя\w*|меняй|менять)\b|\bпока\s+только\s+обсужд\w*")
_LAYOUT = re.compile(r"\b(?:удали|удалить|убери|убрать|исключи|исключить|сократи|сократить|уменьши|уменьшить|"
                     r"объедини|объединить|слей|поменяй|переставь|перенумеруй|пересели)\b.{0,60}"
                     r"\b(?:номер\w*|комнат\w*|rooms?)\b")
_COUNTS = {'один': 1, 'одна': 1, 'два': 2, 'две': 2, 'три': 3, 'четыре': 4,
           'пять': 5, 'шесть': 6, 'семь': 7, 'восемь': 8, 'девять': 9, 'десять': 10}
_COUNT = re.compile(r"\b([0-9]{1,2}|" + '|'.join(_COUNTS) + r")\s+(?:гостиничн\w*\s+)?(?:номер\w*|комнат\w*|rooms?)\b")


def normalize_sparse_room_additions(state: TripState | dict, operations: list, text: str) -> tuple[list, list[dict]]:
    """Convert a proven addition-only known-room subset into typed patches.

    This is compatibility for LLM snapshot-shaped partial output. New room IDs,
    layout/count/removal intents, changed old memberships and uncertain text do
    not qualify. The caller still validates the complete atomic batch; this
    function neither adds occupants nor changes omitted canonical room rows.
    """
    original = deepcopy(operations)
    if not isinstance(text, str) or not 0 < len(text) <= 20_000:
        return original, []
    text = _without_quoted_spans(' '.join(text.casefold().replace('ё', 'е').split()))
    if text is None or _UNASSERTED.search(text) or _VETO.search(text):
        return original, []
    clauses = [clause for clause in re.findall(r'[^.!?;]+[.!?;]?', text) if not clause.rstrip().endswith('?')]
    current = ' '.join(clauses)
    if (not _ADD.search(current) or not any(_ROOM.search(clause) and _MEMBERSHIP.search(clause) for clause in clauses)
            or any(_LAYOUT.search(clause) for clause in clauses)):
        return original, []
    counts = {int(match[1]) if match[1].isdigit() else _COUNTS[match[1]] for match in _COUNT.finditer(current)}
    state = TripState.model_validate(state)
    data = [op.model_dump(mode='json') if isinstance(op, Operation) else deepcopy(op) for op in original]
    existing_people = {person.id for person in state.travelers}
    added = Counter(op['value']['id'] for op in data
                    if op.get('type') == 'add_traveler' and isinstance(op.get('value'), dict)
                    and isinstance(op['value'].get('id'), str))
    new_people = {identifier for identifier, count in added.items()
                  if isinstance(identifier, str) and identifier not in existing_people and count == 1}
    if not new_people:
        return original, []
    saved = {stay.id: stay for stay in state.stays}
    setters = Counter(op.get('target_id') for op in data if op.get('type') == 'upsert_stay'
                      and isinstance(op.get('value'), dict) and 'rooms' in op['value'])
    result, notes = [], []
    for item, operation in zip(data, original, strict=True):
        target, value = item.get('target_id'), item.get('value')
        stay = saved.get(target) if isinstance(target, str) else None
        eligible = (item.get('type') == 'upsert_stay' and isinstance(value, dict) and stay is not None
                    and setters[target] == 1 and isinstance(value.get('rooms'), list)
                    and 0 < len(value['rooms']) < len(stay.rooms)
                    and (not counts or counts == {len(stay.rooms)})
                    and value.get('id', target) == target
                    and ('city' not in value or value['city'] == stay.city))
        if not eligible:
            result.append(operation)
            continue
        rooms = {room.id: room for room in stay.rooms}
        seen, new_assignments, patches = set(), set(), []
        valid = True
        for room in value['rooms']:
            identifier = room.get('id') if isinstance(room, dict) else None
            members = room.get('traveler_ids') if isinstance(room, dict) else None
            if (not isinstance(identifier, str) or identifier not in rooms or identifier in seen
                    or set(room) != {'id', 'traveler_ids'} or not isinstance(members, list)
                    or any(not isinstance(person, str) for person in members) or len(set(members)) != len(members)):
                valid = False
                break
            prior = set(rooms[identifier].traveler_ids)
            extra = set(members) - prior
            if (not prior.issubset(members) or not extra.issubset(new_people)
                    or extra.intersection(new_assignments)):
                valid = False
                break
            seen.add(identifier)
            new_assignments.update(extra)
            patches.append({'type': 'set_room_members', 'target_id': identifier,
                            'value': {'stay_id': target, 'traveler_ids': deepcopy(members)}})
        if not valid or not new_assignments:
            result.append(operation)
            continue
        remainder = {key: deepcopy(val) for key, val in value.items() if key != 'rooms'}
        if set(remainder) - {'id'}:
            retained = {**item, 'value': remainder}
            result.append(Operation.model_validate(retained) if isinstance(operation, Operation) else retained)
        result.extend(Operation.model_validate(patch) if isinstance(operation, Operation) else patch for patch in patches)
        notes.append({'code': 'sparse_new_traveler_room_patch_converted', 'target_id': target,
                      'patched_room_ids': [patch['target_id'] for patch in patches],
                      'preserved_room_ids': [room.id for room in stay.rooms if room.id not in seen],
                      'new_traveler_ids': sorted(new_assignments), 'guest_membership_inferred': False,
                      'source': 'explicit_new_traveler_addition', 'full_layout_replacement': False})
    return (original, []) if len(result) > 30 else (result, notes)
