"""Reject impossible asserted hotel dates, then repair owned initial omissions.

No wish word, model inference, implicit year, new destination, persistence or
HTTP. Current impossible calendar literals conflict independently of the target.
Valid-date repairs still require the fresh owned single target; this is not a
general date editor or a permission grant for other setters.
"""
from __future__ import annotations

import re
from calendar import monthrange
from copy import deepcopy
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .date_slots import _EXPLICIT, _MONTH, _MONTHS, _calendar
from .domain import DomainError, TripState, apply_operations

_QUOTED = re.compile(r'«[^»]*»|“[^”]*”|"[^"\n]*"|(?<!\w)\'[^\'\n]*\'(?!\w)|`[^`]*`')
_NONCURRENT = re.compile(r"\b(?:если|бы|допустим|предположим|возможно|наверное|например|либо|или|раньше|ранее|"
    r"прежде|вчера|цитирую|говорил\w*|писал\w*|обсуждал\w*|if|suppose|previously|hypothetically)\b", re.IGNORECASE)
_INFO = re.compile(r"^\s*(?:объясни|расскажи|напомни|поясни|скажи|покажи|назови|перечисли|проверь|"
    r"почему|как|где|когда|можно|можешь|what|why|can)\b", re.IGNORECASE)
_NO_EDIT = re.compile(r"\b(?:не\s+(?:меняй|изменяй|трогай|задавай|сохраняй|используй)\s+(?:даты|дату|заезд|выезд)|"
    r"(?:убери|удали|очисти|сбрось)\s+(?:даты|дату|заезд|выезд))\b", re.IGNORECASE)
_FIELDS = {'заезд': 'check_in', 'выезд': 'check_out'}
_CALENDAR_MONTH = re.compile(r'([0-9]{1,2})\s*(' + '|'.join(_MONTHS)
    + r')(?:\s*([0-9]{4})(?:\s*года)?)?')
_CALENDAR_TOKEN = r'(?:[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{2}\.[0-9]{2}\.[0-9]{4}|' + _CALENDAR_MONTH.pattern + r')'
_DATE_ASSERTION = re.compile(r'(?:(?:перенеси|перенесите|передвинь|сдвинь|измени|поменяй|установи|задай|поставь|сделай)'
    r'\s+(?:только\s+)?)?(?:(?:дату|дата)\s+)?(?P<label>заезда?|выезда?)'
    r'(?:\s*:\s*|\s+(?:на\s*)?|(?=[0-9]))(?P<token>' + _CALENDAR_TOKEN + r')')


def _current(text):
    if not isinstance(text, str) or not 0 < len(text) <= 4000:
        return None
    masked = _QUOTED.sub(lambda match: ' ' * len(match[0]), text)
    if any(mark in masked for mark in ('"', '«', '»', '“', '”', '`')) or re.search(r"(?<!\w)'|'(?!\w)", masked):
        return None
    masked = '\n'.join('' if line.lstrip().startswith('>') else line for line in masked.splitlines())
    if '?' in masked or _NONCURRENT.search(masked) or _INFO.search(masked) or _NO_EDIT.search(masked):
        return None
    return masked.casefold().replace('ё', 'е')


def _dates(text, today, meta):
    values = {}
    # Dots inside DD.MM.YYYY are not sentence delimiters.
    for sentence in re.split(r"[,;!\n]|(?<!\d)\.|\.(?!\d)", text):
        for piece in re.split(r"\s+и\s+(?=(?:заезд|выезд)\b)", sentence):
            piece = ' '.join(piece.split())
            piece = re.sub(r"^и\s+(?=(?:заезд|выезд)\b)", '', piece)
            match = _EXPLICIT.fullmatch(piece)
            if match is None:
                continue
            field = _FIELDS[match['label']]
            token = match['token'] or match['attached']
            month = _MONTH.fullmatch(token)
            if month is not None and month[3] is None:
                meta['unknowns'].append('Укажите дату ' + ('заезда' if field == 'check_in' else 'выезда') + ' полностью, включая год.')
                continue
            if token in {'сегодня', 'завтра', 'послезавтра'}:
                continue  # Existing owned date-slot resolution owns relative dates.
            try:
                value = _calendar(token, today)
            except (ValueError, OverflowError):
                meta['unknowns'].append('Укажите корректную дату ' + ('заезда' if field == 'check_in' else 'выезда') + ' полностью.')
                continue
            if value < today:
                meta['unknowns'].append('Дата проживания в прошлом. Укажите будущую дату полностью.')
                continue
            if field in values and values[field] != value:
                meta['conflicts'].append('Вы указали разные даты ' + ('заезда' if field == 'check_in' else 'выезда') + '. Уточните одну дату.')
            values[field] = value
    return values


