"""Ground only explicitly requested preservation of current selected hotel IDs."""
import re
from copy import deepcopy

from .domain import Operation, TripState
from .geography import _CITY_ALIASES
from .operation_aliases import _without_quoted_spans

_HOTEL = r'(?:отель|отеля|отелю|отеле|гостиница|гостиницу|гостинице|гостиницы)'
_HOTEL_WORD = re.compile(r'\b' + _HOTEL + r'\b')
_KEEP = re.compile(r'\b(?:сохрани(?:те)?|сохранить|сохраняем|оставь(?:те)?|оставить|оставляем)\b')
_NEGATED_KEEP = re.compile(r'\bне\s+(?:сохран\w*|остав\w*)\b')
_NONCURRENT = re.compile(r'\b(?:если|допустим|предположим|возможно|может|наверное|раньше|ранее|прежде|говорил\w*|просил\w*|цитата)\b')
_REPLACE = re.compile(r'\b(?:замени(?:те)?|поменяй(?:те)?|смени(?:те)?)\b')
_ORDINALS = {'первый': 1, 'первого': 1, 'первую': 1, 'первой': 1,
    'второй': 2, 'второго': 2, 'вторую': 2, 'третий': 3, 'третьего': 3, 'третью': 3, 'третьей': 3,
    'четвертый': 4, 'четвертого': 4, 'четвертую': 4, 'четвертой': 4,
    'пятый': 5, 'пятого': 5, 'пятую': 5, 'пятой': 5}
_ORDINAL_HOTEL = re.compile(r'\b(' + '|'.join(_ORDINALS) + r')\s+' + _HOTEL + r'\b')
_SOURCES = {'DEMO', 'SANDBOX', 'LIVE_INDICATIVE', 'LIVE_BOOKABLE'}


def _targets(clause, stays):
    ordinals = {_ORDINALS[match[1]] for match in _ORDINAL_HOTEL.finditer(clause)}
    if any(not 1 <= ordinal <= len(stays) for ordinal in ordinals):
        return None
    ordinal_ids = {stays[number - 1]['id'] for number in ordinals}
    cities = {city for alias, city in _CITY_ALIASES.items()
              if re.search(r'(?<![\w-])' + re.escape(alias) + r'(?![\w-])', clause)}
    city_ids = {stay['id'] for stay in stays if stay.get('city') in cities}
    if cities and any(city not in {stay.get('city') for stay in stays} for city in cities):
        return None
    if ordinal_ids:
        return ordinal_ids if not cities or city_ids == ordinal_ids else None
    return city_ids if len(city_ids) == 1 else None


def _changed_scope(data, operations):
    stays = {stay['id']: stay for stay in data['stays']}
    for operation in operations:
        kind, value, target = operation.get('type'), operation.get('value'), operation.get('target_id')
        if not isinstance(value, dict):
            return True
        if kind == 'remove_stay':
            return True
        if kind == 'upsert_stay' and (target not in stays or 'city' in value and value['city'] != stays[target]['city']):
            return True
        if kind == 'set_origin' and value.get('city', value.get('origin')) != data.get('origin'):
            return True
        if kind == 'set_route_policy' and any(val != (data.get('route_policy') or {}).get(key) for key, val in value.items()):
            return True
        if kind == 'set_search_policy' and any(key in {'mode', 'hotel_provider', 'flight_provider', 'demo_fallback'}
                and val != data['search_policy'].get(key) for key, val in value.items()):
            return True
    return False


def _selected_hotels(data, selected):
    if (not isinstance(selected, dict) or selected.get('source') != 'ui_preview_selection'
            or selected.get('selection_status') != 'current_reference_only'
            or selected.get('reference_only') is not True or selected.get('prices_included') is not False
            or selected.get('trip_id') != data['trip_id'] or type(selected.get('state_version')) is not int
            or selected['state_version'] != data['state_version']
            or not isinstance(selected.get('plan_id'), str) or not 0 < len(selected['plan_id']) <= 200
            or selected.get('source_type') not in _SOURCES | {'MIXED'} or not isinstance(selected.get('hotels'), list)):
        return None
    stays, hotels = {stay['id']: stay for stay in data['stays']}, {}
    for hotel in selected['hotels']:
        if not isinstance(hotel, dict):
            return None
        stay_id, hotel_id = hotel.get('stay_id'), hotel.get('hotel_id')
        if (not isinstance(stay_id, str) or stay_id not in stays or stay_id in hotels
                or not isinstance(hotel_id, str) or not 0 < len(hotel_id) <= 200 or hotel_id != hotel_id.strip()
                or hotel.get('city') != stays[stay_id]['city'] or hotel.get('source_type') not in _SOURCES
                or stays[stay_id].get('hotel_id') is not None and stays[stay_id]['hotel_id'] != hotel_id):
            return None
        hotels[stay_id] = hotel
    return hotels if set(hotels) == set(stays) else None


