"""Owned destination clarification stages, without models, storage or search."""
import re
from copy import deepcopy

from .geography import parse_destination
from .intent_grounding import _CLEAR_GEO, _key, _pending
from .provider_cities import available_search_cities

_OWNER = 'geo_destination_v1'
_YES = {'да', 'верно', 'подтверждаю', 'давай', 'давайте'}
_KEEP = {'сохраняем', 'сохряняем', 'сохраним', 'сохрани', 'оставляем', 'оставим', 'да сохраняем'}
_NO = {'нет', 'не надо', 'не сохраняем'}
_CHANGE = {'меняем', 'меняем город', 'изменить', 'другой город', 'хочу другой город'}


def _place(value):
    if not isinstance(value, dict) or not isinstance(value.get('name'), str):
        return None
    place = parse_destination(value['name'])['requested_destination']
    return place if place.get('kind') == value.get('kind') and place.get('country_code') == value.get('country_code') else None


def _owned_target(data, pending):
    stays = data.get('stays', [])
    target = pending.get('target_id') or pending.get('stay_id')
    if target is not None:
        return target if sum(s.get('id') == target for s in stays) == 1 else None
    return stays[0]['id'] if len(stays) == 1 else None


def _ask(data, place, target, question, purpose, *, proposal=None):
    if place.get('kind') == 'country':
        place = parse_destination(place['country_code'], available_cities=available_search_cities(data))['requested_destination']
    context = {'field': 'destination.city', 'target_id': target, 'requested_destination': deepcopy(place),
        'geo_destination': {'owner': _OWNER, 'trip_id': data.get('trip_id'),
            'state_version': data.get('state_version'), 'purpose': purpose}}
    if proposal is not None:
        context.update(proposed_destination=deepcopy(proposal), confirmation_required=True,
            proposal_field='destination.city')
    return {'handled': True, 'action': 'ask_destination', 'operations': [], 'question': question,
        'pending_context': context, 'pending_context_clear': list(_CLEAR_GEO),
        'clear_pending': True, 'search': False}


def _retain(data, place, target):
    stays = data.get('stays', [])
    if target is None and stays:
        return _ask(data, place, None, f"Какую остановку нужно изменить на город «{place['name']}»?",
            'choose_owned_stay')
    operations = []
    if target is None:
        target = 'retained-destination-1'
        operations = [{'type': 'upsert_stay', 'target_id': target, 'value': {'city': place['name']}}]
    else:
        stay = next(s for s in stays if s['id'] == target)
        if stay.get('city') != place['name']:
            operations = [{'type': 'upsert_stay', 'target_id': target,
                'value': {'city': place['name'], 'hotel_id': None}}]
    country = parse_destination(place['country_code'])['requested_destination']['name']
    return {'handled': True, 'action': 'retain_unsupported_destination', 'operations': operations,
        'pending_context': None, 'clear_pending': True, 'search': False,
        'notice': f"Сохранил город «{place['name']}», {country}. В текущей версии поиск предложений для этого направления пока не запускается. Можно изменить город или другие условия поездки.",
        'postconditions': {'target_id': target, 'city': place['name'], 'country_code': place['country_code'],
            'supplier_availability_claimed': False, 'search_requested': False}}


def _select(data, place, target):
    result = _retain(data, place, target)
    if result['action'] != 'retain_unsupported_destination':
        return result
    result.update(action='select_destination', notice=f"Выбран город «{place['name']}».",
        pending_context={'requested_destination': deepcopy(place)},
        pending_context_clear=[*_CLEAR_GEO, 'geo_destination'])
    result['postconditions']['search_supported'] = True
    return result


