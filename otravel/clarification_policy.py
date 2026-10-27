"""Narrow optional model questions, never a general unknowns discard policy.

The server still decides search, validates suppliers and owns real blocking
questions. This module only classifies two proved model-generated topics.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import timedelta

from .clarifications import question_field
from .domain import TripState, route_edges, validate_trip


def fulfilled_return_clarifications(before, after, operations, unknowns, *, server_questions=()):
    """Remove one model capability flag only after the backend changed return.

    Ordinary questions, supplier availability and unresolved route edits remain
    blocking. Existing dates alone do not establish a completed requested edit.
    """
    kept = deepcopy(unknowns)
    old, new = TripState.model_validate(before), TripState.model_validate(after)
    if (old.route_policy.kind != "round_trip" or new.route_policy.kind != "round_trip"
            or not old.stays or not new.stays or not old.legs or not new.legs):
        return kept, []
    old_stay, new_stay, old_leg, new_leg = old.stays[-1], new.stays[-1], old.legs[-1], new.legs[-1]
    if (old_stay.id != new_stay.id or old_leg.id != new_leg.id
            or (old_leg.origin, old_leg.destination) != (new_leg.origin, new_leg.destination)
            or old_leg.destination != old.origin or new_leg.destination != new.origin
            or old_stay.check_out is None or new_stay.check_out is None
            or old_stay.check_out == new_stay.check_out
            or old_leg.departure_date != old_stay.check_out or new_leg.departure_date != new_stay.check_out):
        return kept, []
    proven = False
    for operation in operations:
        op = operation.model_dump(mode="json") if hasattr(operation, "model_dump") else operation
        if not isinstance(op, dict) or not isinstance(op.get("value"), dict):
            continue
        value = op["value"]
        if (op.get("type") == "upsert_stay" and (op.get("target_id") or value.get("id")) == new_stay.id
                and value.get("check_out") == new_stay.check_out.isoformat()):
            proven = True
        days = value.get("days")
        if (op.get("type") == "shift_return" and type(days) is int and -366 <= days <= 366
                and old_stay.check_out + timedelta(days=days) == new_stay.check_out):
            proven = True
    if not proven:
        return kept, []
    aliases = {"return_flight_date_change"}
    trusted = {raw.strip().casefold() for raw in server_questions}
    removed = [raw for raw in kept if raw.strip().casefold() in aliases and raw.strip().casefold() not in trusted]
    if not removed:
        return kept, []
    return [raw for raw in kept if raw not in removed], [{
        "finding_id": "HARNESS-004-FULFILLED-RETURN-CLARIFICATION-001",
        "code": "model_return_capability_flag_already_completed", "removed_model_flags": removed,
        "source": "applied_domain_operation", "target_id": new_leg.id,
        "before_date": old_leg.departure_date.isoformat(), "after_date": new_leg.departure_date.isoformat(),
        "supplier_facts_inferred": False, "booking_consent_granted": False}]

_ROOM_TOPIC = re.compile(r"кроват|двуспал|односпал|ванн|душ|(?:тип|категори)\w*\s+номер|bed|bath|shower|room\s*type")
_ROOM_WORD = re.compile(r"(?:требован\w*|ограничен\w*|предпочтен\w*|пожелан\w*|уточн\w*|тип\w*|категори\w*|"
    r"номер\w*|комнат\w*|кроват\w*|взросл\w*|ванн\w*|душ\w*|отдельн\w*|двуспал\w*|односпал\w*|"
    r"нуж\w*|необходим\w*|како\w*|есть|ли|у|вас|нам|вам|для|по|к|на|и|или|например|"
    r"bed|beds|bath|shower|room|type|adults|preferences|requirements)")
_FLIGHT_TOPIC = re.compile(r"перелет|авиабилет|авиационн|flight")
_PERMISSION = re.compile(r"необходим|нуж|брониров|оформ|покуп|permission|need|book")
_FLIGHT_WORD = re.compile(r"(?:необходим\w*|нуж\w*|перелет\w*|авиабилет\w*|авиационн\w*|билет\w*|"
    r"брониров\w*|брон\w*|оформ\w*|покуп\w*|подтвержден\w*|подтверд\w*|обратн\w*|"
    r"ли|нужно|надо|также|и|или|для|вам|нам|вас|нас|их|эти|это|есть|требуется|"
    r"flight|flights|permission|need|booking|book|tickets)")
_USER_ROOM_TOPIC = re.compile(r"кроват|двуспал|односпал|ванн|душ|(?:тип|категори)\w*\s+номер|"
    r"смежн\w*\s+номер|соединенн\w*\s+номер|люкс|апартамент|некурящ|bed|bath|shower|room.?type|suite|deluxe|superior|connecting")
_USER_UNSUPPORTED_NEED = re.compile(r"безбарьер|пандус|инвалид|коляс|wheelchair|accessible|аллерг|allerg|глютен|gluten|"
    r"халяль|halal|медицин|medical|кислород|oxygen")
_UNSUPPORTED_ROOM_TOPIC = (r"вид\w*\s+(?:на\s+)?мор\w*|балкон\w*|этаж\w*|бассейн\w*|аквапарк\w*|"
    r"парковк\w*|шумоизоляц\w*|тишин\w*|тих\w*\s+номер\w*")
_HARD_MARKER = r"(?:обязател\w*|строго|только|необходим\w*|принципиальн\w*)"
_USER_HARD_ROOM_NEED = re.compile(r"(?:" + _HARD_MARKER + r")[^.!?;]{0,120}(?:" + _UNSUPPORTED_ROOM_TOPIC
    + r")|(?:" + _UNSUPPORTED_ROOM_TOPIC + r")[^.!?;]{0,120}(?:" + _HARD_MARKER + r")")
_TRANSPORT_UNCERTAIN = re.compile(r"(?:не\s+(?:знаю|решил\w*|определил\w*)|возможно|может|нужно\s+ли|нужны\s+ли)"
    r"[^.!?;]{0,100}(?:перелет|лететь|самолет|авиа|поезд|транспорт)|"
    r"(?:перелет|лететь|самолет|авиа|поезд|транспорт)[^.!?;]{0,100}(?:пока\s+не|не\s+решил\w*|под\s+вопросом)")
_TRANSPORT_VETO = re.compile(r"\bне\s+(?:летим|лечу|лететь|летаем|нуж\w*\s+(?:авиа|перелет|транспорт)|брониру\w*\s+(?:перелет|билет))|"
    r"\bбез\s+(?:перелет\w*|самолет\w*|авиа\w*|транспорт\w*)|"
    r"(?:только|лишь)\s+(?:отел\w*|гостиниц\w*|проживан\w*)|"
    r"(?:авиа\w*|перелет\w*|транспорт\w*)\s+не\s+(?:нуж\w*|надо)|\bno\s+(?:flights?|transport)\b")
_SAFE_TEXT = re.compile(r"[А-Яа-яЁё]")
_UNSAFE_TEXT = re.compile(r"https?://|www\.|@|[{}<>\\]|\b\w+[._]\w+\b|"
    r"\b(?:Bearer|api[_ -]?key|token|secret|password)\b|\b[a-z0-9]{24,}\b|"
    r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b|"
    r"\b(?:new-)?(?:trip|stay|room|leg|traveler|group|run|order)-[a-z0-9-]+\b", re.IGNORECASE)


def _key(text: str) -> str:
    return ' '.join(text.casefold().replace('ё', 'е').split())


def _only_words(text: str, allowed: re.Pattern) -> bool:
    words = re.findall(r'\w+', _key(text))
    return bool(words) and all(allowed.fullmatch(word) for word in words)


def safe_blocking_model_question(raw: str) -> str | None:
    """Keep a plain unknown's meaning without exposing an invented field ID."""
    if (not isinstance(raw, str) or not 2 <= len(raw.split()) or len(raw) > 500
            or not _SAFE_TEXT.search(raw) or _UNSAFE_TEXT.search(raw)
            or any(ord(char) < 32 for char in raw)):
        return None
    text = raw.strip()
    if not text:
        return None
    return text if '?' in text else 'Уточните: ' + text.rstrip('.! ') + '?'