def ground_preserved_hotel_selection(state: TripState | dict, operations: list, text: str,
                                     current_selected_plan: dict | None) -> tuple[list, dict]:
    """Pin a literal keep wish from the freshly re-read code-owned selection.

    A selected reference grants only identity. It never grants a price, consent,
    availability or booking. The caller must load it afresh through the Store
    after model extraction; the model's prompt copy is not authoritative.
    """
    original = deepcopy(operations)
    meta = {'notes': [], 'unknowns': [], 'conflicts': [], 'pending_context': None}
    if not isinstance(text, str) or not 0 < len(text) <= 20_000:
        return original, meta
    current = _without_quoted_spans(' '.join(text.casefold().replace('ё', 'е').split()))
    if current is None:
        return original, meta
    clauses = [clause for clause in re.findall(r'[^.!?;]+[.!?;]?', current)
               if not clause.rstrip().endswith('?') and not _NONCURRENT.search(clause)]
    requests = [clause for clause in clauses if _KEEP.search(clause) and _HOTEL_WORD.search(clause) and not _NEGATED_KEEP.search(clause)]
    if not requests:
        return original, meta
    data = state.model_dump(mode='json') if isinstance(state, TripState) else TripState.model_validate(state).model_dump(mode='json')
    copied = [op.model_dump(mode='json') if isinstance(op, Operation) else deepcopy(op) for op in original]
    stays = {stay['id']: stay for stay in data['stays']}
    requested = set()
    for clause in requests:
        targets = _targets(clause, data['stays'])
        if not targets:
            meta['unknowns'] = ['Какой отель нужно сохранить? Укажите город или номер проживания в маршруте.']
            return original, meta
        requested.update(targets)
    replaced = set()
    for clause in clauses:
        if _REPLACE.search(clause) and _HOTEL_WORD.search(clause):
            replaced.update(_targets(clause, data['stays']) or set())
    if replaced.intersection(requested) or _changed_scope(data, copied):
        meta['conflicts'] = ['Просьба сохранить выбранный отель противоречит замене того же отеля или изменению маршрута/источника. Уточните, какой выбор оставить.']
        return original, meta
    assignments = {}
    for operation in copied:
        if operation['type'] in {'replace_hotel', 'upsert_stay'} and 'hotel_id' in operation['value']:
            assignments.setdefault(operation.get('target_id'), []).append(operation['value']['hotel_id'])
    selected = _selected_hotels(data, current_selected_plan)
    result = deepcopy(original)
    for stay_id in sorted(requested):
        saved = stays[stay_id].get('hotel_id')
        wanted = saved or (selected[stay_id]['hotel_id'] if selected else None)
        if wanted is None:
            meta['notes'] = []
            meta['unknowns'] = ['Чтобы сохранить конкретный отель, выберите актуальный вариант через просмотр корзины. Первый показанный план сам по себе не означает ваш выбор.']
            return original, meta
        values = assignments.get(stay_id, [])
        if any(value != wanted for value in values):
            meta['notes'] = []
            meta['conflicts'] = ['Для одного проживания одновременно указаны сохранение выбранного отеля и другой отель. Уточните, какой отель оставить.']
            return original, meta
        if saved is not None or values:
            continue
        operation = {'type': 'replace_hotel', 'target_id': stay_id, 'value': {'hotel_id': wanted}}
        result.append(Operation.model_validate(operation) if original and isinstance(original[0], Operation) else operation)
        meta['notes'].append({'code': 'explicit_selected_hotel_preserved', 'stay_id': stay_id, 'hotel_id': wanted,
            'plan_id': current_selected_plan['plan_id'], 'source': 'fresh_ui_preview_identity',
            'price_or_consent_reused': False})
    if len(result) > 30:
        meta['notes'] = []
        meta['unknowns'] = ['Сначала уточним сохранение выбранного отеля: составная команда превышает допустимое число операций.']
        return original, meta
    return result, meta
