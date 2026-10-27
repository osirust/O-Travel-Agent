"""Fresh country intake: distinguish a missing city from contradictory facts.

This helper neither invents operations nor picks a city. It can reclassify only
a bounded country-not-city model conflict after checking every proposed fact
against the current literal declaration. Normal operation/domain checks remain
authoritative after this step.
"""
import re
from copy import deepcopy

from .budget_intent import _current_parts
from .geography import _CITY_ALIASES, _COUNTRY_ALIASES, parse_destination
from .intent_grounding import _CLEAR_GEO, _facts, _key
from .money import _MARKER, _MONEY, _TOTAL_SCOPE, _VALUES, _number

_COUNTRY_NOT_CITY = re.compile(
    r'\bcountry\s*[,;:]?\s*(?:and\s+)?not\s+(?:(?:a|an|bookable|supported)\s+)*city\b|'
    r'\bстрана\b.{0,45}\b(?:а\s+)?не\b.{0,25}\bгород\w*\b', re.IGNORECASE)
_MISSING_CITY = re.compile(
    r'\bcountry\b.{0,100}\b(?:need\w*|choose|pick|specify)\b.{0,40}\bcity\b|'
    r'\bстрана\b.{0,90}\b(?:выбер\w*|выбрат\w*|уточн\w*|укаж\w*)\b.{0,35}\bгород\w*\b', re.IGNORECASE)
_OTHER_CONFLICT = re.compile(
    r'\d|\b(?:budget\w*|money|amount\w*|adult\w*|child\w*|traveler\w*|passenger\w*|'
    r'dates?|contradict\w*|incompatib\w*|different|versus|vs|currency|'
    r'бюджет\w*|сумм\w*|взросл\w*|дет\w*|ребен\w*|участник\w*|пассажир\w*|'
    r'дат\w*|противореч\w*|несовместим\w*|разн\w*|валют\w*)\b', re.IGNORECASE)
_NUMBERS = {**_VALUES, 'двое': 2, 'трое': 3, 'четверо': 4, 'пятеро': 5}
_ADULT = re.compile(r'(?<!\w)(?P<count>\d{1,2}|' + '|'.join(
    re.escape(word) for word in sorted(_NUMBERS, key=len, reverse=True)) + r')\s+взросл\w*\b')
_ALTERNATIVE = re.compile(r'\b(?:или|либо|or|either)\b')
_LITERAL_CITY = re.compile(r'(?<![\w-])(?P<city>' + '|'.join(
    re.escape(name) for name in sorted(_CITY_ALIASES, key=len, reverse=True)) + r')(?![\w-])')
_LITERAL_COUNTRY = re.compile(r'(?<![\w-])(?P<country>' + '|'.join(
    re.escape(name) for name in sorted(_COUNTRY_ALIASES, key=len, reverse=True)) + r')(?![\w-])')


def _fresh(data):
    return (isinstance(data, dict) and not data.get('origin') and data.get('budget') is None
        and all(not data.get(key) for key in ('travelers', 'groups', 'stays', 'legs', 'constraints', 'preferences')))


def _declarations(text):
    for sentence in re.finditer(r'[^.!?;\n]+(?:[.!?;\n]|$)', text):
        if sentence.group().rstrip().endswith('?'):
            continue
        yield from _current_parts(sentence.group())


def _literal_budget(text):
    amounts = set()
    for clause in _declarations(text):
        if not (_MARKER.search(clause) or _TOTAL_SCOPE.search(clause)):
            continue
        if _ALTERNATIVE.search(clause):
            return None
        matches = list(_MONEY.finditer(clause))
        if len(matches) != 1:
            return None
        amount = _number(matches[0]['number'])
        if amount is None or amount <= 0 or amount * 100 != (amount * 100).to_integral_value():
            return None
        amounts.add(int(amount * 100))
    return next(iter(amounts)) if len(amounts) == 1 else None


def _literal_adults(text):
    counts = set()
    for clause in _declarations(text):
        if not re.search(r'\bвзросл\w*\b', clause):
            continue
        if _ALTERNATIVE.search(clause):
            return None
        for match in _ADULT.finditer(clause):
            value = match['count']
            counts.add(int(value) if value.isascii() and value.isdigit() else _NUMBERS[value])
    return next(iter(counts)) if len(counts) == 1 else None


