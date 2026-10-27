"""Complete only a uniquely stated fresh multi-city family room layout."""
import re
from copy import deepcopy

from .domain import MVP_LIMITS, DomainError, Operation, TripState, apply_operations
from .operation_aliases import _without_quoted_spans

_NUMBERS = {'один': 1, 'одна': 1, 'два': 2, 'две': 2, 'три': 3, 'четыре': 4,
            'пять': 5, 'шесть': 6, 'семь': 7, 'восемь': 8, 'девять': 9, 'десять': 10}
_ORDINALS = {'перв': 1, 'втор': 2, 'трет': 3, 'четверт': 4, 'пят': 5, 'шест': 6,
             'седьм': 7, 'восьм': 8, 'девят': 9, 'десят': 10}
_AGES = {**_NUMBERS, 'одного': 1, 'двух': 2, 'трех': 3, 'четырех': 4, 'пяти': 5,
         'шести': 6, 'семи': 7, 'восьми': 8, 'девяти': 9, 'десяти': 10}
_UNIVERSAL = re.compile(r'\bв\s+(?:каждом\s+городе|обоих\s+городах|каждом\s+из\s+городов)\b')
_COUNT = re.compile(r'\b([0-9]{1,2}|' + '|'.join(_NUMBERS) + r')\s+(?:отдельн\w*\s+)?(?:номер(?:а|ов)?|комнат(?:а|ы)?)(?!\w)')
_ROOM = re.compile(r'\b(?:в|во)\s+(?P<ordinal>первом|втором|третьем|четвертом|пятом|шестом|седьмом|восьмом|девятом|десятом|[0-9]{1,2})'
                   r'(?:\s+(?P<noun>номере|комнате))?(?=\s|[,;.!?]|$)')
_ADULT = re.compile(r'\b(?P<ordinal>перв(?:ый|ая|ого|ую)|втор(?:ой|ая|ого|ую)|трет(?:ий|ья|ьего|ью)|четверт(?:ый|ая|ого|ую)|'
                    r'пят(?:ый|ая|ого|ую)|шест(?:ой|ая|ого|ую)|седьм(?:ой|ая|ого|ую)|восьм(?:ой|ая|ого|ую)|'
                    r'девят(?:ый|ая|ого|ую)|десят(?:ый|ая|ого|ую)|[0-9]{1,2})\s+взросл(?:ый|ая|ого|ую)\b')
_CHILD = re.compile(r'\bребен(?:ок|ком|ка|ку)(?:\s+(?P<age>[0-9]{1,2}|' + '|'.join(_AGES) + r')\s+(?:лет|год|года))?\b')
_UNASSERTED = re.compile(r'\b(?:если|допустим|предположим|может|возможно|наверное|гипотетически|раньше|ранее|прежде|цитата|говорил\w*)\b')
_VETO = re.compile(r'\bне\s+(?:нуж\w*|надо|требу\w*|распредел\w*|размещ\w*|сели\w*|брониру\w*|созда\w*|применя\w*)\b')


def _ordinal(token):
    if token.isdigit():
        return int(token)
    return next((value for prefix, value in _ORDINALS.items() if token.startswith(prefix)), None)


def _people_in_clause(body, candidate):
    adults = [person for person in candidate.travelers if person.type == 'adult']
    children = [person for person in candidate.travelers if person.type == 'child']
    references = list(_ADULT.finditer(body))
    if not references:
        return None
    selected = []
    for reference in references:
        number = _ordinal(reference['ordinal'])
        if number is None or not 1 <= number <= len(adults):
            return None
        selected.append(adults[number - 1].id)
    for reference in _CHILD.finditer(body):
        age = reference['age']
        if age is None:
            matches = children
        else:
            value = int(age) if age.isdigit() else _AGES[age]
            matches = [person for person in children if person.age == value]
        if len(matches) != 1:
            return None
        selected.append(matches[0].id)
    rest = _CHILD.sub(' ', _ADULT.sub(' ', body))
    if (re.search(r'[0-9]', rest) or set(re.findall(r'\w+', rest)) - {'с', 'и', 'отдельно', 'вместе', 'один', 'одна'}
            or len(set(selected)) != len(selected)):
        return None
    return set(selected)