def _invalid_current_hotel_date(current):
    """Reject an impossible asserted literal, independent of target ownership.

    No date/target is chosen. Month bounds use the maximum possible length,
    so February29 without a year is possible/undecided, never guessed invalid.
    """
    for sentence in re.split(r'[,;!\n]|(?<!\d)\.|\.(?!\d)', current):
        pieces = re.split(r'\s+(?:и|а|но)\s+(?=(?:установи|задай|поставь|уменьши|увеличь|сократи|снизь|урежь|'
            r'перенеси|измени|напомни|бюджет|заезд|выезд)\b)', sentence)
        for piece in pieces:
            match = _DATE_ASSERTION.fullmatch(' '.join(piece.split()))
            if match is None:
                continue
            token = match['token']
            month = _CALENDAR_MONTH.fullmatch(token)
            invalid = False
            if month:
                day, number = int(month[1]), _MONTHS[month[2]]
                if month[3] is None:
                    invalid = not 1 <= day <= monthrange(2000, number)[1]
                else:
                    try:
                        date(int(month[3]), number, day)
                    except (ValueError, OverflowError):
                        invalid = True
            else:
                try:
                    _calendar(token, date(2000, 1, 1))
                except (ValueError, OverflowError):
                    invalid = True
            if invalid:
                field = 'check_in' if match['label'].startswith('заезд') else 'check_out'
                label = 'заезда' if field == 'check_in' else 'выезда'
                reason = f'Дата «{token}» не существует в календаре. Укажите корректную дату {label}.'
                return field, token, reason
    return None