def normalize_country_intake_clarification(before_state, operations, text, conflicts, *, speech_act='CHANGE'):
    """Return unchanged copied ops plus narrowly server-owned clarification.

    handled=False grants no exception to model conflicts or country-only guards.
    No person, monetary value, city, date, room or membership is inferred here.
    """
    data = before_state.model_dump(mode='json') if hasattr(before_state, 'model_dump') else deepcopy(before_state)
    copied = [op.model_dump(mode='json') if hasattr(op, 'model_dump') else deepcopy(op) for op in operations]
    meta = {'handled': False, 'conflicts': deepcopy(conflicts), 'unknowns': [], 'server_questions': [],
        'pending_context': None, 'pending_context_clear': [], 'destination_request': None,
        'authorizes_retain_independent_ops': False, 'notes': []}
    if (not _fresh(data) or speech_act != 'CHANGE' or not isinstance(text, str)
            or not 0 < len(text) <= 20_000 or not isinstance(conflicts, list)):
        return copied, meta
    if any(not isinstance(item, str) or len(item) > 1000 or _OTHER_CONFLICT.search(_key(item))
            or not (_COUNTRY_NOT_CITY.search(_key(item)) or _MISSING_CITY.search(_key(item))) for item in conflicts):
        return copied, meta
    facts = _facts(data, text, {})
    if (len(facts['country']) != 1 or facts['destination'] or facts['ambiguous']
            or facts['alternative'] or facts['multicity'] or facts['replace'] or facts['old']
            or len(facts['origin']) > 1):
        return copied, meta
    current_country_codes = {_COUNTRY_ALIASES[match['country']]
        for clause in _declarations(text) for match in _LITERAL_COUNTRY.finditer(clause)}
    if current_country_codes != facts['country']:
        return copied, meta
    # Country intent is scoped by _facts. 'детей нет'/'даты не выбрал' are
    # independent clauses, not a negation of the destination-country wish.
    literal_cities = {_CITY_ALIASES[match['city']] for match in _LITERAL_CITY.finditer(_key(text))}
    if literal_cities - facts['origin']:
        return copied, meta
    kinds = [op.get('type') for op in copied if isinstance(op, dict)]
    if (len(kinds) != len(copied) or any(kind not in {'set_origin', 'set_budget', 'add_traveler'} for kind in kinds)
            or kinds.count('set_origin') > 1 or kinds.count('set_budget') > 1):
        return copied, meta
    adult_count = _literal_adults(text)
    added = [op for op in copied if op['type'] == 'add_traveler']
    if added and (adult_count is None or len(added) != adult_count):
        return copied, meta
    for op in copied:
        value = op.get('value')
        if not isinstance(value, dict):
            return copied, meta
        if op['type'] == 'set_origin':
            if not isinstance(value.get('city'), str):
                return copied, meta
            place = parse_destination(value['city'])['requested_destination']
            if place.get('kind') != 'city' or facts['origin'] != {place['name']}:
                return copied, meta
        elif op['type'] == 'set_budget':
            amount = _literal_budget(text)
            if (amount is None or type(value.get('amount_minor')) is not int or value['amount_minor'] != amount
                    or value.get('currency', 'RUB') != 'RUB' or value.get('scope', 'whole_trip') != 'whole_trip'):
                return copied, meta
        elif (value.get('type') != 'adult' or value.get('age') is not None
                or value.get('birth_date') is not None or value.get('group_id') is not None):
            return copied, meta
    location = parse_destination(next(iter(facts['country'])))
    place = location['requested_destination']
    question = location['clarification']
    meta.update(handled=True, conflicts=[], unknowns=[question], server_questions=[question],
        pending_context={'field': 'destination.city', 'target_id': None, 'requested_destination': place},
        pending_context_clear=list(_CLEAR_GEO), destination_request=place,
        authorizes_retain_independent_ops=True,
        notes=[{'code': 'country_missing_city_is_clarification', 'finding_id': 'HARNESS-004-COUNTRY-COMPOUND-INTAKE-001',
            'country_code': place['country_code'], 'retained_operation_count': len(copied),
            'reclassified_model_conflict_count': len(conflicts), 'facts_source': 'current_literal_declaration'}])
    return copied, meta


_WISH_COUNTRY = '|'.join(re.escape(name) for name in sorted(_COUNTRY_ALIASES, key=len, reverse=True))
_WISH_RU = r'(?:(?:я|мы)\s+)?(?:(?:хочу|хотим)(?:\s+(?:поехать|полететь|отправиться))?|еду|едем|летим|лечу|поеду|поедем|поехать|полететь|отправиться)\s+(?:в|во)\s+'
_WISH_EN = r'(?:(?:i|we)\s+)?(?:(?:want|would like)\s+to\s+(?:go to|travel to|visit)|go to|travel to|visit)\s+'
_FULL_COUNTRY_WISH = re.compile(
    rf'(?:(?:{_WISH_RU}|{_WISH_EN})(?P<country>{_WISH_COUNTRY})|'
    rf'(?:в|во)\s+(?P<postfix_country>{_WISH_COUNTRY})\s+(?:хочу|хотим))\s*[.!]*')


def literal_country_wish(before_state, text):
    """Bind one complete explicit fresh travel wish, without a model or ops.

    Countries are remembered as country intent, never as bookable cities. A
    question, hypothetical, extra fact/clause or saved trip stays on the normal
    interpretation path; this helper neither infers nor deletes any trip fact.
    """
    data = before_state.model_dump(mode='json') if hasattr(before_state, 'model_dump') else before_state
    if not _fresh(data) or not isinstance(text, str) or not 0 < len(text) <= 300:
        return None
    match = _FULL_COUNTRY_WISH.fullmatch(_key(text))
    if match is None:
        return None
    location = parse_destination(match['country'] or match['postfix_country'])
    if location['requested_destination']['kind'] != 'country':
        return None
    return {'requested_destination': location['requested_destination'], 'question': location['clarification']}