def resolve_geo_destination(before_state, pending_question, text):
    """Resolve only owned short geography choices/KEEP, never a compound edit.

    The caller applies returned operations atomically through normal domain/CAS
    validation. A retain action must finish without search or unrelated booking
    questions. Country/city metadata is not supplier availability evidence.
    """
    unhandled = {'handled': False, 'operations': []}
    data = before_state.model_dump(mode='json') if hasattr(before_state, 'model_dump') else before_state
    if not isinstance(data, dict) or not isinstance(text, str) or not 0 < len(text) <= 100 or '?' in text:
        return unhandled
    key = _key(text).strip(' .!')
    coverage = available_search_cities(data)
    additional_coverage = set(coverage) - set(available_search_cities({}))
    pending = _pending(pending_question)
    if pending.get('reason') == 'user_pause':
        return unhandled
    if not pending:
        stays = data.get('stays', [])
        if key in _KEEP and len(stays) == 1:
            place = parse_destination(stays[0].get('city', ''))['requested_destination']
            if place['kind'] == 'city' and place['name'] not in coverage:
                return _retain(data, place, stays[0]['id'])
        return unhandled
    if type(pending.get('state_version')) is not int or pending['state_version'] != data.get('state_version'):
        return unhandled
    requested = _place(pending.get('requested_destination'))
    proposal = _place(pending.get('proposed_destination'))
    target = _owned_target(data, pending)
    stage = pending.get('geo_destination')
    owned_stage = (isinstance(stage, dict) and stage.get('owner') == _OWNER
        and stage.get('trip_id') == data.get('trip_id') and stage.get('state_version') == data.get('state_version'))
    if stage is not None and not owned_stage:
        return unhandled
    if (pending.get('field') == 'destination.city' and requested and requested.get('country_code') == 'RS'
            and key == 'белгород'):
        if target is not None and any(stay['id'] == target and stay.get('city') == 'Белград'
                                      for stay in data.get('stays', [])):
            # The owned stop is already Belgrade. Existing contextual spelling
            # handling can acknowledge it without asking to choose it again.
            return unhandled
        place = parse_destination('Белград')['requested_destination']
        return _ask(data, place, target,
            'Понял как «Белград», Сербия. Подтвердите: выбираем именно этот город в Сербии?',
            'confirm_serbian_city_spelling', proposal=place)
    active = pending.get('field') == 'destination.city' and pending.get('confirmation_required') is True and proposal
    if (active and proposal['kind'] == 'city' and proposal['name'] in coverage
            and (proposal['name'] in additional_coverage or owned_stage) and key in _YES | _KEEP):
        return _select(data, proposal, target)
    if active and proposal['kind'] == 'city' and proposal['name'] not in coverage:
        if key in _YES | _KEEP:
            return _retain(data, proposal, target)
        if key in _NO:
            country = parse_destination((requested or proposal)['country_code'])['requested_destination']
            return _ask(data, country, target, f"Какой город в стране «{country['name']}» выбираем?", 'choose_city')
    if active:
        # A different active city proposal owns the next yes/no. Do not
        # reinterpret it as retention of the previous unsupported city.
        return unhandled
    if not requested or requested['kind'] != 'city':
        return unhandled
    stays = data.get('stays', [])
    mismatched = target is not None and next(s for s in stays if s['id'] == target).get('city') != requested['name']
    eligible = pending.get('field') == 'destination.city' or mismatched
    if not eligible:
        return unhandled
    if requested['name'] in coverage:
        if requested['name'] not in additional_coverage and not owned_stage:
            return unhandled
        if key in _KEEP or owned_stage and stage.get('purpose') in {'retain_without_search', 'select_destination'} and key in _YES:
            return _select(data, requested, target)
        if key in _YES | _NO or mismatched and pending.get('field') == 'stay.rooms' and re.fullmatch(r'\d{1,2}', key):
            return _ask(data, requested, target, f"Выбираем город «{requested['name']}» для поездки?", 'select_destination')
        return unhandled
    if key in _KEEP or owned_stage and stage.get('purpose') == 'retain_without_search' and key in _YES:
        return _retain(data, requested, target)
    if key in _CHANGE or owned_stage and stage.get('purpose') == 'retain_without_search' and key in _NO:
        country = parse_destination(requested['country_code'])['requested_destination']
        return _ask(data, country, target, f"Какой город выбираем вместо города «{requested['name']}»?", 'choose_city')
    if key in _YES | _NO or mismatched and pending.get('field') == 'stay.rooms' and re.fullmatch(r'\d{1,2}', key):
        return _ask(data, requested, target,
            f"Сохранить город «{requested['name']}» как направление поездки без поиска предложений?",
            'retain_without_search')
    return unhandled