def _complete_search_facts(state: TripState) -> bool:
    issues = [issue for issue in validate_trip(state) if not (issue['code'] == 'unsupported_budget'
        and state.budget and state.budget.scope == 'whole_trip')]
    if issues or not state.legs or any(leg.departure_date is None for leg in state.legs):
        return False
    if [(leg.origin, leg.destination, leg.departure_date) for leg in state.legs] != route_edges(state):
        return False
    if (state.search_policy.mode == 'HYBRID' and state.search_policy.hotel_provider == 'liteapi'
            and not state.search_policy.guest_nationality):
        return False
    people = {person.id: person for person in state.travelers}
    separate = {group.id for group in state.groups if group.keep_separate}
    for stay in state.stays:
        assigned = [person for room in stay.rooms for person in room.traveler_ids]
        if sorted(assigned) != sorted(people):
            return False
        for room in stay.rooms:
            if not room.traveler_ids or not any(people[p].type == 'adult' for p in room.traveler_ids):
                return False
            groups = {people[p].group_id for p in room.traveler_ids}
            if len(groups) > 1 and groups.intersection(separate):
                return False
    supported = {'breakfast', 'refundable', 'pets_allowed', 'pets', 'nonstop'}
    return not any(c.hard and c.key not in supported for c in state.constraints)


