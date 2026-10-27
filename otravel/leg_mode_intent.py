"""Model-only permission/address guard for existing leg transport modes.

This module never edits the trip, creates legs, resolves geography or promises
train/flight availability. Actual provider coverage is validated elsewhere.
"""
from __future__ import annotations

import re
from collections import defaultdict
from copy import deepcopy

from .route_policy_intent import current_clauses

_MODE = r"(?:поезд(?:ом|е|а)?|train|самолет(?:ом|е|а)?|flight)"
_MODES = re.compile(rf"(?<![\w-])(?P<mode>{_MODE})(?![\w-])")
_LEG = r"(?:участ(?:ок|ка|ке|ку|ком)|плеч(?:о|а|е|у|ом)|перелет(?:а|е|у|ом)?|рейс(?:а|е|у|ом)?|сегмент(?:а|е|у|ом)?|leg)"
_ORD = r"(?:перв\w*|втор\w*|трет\w*|четверт\w*|пят\w*|шест\w*|седьм\w*|восьм\w*|девят\w*|десят\w*|последн\w*|\d{1,3}(?:-?(?:й|го|му|м))?)"
_ORDINAL_LEG = re.compile(rf"\b(?P<ordinal>{_ORD})\s+{_LEG}\b")
_NUMBERED_LEG = re.compile(rf"\b{_LEG}\s+(?:номер\s+|№\s*)?(?P<number>\d{{1,3}})(?!\d)")
_VERB = r"(?:замени|поменяй|смени|переключи|измени|сделай|выбери|выбираем|используй|установи|поставь|хочу|хотим|нужен|нужна|нужно|давай|поедем|поеду|едем|еду|полетим|полечу|летим|лечу|choose|use|set|change|switch|take)"
_INSTRUCTION = re.compile(rf"\b{_VERB}\b")
_OTHER_EDIT = re.compile(r"\b(?:измени|поменяй|сделай|установи|поставь|замени)\s+(?:пожалуйста\s+)?(?:цен\w*|стоимост\w*|бюджет\w*|дат\w*|врем\w*|багаж\w*)\b|\b(?:change|set|switch)\s+(?:the\s+)?(?:price|budget|date|time|cost)\b")
_CHANGE_TO = re.compile(rf"\b(?:замени|поменяй|смени|переключи|измени|сделай|change|switch)\b[^.!?;]{{0,120}}?\b(?:на|to)\s+(?P<mode>{_MODE})(?![\w-])")
_NEGATIVE = re.compile(r"\b(?:не|нельзя|not|never|no)\b|\b(?:don't|dont|can't|cannot)\b")
_NONCURRENT = re.compile(r"\b(?:if|would|maybe|perhaps|previously|yesterday|earlier|suppose|hypothetically|said|wrote|asked)\b")
_QUESTION = re.compile(r"^(?:(?:а|и)\s+)?(?:можно|можешь|умеешь|сможешь|почему|как|can|could|what|how)\b")
_DISCUSSION = re.compile(r"\b(?:обсудить|узнать|понять|сравнить|расскажи|объясни|покажи|discuss|know|compare|tell|show)\b")
_HOTEL = re.compile(r"\b(?:отел\w*|гостиниц\w*|проживан\w*|номер\w*)\b")
_EXTRA_QUOTES = re.compile(r"‘[^’]*’|„[^“]*“")
_SPLIT = re.compile(rf"\s+(?:и|но|а|and|but)\s+(?=(?:{_VERB}|{_ORD}\s+{_LEG}|туда|обратно)\b)")