def ground_explicit_stay_dates(state, text, operations, *, pending, now, timezone='Europe/Moscow'):
    """Return copied dict operations plus omission/contradiction diagnostics.

    The caller provides the server-owned current pending question and controls
    speech-act/pause/search. An explicit new pause does not prohibit saving
    positive facts; an already paused/foreign/stale pending packet grants none.
    """
    copied = [op.model_dump(mode='json') if hasattr(op, 'model_dump') else deepcopy(op) for op in operations]
    meta = {'recovered': False, 'notes': [], 'conflicts': [], 'unknowns': []}
    current = _current(text)
    if current is None:
        return copied, meta
    invalid = _invalid_current_hotel_date(current)
    if invalid is not None:
        field, token, reason = invalid
        meta['conflicts'] = [reason]
        meta['unknowns'] = [reason]
        meta['pending_context_clear'] = ['date_proposal', 'duration_proposal', 'duration_days']
        meta['notes'].append({'finding_id': 'HARNESS-004-INVALID-CALENDAR-ROLLOVER-001',
            'code': 'asserted_calendar_literal_invalid', 'field': 'stay.' + field, 'literal': token,
            'whole_batch_conflict': True, 'state_changed': False})
        try:
            before = TripState.model_validate(state.model_dump(mode='json') if hasattr(state, 'model_dump') else state)
        except (ValueError, TypeError):
            before = None
        if before is not None and len(before.stays) == 1:
            stay = before.stays[0]
            setters = [op for op in copied if isinstance(op, dict) and op.get('type') == 'upsert_stay']
            if (all(isinstance(op, dict) and isinstance(op.get('value', {}), dict) for op in copied)
                    and all((op.get('target_id') or op.get('value', {}).get('id')) == stay.id for op in setters)):
                meta['pending_context'] = {'field': 'stay.' + field, 'target_id': stay.id}
        return copied, meta
    if not re.search(r'\b(?:заезд|выезд)\b',current):
        return copied, meta
    try:
        if not isinstance(now, datetime) or now.utcoffset() is None:
            raise ValueError
        today = now.astimezone(ZoneInfo(timezone)).date()
        before = TripState.model_validate(state.model_dump(mode='json') if hasattr(state, 'model_dump') else state)
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        meta['notes'].append({'code': 'date_intake_unvalidated_context'})
        return copied, meta
    if len(before.stays) != 1 or not before.stays[0].city:
        meta['notes'].append({'code': 'date_intake_requires_single_owned_stay'})
        return copied, meta
    stay = before.stays[0]
    if (not isinstance(pending, dict) or not isinstance(pending.get('question_id'), str) or not pending['question_id']
            or type(pending.get('state_version')) is not int or pending['state_version'] != before.state_version
            or pending.get('reason') == 'user_pause'
            or pending.get('field') not in {'stay.check_in', 'stay.check_out', 'stay.dates'}):
        meta['notes'].append({'code': 'date_intake_pending_not_fresh'})
        return copied, meta
    context = pending.get('context') if isinstance(pending.get('context'), dict) else {}
    for source in (pending, context):
        if (source.get('trip_id', before.trip_id) != before.trip_id
                or any(key in source and (type(source[key]) is not int or source[key] != before.state_version)
                    for key in ('state_version', 'based_state_version'))):
            meta['notes'].append({'code': 'date_intake_foreign_pending'})
            return copied, meta
    raw_targets = [source[key] for source in (pending, context) for key in ('target_id', 'stay_id')
        if source.get(key) is not None]
    if any(not isinstance(value, str) for value in raw_targets) or set(raw_targets) != {stay.id}:
        meta['notes'].append({'code': 'date_intake_pending_target_required'})
        return copied, meta
    if any(not isinstance(op, dict) or not isinstance(op.get('value', {}), dict) for op in copied):
        return copied, meta
    setters = [op for op in copied if op.get('type') == 'upsert_stay']
    if any((op.get('target_id') or op['value'].get('id')) != stay.id for op in setters):
        meta['notes'].append({'code': 'date_intake_conflicting_stay_targets'})
        return copied, meta
    if any(op.get('type') == 'remove_stay' and op.get('target_id') == stay.id for op in copied):
        return copied, meta
    # An omission fallback owns no date question outside this fresh initial
    # target. Parsing can create diagnostics, so it follows every scope fence.
    values = _dates(current, today, meta)
    if not values or meta['conflicts'] or meta['unknowns']:
        return copied, meta
    effective = {field: values.get(field) or getattr(stay, field) for field in ('check_in', 'check_out')}
    nights = [stay.requested_nights] if stay.requested_nights is not None else []
    nights.extend(op['value']['requested_nights'] for op in setters if op['value'].get('requested_nights') is not None)
    if all(effective.values()):
        span = (effective['check_out'] - effective['check_in']).days
        if span <= 0:
            meta['conflicts'].append('Выезд должен быть позже заезда. Уточните даты проживания.')
        if any(type(value) is not int or value != span for value in nights):
            meta['conflicts'].append('Указанная длительность не совпадает с датами проживания. Уточните даты или число ночей.')
    patch = {}
    for field, value in values.items():
        original = getattr(stay, field)
        proposed = [op['value'][field] for op in setters if field in op['value']]
        concrete = [entry for entry in proposed if entry is not None]
        if any(not isinstance(entry, str) or entry != value.isoformat() for entry in concrete):
            meta['conflicts'].append('Предложенные моделью даты отличаются от явно указанных вами. Уточните даты проживания.')
        elif original is not None:
            if any(entry is None for entry in proposed):
                meta['conflicts'].append('Предложено удалить сохранённую дату, хотя вы указали дату проживания. Уточните изменение.')
        elif not concrete:
            patch[field] = value.isoformat()
    if meta['conflicts'] or not patch:
        meta['conflicts'] = list(dict.fromkeys(meta['conflicts']))
        return copied, meta
    prepared = deepcopy(copied)
    selected = [op for op in prepared if op.get('type') == 'upsert_stay']
    if selected:
        for field, value in patch.items():
            holders = [op for op in selected if field in op['value']]
            for op in holders or selected[:1]:
                op['value'][field] = value
    else:
        prepared.append({'type': 'upsert_stay', 'target_id': stay.id, 'value': patch})
    try:
        apply_operations(before, prepared)
    except (DomainError, ValueError, TypeError):
        meta['notes'].append({'code': 'date_intake_candidate_not_validated'})
        return copied, meta
    meta['recovered'] = True
    meta['notes'].append({'code': 'explicit_initial_stay_dates_grounded', 'stay_id': stay.id,
        'fields': sorted(patch), 'reference_date': today.isoformat(), 'timezone': timezone})
    return prepared, meta
