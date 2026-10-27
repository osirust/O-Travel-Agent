"""Ground a bounded explicit room count without guessing guests or a stay.

Existing allocations are not destructively rewritten. Multiple unaddressed
stays require clarification; this is not a deferred multi-stay plan executor.
"""
from __future__ import annotations

import re
from collections import Counter
from copy import deepcopy

from .domain import MVP_LIMITS, Operation, TripState
from .geography import _CITY_ALIASES

_COUNTS = {"один": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5}
_COUNT = re.compile(r"\b(?P<count>\d{1,2}|" + "|".join(_COUNTS)
    + r")\s+(?:(?:отдельн\w*|раздельн\w*|гостиничн\w*)\s+)?(?:номер(?:а|ов)?|комнат(?:а|ы)?)(?![\w-])"
      r"(?!\s+(?:телефон\w*|заказ\w*|паспорт\w*|рейс\w*))")
_NONCURRENT = re.compile(r"\b(?:если|if|бы|допустим|предположим|возможно|может|раньше|прежде|ранее|вчера|прошл\w*|цитата|реклама)\b")
_NONSET = re.compile(r"\b(?:добав\w*|убер\w*|удал\w*|сократ\w*|увелич\w*|уменьш\w*|проверь|напомни|расскажи|объясни|сравни)\b|\bне\s+(?:нуж\w*|требу\w*|хоч\w*|брониру\w*|дела\w*|став\w*)\b")
_ALLOCATION_EDIT = re.compile(r"\b(?:пересел\w*|перемест\w*|распредели\w*|назнач\w*)\b|\b(?:в|во)\s+(?:перв\w*|втор\w*|трет\w*|номер\s+\d+)\s+номер\w*\b")
_ADD_PERSON = re.compile(r"\b(?:добавь(?:те)?|добавить|включи(?:те)?|включить)\b[^.!?;]{0,120}"
                         r"\b(?:ребен\w*|дет\w*|взросл\w*|участник\w*|путешественник\w*)\b")
_NO_ADD = re.compile(r"\bне\s+(?:добав\w*|включ\w*|примен\w*)\b")


def _key(text: str) -> str:
    return " ".join(text.casefold().replace("ё", "е").split())


def _asserted_counts(text: str) -> tuple[set[int], str]:
    unquoted = re.sub(r"[«“\"]([^»”\"]*)[»”\"]", " ", _key(text))
    asserted, counts = [], set()
    for match in re.finditer(r"[^.!?;\n]+[.!?;\n]?", unquoted):
        clause = match.group().strip()
        if clause.endswith("?") or _NONCURRENT.search(clause) or _NONSET.search(clause):
            continue
        found = list(_COUNT.finditer(clause))
        if found:
            asserted.append(clause)
            counts.update(int(m["count"]) if m["count"].isdigit() else _COUNTS[m["count"]] for m in found)
    return counts, " ".join(asserted)


def _declared_new_people(data: dict, operations: list[dict], text: str) -> set[str]:
    """Only raw same-batch new IDs with an asserted addition, no ID selection."""
    from .operation_aliases import _without_quoted_spans
    unquoted = _without_quoted_spans(_key(text))
    if unquoted is None or _NO_ADD.search(unquoted):
        return set()
    asserted = [clause for clause in re.findall(r'[^.!?;]+[.!?;]?', unquoted)
                if not clause.rstrip().endswith('?') and not _NONCURRENT.search(clause)]
    if not any(_ADD_PERSON.search(clause) for clause in asserted):
        return set()
    existing = {person['id'] for person in data.get('travelers', [])}
    added = Counter(op['value']['id'] for op in operations if op.get('type') == 'add_traveler'
        and isinstance(op.get('value'), dict) and isinstance(op['value'].get('id'), str))
    return {identifier for identifier, count in added.items() if count == 1 and identifier not in existing}


def _protect_count_only_memberships(prior: dict, copied: list[dict], count: int,
                                    added_people: set[str]) -> list[str]:
    """Keep count-only requests from changing existing guest assignments.

    Both full layout setters and the scoped patch primitive obey this boundary.
    An explicit same-batch addition may retain raw addition-only memberships at
    an unchanged room count. Moving/removing old people still needs allocation
    intent; this code never chooses a room or assigns an unmentioned person.
    """
    protected = []

    def addition_only(old: dict, members) -> bool:
        if not isinstance(members, list) or any(not isinstance(p, str) for p in members):
            return False
        proposed, saved = set(members), set(old['traveler_ids'])
        extra = proposed - saved
        return (len(proposed) == len(members) and saved.issubset(proposed) and extra.issubset(added_people)
                and (bool(extra) or members == old['traveler_ids']))

    for op in copied:
        value = op.get('value')
        if not isinstance(value, dict):
            continue
        target = value.get('stay_id') if op.get('type') == 'set_room_members' else op.get('target_id') or value.get('id')
        stay = prior.get(target) if isinstance(target, str) else None
        if stay is None or not any(room.get('traveler_ids') for room in stay.get('rooms', [])):
            continue
        old_rooms = {room['id']: room for room in stay['rooms']}
        unchanged_count = len(old_rooms) == count
        if op.get('type') == 'set_room_members' and op.get('target_id') in old_rooms:
            old = old_rooms[op['target_id']]
            members = value.get('traveler_ids')
            if unchanged_count and addition_only(old, members):
                continue
            if members != old['traveler_ids']:
                value['traveler_ids'] = deepcopy(old['traveler_ids'])
                protected.append(target)
        elif op.get('type') == 'upsert_stay' and 'rooms' in value:
            proposed = value['rooms']
            keep_additions = (unchanged_count and isinstance(proposed, list)
                and all(isinstance(room, dict) and isinstance(room.get('id'), str) for room in proposed)
                and [room['id'] for room in proposed] == [room['id'] for room in stay['rooms']]
                and all(addition_only(old_rooms[room['id']], room.get('traveler_ids')) for room in proposed))
            if not keep_additions and proposed != stay['rooms']:
                value['rooms'] = deepcopy(stay['rooms'])
                protected.append(target)
    return list(dict.fromkeys(protected))


