"""Closed canonical reminders only; no edits, tools, clock or persistence."""
from __future__ import annotations

import re
from copy import deepcopy

from .answering import checked_reply, facts_projection

_FORBIDDEN = re.compile(r"[\d\"'«»“”„\x60<>]|"
    r"\b(?:если|бы|допустим|предположим|возможно|может|раньше|"
    r"прежн\w*|измен\w*|было|стало|сравн\w*|паспорт\w*|виз\w*|"
    r"закон\w*|стоимост\w*|цен\w*|отел\w*|тариф\w*|брони\w*|куп\w*)\b")
_MONTH = r"(?:январь|февраль|март|апрель|май|июнь|июль|август|сентябрь|октябрь|ноябрь|декабрь)"
_CALENDAR_FOOTER = re.compile(rf"[.!?]\s*{_MONTH}\s+был\s+только\s+вопросом[.!]*$")
_CLOSED_DATES_ADULTS = re.compile(r"сверь\s+(?:(?:пожалуйста|пожалуста)[, ]+)?"
    r"(?:обе\s+)?даты\s+и\s+число\s+взрослых")
_NO_CHANGE_TAIL = re.compile(r"(?:[.?!]\s*|,\s*)ничего\s+не\s+меняй[.?!]*$")
_REMINDER = re.compile(r"(?:пожалуйста,\s*)?напомни\s+(?P<topic>.+)")
_PARTY = re.compile(r"(?:(?:наш|мой|текущий|сохраненный)\s+)*состав"
    r"(?:\s+(?:поездки|нашей поездки))?")
_CHILD_AGES = re.compile(r"возраста\s+(?:(?:наших|моих)\s+)?детей")
_DATES = re.compile(r"(?:(?:наши|мои|текущие|сохраненные)\s+)*даты"
    r"(?:\s+(?:поездки|нашей поездки))?")
_ROUTE = re.compile(r"(?:(?:наш|мой|текущий|сохраненный)\s+)*маршрут"
    r"(?:\s+(?:поездки|нашей поездки))?")
_BUDGET = re.compile(r"(?:(?:наш|мой|текущий|сохраненный|общий)\s+)*бюджет"
    r"(?:\s+(?:поездки|нашей поездки))?")
_DIRECT = (
    (re.compile(r"кто\s+у\s+нас\s+(?:(?:сейчас|в итоге)\s+)?(?:едет|поедет)"), "party"),
    (re.compile(r"какой\s+у\s+нас\s+(?:сейчас\s+)?состав"), "party"),
    (re.compile(r"какой\s+у\s+нас\s+(?:сейчас\s+)?бюджет"), "budget"),
    (re.compile(r"какие\s+у\s+нас\s+(?:сейчас\s+)?даты"), "dates"),
    (re.compile(r"какой\s+у\s+нас\s+(?:сейчас\s+)?маршрут"), "route"),
)


def classify_closed_saved_fact_question(text):
    if not isinstance(text, str) or not 0 < len(text) <= 500:
        return None
    key = " ".join(text.casefold().replace("ё", "е").split())
    key = _CALENDAR_FOOTER.sub("", key)
    if _FORBIDDEN.search(key):
        return None
    key = _NO_CHANGE_TAIL.sub("", key).strip(" .?!")
    if _CLOSED_DATES_ADULTS.fullmatch(key):
        return "dates,party"
    reminder = _REMINDER.fullmatch(key)
    if reminder:
        topic = reminder["topic"]
        for pattern, section in ((_PARTY, "party"), (_CHILD_AGES, "child_ages"),
                (_DATES, "dates"), (_ROUTE, "route"), (_BUDGET, "budget")):
            if pattern.fullmatch(topic):
                return section
        return None
    for pattern, section in _DIRECT:
        if pattern.fullmatch(key):
            return section
    return None


def canonical_saved_fact_answer(text, state):
    kind = classify_closed_saved_fact_question(text)
    if kind is None:
        return None
    data = state.model_dump(mode="json") if hasattr(state, "model_dump") else deepcopy(state)
    if not isinstance(data, dict):
        return None
    people = data.get("travelers", [])
    if not isinstance(people, list) or any(not isinstance(person, dict) for person in people):
        return None
    sections = kind.split(",")
    if kind == "child_ages":
        # Reuse date formatter for DOB-derived trip ages; do not use today's
        # date or invent an age calculator. Otherwise the saved roster suffices.
        children = [p for p in data.get("travelers", []) if p.get("type") == "child"]
        if any(p.get("birth_date") is not None for p in children):
            sections = ["dates"] if data.get("stays") or data.get("legs") else ["party", "dates"]
        else:
            sections = ["party"]
    try:
        facts = facts_projection(data, [], [], [], [], question=text)
        reply, notes = checked_reply(",".join(sections), facts, text)
    except (ValueError, TypeError, KeyError):
        return None
    return reply if not notes and isinstance(reply, str) and reply else None
