"""Model-only source policy permission; checkout stays simulated in all modes."""
from __future__ import annotations

import re
from copy import deepcopy

from .route_policy_intent import current_clauses

_MODE_NAME = r"(?:demo(?:[- ]?режим)?|демо(?:[- ]?режим)?|hybrid|гибрид(?:ный)?(?:[- ]?режим)?)"
_MODE = re.compile(r"\b(?:включи|переключи|используй|выбери|установи|активируй|перейди|хочу\s+режим|"
    r"enable|switch|use|select|set|activate)\s+"
    rf"(?:(?:на|в|режим|поиска|поиск|только|пожалуйста|to|on|the|search|mode)\s+)*({_MODE_NAME})\b"
    r"(?!\s*[- ]\s*(?:fallback|фолб[еэ]к)\b)")
_MODE_ALTERNATIVE = re.compile(rf"\s*(?:(?:или|либо|и|or|and)\s+|/\s*)({_MODE_NAME})\b")
_MODE_CHOICE_OR_SEQUENCE = re.compile(r"(?:\b(?:или|либо|or|потом|затем|позже|then)\s+|/\s*)"
    r"(?:(?:включи|переключи|используй|выбери|установи|enable|switch|use|set)\s+)?"
    rf"(?:(?:на|в|режим|поиска|поиск|to|on|the|search|mode)\s+)*{_MODE_NAME}\b")
_MODE_NONCURRENT = re.compile(r"\b(?:if|would|suppose|yesterday|previously|said|wrote|quoted|never|don't|do\s+not|"
    r"спрашивал\w*|обсуждал\w*|цитат\w*|инструкц\w*|пример\w*|фраз\w*|надпис\w*|"
    r"можно|можешь|умеешь|как|зачем|почему)\b|\b(?:не\s+сейчас|пока\s+не|не\s+нужно)\b")
_AFFIRM = re.compile(r"\b(?:включи|переключи|используй|выбери|установи|ищи|показывай|работай|хочу|отключи|запрети|разреши)\b")
_NEGATIVE = re.compile(r"\bне\s+(?:включи|включай|переключи|переключай|используй|выбери|выбирай|"
    r"установи|устанавливай|активируй|переходи|перейди|хочу)\b")
_HOTEL = re.compile(r"\b(?:отел\w*|гостиниц\w*)\b")
_FLIGHT = re.compile(r"\b(?:перелет\w*|рейс\w*|авиабилет\w*)\b")
_DEFAULT = {"mode": "DEMO", "hotel_provider": "liteapi", "flight_provider": "serpapi", "demo_fallback": True}


