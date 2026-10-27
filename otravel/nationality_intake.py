"""Recover an explicit current hotel-search nationality, never a passport guess.

This is an omission fallback after model/domain intent grounding. It does not
authorize unrelated existing nationality operations or infer individual travel
documents from origin, language, saved history or other group members.
"""
from __future__ import annotations

import re
from copy import deepcopy

from .country_lexicon import CountryLexiconError, _key, _lexicon
from .domain import DomainError, TripState, apply_operations

_QUOTED = re.compile(r'«[^»]*»|“[^”]*”|"[^"\n]*"|(?<!\w)\'[^\'\n]*\'(?!\w)|`[^`]*`')
_NONCURRENT = re.compile(r"\b(?:если|бы|допустим|предположим|возможно|наверное|например|раньше|ранее|прежде|вчера|"
    r"цитирую|обсуждал\w*|говорил\w*|писал\w*|сказал\w*|if|suppose|previously|hypothetically)\b", re.IGNORECASE)
_NEGATIVE = re.compile(r"\b(?:не|нет|без|not|never)\b", re.IGNORECASE)
_NO_CHILDREN = re.compile(r"\b(?:без\s+детей|детей\s+(?:пока\s+)?нет|дети\s+не\s+едут)\b", re.IGNORECASE)
_VALUE = r"(?::|—|–|-)?\s*(?P<country>.+?)\s*"
_PERSONAL = re.compile(r"^\s*гражданство\s+" + _VALUE + r"$", re.IGNORECASE)
_PERSONAL_COLON = re.compile(r"^\s*гражданство\s*[:—–-]\s*" + r"(?P<country>.+?)\s*$", re.IGNORECASE)
_HOTEL_POLICY = re.compile(r"^\s*для\s+поиска\s+(?:отеля|отелей|гостиницы|гостиниц)\s+"
    r"используй(?:те)?\s+гражданство(?:\s+|\s*[:—–-]\s*)" + _VALUE + r"$", re.IGNORECASE)


def _unquoted_current(text):
    if not isinstance(text, str) or not 0 < len(text) <= 4000:
        return None
    masked = _QUOTED.sub(lambda match: ' ' * len(match[0]), text)
    if (any(mark in masked for mark in ('"', '«', '»', '“', '”', '`'))
            or re.search(r"(?<!\w)'|'(?!\w)", masked)):
        return None
    masked = '\n'.join('' if line.lstrip().startswith('>') else line for line in masked.splitlines())
    if _NONCURRENT.search(_key(masked)) or _NEGATIVE.search(_NO_CHILDREN.sub(' ', _key(masked))):
        return None
    return masked


def _claims(text):
    current = _unquoted_current(text)
    if current is None:
        return []
    result = []
    for match in re.finditer(r"([^.;!?\n]+)([.;!?\n]|$)", current):
        if match[2] == '?':
            continue
        sentence = match[1].strip()
        policy = _HOTEL_POLICY.fullmatch(sentence)
        claim = policy or _PERSONAL.fullmatch(sentence) or _PERSONAL_COLON.fullmatch(sentence)
        if claim is None:
            continue
        raw = claim['country'].strip()
        # Only a complete CLDR label/form or an explicit uppercase ISO-2 value.
        if re.fullmatch(r'[A-Za-z]{2}', raw) and not raw.isupper():
            continue
        _, aliases, _ = _lexicon()
        countries = aliases.get(_key(raw), set())
        if len(countries) == 1:
            result.append((next(iter(countries)), policy is not None))
    return result


def ground_explicit_nationality(state, text, operations):
    """Return copied dict operations and notes/conflicts; no persistence or HTTP.

    A personal citizenship clause requires exactly one adult traveler after
    the proposed edits. An explicit instruction about hotel-search policy may
    set that API search parameter for a group without claiming their passports.
    The caller owns speech-act/control authorization and final atomic validation.
    """
    copied = [operation.model_dump(mode='json') if hasattr(operation, 'model_dump') else deepcopy(operation)
        for operation in operations]
    meta = {'recovered': False, 'notes': [], 'conflicts': [], 'unknowns': []}
    try:
        claims = _claims(text)
    except CountryLexiconError:
        meta['notes'].append({'code': 'nationality_reference_unavailable'})
        return copied, meta
    if not claims:
        return copied, meta
    countries = {code for code, _ in claims}
    if len(countries) != 1:
        meta['conflicts'].append('Вы указали разные значения гражданства. Какое одно использовать для поиска отеля?')
        return copied, meta
    code = next(iter(countries))
    try:
        before = TripState.model_validate(state.model_dump(mode='json') if hasattr(state, 'model_dump') else state)
        candidate, _ = apply_operations(before, copied)
    except (DomainError, ValueError, TypeError):
        meta['notes'].append({'code': 'nationality_context_not_validated'})
        return copied, meta
    explicit_policy = any(policy for _, policy in claims)
    if not explicit_policy and not (len(candidate.travelers) == 1 and candidate.travelers[0].type == 'adult'):
        meta['notes'].append({'code': 'nationality_person_scope_ambiguous'})
        return copied, meta
    requested = [operation['value']['guest_nationality'] for operation in copied
        if isinstance(operation, dict) and operation.get('type') == 'set_search_policy'
        and isinstance(operation.get('value'), dict) and 'guest_nationality' in operation['value']]
    if any(value != code for value in requested):
        meta['conflicts'].append('Указанное вами гражданство отличается от предложенных изменений. Какое значение использовать для поиска отеля?')
        return copied, meta
    if requested or before.search_policy.guest_nationality == code:
        return copied, meta
    copied.append({'type': 'set_search_policy', 'target_id': None, 'value': {'guest_nationality': code}})
    meta['recovered'] = True
    meta['notes'].append({'code': 'explicit_hotel_guest_nationality_grounded', 'country_iso2': code,
        'scope': 'explicit_hotel_search_policy' if explicit_policy else 'single_adult'})
    return copied, meta