def classify_model_clarifications(state: TripState | dict, unknowns: list[str], *,
        server_questions: tuple[str, ...] | list[str] = (), current_text: str = '',
        recent_user_texts: tuple[str, ...] | list[str] = (), search_requested: bool = False) -> dict:
    """Classify only exact optional topics proved unrelated to known requirements.

    ``search_requested`` is a server decision, never a model-supplied consent.
    Raw/checked contracts stay unchanged. Unrecognised or server-owned questions
    remain blocking; no prices, capacity, beds or transport choices are inferred.
    """
    if not isinstance(unknowns, list) or any(not isinstance(raw, str) for raw in unknowns):
        raise ValueError('Model unknowns must be a validated list of strings')
    if (not isinstance(server_questions, (list, tuple)) or any(not isinstance(raw, str) for raw in server_questions)
            or not isinstance(recent_user_texts, (list, tuple)) or any(not isinstance(raw, str) for raw in recent_user_texts)
            or not isinstance(current_text, str) or type(search_requested) is not bool):
        raise ValueError('Clarification provenance must be typed server context')
    canonical = TripState.model_validate(state)
    ready = _complete_search_facts(canonical)
    user_text = _key(' '.join([*recent_user_texts, current_text]))
    saved = _key(json.dumps({'preferences': [p.model_dump(mode='json') for p in canonical.preferences],
        'constraints': [c.model_dump(mode='json') for c in canonical.constraints]}, ensure_ascii=False))
    user_room_need = bool(_USER_ROOM_TOPIC.search(user_text) or _USER_ROOM_TOPIC.search(saved))
    unsupported_need = bool(_USER_UNSUPPORTED_NEED.search(user_text) or _USER_UNSUPPORTED_NEED.search(saved)
        or _USER_HARD_ROOM_NEED.search(user_text))
    transport_block = bool(_TRANSPORT_UNCERTAIN.search(user_text) or _TRANSPORT_VETO.search(user_text))
    trusted = {_key(raw).strip('.?! ') for raw in server_questions}
    output = {'blocking_questions': [], 'optional_clarifications': [], 'notes': [], 'blocking_renderings': {}}
    for raw in deepcopy(unknowns):
        key = _key(raw)
        topic = None
        if ready and key.strip('.?! ') not in trusted and question_field(raw) is None and not unsupported_need:
            if _ROOM_TOPIC.search(key) and _only_words(raw, _ROOM_WORD) and not user_room_need:
                topic = 'room_type_preference'
            elif (_FLIGHT_TOPIC.search(key) and _PERMISSION.search(key) and _only_words(raw, _FLIGHT_WORD)
                    and search_requested and not transport_block):
                topic = 'transport_booking_permission'
        if topic is None:
            output['blocking_questions'].append(raw)
            if question_field(raw) is None and key.strip('.?! ') not in trusted:
                rendered = safe_blocking_model_question(raw)
                if rendered is not None:
                    output['blocking_renderings'][raw] = rendered
        else:
            output['optional_clarifications'].append({'topic': topic, 'question': raw, 'blocking': False,
                'reason': 'canonical_search_facts_complete_no_corresponding_user_requirement'})
            output['notes'].append({'code': 'optional_model_question_not_search_blocker', 'topic': topic,
                'source': 'model_unknown', 'supplier_facts_inferred': False, 'booking_consent_granted': False})
    return output