def ground_room_count_operations(state: TripState | dict, operations: list, text: str,
                                  pending_context: dict | None = None) -> tuple[list[dict], dict]:
    """Return copied operations and owned notes/questions/context, no commits.

    The caller runs this after geographic targets, before group clarification.
    A later group-membership question must remain first when families are empty.
    ``room_target_selection_required`` prevents the caller's first-stay autobind.
    """
    data = state.model_dump(mode="json") if isinstance(state, TripState) else deepcopy(state)
    copied = [op.model_dump(mode="json") if isinstance(op, Operation) else deepcopy(op) for op in operations]
    meta = {"notes": [], "unknowns": [], "pending_context": None}
    if not isinstance(data, dict) or not isinstance(text, str) or not 0 < len(text) <= 20_000:
        return copied, meta
    counts, asserted = _asserted_counts(text)
    if not counts or _ALLOCATION_EDIT.search(_key(text)):
        return copied, meta
    prior = {s["id"]: s for s in data.get("stays", []) if isinstance(s, dict) and isinstance(s.get("id"), str)}
    projected = deepcopy(prior)
    for op in copied:
        if not isinstance(op, dict):
            return copied, meta
        if not isinstance(op.get("value"), dict):
            return copied, meta
        value = op["value"]
        identifier = op.get("target_id") or value.get("id")
        if not isinstance(identifier, str):
            continue
        if op.get("type") == "remove_stay":
            projected.pop(identifier, None)
        elif op.get("type") == "upsert_stay":
            if op.get("target_id") and value.get("id") and op["target_id"] != value["id"]:
                return copied, meta  # Let the normal domain conflict path decide.
            current = projected.setdefault(identifier, {"id": identifier, "rooms": []})
            for key in ("city", "rooms"):
                if key in value:
                    current[key] = deepcopy(value[key])
    pending = {}
    if isinstance(pending_context, dict):
        context = pending_context.get("context")
        pending = {**(context if isinstance(context, dict) else {}), **{k: v for k, v in pending_context.items() if k != "context"}}
    fresh_pending = ("state_version" not in pending or type(pending["state_version"]) is int
                     and pending["state_version"] == data.get("state_version"))
    target = pending.get("target_id") or pending.get("stay_id")
    target = target if (fresh_pending and pending.get("field") == "stay.rooms"
        and isinstance(target, str) and target in projected) else None
    mentioned = {identifier for identifier in projected
                 if re.search(r"(?<![\w-])" + re.escape(_key(identifier)) + r"(?![\w-])", asserted)}
    if len(mentioned) == 1:
        target = next(iter(mentioned))
    elif len(mentioned) > 1:
        target = None
    elif target is None:
        cities = {name for alias, name in _CITY_ALIASES.items()
                  if re.search(r"(?<![\w-])" + re.escape(alias) + r"(?![\w-])", asserted)}
        matches = [identifier for identifier, stay in projected.items() if stay.get("city") in cities]
        if len(matches) == 1:
            target = matches[0]
        elif not matches and len(projected) == 1:
            target = next(iter(projected))

    def question(message, *, count=None, selection=False):
        meta["unknowns"] = [message]
        meta["pending_context"] = {"field": "stay.rooms", "target_id": target,
            "requested_room_count": count, "room_target_selection_required": selection, "force_clarification": True}

    if len(counts) != 1:
        question("Вы указали разные количества номеров. Сколько номеров нужно для этой остановки?")
        return copied, meta
    count = next(iter(counts))
    if not 1 <= count <= MVP_LIMITS["rooms_per_stay"]:
        question("Количество номеров выходит за текущий технический предел прототипа. Уточните допустимое размещение.", count=count)
        return copied, meta
    added_people = _declared_new_people(data, copied, text)
    protected = _protect_count_only_memberships(prior, copied, count, added_people)
    if protected:
        meta['notes'].append({'code': 'count_only_room_membership_preserved', 'stay_ids': protected,
                              'requested_room_count': count, 'occupants_inferred': False})
        # A restored full setter must not leave its destructive count in the
        # projection and accidentally satisfy an unaddressed multi-stay count.
        for op in copied:
            if op.get('type') == 'upsert_stay' and 'rooms' in op.get('value', {}):
                identifier = op.get('target_id') or op['value'].get('id')
                if identifier in projected:
                    projected[identifier]['rooms'] = deepcopy(op['value']['rooms'])
    # Correct already-extracted per-stay room counts require no reinterpretation
    # of global 'two rooms', especially when several cities were mentioned.
    if target is None and projected and all(isinstance(s.get("rooms"), list) and len(s["rooms"]) == count for s in projected.values()):
        return copied, meta
    if target is None:
        question("К какой именно остановке относятся эти номера? Укажите город или конкретное проживание.", count=count, selection=True)
        return copied, meta
    old_rooms = prior.get(target, {}).get("rooms") or []
    if any(isinstance(room, dict) and room.get("traveler_ids") for room in old_rooms):
        # Memberships have been preserved above for both setter primitives;
        # unrelated city/date/budget/party operations remain in the batch.
        if len(old_rooms) != count:
            question("В существующих номерах уже распределены участники. Как перераспределить их? "
                     f"Укажите точный состав каждого из {count} запрошенных номеров.", count=count)
            meta["pending_context"]["room_reallocation_required"] = True
        meta["notes"].append({"code": "existing_room_allocation_preserved", "requested_room_count": count})
        return copied, meta
    current_rooms = projected[target].get("rooms")
    if not isinstance(current_rooms, list):
        return copied, meta  # Invalid model shape belongs to the domain validator.
    if len(current_rooms) == count:
        return copied, meta
    proposed_filled = any(isinstance(room, dict) and room.get("traveler_ids") for room in current_rooms)
    if proposed_filled and len(current_rooms) > count:
        question("В предложенных номерах уже распределены участники. "
                 f"Укажите точный состав каждого из {count} запрошенных номеров.", count=count)
        meta["pending_context"]["room_reallocation_required"] = True
        meta["notes"].append({"code": "proposed_room_allocation_preserved", "requested_room_count": count})
        return copied, meta
    # Preserve proposed filled slots as well as prior empty canonical slots.
    # Only additional slots are empty; no valid proposed membership is erased.
    empty = (deepcopy(current_rooms) if proposed_filled else
        [deepcopy(room) for room in old_rooms if isinstance(room, dict) and not room.get("traveler_ids")][:count])
    occupied = {item.get("id") for collection in ("travelers", "groups", "legs", "stays")
                for item in data.get(collection, []) if isinstance(item, dict)}
    occupied.update(room.get("id") for stay in projected.values() if isinstance(stay.get("rooms"), list)
                    for room in stay["rooms"] if isinstance(room, dict))
    number = 1
    while len(empty) < count:
        identifier = f"new-room-{number}"
        number += 1
        if identifier not in occupied:
            empty.append({"id": identifier, "traveler_ids": []})
            occupied.add(identifier)
    matching = [op for op in copied if op.get("type") == "upsert_stay"
                and (op.get("target_id") or op.get("value", {}).get("id")) == target]
    if matching:
        matching[-1]["value"]["rooms"] = empty
    elif len(copied) >= 30:
        question("Количество номеров нужно сохранить отдельным следующим изменением: текущая правка уже содержит много операций.", count=count)
        return copied, meta
    else:
        copied.append({"type": "upsert_stay", "target_id": target, "value": {"rooms": empty}})
    meta["notes"].append({"code": "explicit_room_count_grounded", "requested_room_count": count,
                          "occupants_inferred": False})
    return copied, meta