def _get(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _mode_name(value: str) -> str:
    return "DEMO" if value.startswith(("demo", "демо")) else "HYBRID"


def _requested_modes(text: str) -> set[str]:
    requested = set()
    for clause in current_clauses(text):
        if _NEGATIVE.search(clause) or _MODE_NONCURRENT.search(clause):
            continue
        for match in _MODE.finditer(clause):
            requested.add(_mode_name(match[1]))
            tail = clause[match.end():]
            # A single command cannot choose DEMO and HYBRID at once. Only
            # directly connected mode alternatives extend its literal scope.
            while alternative := _MODE_ALTERNATIVE.match(tail):
                requested.add(_mode_name(alternative[1]))
                tail = tail[alternative.end():]
    return requested


def explicit_search_mode_request(text: str) -> str | None:
    """One current literal mode command, never a fallback or quoted question."""
    requested = _requested_modes(text)
    return next(iter(requested)) if len(requested) == 1 else None


def guard_search_policy_operations(state, ops: list, text: str) -> tuple[list, dict]:
    """Remove unaddressed protected fields; do not inspect nationality slots.

    A current literal mode command is completed if the model omitted it. No
    policy is inferred from 'how do I do a test checkout'. A partly allowed
    model operation can keep an explicitly selected mode while unrequested
    provider/fallback edits are removed. Identical-only policy ops are dropped
    so they cannot become a reason for another search. Typed commands bypass
    this function in the caller.
    """
    prior = _get(state, "search_policy", {})
    requested_modes = _requested_modes(text)
    literal_mode = next(iter(requested_modes)) if len(requested_modes) == 1 else None
    allowed = {"mode": requested_modes} if requested_modes else {}
    for clause in current_clauses(text):
        if _NEGATIVE.search(clause):
            continue
        affirmative = bool(_AFFIRM.search(clause))
        if affirmative or clause.strip() == "без демо":
            if re.search(r"\bбез\s+демо\b|\b(?:отключи|запрети)\s+(?:demo\s+)?fallback\b", clause):
                allowed.setdefault("demo_fallback", set()).add(False)
            if re.search(r"\b(?:разреши|включи)\s+(?:demo\s+)?fallback\b", clause):
                allowed.setdefault("demo_fallback", set()).add(True)
        if affirmative:
            for field, subject, providers in (("hotel_provider", _HOTEL, ("liteapi", "serpapi")),
                                              ("flight_provider", _FLIGHT, ("duffel", "serpapi"))):
                if subject.search(clause):
                    for provider in providers:
                        if re.search(rf"\b{provider}\b", clause):
                            allowed.setdefault(field, set()).add(provider)
    result, meta = [], {"notes": [], "conflicts": [], "unknowns": []}
    if len(requested_modes) > 1:
        question = "Какой один режим поиска включить: DEMO или HYBRID?"
        meta["unknowns"].append(question)
        current = ", ".join(clause for clause in current_clauses(text)
            if not _NEGATIVE.search(clause) and not _MODE_NONCURRENT.search(clause))
        if not _MODE_CHOICE_OR_SEQUENCE.search(current):
            # Simultaneous mutually exclusive instructions are a batch
            # conflict. An offered choice/sequence only needs clarification.
            meta["conflicts"].append(question)
    mode_written, model_mode_corrected = False, False
    for operation in deepcopy(ops):
        if _get(operation, "type") != "set_search_policy":
            result.append(operation)
            continue
        value = _get(operation, "value", {})
        if not isinstance(value, dict):
            result.append(operation)  # General schema validation owns shape.
            continue
        kept, removed = {}, []
        for key, proposed in value.items():
            if key == "mode" and literal_mode:
                model_mode_corrected |= proposed != literal_mode
                if _get(prior, "mode", _DEFAULT["mode"]) != literal_mode and not mode_written:
                    kept["mode"] = literal_mode
                    mode_written = True
                continue
            if key == "mode" and len(requested_modes) > 1:
                removed.append(key)
                continue
            if key not in _DEFAULT:
                kept[key] = proposed  # guest_nationality is separately owned.
                continue
            existing = _get(prior, key, _DEFAULT[key])
            if proposed == existing and type(proposed) is type(existing):
                continue
            permissions = allowed.get(key, set())
            if len(permissions) == 1 and any(type(proposed) is type(value) and proposed == value for value in permissions):
                kept[key] = proposed
            else:
                removed.append(key)
                if len(permissions) > 1:
                    meta["unknowns"].append("Какой один режим или источник поиска нужно включить? Уточните изменение политики поиска.")
        if removed:
            meta["notes"].append({"code": "search_policy_change_not_requested", "status": "suppressed",
                "finding_id": "ROUTER8-ASK-POLICY-001", "fields": removed})
        if kept:
            if isinstance(operation, dict):
                operation["value"] = kept
            else:
                operation.value = kept
            result.append(operation)
    mode_added = False
    if literal_mode:
        if _get(prior, "mode", _DEFAULT["mode"]) != literal_mode and not mode_written:
            result.append({"type": "set_search_policy", "value": {"mode": literal_mode}})
            mode_added = True
        meta["notes"].append({"code": "search_policy_literal_grounded", "mode": literal_mode,
            "source": "current_explicit_mode_command", "operation_added": mode_added,
            "model_mode_corrected": model_mode_corrected,
            "finding_id": "HARNESS-004-DEMO-COMMAND-IGNORED-001"})
    meta["unknowns"] = list(dict.fromkeys(meta["unknowns"]))
    return result, meta
