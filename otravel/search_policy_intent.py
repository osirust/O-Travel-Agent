"""Model-only source policy permission; checkout stays simulated in all modes."""
from __future__ import annotations

import re
from copy import deepcopy

from .route_policy_intent import current_clauses

_MODE = re.compile(r"\b(?:включи|переключи|используй|выбери|установи|хочу\s+режим)\s+"
    r"(?:(?:на|в|режим|поиск|только|пожалуйста)\s+)*(demo|hybrid|демо|гибрид(?:ный)?)\b(?!\s+fallback\b)")
_AFFIRM = re.compile(r"\b(?:включи|переключи|используй|выбери|установи|ищи|показывай|работай|хочу|отключи|запрети|разреши)\b")
_NEGATIVE = re.compile(r"\bне\s+(?:включай|переключай|используй|выбирай|устанавливай|хочу)\b")
_HOTEL = re.compile(r"\b(?:отел\w*|гостиниц\w*)\b")
_FLIGHT = re.compile(r"\b(?:перелет\w*|рейс\w*|авиабилет\w*)\b")
_DEFAULT = {"mode": "DEMO", "hotel_provider": "liteapi", "flight_provider": "serpapi", "demo_fallback": True}


def _get(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def guard_search_policy_operations(state, ops: list, text: str) -> tuple[list, dict]:
    """Remove unaddressed protected fields; do not inspect nationality slots.

    No policy is inferred from 'how do I do a test checkout'. A partly allowed
    model operation can keep an explicitly selected mode while unrequested
    provider/fallback edits are removed. Identical-only policy ops are dropped
    so they cannot become a reason for another search. Typed commands bypass
    this function in the caller.
    """
    prior = _get(state, "search_policy", {})
    allowed = {}
    for clause in current_clauses(text):
        if _NEGATIVE.search(clause):
            continue
        for mode in _MODE.finditer(clause):
            allowed.setdefault("mode", set()).add("DEMO" if mode[1] in {"demo", "демо"} else "HYBRID")
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
    meta["unknowns"] = list(dict.fromkeys(meta["unknowns"]))
    return result, meta