def resolve_room_count_answer(state: dict, pending: dict | None, text: str) -> dict:
    """Resolve only a short count addressed to a fresh owned room question."""
    no = {"handled": False, "operations": []}
    if not isinstance(pending, dict) or pending.get("field") != "stay.rooms":
        return no
    if type(pending.get("state_version")) is not int or pending["state_version"] != state.get("state_version"):
        return no
    target = pending.get("target_id")
    if not isinstance(target, str) or sum(stay.get("id") == target for stay in state.get("stays", [])) != 1:
        return no
    key = _key(text).strip(" .!")
    match = re.fullmatch(r"(?:(?:нужен|нужна|нужно|мне|нам)\s+)?(?P<count>\d{1,2}|один|одна|два|две|три|четыре|пять)"
        r"(?:\s+(?:номер(?:а|ов)?|комнат(?:а|ы)?))?(?:,?\s+(?P<self>для меня|живу один|живу одна)|,?\s+для нас)?", key)
    if not match:
        return no
    if match["self"] and len(state.get("travelers", [])) != 1:
        return no
    count = int(match["count"]) if match["count"].isdigit() else _COUNTS[match["count"]]
    ops, meta = ground_room_count_operations(state, [], f"Нужны {count} номера.", pending)
    context = {**(pending.get("context") or {}), **(meta["pending_context"] or {}), "field": "stay.rooms", "target_id": target}
    return {"handled": True, "operations": ops, "question": next(iter(meta["unknowns"]), None), "pending_context": context}
