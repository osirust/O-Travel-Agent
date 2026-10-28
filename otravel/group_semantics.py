"""Small omission guard for explicit travel-party removals, never an ID picker.

This guard does not infer intent from a room deletion. It only prevents a
declared non-travelling adult from being silently kept in the trip roster when
the extracted batch contains no remove_traveler operation.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from .clarifications import human_question
from .domain import DomainError, Operation, TripState, apply_operations

_ADULT = r"(?:(?:(?:один|первый|второй|третий)\s+)?взрослый|(?:один|одна)\s+из\s+взрослых|одна\s+взрослая|взрослая|мама|папа|супруг|супруга|муж|жена)"
_CANCEL = re.compile(r"\b" + _ADULT + r"(?:\s+из\s+[^.!?;]{1,55}?)?\s+(?:(?:все-таки|больше|теперь)\s+)?не\s+(?:поедет|едет|полетит|летит)\b")
_REMOVE = re.compile(r"\b(?:удали|убери|исключи)\s+(?:(?:этого|одного|одну)\s+)?(?:взрослого|взрослую|участника)\s+из\s+(?:самой\s+)?(?:поездки|состава\s+(?:поездки|участников))\b")
_HYPOTHETICAL = re.compile(r"\b(?:если|допустим|предположим|возможно|может|наверное|гипотетически|например|напомни|расскажи|объясни|скажи|раньше|ранее|вчера|сказал|сказала|писал|писала|говорил|говорила)\b|не\s+(?:удаляй|убирай|исключай)|никого\s+не\s+(?:удаляй|убирай|исключай)")
_FAMILIES = re.compile(r"\bсем(?:ья|ьи|ей|ьям|ьями)\b")
_SEPARATE = re.compile(r"\bне\s+смеш(?:ивай|ивать|ивайте|иваем)\b|\bне\s+(?:селить|сели|размещать|размещай)\b[^.!?;]{0,45}\b(?:вместе|в\s+один\s+номер)\b|\bсем(?:ьи|ей|ьям)\b[^.!?;]{0,35}\b(?:отдельно|раздельно)\b")
_MIX_ALLOW = re.compile(r"\b(?:можно|можешь|разрешаю|разрешено)\s+(?:их\s+|семьи\s+)?(?:смешивать|смешать|селить\s+вместе|размещать\s+вместе)\b|\bне\s+(?:обязательно|нужно|надо)\s+(?:семьи\s+)?(?:раздельно|отдельно)\b|\b(?:отдельно|раздельно)\s+не\s+(?:нужно|надо)\b")
_FAMILY_COUNTS = {"одна": 1, "одной": 1, "две": 2, "двух": 2, "два": 2, "три": 3, "трех": 3,
                  "четыре": 4, "четырех": 4, "пять": 5, "пяти": 5}
_COUNT_FAMILIES = re.compile(r"\b(\d{1,2}|" + "|".join(_FAMILY_COUNTS) + r")\s+(?:(?:разные|разных|отдельные|отдельных)\s+)?сем(?:ья|ьи|ей)\b")
_GROUP_UNASSERTED = re.compile(r"\b(?:если|допустим|предположим|возможно|может|наверное|гипотетически|например|напомни|расскажи|объясни|скажи|раньше|ранее|вчера|сказал|сказала|писал|писала|говорил|говорила)\b")
_COUNT_ALTERNATIVE = re.compile(r"\b(?:\d+|" + "|".join(_FAMILY_COUNTS) + r")\s+(?:сем(?:ьи|ей)\s+)?(?:или|либо)\s+(?:\d+|" + "|".join(_FAMILY_COUNTS) + r")\b")
_ORDINAL_FAMILY_PLACEMENT = r"(?:[—–-]\s*|(?:живет|живут|размещается|размещаются)\s+)"
_FIRST_FAMILY = re.compile(r"\bпервая\s+семья\s+" + _ORDINAL_FAMILY_PLACEMENT)
_SECOND_FAMILY = re.compile(r"\bвторая\s+(?:семья\s+)?" + _ORDINAL_FAMILY_PLACEMENT)
_ORDINAL_FAMILY_UNASSERTED = re.compile(r"[?\"'«»“”`]|\b(?:или|либо|третья|подожди|пока)\b|\bне\s+первая\s+семья\b")


def _declares_current_two_families(text: str) -> bool:
    """Recognize a narrow asserted ordinal pair, never its members or rooms.

    Cardinal counts retain their existing intake path. Questions, quotation,
    historical/hypothetical context, alternatives, or permission to mix do not
    acquire a new count merely by mentioning the first and second families.
    """
    value = " ".join(text.casefold().replace("ё", "е").split())
    if (_ORDINAL_FAMILY_UNASSERTED.search(value) or _GROUP_UNASSERTED.search(value)
            or _MIX_ALLOW.search(value) or not _FAMILIES.search(value) or not _SEPARATE.search(value)):
        return False
    first = _FIRST_FAMILY.search(value)
    return bool(first and _SECOND_FAMILY.search(value, first.end()))


def _asserted_text(text: str) -> str:
    """Keep complete non-question clauses; a later FAQ is not a revocation.

    This is deliberately sentence-bounded. A lone sentence ending in ? is not
    interpreted as a declaration, even if it contains imperative-looking words.
    """
    clauses = re.findall(r"[^.!?;]+[.!?;]?", text)
    return " ".join(clause.strip() for clause in clauses if not clause.rstrip().endswith("?"))


def protect_group_separation(state: TripState | dict, operations: list[Operation | dict], text: str) -> dict[str, Any]:
    """Prevent an unrelated membership setter from dropping saved separation.

    Restores only a previously true flag on that exact existing group, never
    creates a group or changes its proposed membership. An asserted permission
    to mix may clear it; a question/hypothesis is not that permission.
    """
    state = TripState.model_validate(state)
    ops = [deepcopy(op) if isinstance(op, Operation) else Operation.model_validate(deepcopy(op)) for op in operations]
    result: dict[str, Any] = {"handled": False, "operations": ops}
    asserted = _asserted_text(" ".join(text.casefold().replace("ё", "е").split())) if isinstance(text, str) else ""
    remaining = _MIX_ALLOW.sub("", asserted)
    contradicts_permission = _FAMILIES.search(remaining) and _SEPARATE.search(remaining)
    if _MIX_ALLOW.search(asserted) and not _GROUP_UNASSERTED.search(asserted) and not contradicts_permission:
        return result
    saved = {group.id for group in state.groups if group.keep_separate}
    writes = {}
    for operation in ops:
        if operation.type == "upsert_group":
            if operation.target_id in writes and writes[operation.target_id] != operation.value:
                return result  # Preserve raw conflicts for the caller's gate.
            writes[operation.target_id] = operation.value
    protected = []
    for operation in ops:
        if operation.type == "upsert_group" and operation.target_id in saved and operation.value.get("keep_separate") is False:
            operation.value["keep_separate"] = True
            protected.append(operation.target_id)
    if protected:
        result.update(handled=True, block_batch=False, notes=[{"code": "saved_family_separation_preserved",
            "field": "groups.keep_separate", "source": "canonical_saved_policy", "group_ids": list(dict.fromkeys(protected))}])
    return result


def guard_unapplied_traveler_removal(state: TripState | dict, operations: list[Operation | dict], text: str) -> dict[str, Any]:
    """Return a precise ASK for omitted explicit removal, otherwise unhandled.

    The caller must block this batch before applying it; this helper never
    selects a person, changes ops/state or discards other pending user intents.
    A question/hypothesis/history or a room-only move is outside this contract.
    """
    data = TripState.model_validate(state)
    result: dict[str, Any] = {"handled": False}
    if not isinstance(text, str) or not text.strip():
        return result
    normalized = _asserted_text(" ".join(text.casefold().replace("ё", "е").split()))
    if _HYPOTHETICAL.search(normalized):
        return result
    types = [op.type if isinstance(op, Operation) else op.get("type") if isinstance(op, dict) else None for op in operations]
    if "remove_traveler" in types or not any(p.type == "adult" for p in data.travelers):
        return result
    match = _CANCEL.search(normalized) or _REMOVE.search(normalized)
    if match is None:
        return result
    # "Не едет утром/поездом/в этот отель" changes a service, not the roster.
    tail = normalized[match.end():].lstrip()
    if re.match(r"(?:утром|вечером|ночью|днем|поездом|самолетом|в\s+(?:этот|тот|другой)\s+отель|на\s+этом)\b", tail):
        return result
    if re.match(r"в\s+", tail) and not re.match(r"в\s+(?:поездку|путешествие)\b", tail):
        return result
    return {"handled": True, "field": "party.removal", "target_id": None,
        "question": "Какого взрослого исключить из всей поездки? Уточните участника: изменение состава номера само по себе не удаляет человека из поездки и транспорта.",
        "context": {"field": "party.removal", "force_clarification": True},
        "notes": [{"code": "explicit_traveler_removal_missing", "field": "travelers", "source": "explicit_user_removal", "action": "ASK_NOT_APPLIED"}]}


def guard_group_separation(state: TripState | dict, operations: list[Operation | dict], text: str,
                           pending_context: dict | None = None) -> dict[str, Any]:
    """Ground an explicit no-mix family policy without inventing membership.

    Always returns copied typed operations. For a known number of groups,
    preserves intake edits and inserts unresolved empty groups plus a targeted
    question where membership is absent. For an unknown count with no groups,
    preserves a trusted pending policy and asks its count, never searches this
    batch. The caller must persist pending context while applying safe intake.
    No actual extraction, history replay, room filling or API work occurs here.
    """
    original = TripState.model_validate(state)
    result = protect_group_separation(original, operations, text)
    ops = result["operations"]
    if not isinstance(text, str):
        return result
    normalized = _asserted_text(" ".join(text.casefold().replace("ё", "е").split()))
    ctx = pending_context if isinstance(pending_context, dict) else {}
    pending_policy = ctx.get("family_separation_required") is True and ctx.get("field") == "group.membership"
    explicit_policy = _FAMILIES.search(normalized) and _SEPARATE.search(normalized)
    if (not (explicit_policy or pending_policy) or _GROUP_UNASSERTED.search(normalized)
            or _MIX_ALLOW.search(normalized) or _COUNT_ALTERNATIVE.search(normalized)):
        return result
    counts = {int(m[1]) if m[1].isascii() and m[1].isdigit() else _FAMILY_COUNTS[m[1]]
              for m in _COUNT_FAMILIES.finditer(normalized)}
    short = normalized.strip(" .!,:;")
    if pending_policy and ctx.get("membership_unknown") is True and not counts:
        if short in _FAMILY_COUNTS:
            counts = {_FAMILY_COUNTS[short]}
        elif re.fullmatch(r"[0-9]{1,2}", short):
            counts = {int(short)}
    # Conflicting counts, unsupported cardinalities, or invalid other edits
    # are not permission to impose a partition. The normal extractor handles it.
    if len(counts) > 1 or counts and not 2 <= next(iter(counts)) <= 10:
        return result
    writes, owners = {}, {}
    for operation in ops:
        if operation.type != "upsert_group":
            continue
        if operation.target_id in writes and writes[operation.target_id] != operation.value:
            return result
        writes[operation.target_id] = operation.value
        for identifier in operation.value.get("traveler_ids", []):
            if identifier in owners and owners[identifier] != operation.target_id:
                return result
            owners[identifier] = operation.target_id
    try:
        candidate, _ = apply_operations(original, ops)
    except DomainError:
        return result
    groups = [g.model_copy(deep=True) for g in candidate.groups]
    declared = next(iter(counts)) if counts else None
    if declared is None and _declares_current_two_families(text) and len(groups) != 2:
        return {**result, "handled": True, "block_batch": True, "field": "group.membership", "target_id": None,
            "question": "Не удалось сохранить две семьи раздельно. Кто входит в первую семью, а кто — во вторую? Изменения пока не применены.",
            "context": {"field": "group.membership", "force_clarification": True, "family_separation_required": True},
            "notes": [{"code": "explicit_ordinal_family_count_mismatch", "source": "explicit_user_no_mix",
                       "declared_count": 2, "candidate_count": len(groups), "membership_inferred": False,
                       "action": "ASK_NOT_APPLIED"}]}
    if declared is not None and len(groups) > declared:
        return {**result, "handled": True, "block_batch": True, "field": "group.membership", "target_id": None,
            "question": "Вы указали другое число семей. Уточните, какие сохранённые группы нужно оставить и кто в них входит.",
            "context": {"field": "group.membership", "force_clarification": True, "family_separation_required": True},
            "notes": [{"code": "family_count_conflict", "source": "explicit_user_no_mix", "action": "ASK_NOT_APPLIED"}]}
    if not groups and declared is None:
        return {**result, "handled": True, "block_batch": False, "field": "group.membership", "target_id": None,
            "question": "Сколько отдельных семей едет и кто входит в каждую? Состав номеров без этого не распределяю.",
            "context": {"field": "group.membership", "force_clarification": True, "family_separation_required": True, "membership_unknown": True},
            "notes": [{"code": "family_count_unknown", "source": "explicit_user_no_mix", "action": "ASK_NOT_APPLIED"}]}
    from .domain import Group
    used = {g.id for g in groups}
    while declared is not None and len(groups) < declared:
        index = 1
        while f"new-group-{index}" in used:
            index += 1
        identifier = f"new-group-{index}"
        used.add(identifier)
        groups.append(Group(id=identifier))
    by_id = {g.id: g for g in groups}
    # Replace an extracted false setter instead of adding a contradictory pair.
    grounded = []
    written = set()
    for operation in ops:
        if operation.type == "upsert_group" and operation.target_id in by_id:
            group = by_id[operation.target_id]
            grounded.append(Operation(type="upsert_group", target_id=group.id,
                value={"traveler_ids": list(group.traveler_ids), "keep_separate": True}))
            written.add(group.id)
        else:
            grounded.append(operation)
    for group in groups:
        if group.id not in written:
            grounded.append(Operation(type="upsert_group", target_id=group.id,
                value={"traveler_ids": list(group.traveler_ids), "keep_separate": True}))
    result = {"handled": True, "operations": grounded, "block_batch": False,
        "notes": [{"code": "family_separation_grounded", "field": "groups.keep_separate", "source": "explicit_user_no_mix",
                   "group_ids": [g.id for g in groups], "membership_inferred": False}]}
    if len(grounded) > 30:
        return {"handled": True, "operations": ops, "block_batch": True, "field": "group.membership", "target_id": None,
            "question": "Сначала уточним состав отдельных семей. Остальные изменения нужно применить отдельным сообщением.",
            "context": {"field": "group.membership", "force_clarification": True, "family_separation_required": True},
            "notes": [{"code": "group_grounding_operation_cap", "source": "explicit_user_no_mix", "action": "ASK_NOT_APPLIED"}]}
    missing = next((g for g in groups if not g.traveler_ids), None)
    if missing is not None:
        display = candidate.model_copy(deep=True)
        display.groups = groups
        result.update(field="group.membership", target_id=missing.id,
            question=human_question({"field": "group.membership", "target_id": missing.id}, display),
            context={"field": "group.membership", "target_id": missing.id, "force_clarification": True, "family_separation_required": True})
    return result