def _get(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _mode(word):
    return "train" if word.startswith("поезд") or word == "train" else "flight"


def _owned_legs(state):
    legs = _get(state, "legs", [])
    if not isinstance(legs, list):
        return []
    valid = (all(isinstance(_get(leg, "id"), str) and _get(leg, "id") for leg in legs)
             and len({_get(leg, "id") for leg in legs}) == len(legs)
             and len({_get(leg, "id").casefold() for leg in legs}) == len(legs))
    return legs if valid else []


def make_leg_mode_pending_context(state, mode):
    """Bind a NEW server-owned question to the current committed trip."""
    version, trip_id = _get(state, "state_version"), _get(state, "trip_id")
    legs = _owned_legs(state)
    if (type(version) is not int or version < 0 or not isinstance(trip_id, str) or not trip_id
            or not isinstance(mode, str) or mode not in {"flight", "train"} or not legs):
        return {}
    return {"field": "leg.mode", "leg_mode_proposal": {"owner": "leg_mode_intent_v1", "trip_id": trip_id,
        "based_state_version": version, "mode": mode, "candidate_leg_ids": [_get(leg, "id") for leg in legs]}}


def _ordinal(word, count):
    for prefix, index in (("перв", 1), ("втор", 2), ("трет", 3), ("четверт", 4), ("пят", 5),
                          ("шест", 6), ("седьм", 7), ("восьм", 8), ("девят", 9), ("десят", 10)):
        if word.startswith(prefix):
            return index
    return count if word.startswith("последн") else int(re.match(r"\d+", word)[0])


def _targets(clause, legs, origin, route_kind):
    identifiers, invalid = set(), False
    count = len(legs)
    for match in _ORDINAL_LEG.finditer(clause):
        index = _ordinal(match["ordinal"], count)
        if 1 <= index <= count:
            identifiers.add(_get(legs[index - 1], "id"))
        else:
            invalid = True
    for match in _NUMBERED_LEG.finditer(clause):
        index = int(match["number"])
        if 1 <= index <= count:
            identifiers.add(_get(legs[index - 1], "id"))
        else:
            invalid = True
    for leg in legs:
        identifier = _get(leg, "id")
        literal = re.escape(identifier.casefold())
        explicit = re.search(rf"\b(?:leg|id|для|участок|плечо)\s*(?:[:=]\s*)?{literal}(?![\w-])", clause)
        structured = re.search(r"[0-9:_-]", identifier) and re.search(r"(?<![\w-])" + literal + r"(?![\w-])", clause)
        if explicit or structured:
            identifiers.add(identifier)
    if re.search(r"\bтуда\b", clause):
        if legs:
            identifiers.add(_get(legs[0], "id"))
        else:
            invalid = True
    if re.search(r"\bобратно\b", clause):
        if count >= 2 and origin and route_kind == "round_trip" and _get(legs[-1], "destination") == origin:
            identifiers.add(_get(legs[-1], "id"))
        else:
            invalid = True
    return identifiers, invalid


def guard_leg_mode_operations(state, ops: list, text: str) -> tuple[list, dict]:
    """Retain only addressed current requests; unrelated operations are copied."""
    copied = deepcopy(ops)
    meta = {"notes": [], "unknowns": [], "conflicts": []}
    if not any(_get(op, "type") == "set_leg_mode" for op in copied):
        return copied, meta
    legs = _owned_legs(state)
    owned = {_get(leg, "id"): leg for leg in legs}
    permissions = defaultdict(set)
    unaddressed_modes = set()
    question = None
    clauses = current_clauses(_EXTRA_QUOTES.sub(" ", text) if isinstance(text, str) else text)
    for raw in clauses:
        # Keep 'туда и обратно' together; split independent positive commands.
        parts = [raw] if re.search(r"\bтуда\s+и\s+обратно\b", raw) else _SPLIT.split(raw)
        for clause in parts:
            if (_NEGATIVE.search(clause) or _NONCURRENT.search(clause) or _QUESTION.search(clause)
                    or _OTHER_EDIT.search(clause) or _DISCUSSION.search(clause)):
                continue
            selected = _CHANGE_TO.search(clause)
            choice = clause.split("вместо", 1)[0] if selected is None else selected["mode"]
            modes = {_mode(match["mode"]) for match in _MODES.finditer(choice)}
            if not modes:
                continue
            targets, invalid = _targets(clause, legs, _get(state, "origin"),
                _get(_get(state, "route_policy", {}), "kind", "round_trip"))
            bare = re.fullmatch(rf"(?:на\s+)?{_MODE}", clause) is not None
            if not (_INSTRUCTION.search(clause) or targets or invalid or bare or _HOTEL.search(clause)):
                continue
            if len(modes) != 1:
                question = "Какой транспорт выбрать для участка: самолёт или поезд?"
                continue
            if invalid:
                question = "Какой существующий участок маршрута нужно изменить? Обратный участок должен быть уже определён."
                continue
            if not targets and _HOTEL.search(clause):
                question = "Вы упомянули отель. Для смены транспорта укажите конкретный участок маршрута."
                continue
            if not targets and len(legs) == 1:
                targets = set(owned)
            if not targets:
                question = "Какой участок маршрута изменить: укажите его номер или направление «туда»/«обратно»?"
                unaddressed_modes.update(modes)
                continue
            for identifier in targets:
                permissions[identifier].update(modes)
    ambiguous = {identifier for identifier, modes in permissions.items() if len(modes) > 1}
    retained = []
    for operation in copied:
        if _get(operation, "type") != "set_leg_mode":
            retained.append(operation)
            continue
        target, value = _get(operation, "target_id"), _get(operation, "value", {})
        mode = _get(value, "mode")
        leg = owned.get(target) if isinstance(target, str) else None
        if (leg is not None and isinstance(mode, str) and mode in {"flight", "train"}
                and mode == _get(leg, "mode", "flight") and target not in ambiguous):
            retained.append(operation)
            continue
        if isinstance(target, str) and target in ambiguous:
            meta["conflicts"].append("Для одного участка указаны и самолёт, и поезд. Уточните итоговый транспорт.")
        elif leg is not None and isinstance(mode, str) and mode in {"flight", "train"} and mode in permissions.get(target, ()):
            retained.append(operation)
            continue
        elif permissions and isinstance(target, str) and target not in permissions:
            meta["unknowns"].append("Укажите тот существующий участок, для которого нужно изменить транспорт.")
        elif question:
            meta["unknowns"].append(question)
        elif leg is None:
            meta["unknowns"].append("Какой существующий участок маршрута нужно изменить?")
        meta["notes"].append({"code": "leg_mode_change_not_authorized", "status": "suppressed",
            "finding_id": "HARNESS-004-LEG-MODE-PERMISSION-001"})
    for field in ("unknowns", "conflicts"):
        meta[field] = list(dict.fromkeys(meta[field]))
    if meta["unknowns"] and not meta["conflicts"] and len(unaddressed_modes) == 1:
        packet = make_leg_mode_pending_context(state, next(iter(unaddressed_modes)))
        if packet:
            meta["pending_context"] = packet
            meta["notes"].append({"code": "leg_mode_proposal_created", "owner": "leg_mode_intent_v1",
                "based_state_version": _get(state, "state_version"), "mode": packet["leg_mode_proposal"]["mode"]})
    return retained, meta


def resolve_leg_mode_answer(state, pending, text):
    """Resolve a short addressed answer only from a fresh code-owned packet."""
    no = {"handled": False, "operations": []}
    if not isinstance(pending, dict) or pending.get("field") != "leg.mode" or not isinstance(text, str):
        return no
    context = pending.get("context")
    proposal = context.get("leg_mode_proposal") if isinstance(context, dict) else None
    required = {"owner", "trip_id", "based_state_version", "mode", "candidate_leg_ids"}
    legs = _owned_legs(state)
    version = _get(state, "state_version")
    if (not isinstance(proposal, dict) or set(proposal) != required or not legs
            or proposal["owner"] != "leg_mode_intent_v1" or proposal["trip_id"] != _get(state, "trip_id")
            or not isinstance(_get(state, "trip_id"), str) or not _get(state, "trip_id")
            or type(version) is not int or type(pending.get("state_version")) is not int
            or pending["state_version"] != version or type(proposal["based_state_version"]) is not int
            or proposal["based_state_version"] != version
            or proposal["candidate_leg_ids"] != [_get(leg, "id") for leg in legs]
            or not isinstance(proposal["mode"], str) or proposal["mode"] not in {"flight", "train"}):
        return no
    key = text.casefold().replace("ё", "е").strip(" .!")
    identifiers = {identifier.casefold(): identifier for identifier in proposal["candidate_leg_ids"]}
    if key in identifiers:
        targets, invalid = {identifiers[key]}, False
    elif re.fullmatch(_ORD, key):
        index = _ordinal(key, len(legs))
        targets, invalid = ({_get(legs[index - 1], "id")}, False) if 1 <= index <= len(legs) else (set(), True)
    elif re.fullmatch(rf"(?:{_ORD}\s+{_LEG}|{_LEG}\s+(?:номер\s+|№\s*)?\d{{1,3}}|туда|обратно)", key):
        targets, invalid = _targets(key, legs, _get(state, "origin"), _get(_get(state, "route_policy", {}), "kind", "round_trip"))
    else:
        return no
    if invalid or len(targets) != 1:
        return {"handled": True, "operations": [], "question": "Укажите один существующий участок: первый, второй или последний.",
            "pending_context": {"field": "leg.mode", "leg_mode_proposal": deepcopy(proposal)}}
    return {"handled": True, "operations": [{"type": "set_leg_mode", "target_id": next(iter(targets)),
            "value": {"mode": proposal["mode"]}}], "pending_context_clear": ["leg_mode_proposal"]}
