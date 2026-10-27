"""Suppress invented ages on newly added adults, without guessing an age.

Explicit age-to-person attribution and invented birth dates are outside this
narrow guard. Schema/domain validation remains responsible for invalid values.
"""
from __future__ import annotations

import re
from copy import deepcopy

from .preference_grounding import _clauses

FINDING_ID = "HARNESS-004-UNASKED-ADULT-AGE-001"
_ADULT_SUBJECT = re.compile(r"\b(?:мне|нам|мужу|жене|муж|жена|взросл\w*|мужчин\w*|женщин\w*|"
    r"маме|папе|матери|отцу|я|мой|моя|adult\w*|i|my|wife|husband)\b")
_OTHER_SUBJECT = re.compile(r"\b(?:ребен\w*|дет\w*|сын\w*|доч\w*|малыш\w*|отел\w*|гостиниц\w*|"
    r"child\w*|kid\w*|son|daughter|hotel)\b")
_AGE = re.compile(r"(?<!\d)(\d{2,3})\s*(?:лет|год(?:а)?|years?(?:\s+old)?|летн\w*)(?!\w)")
_SHORT_AGE = re.compile(r"\b(?:мне|нам|мужу|жене|маме|папе|матери|отцу|взросл\w*|adult\w*|i\s+am|wife|husband)"
    r"\s*(?:по\s+|возраст\w*\s*[:=]?\s*|aged?\s*)?(\d{2,3})(?=\s*$|\s+(?:лет|год(?:а)?|years?)\b)")
_DOB_WORD = re.compile(r"\b(?:дат\w*\s+рождени\w*|родил\w*|birth\s*date|born|dob)\b")
_DATE = re.compile(r"(?<!\d)(?:\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]\d{4})(?!\d)")
_NONCURRENT_EN = re.compile(r"\b(?:if|would|hypothetically|previously|earlier|yesterday|suppose)\b")
_PAST_AGE = re.compile(r"\b(?:мне|нам|мужу|жене|маме|папе|матери|отцу|взросл\w*)\s+(?:тогда\s+)?"
    r"(?:было|исполнилось)\s+\d|\b(?:i|wife|husband|adult\w*)\s+(?:was|were)\s+\d")
_NEGATIVE_AGE = re.compile(r"\b(?:не|not)\s+(?:\d|родил|born)|\b(?:не\s+(?:указывай|сохраняй|записывай|добавляй)\s+возраст|"
    r"возраст\s+не\s+(?:указывай|сохраняй|записывай|добавляй)|do\s+not\s+(?:store|save)\s+(?:my\s+)?age)\b")


def _adult_age_or_dob_is_explicit(text: str) -> bool:
    for clause in _clauses(text):
        if _NONCURRENT_EN.search(clause) or _NEGATIVE_AGE.search(clause) or _PAST_AGE.search(clause):
            continue
        for match in _AGE.finditer(clause):
            if int(match[1]) < 18:
                continue
            prefix = clause[:match.start()]
            adults = list(_ADULT_SUBJECT.finditer(prefix))
            others = list(_OTHER_SUBJECT.finditer(prefix))
            if adults and (not others or adults[-1].start() > others[-1].start()):
                return True
        if any(int(match[1]) >= 18 for match in _SHORT_AGE.finditer(clause)):
            return True
        if _DOB_WORD.search(clause) and _DATE.search(clause) and _ADULT_SUBJECT.search(clause):
            # Do not use a child's explicit DOB as an adult's authority.
            before_dob = clause[:_DATE.search(clause).start()]
            adults = list(_ADULT_SUBJECT.finditer(before_dob))
            others = list(_OTHER_SUBJECT.finditer(before_dob))
            if adults and (not others or adults[-1].start() > others[-1].start()):
                return True
    return False


def guard_adult_age_operations(before_state, operations, text):
    """Copy proposals and remove only unasked integer age>=18 from new adults."""
    data = before_state.model_dump(mode="json") if hasattr(before_state, "model_dump") else deepcopy(before_state)
    copied = [operation.model_dump(mode="json") if hasattr(operation, "model_dump") else deepcopy(operation)
        for operation in operations]
    meta = {"notes": [], "unknowns": [], "conflicts": []}
    if not isinstance(data, dict) or not isinstance(text, str) or not 0 < len(text) <= 20_000:
        return copied, meta
    if _adult_age_or_dob_is_explicit(text):
        return copied, meta
    existing = {person.get("id") for person in data.get("travelers", []) if isinstance(person, dict)}
    for operation in copied:
        if not isinstance(operation, dict) or operation.get("type") != "add_traveler":
            continue
        value = operation.get("value")
        if (not isinstance(value, dict) or value.get("type") != "adult"
                or value.get("id") in existing or type(value.get("age")) is not int or value["age"] < 18):
            continue
        operation["value"] = {key: item for key, item in value.items() if key != "age"}
        meta["notes"].append({"code": "unasked_new_adult_age_removed", "finding_id": FINDING_ID,
            "field": "travelers.age", "traveler_id": value.get("id"), "status": "suppressed"})
    return copied, meta
