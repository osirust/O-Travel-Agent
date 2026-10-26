"""Bound model route-policy changes to current explicit route instructions."""
from __future__ import annotations

import re
from copy import deepcopy

from .geography import _CITY_ALIASES

_QUOTES = re.compile(r'«[^»]*»|“[^”]*”|"[^"\n]*"|\'[^\'\n]*\'|`[^`]*`', re.DOTALL)
_NONCURRENT = re.compile(r"\b(?:если|бы|допустим|предположим|например|раньше|вчера|ранее|думал\w*|говорил\w*|писал\w*|сказал\w*|просил\w*|цитирую)\b|\bв\s+прошл\w*\b")
_DECLARED_END = r"(?:поездка|маршрут)\s+(?:заканчивается|завершается|закончится|завершится)"
_KINDS = {
    "one_way": re.compile(r"\b(?:в\s+одну\s+сторону|только\s+туда|обратн\w*\s+(?:перелет|билет|путь)\s+не\s+нуж\w*|без\s+обратного\s+(?:перелета|билета))\b"),
    "round_trip": re.compile(r"\b(?:туда\s*(?:и\s+|[-–]\s*)?обратно|возвращаемся\s+домой|вернемся\s+домой)\b"),
    "undecided": re.compile(r"\b(?:возвращение\s+(?:пока\s+)?не\s+(?:решили|определено|определили)|обратный\s+(?:маршрут|путь)\s+(?:пока\s+)?не\s+решили)\b"),
    "open_jaw": re.compile(rf"\b(?:open[-_ ]jaw|вернемся\s+в|возвращаемся\s+в|закончим\s+(?:маршрут|поездку)\s+в|{_DECLARED_END}\s+в)\b"),
}
_DIRECT = re.compile(r"\b(?:выбери|используй|установи|включи|хочу)\s+(?:тип\s+маршрута\s+)?(one_way|round_trip|open_jaw|undecided)\b")
_NEGATIVE = re.compile(r"\bне\s+(?:выбирай|используй|устанавливай|включай|хочу|хотим|едем|летим|вернемся|возвращаемся)\b")
_END = re.compile(rf"\b(?:вернемся|возвращаемся|закончим\s+(?:маршрут|поездку)|{_DECLARED_END})\s+в\s+(.+)$")
_CITY_NAMES = "|".join(re.escape(k) for k in sorted(_CITY_ALIASES, key=len, reverse=True))
_CITY = re.compile(rf"(?<![\w-])(?:{_CITY_NAMES})(?![\w-])")


def current_clauses(text: str) -> list[str]:
    """Bounded assertion scope shared by the two model-only policy guards."""
    if not isinstance(text, str):
        return []
    unquoted = _QUOTES.sub(" ", text.casefold().replace("ё", "е"))
    unquoted = "\n".join(line for line in unquoted.splitlines() if not line.lstrip().startswith(">"))
    result = []
    for match in re.finditer(r"[^.!?;\n]+(?:[.!?;\n]|$)", unquoted):
        sentence = match.group()
        if sentence.rstrip().endswith("?"):
            continue
        for branch in re.split(r"\b(?:но|а)\s+(?:сейчас|теперь)\b", sentence):
            if not _NONCURRENT.search(branch):
                result.extend(part.strip(" .!;\n") for part in branch.split(","))
    return result


def _get(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _policy(value) -> dict:
    return {"kind": _get(value, "kind", "round_trip"), "end_city": _get(value, "end_city")}


def guard_route_policy_operations(state, ops: list, text: str) -> tuple[list, dict]:
    """Suppress only unpermitted model policies, never create a route intent.

    Existing identical policy operations are harmless. Ambiguous explicit end
    cities produce a human question; unrelated operations remain untouched.
    Legacy round_trip defaults and typed commands are outside this guard.
    """
    result, meta = [], {"notes": [], "conflicts": [], "unknowns": []}
    prior = _policy(_get(state, "route_policy", {}))
    requested, endpoint_parts, explicit_open_kind = set(), [], False
    for clause in current_clauses(text):
        if _NEGATIVE.search(clause):
            continue
        direct = _DIRECT.search(clause)
        if direct:
            requested.add(direct[1])
            explicit_open_kind |= direct[1] == "open_jaw"
        for kind, pattern in _KINDS.items():
            if pattern.search(clause):
                requested.add(kind)
                if kind == "open_jaw":
                    target = _END.search(clause)
                    endpoint_parts.append(target[1] if target else clause)
    endpoints = {_CITY_ALIASES[m.group()] for part in endpoint_parts for m in _CITY.finditer(part)}
    origin = _get(state, "origin")
    origin = _CITY_ALIASES.get(origin.casefold()) if isinstance(origin, str) else None
    if requested == {"open_jaw"} and endpoints == {origin} and origin and not explicit_open_kind:
        requested = {"round_trip"}
    for operation in deepcopy(ops):
        if _get(operation, "type") != "set_route_policy":
            result.append(operation)
            continue
        policy = _policy(_get(operation, "value", {}))
        if policy == prior:
            result.append(operation)
            continue
        allowed = isinstance(policy["kind"], str) and len(requested) == 1 and policy["kind"] in requested
        if allowed and policy["kind"] == "open_jaw":
            allowed = isinstance(policy["end_city"], str) and len(endpoints) == 1 and policy["end_city"] in endpoints
            if not allowed:
                meta["unknowns"].append("В каком одном городе должна закончиться поездка? Уточните конечный город открытого маршрута.")
        elif allowed:
            allowed = policy["end_city"] is None
        if allowed:
            result.append(operation)
        else:
            meta["notes"].append({"code": "route_policy_change_not_requested", "status": "suppressed",
                "finding_id": "ROUTER8-ROUTE-ASSUMPTION-001", "kind": policy["kind"]})
            if len(requested) > 1:
                meta["unknowns"].append("Какой вариант маршрута выбрать: с возвращением, в одну сторону или с другим конечным городом?")
    meta["unknowns"] = list(dict.fromkeys(meta["unknowns"]))
    return result, meta
