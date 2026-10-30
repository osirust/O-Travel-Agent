"""Preserve literal financial requirements without claiming supplier proof.

The temporal key is deliberately unverified by inventory/planning. It records a
hard request and makes the existing unknown-hard checks refuse a ready quote.
No refund boolean, deadline, city timezone or booking right is inferred here.
Only the current user message authorizes creation or removal of this key.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from .operation_aliases import _without_quoted_spans
from .route_intake import _FORMS as _CITY_FORMS


TEMPORAL_KEY = "free_cancel_until_checkin"
FINDING_ID = "HARNESS-004-FINAL40-REFUND-DEADLINE-001"
CONFLICT_ID = "HARNESS-004-F40-CONTRADICTORY-CONSTRAINTS-021-001"
_TYPED = "\x1eOTRAVEL_TYPED_V1:"
_NONCURRENT = re.compile(r"\b(?:если|бы|допустим|предположим|возможно|может|наверное|гипотетически|"
    r"раньше|ранее|прежде|вчера|говорил\w*|просил\w*|писал\w*|цитата|реклам\w*|if|would|previously|suppose)\b")
_HISTORY = re.compile(r"\b(?:раньше|ранее|прежде|вчера|говорил\w*|просил\w*|писал\w*|цитата|реклам\w*|previously)\b")
_QUESTION = re.compile(r"^(?:а\s+)?(?:почему|как|где|куда|откуда|какой|какие|когда|сколько|объясни|"
    r"расскажи|напомни|подскажи|сможешь|можешь|можно|сравни\w*|what|why|can|compare)\b")
_READONLY = re.compile(r"\b(?:только\s+(?:вопрос|обсужд\w*|сравн\w*)|ничего\s+не\s+меня\w*|"
    r"не\s+применя\w*|не\s+меняй|do\s+not\s+(?:apply|change))\b")
_SOFT = re.compile(r"\b(?:желательн\w*|по\s+возможности|не\s+обязател\w*|предпоч\w*|prefer\w*|optional)\b")
_REFUND = re.compile(r"\b(?:возврат\w*|refundable|free\s+cancellation)\b|\bбесплатн\w*\s+отмен\w*")
_NONREFUND = re.compile(r"\b(?:невозврат\w*|non[ -]?refundable|nonrefundable)\b")
_TEMPORAL = re.compile(r"\b(?:до|вплоть\s+до|включая)\s+(?:(?:сам\w*|дня|даты|момента|начала|день|дату)\s+){0,3}"
    r"(?:заезд\w*|check[ -]?in)\b|\b(?:until|through)\s+(?:the\s+)?check[ -]?in\b")
_CANCEL_SUBJECT = re.compile(r"\b(?:отмен\w*|возврат\w*|refundable|refund|cancellation)\b")
_WAIVER = re.compile(r"\b(?:сними\w*|снимаю|снять|убери\w*|убрать|удали\w*|удалить|отказыва\w*|откаж\w*)\b|"
    r"\b(?:больше\s+)?не\s+(?:нуж\w*|надо|требу\w*|хочу|обязател\w*)\b|"
    r"\b(?:remove|drop|waive|no\s+longer\s+need)\b")
_DIRECT_ONLY = re.compile(r"\b(?:только|строго|исключительно|обязательно)\s+(?:\w+\s+){0,2}прям\w*\s+"
    r"(?:рейс\w*|перелет\w*|вылет\w*)\b|\b(?:nonstop|direct\s+flights?)\s+only\b")
_CONNECTING = re.compile(r"\bпересад\w*|\b(?:connection|connecting\s+flight|stopover)\b")
_PROPOSE = re.compile(r"\b(?:предлож\w*|рассмотр\w*|покаж\w*|предлаг\w*|можно|разреш\w*|suggest|show|consider|allow)\b")
_FREE_ANY = re.compile(r"\bбесплатн\w*\s+отмен\w*|\bfree\s+cancellation\b")


def _segments(text: str) -> list[tuple[str, bool]]:
    if not isinstance(text, str) or not 0 < len(text) <= 20_000:
        return []
    plain = _without_quoted_spans(text.casefold().replace("ё", "е"))
    if plain is None or "`" in plain:
        return []
    pieces = re.split(r"([!?;\n]|(?<!\d)\.|\.(?!\d))", plain)
    return [(" ".join(pieces[i].split()), pieces[i + 1] == "?" if i + 1 < len(pieces) else False)
        for i in range(0, len(pieces), 2) if pieces[i].strip() and not pieces[i].lstrip().startswith(">")]


def _asserted(segment: str, question: bool) -> bool:
    return not (question or _QUESTION.search(segment) or _NONCURRENT.search(segment) or _READONLY.search(segment))


def _temporal_required(segment: str) -> bool:
    # Split the correction tail of "до заезда, а не до недели". The negative
    # tail does not negate the positive requirement before it.
    return any(_REFUND.search(part) and _TEMPORAL.search(part)
        and not _SOFT.search(part) and not _WAIVER.search(part)
        for part in segment.split(","))


def _positive_free(segment: str) -> bool:
    return any(_FREE_ANY.search(part) and not _SOFT.search(part) and not _WAIVER.search(part)
        and not re.search(r"\bбез\s+(?:\w+\s+){0,2}(?:бесплатн\w*|отмен\w*)", part)
        for part in segment.split(","))


def _nonrefundable_wanted(segment: str) -> bool:
    return any(_NONREFUND.search(part)
        and not re.search(r"\b(?:не\s+(?:нуж\w*|хочу|надо)|без)\s+невозврат\w*", part)
        and not re.search(r"\bневозврат\w*\s+(?:(?:мне|нам|больше)\s+)?не\s+(?:нуж\w*|надо|подход\w*)", part)
        for part in segment.split(","))


def _scope(value: dict) -> tuple[Any, Any]:
    return value.get("stay_id"), value.get("room_id")


def _room_waiver_scopes(data: dict, saved: list[dict], statements: list[str]) -> set[tuple[Any, Any]]:
    """Resolve a numbered room only inside the single saved affected stay.

    A model's valid room ID alone is not proof that it is the room the user
    waived. Ambiguous multi-stay/ordinal requests are kept blocked for clarity.
    """
    scopes = {_scope(c) for c in saved if c.get("stay_id") is not None and c.get("room_id") is not None}
    stay_ids = {sid for sid, _ in scopes}
    if len(stay_ids) != 1:
        return set()
    sid = next(iter(stay_ids))
    stay = next((s for s in data.get("stays", []) if isinstance(s, dict) and s.get("id") == sid), {})
    rooms = stay.get("rooms", [])
    ordinals = []
    for text in statements:
        for stem, index in (("перв", 1), ("втор", 2), ("трет", 3), ("четверт", 4), ("пят", 5)):
            if re.search(r"\b" + stem + r"\w*\s+(?:номер\w*|комнат\w*)\b", text):
                ordinals.append(index)
        ordinals.extend(int(m[1]) for m in re.finditer(r"\b(?:номер\w*|комнат\w*)\s*[№#]?\s*([1-5])(?!\d)", text))
    if len(set(ordinals)) == 1 and 1 <= ordinals[0] <= len(rooms):
        return {(sid, rooms[ordinals[0] - 1].get("id"))} & scopes
    return scopes if not ordinals and len(scopes) == 1 else set()


def _current_waiver_scopes(data: dict, saved: list[dict], statements: list[str], mentions_room: bool) -> set[tuple[Any, Any]]:
    """Only current literal/unique saved scope authorizes removal, never an NN ID."""
    scopes = {_scope(c) for c in saved}
    all_stays = any(re.search(r"\b(?:везде|всюду|во?\s+всех\s+(?:отел\w*|гостиниц\w*|останов\w*)|"
        r"для\s+всей\s+поездки)\b", text) for text in statements)
    if all_stays and not mentions_room:
        return scopes
    named_ids = set()
    known_place_mentioned = False
    for text in statements:
        for alias, city in _CITY_FORMS.items():
            if re.search(r"(?<![\w-])" + re.escape(alias) + r"(?![\w-])", text):
                known_place_mentioned = True
                named_ids.update(s.get('id') for s in data.get('stays', []) if isinstance(s, dict) and s.get('city') == city)
        for stay in data.get('stays', []):
            city = stay.get('city') if isinstance(stay, dict) else None
            if isinstance(city, str) and re.search(r"(?<![\w-])" + re.escape(city.casefold().replace('ё', 'е')) + r"(?![\w-])", text):
                known_place_mentioned = True
                named_ids.add(stay.get('id'))
    if len(named_ids) > 1:
        return set()  # Includes repeated visits to the same named city.
    if named_ids:
        saved = [c for c in saved if c.get('stay_id') in named_ids]
        return _room_waiver_scopes(data, saved, statements) if mentions_room else {_scope(c) for c in saved}
    if known_place_mentioned or any(re.search(r"\b(?:в|во|in)\s+(?!(?:люб\w*|кажд\w*|вс\w*|отел\w*|"
            r"номер\w*|комнат\w*|день|дат\w*|момент\w*|услов\w*|тариф\w*)\b)[а-яa-z][\w-]*", text) for text in statements):
        return set()  # Named place not matched to a saved stay is not a waiver.
    if mentions_room:
        return _room_waiver_scopes(data, saved, statements)
    return scopes if len(scopes) == 1 else set()


def guard_financial_requirements(before_state: Any, operations: list, current_text: str) -> tuple[list[dict], dict]:
    """Bound current financial wishes and preserve saved temporal hard guards.

    Conflicts refuse the whole proposed batch. The caller persists the returned
    operations only through normal schema/domain/owner/version checks, records
    notes, and surfaces conflicts/unknowns as server questions. A temporal hard
    key is *not* evidence that any supplier tariff satisfies its deadline.
    """
    data = before_state.model_dump(mode="json") if hasattr(before_state, "model_dump") else deepcopy(before_state)
    copied = [op.model_dump(mode="json") if hasattr(op, "model_dump") else deepcopy(op) for op in operations]
    meta = {"notes": [], "conflicts": [], "unknowns": []}
    if not isinstance(data, dict) or not isinstance(current_text, str) or current_text.startswith(_TYPED):
        return copied, meta
    segments = _segments(current_text)
    asserted = [text for text, question in segments if _asserted(text, question)]
    required = any(_temporal_required(text) for text in asserted)
    waiver = any(_WAIVER.search(text) and _CANCEL_SUBJECT.search(text)
        and (_TEMPORAL.search(text) or re.search(r"\bуслови\w*\b", text)) for text in asserted)
    saved = [c for c in data.get("constraints", []) if isinstance(c, dict)
        and c.get("key") == TEMPORAL_KEY and c.get("value") is True and c.get("hard", True) is True]

    def note(code: str, **detail: Any) -> None:
        meta["notes"].append({"finding_id": FINDING_ID, "code": code, **detail})

    positive_free = any(_positive_free(text) for text in asserted)
    negative_rate = any(_nonrefundable_wanted(text) for text in asserted)
    # Literal positive and negative requirements in one current statement must
    # not be resolved by whichever polarity the model happened to emit.
    if positive_free and negative_rate:
        meta["conflicts"].append("Вы одновременно просите бесплатную отмену и невозвратный тариф. Что выбираем: тариф с подходящим сроком бесплатной отмены или невозвратный тариф?")
    hard_direct = any(_DIRECT_ONLY.search(text) and not _SOFT.search(text)
        and not re.search(r"\bне\s+(?:только|строго|исключительно)\b", text) for text in asserted)
    # A request to suggest a conditional alternative is not authorization to
    # silently weaken the separately asserted "only direct" requirement.
    alternatives = [text for text, question in segments if not question and not _HISTORY.search(text)
        and not _READONLY.search(text) and _CONNECTING.search(text) and _PROPOSE.search(text)
        and not re.search(r"\bбез\s+пересад\w*|\bне\s+(?:предлаг\w*|показыв\w*|рассматр\w*|нуж\w*)", text)]
    if hard_direct and alternatives:
        meta["conflicts"].append("Сохраняем обязательный прямой рейс или разрешаем варианты с пересадкой ради более низкой цены? Пока условие прямого рейса не ослаблено.")
    if saved and negative_rate and not waiver and not positive_free:
        meta["conflicts"].append("Ранее вы потребовали бесплатную отмену до заезда. Сохранить это условие или явно снять его и рассматривать невозвратный тариф?")
    if meta["conflicts"]:
        meta["notes"].append({"finding_id": CONFLICT_ID, "code": "current_financial_requirements_conflict"})
        return [], meta

    proposed_waiver_scopes = {_scope(op.get("value", {})) for op in copied if op.get("type") in {"set_constraint", "clear_constraint"}
        and isinstance(op.get("value"), dict) and op["value"].get("key") == TEMPORAL_KEY}
    scoped_waiver = {scope for scope in proposed_waiver_scopes if scope != (None, None)}
    mentions_room = any(re.search(r"\b(?:номер\w*|комнат\w*)\b", text) for text in asserted)
    authorized_waiver_scopes = set()
    if waiver and saved:
        waiver_statements = [text for text in asserted if _WAIVER.search(text) and _CANCEL_SUBJECT.search(text)]
        authorized_waiver_scopes = _current_waiver_scopes(data, saved, waiver_statements, mentions_room)
        if not authorized_waiver_scopes or not proposed_waiver_scopes.issubset(authorized_waiver_scopes):
            waiver = False
            meta["unknowns"].append("Для какого сохранённого отеля или номера нужно снять условие отмены до заезда? Остальные условия сохранены.")
            note("temporal_waiver_current_scope_not_proven")
    result = []
    for op in copied:
        kind, value = op.get("type"), op.get("value")
        if not isinstance(value, dict) or value.get("key") != TEMPORAL_KEY:
            result.append(op)
            continue
        removes = kind == "clear_constraint" or kind == "set_constraint" and (
            value.get("value") is not True or value.get("hard", True) is not True)
        if removes and not waiver:
            note("unrequested_temporal_requirement_weakening_suppressed", stay_id=value.get("stay_id"), room_id=value.get("room_id"))
            continue
        if removes and waiver:
            if saved and _scope(value) not in authorized_waiver_scopes:
                note("temporal_requirement_waiver_scope_preserved")
                continue
            # A false hard unknown key would still block assembly. Explicit
            # waiver removes the requirement; it is never a fake verifiedfalse.
            result.append({"type": "clear_constraint", "value": {name: val for name, val in value.items()
                if name in {"key", "stay_id", "room_id"}}})
            continue
        if kind == "set_constraint" and not removes and not required:
            if not any(_scope(c) == _scope(value) for c in saved):
                note("unrequested_temporal_requirement_creation_suppressed")
                continue
        result.append(op)

    if waiver and not required:
        for old in saved:
            if _scope(old) not in authorized_waiver_scopes:
                continue
            value = {name: old[name] for name in ("key", "stay_id", "room_id") if old.get(name) is not None}
            if not any(op.get("type") == "clear_constraint" and isinstance(op.get("value"), dict)
                    and op["value"].get("key") == TEMPORAL_KEY and _scope(op["value"]) == _scope(old) for op in result):
                result.append({"type": "clear_constraint", "value": value})
        if saved:
            note("explicit_temporal_cancellation_requirement_waived")
    if required:
        # An existing bool-refund target can identify a known canonical room;
        # it never authorizes an invented ID or a broader source guarantee.
        stays = {s.get("id"): s for s in data.get("stays", []) if isinstance(s, dict)}
        scopes = []
        for op in copied:
            value = op.get("value", {})
            if op.get("type") != "set_constraint" or not isinstance(value, dict) or value.get("key") not in {"refundable", TEMPORAL_KEY}:
                continue
            sid, rid = _scope(value)
            if sid is None and rid is None:
                scopes.append((None, None))
            elif sid in stays and (rid is None or any(r.get("id") == rid for r in stays[sid].get("rooms", []))):
                scopes.append((sid, rid))
        if not scopes:
            scopes = [(next(iter(stays)), None)] if len(stays) == 1 else [(None, None)]
        scopes = list(dict.fromkeys(scopes))
        for sid, rid in scopes:
            value = {"key": TEMPORAL_KEY, "value": True, "hard": True}
            if sid is not None:
                value["stay_id"] = sid
            if rid is not None:
                value["room_id"] = rid
            # Do not keep a model's soft/false or malformed duplicate next to
            # the server-grounded hard value for the same scope.
            result = [op for op in result if not (op.get("type") in {"set_constraint", "clear_constraint"}
                and isinstance(op.get("value"), dict) and op["value"].get("key") == TEMPORAL_KEY and _scope(op["value"]) == (sid, rid))]
            result.append({"type": "set_constraint", "value": value})
        note("literal_temporal_cancellation_requirement_saved", scopes=[{"stay_id": sid, "room_id": rid} for sid, rid in scopes])
        meta["unknowns"].append("Условие бесплатной отмены до заезда сохранено. Точный подходящий срок выбранного тарифа пока не подтверждён; готовую корзину по этому условию оформить нельзя.")
    return result, meta
