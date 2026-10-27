"""Current-message permission for model-proposed traveler removals.

This narrow guard does not choose a person or prove pronoun/ID attribution.
Positive requests continue to the existing addressed/group/domain resolvers.
Typed commands and canonical short-answer operations bypass this model guard.
"""
from __future__ import annotations

import re
from copy import deepcopy

from .party_literals import explicit_no_children
from .preference_grounding import _clauses

FINDING_ID = "HARNESS-004-UNREQUESTED-TRAVELER-REMOVAL-001"
_REMOVE = re.compile(r"\b(?:убери|уберите|убрать|удали|удалите|удалить|исключи|исключите|исключить|"
    r"вычеркни|вычеркните|remove|exclude|drop|delete)\b")
_PERSON = re.compile(r"\b(?:участник\w*|путешественник\w*|гост(?:ь|я|ю|ем|е|и|ей|ям|ями|ях)|взросл\w*|ребен\w*|дет(?:и|ей|ям|ьми|ях)|младен\w*|"
    r"сын\w*|доч\w*|сестр\w*|брат\w*|муж\w*|жен[ауые]\w*|мам\w*|пап\w*|родител\w*|отец|отца|"
    r"он|она|они|я|меня|его|ее|их|нас|всех|adult\w*|child\w*|infant\w*|travell?er\w*|guest\w*|"
    r"sister|brother|mother|father|wife|husband|him|her|them|me)\b")
_NONTRIP = re.compile(r"\bне\s+(?:(?:будет|будут|буду|будем)\s+(?:ехать|лететь|участвовать)|"
    r"ед(?:ет|ут|у|ем|ете)|поед(?:ет|ут|у|ем|ете)|летит|летят|лечу|летим|полетит|полетят|полечу|полетим)\b|"
    r"\b(?:is|are|am)\s+not\s+(?:coming|going|travelling|traveling)\b")
_NONCURRENT = re.compile(r"\b(?:думал\w*|хотел\w*|планировал\w*|советуют|пишут|говорят|"
    r"if|would|hypothetically|previously|earlier|yesterday|suppose)\b")
_NEGATED = re.compile(r"\b(?:не\s+(?:(?:надо|нужно)\s+)?(?:убира\w*|удаля\w*|исключа\w*|вычеркива\w*)|"
    r"(?:do\s+not|don't|never)\s+(?:remove|exclude|drop|delete))\b")
_OTHER_OBJECT = re.compile(r"\s*(?:это\s+|этот\s+|данное\s+)?(?:пожелан\w*|предпочтен\w*|ограничен\w*|"
    r"услови\w*|фильтр\w*|бюджет\w*|детал\w*|отел\w*|гостиниц\w*|номер\w*|проживан\w*|"
    r"рейс\w*|перелет\w*|тариф\w*|preference|constraint|filter|budget|hotel|flight|fare)\b")
_LOCAL_SCOPE = re.compile(r"\b(?:из|с)\s+(?:групп\w*|номер\w*|комнат\w*|семь\w*)\b")
_TRIP_SCOPE = re.compile(r"\b(?:поездк\w*|состав\w*|маршрут\w*|trip|travel\s+party)\b")


def _has_current_removal(state: dict, text: str) -> bool:
    owned_ids = [person.get("id") for person in state.get("travelers", [])
        if isinstance(person, dict) and isinstance(person.get("id"), str)]
    for clause in _clauses(text):
        if _NONCURRENT.search(clause) or _NEGATED.search(clause):
            continue
        if _LOCAL_SCOPE.search(clause) and not _TRIP_SCOPE.search(clause):
            continue
        if explicit_no_children(clause) or _NONTRIP.search(clause) and _PERSON.search(clause):
            return True
        for verb in _REMOVE.finditer(clause):
            tail = clause[verb.end():].strip()[:160]
            if _OTHER_OBJECT.match(tail):
                continue
            if (_PERSON.search(tail) or re.search(r"\bиз\s+(?:поездк\w*|состав\w*)\b", tail)
                    or any(re.search(r"(?<!\w)" + re.escape(identifier.casefold()) + r"(?!\w)", tail)
                        for identifier in owned_ids)):
                return True
    return False


def guard_traveler_removal_operations(before_state, operations, text):
    data = before_state.model_dump(mode="json") if hasattr(before_state, "model_dump") else deepcopy(before_state)
    copied = [op.model_dump(mode="json") if hasattr(op, "model_dump") else deepcopy(op) for op in operations]
    meta = {"notes": [], "unknowns": [], "conflicts": []}
    if not isinstance(data, dict) or not isinstance(text, str) or not 0 < len(text) <= 20_000:
        return copied, meta
    if _has_current_removal(data, text):
        return copied, meta
    result = []
    for operation in copied:
        if isinstance(operation, dict) and operation.get("type") == "remove_traveler":
            meta["notes"].append({"code": "traveler_removal_not_requested", "finding_id": FINDING_ID,
                "field": "travelers", "target_id": operation.get("target_id"), "status": "suppressed"})
        else:
            result.append(operation)
    return result, meta