def complete_initial_room_layouts(state: TripState | dict, operations: list, text: str) -> tuple[list, dict]:
    """Use a complete literal room→adult→known-group bijection, or ask.

    The helper does not invent parties, groups, ages or a default one-family
    allocation. Only fresh empty stays qualify; existing layouts are immutable
    here. Candidate projection supplies IDs, not evidence of user intent.
    """
    original = deepcopy(operations)
    meta = {'notes': [], 'unknowns': [], 'pending_context': None}
    if not isinstance(text, str) or not 0 < len(text) <= 20_000:
        return original, meta
    unquoted = _without_quoted_spans(' '.join(text.casefold().replace('ё', 'е').split()))
    if unquoted is None or not re.search(r'\b(?:номер\w*|комнат\w*)\b', unquoted):
        return original, meta
    state = TripState.model_validate(state)
    try:
        candidate, _ = apply_operations(state, original)
    except (DomainError, ValueError):
        return original, meta  # Other semantic errors retain their normal path.
    if (len(candidate.stays) < 2 or not _UNIVERSAL.search(unquoted)
            or all(stay.rooms for stay in candidate.stays)):
        return original, meta

    known_count = None

    def ask(message):
        meta['unknowns'] = [message]
        meta['pending_context'] = {'field': 'stay.rooms', 'target_id': None,
            'room_target_selection_required': True, 'force_clarification': True}
        if known_count is not None:
            meta['pending_context'].update(requested_room_count=known_count, room_reallocation_required=True)
        meta['notes'] = [{'code': 'initial_room_mapping_unproven', 'source': 'bounded_literal_mapping',
                          'status': 'clarification_required', 'question': message}]
        return original, meta

    prior_ids = {stay.id for stay in state.stays}
    if any(stay.id in prior_ids or stay.rooms for stay in candidate.stays):
        return original, meta  # Never rewrite a prior or already-proposed layout.
    if _UNASSERTED.search(unquoted) or _VETO.search(unquoted):
        return ask('Раскладка номеров пока не изменена. Уточните, какое распределение нужно действительно применить для каждого города.')
    clauses = [clause.strip() for clause in re.findall(r'[^.!?;]+[.!?;]?', unquoted)
               if not clause.rstrip().endswith('?')]
    universal = [clause for clause in clauses if _UNIVERSAL.search(clause)]
    counts = {int(match[1]) if match[1].isdigit() else _NUMBERS[match[1]]
              for clause in universal for match in _COUNT.finditer(clause)}
    if len(counts) != 1:
        return ask('Уточните, одинаковое ли число номеров нужно в каждом городе, и перечислите состав каждого номера.')
    count = next(iter(counts))
    known_count = count
    meta['pending_context'] = None
    if len(candidate.stays) != 2 and any(re.search(r'\bв\s+обоих\s+городах\b', clause) for clause in universal):
        return ask('Уточните, к каким двум городам относится распределение: в маршруте больше двух остановок. На остальные города его не переношу.')
    groups = candidate.groups
    all_members = [identifier for group in groups for identifier in group.traveler_ids]
    people = {person.id for person in candidate.travelers}
    if (not 1 <= count <= MVP_LIMITS['rooms_per_stay'] or len(groups) != count
            or any(not group.keep_separate or not group.traveler_ids for group in groups)
            or len(all_members) != len(set(all_members)) or set(all_members) != people):
        return ask(f'Число номеров ({count}) известно. Уточните полный состав отдельных семей: без однозначных групп не распределяю людей по номерам.')
    mapping, used_groups = {}, set()
    for clause in clauses:
        markers = list(_ROOM.finditer(clause))
        explicit = False
        for index, marker in enumerate(markers):
            explicit = explicit or marker['noun'] is not None
            if not explicit:
                continue  # An unrelated ordinal without a room noun is not a room.
            ordinal = _ordinal(marker['ordinal'])
            body = clause[marker.end():markers[index + 1].start() if index + 1 < len(markers) else len(clause)].strip(' ,:.!;')
            selected = _people_in_clause(body, candidate)
            matching = [group for group in groups if selected is not None and set(group.traveler_ids) == selected]
            if ordinal not in range(1, count + 1) or ordinal in mapping or len(matching) != 1 or matching[0].id in used_groups:
                return ask('Число номеров известно. Уточните, какие взрослые и дети живут в первом, втором и остальных номерах; ребёнка при необходимости укажите по возрасту.')
            mapping[ordinal] = list(matching[0].traveler_ids)
            used_groups.add(matching[0].id)
    if set(mapping) != set(range(1, count + 1)) or len(used_groups) != len(groups):
        return ask('Число номеров известно. Нужен полный состав каждого номера в каждом городе, включая взрослых и детей; недостающие назначения не угадываю.')
    occupied = {item.id for collection in (candidate.travelers, candidate.groups, candidate.stays, candidate.legs) for item in collection}
    room_ids = [f'new-room-{number}' for number in range(1, MVP_LIMITS['rooms_per_stay'] + 1)
                if f'new-room-{number}' not in occupied][:count]
    if len(room_ids) != count:
        return ask('Не удалось безопасно создать идентификаторы номеров. Уточните раскладку через форму размещения.')
    result = deepcopy(original)
    for stay in candidate.stays:
        indices = [index for index, op in enumerate(result)
                   if (op.type if isinstance(op, Operation) else op.get('type')) == 'upsert_stay'
                   and (op.target_id if isinstance(op, Operation) else op.get('target_id')) == stay.id]
        if not indices:
            return ask('Уточните, к какой новой остановке относится распределение номеров.')
        op = result[indices[-1]]
        value = op.value if isinstance(op, Operation) else op['value']
        value['rooms'] = [{'id': room_ids[ordinal - 1], 'traveler_ids': list(mapping[ordinal])}
                          for ordinal in range(1, count + 1)]
    try:
        apply_operations(state, result)
    except (DomainError, ValueError):
        return ask('Распределение не совместимо с сохранёнными условиями. Уточните состав номеров, сохранив обязательные требования.')
    meta['notes'] = [{'code': 'initial_literal_family_rooms_completed', 'source': 'explicit_universal_ordinal_group_bijection',
        'stay_ids': [stay.id for stay in candidate.stays], 'rooms_per_stay': count,
        'room_membership': {str(key): value for key, value in sorted(mapping.items())},
        'people_or_groups_invented': False, 'existing_layouts_changed': False}]
    return result, meta
