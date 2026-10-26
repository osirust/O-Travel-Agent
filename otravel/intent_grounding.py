"""Bound current-message geography to typed roles, without tools or mutation.

Only curated known places and an owned city slot authorize a city change. Other
operations are copied untouched. Questions in metadata are server-generated;
the caller merges their context only through its normal domain validation/CAS.
This is not general NLU, fuzzy geocoding, or proof of catalogue availability.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from .domain import DomainError, canonical_city
from .geography import _COUNTRY_ALIASES, parse_destination
from .route_intake import _FORMS as _BASE_FORMS


def _key(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").replace("–", "-").replace("—", "-").split())


_FORMS = dict(_BASE_FORMS)
for _city, _aliases in {
    "Москва": ("moskva", "moskvy"), "Казань": ("kazani",),
    "Стамбул": ("stambul", "stambula"), "Анталья": ("antaliyu", "antalyu"),
    "Санкт-Петербург": ("peterburga", "petersburg", "sankt-peterburga"),
}.items():
    for _alias in _aliases:
        _FORMS[_key(_alias)] = canonical_city(_city)
_NAMES = "|".join(re.escape(name) for name in sorted(_FORMS, key=len, reverse=True))
_COUNTRIES = dict(_COUNTRY_ALIASES)
_COUNTRY_NAMES = "|".join(re.escape(name) for name in sorted(_COUNTRIES, key=len, reverse=True))
_CITY = re.compile(rf"(?<![\w-])(?P<city>{_NAMES})(?![\w-])")
_FROM = re.compile(rf"\b(?:из|from|iz)\s+(?:города\s+)?(?P<city>{_NAMES})(?![\w-])")
_START_CUE = r"(?:начинаем(?: поездку| маршрут)?|(?:поездка|маршрут) начинается|стартуем|starting|(?:trip )?starts?)"
_START = re.compile(rf"\b{_START_CUE}\s+(?:в|с|из|in|from)\s+(?P<city>{_NAMES})(?![\w-])")
_START_BEFORE = re.compile(rf"\b{_START_CUE}\s*$")
_END_BEFORE = re.compile(r"\b(?:заканчива\w*|заверша\w*|финиширу\w*|конец поездки|ends?|ending)\s*$")
_TO = re.compile(rf"\b(?:в|во|to|v|vo)\s+(?:город\s+)?(?P<city>{_NAMES})(?![\w-])")
_ONTO = re.compile(rf"\bна\s+(?P<city>{_NAMES})(?![\w-])")
_CHOICE = re.compile(rf"\b(?:выбираю|выбираем|выбрали|назначение|направление|город назначения)\s*[:=-]?\s*(?:именно\s+)?(?P<city>{_NAMES})(?![\w-])")
_ORIGIN_FIELD = re.compile(rf"\b(?:отправление|город отправления|город вылета)\s*(?:теперь\s*)?[:=-]?\s*(?P<city>{_NAMES})(?![\w-])")
_COUNTRY_TO = re.compile(rf"\b(?:в|во|to|v|vo)\s+(?P<country>{_COUNTRY_NAMES})(?![\w-])")
_REPLACE = re.compile(rf"\b(?:замени|заменяем|меняем|поменяй)\s+(?P<old>{_NAMES})\s+на\s+(?P<new>{_NAMES})(?![\w-])")
_INSTEAD = re.compile(rf"(?P<new>{_NAMES})\s+вместо\s+(?P<old>{_NAMES})(?![\w-])")
_OLD = re.compile(rf"\bвместо\s+(?P<city>{_NAMES})(?![\w-])")
_HYPOTHETICAL = re.compile(r"\b(?:если|if|бы|допустим|предположим|может|возможно)\b")
_HISTORY = re.compile(r"\b(?:раньше|прежде|ездил\w*|летал\w*|прошл\w*|цитата|реклам\w*)\b")
_QUESTION = re.compile(r"^(?:а\s+)?(?:почему|как|где|куда|откуда|какой|какие|когда|сколько|объясни|расскажи|напомни|подскажи|сможешь|можешь|можно)\b")
_NEGATIVE = re.compile(r"\bне\s+(?:хочу|хотим|выбира\w*|меня\w*|применя\w*|перенос\w*|надо|нужно|поед\w*|едем|летим|будем)\b")
_NEGATIVE_START = re.compile(r"\b(?:не|not)\s+(?:начина\w*|старт\w*|start\w*)\b")
_NO_LODGING = re.compile(r"\b(?:отел\w*|гостиниц\w*|проживан\w*)\b.{0,40}\bне\s+(?:нуж\w*|требу\w*)\b|\bбез\s+(?:отел\w*|гостиниц\w*|проживан\w*)\b")
_RETURN_OR_LATER = re.compile(r"\b(?:обратно|домой|возвращ\w*|потом|затем|далее|после)\b")
_RETURN = re.compile(r"\b(?:обратно|домой|возвращ\w*)\b")
_CLEAR_GEO = ["proposed_destination", "confirmation_required", "suggested_cities", "contextual_correction", "proposal_field"]


def _city(value: Any) -> str | None:
    try:
        return canonical_city(value) if isinstance(value, str) else None
    except DomainError:
        return None


def _pending(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    result = deepcopy(value.get("context")) if isinstance(value.get("context"), dict) else {}
    result.update({k: deepcopy(v) for k, v in value.items() if k != "context"})
    return result


def _unquote(text: str) -> str:
    def literal(match):
        body = _key(match.group(1))
        return " " + body + " " if body in _FORMS or body in _COUNTRIES else " "
    return re.sub(r"[«“\"]([^»”\"]*)[»”\"]", literal, _key(text))


def _declared_unknown_destination(text: str, operations: list[dict]) -> str | None:
    """An unsupported literal command asks; it never authorizes a city edit.

    Only an exact current imperative, optionally conditional on catalogue
    support, qualifies. A model's unseen city, quotes, questions, history and
    price/other hypothetical conditions cannot create this clarification.
    """
    command = _unquote(text).strip()
    for operation in operations:
        value = operation.get("value")
        name = value.get("city") if isinstance(value, dict) else None
        if (operation.get("type") != "upsert_stay" or not isinstance(name, str)
                or not 1 <= len(name.strip()) <= 120 or _city(name) is not None
                or parse_destination(name)["requested_destination"]["kind"] != "unknown"):
            continue
        literal = re.escape(_key(name))
        pattern = (r"(?:пожалуйста\s*,?\s*)?(?:добавь(?:те)?|включи(?:те)?|выбираю|выбираем)\s+"
                   r"(?:(?:город|направление|остановку)\s+)?(?:в\s+)?" + literal
                   + r"(?:\s*,?\s*если\s+(?:он|она|оно|этот город|это направление|эта остановка)\s+"
                   r"поддерживается(?:\s+(?:каталогом|системой))?)?[.!]?")
        if re.fullmatch(pattern, command):
            return name.strip()
    return None


def _replacement_role(old: str, new: str, clause: str, data: dict, pending: dict, facts: dict) -> str | None:
    if re.search(r"отправлен|город вылета", clause):
        return "origin"
    if re.search(r"направлен|назначен", clause):
        return "destination"
    if pending.get("field") in {"origin.city", "destination.city"}:
        return "origin" if pending["field"] == "origin.city" else "destination"
    if new in facts["destination"]:
        return "destination"
    if new in facts["origin"]:
        return "origin"
    matching = [stay for stay in data.get("stays", []) if _city(stay.get("city")) == old]
    if old == _city(data.get("origin")) and not matching:
        return "origin"  # A unique current value is an owned reference, not a new default.
    return "destination" if matching else None


def _facts(data: dict, text: str, pending: dict) -> dict:
    result = {"origin": set(), "destination": set(), "country": set(), "old": set(),
              "targets": set(), "replace": False, "choice": False, "alternative": False, "multicity": False, "current": False}
    hypothetical = False
    bare_origins, bare_destinations = set(), set()
    for match in re.finditer(r"[^,;.!?\n]+(?:[,;.!?\n]|$)", _unquote(text)):
        raw = match.group().strip()
        stop = raw[-1:] in {";", ".", "!", "?", "\n"}
        clause = raw.rstrip(",;.!?\n ")
        if _HYPOTHETICAL.search(clause):
            hypothetical = True
        skip = (hypothetical or _HISTORY.search(clause) or _QUESTION.search(clause) or raw.endswith("?")
            or _NEGATIVE.search(clause) or _NEGATIVE_START.search(clause) or _NO_LODGING.search(clause))
        if not skip:
            matches = list(_CITY.finditer(clause))
            for stay in data.get("stays", []):
                identifier = stay.get("id")
                if isinstance(identifier, str) and re.search(r"(?<![\w-])" + re.escape(identifier) + r"(?![\w-])", clause):
                    result["targets"].add(identifier)
                    directed = re.search(rf"\bна\s+(?P<city>{_NAMES})(?![\w-])", clause)
                    if directed and re.search(r"\b(?:замени|поменяй|измени|смени)\b", clause):
                        result["destination"].add(_FORMS[directed["city"]]); result["replace"] = True
            if re.search(r"\b(?:или|либо)\b", clause) and len({m["city"] for m in matches}) > 1:
                result["alternative"] = True
            else:
                for found in _FROM.finditer(clause):
                    if not _RETURN_OR_LATER.search(clause[:found.start()]):
                        result["origin"].add(_FORMS[found["city"]])
                for found in _START.finditer(clause):
                    result["origin"].add(_FORMS[found["city"]])
                for found in _ORIGIN_FIELD.finditer(clause):
                    result["origin"].add(_FORMS[found["city"]])
                if re.search(r"\b(?:вместо|замен\w*|поменя\w*|меняем|измен\w*|согласен)\b", clause):
                    for found in _ONTO.finditer(clause):
                        role = "origin" if re.search(r"отправлен|город вылета", clause) else "destination"
                        result[role].add(_FORMS[found["city"]])
                for found in _TO.finditer(clause):
                    prefix = clause[:found.start()]
                    if (not re.search(r"(?:живу|живем|прожива\w*|работа\w*)\s*$", prefix)
                            and not _RETURN.search(prefix) and not _START_BEFORE.search(prefix) and not _END_BEFORE.search(prefix)):
                        result["destination"].add(_FORMS[found["city"]])
                        if re.search(r"\b(?:потом|затем|далее|после|через)\b", prefix):
                            result["multicity"] = True
                for found in _CHOICE.finditer(clause):
                    result["choice"] = True
                    role = "origin" if (pending.get("field") == "origin.city" or re.search(r"отправлен|город вылета", clause)) and not re.search(r"направлен|назначен", clause) else "destination"
                    result[role].add(_FORMS[found["city"]])
                for found in _COUNTRY_TO.finditer(clause):
                    if not re.search(r"(?:живу|живем|прожива\w*|работа\w*)\s*$", clause[:found.start()]):
                        result["country"].add(_COUNTRIES[found["country"]])
                # Destination-before-origin compound: 'Белград в Сербии из Москвы'.
                leading = _CITY.match(clause)
                if leading and (_FROM.search(clause[leading.end():]) or pending.get("field") == "destination.city" and _COUNTRY_TO.search(clause[leading.end():])):
                    result["destination"].add(_FORMS[leading["city"]])
                chain = re.fullmatch(rf"(?P<a>{_NAMES})\s*(?:→|->|-)\s*(?P<b>{_NAMES})", clause)
                if chain:
                    result["origin"].add(_FORMS[chain["a"]]); result["destination"].add(_FORMS[chain["b"]])
                for found in [*_REPLACE.finditer(clause), *_INSTEAD.finditer(clause)]:
                    old, new = _FORMS[found["old"]], _FORMS[found["new"]]
                    result["old"].add(old); result["replace"] = True
                    role = _replacement_role(old, new, clause, data, pending, result)
                    if role:
                        result[role].add(new)
                old = {_FORMS[found["city"]] for found in _OLD.finditer(clause)}
                result["old"].update(old)
                if old:
                    result["replace"] = True
                    remaining = {_FORMS[m["city"]] for m in matches} - old
                    if len(remaining) == 1:
                        new = next(iter(remaining))
                        roles = {_replacement_role(previous, new, clause, data, pending, result) for previous in old}
                        if len(roles) == 1 and None not in roles:
                            result[next(iter(roles))].add(new)
                bare = re.sub(r"^(?:да|а)\s+", "", clause).strip()
                if bare in _FORMS and pending.get("field") in {"origin.city", "destination.city"}:
                    (bare_origins if pending["field"] == "origin.city" else bare_destinations).add(_FORMS[bare])
                if bare in _COUNTRIES and pending.get("field") == "destination.city":
                    result["country"].add(_COUNTRIES[bare])
                if re.search(r"\b(?:вместо|замен\w*|меняем|отменяем)\b", clause):
                    result["replace"] = True
        if stop:
            hypothetical = False
    if not result["origin"]:
        result["origin"].update(bare_origins)
    if not result["destination"]:
        result["destination"].update(bare_destinations)
        if len(bare_destinations) > 1:
            result["alternative"] = True
    result["current"] = bool(result["origin"] or result["destination"] or result["country"] or result["alternative"])
    return result


def _target(data: dict, operations: list[dict], facts: dict, pending: dict) -> str | None:
    stays = data.get("stays") or []
    if facts["targets"]:
        return next(iter(facts["targets"])) if len(facts["targets"]) == 1 else None
    if pending.get("field") in {"destination.city", "stay.city"}:
        target = pending.get("target_id") or pending.get("stay_id")
        if target is not None:
            return target if isinstance(target, str) and sum(s.get("id") == target for s in stays) == 1 else None
    matches = [stay["id"] for stay in stays if _city(stay.get("city")) in facts["old"]]
    if len(matches) == 1:
        return matches[0]
    if len(stays) == 1:
        return stays[0].get("id")
    if stays:
        return None
    proposed_ops = [op for op in operations if op.get("type") == "upsert_stay"]
    authorised = [op.get("target_id") for op in proposed_ops if _city(op.get("value", {}).get("city")) in facts["destination"]]
    if len(authorised) == 1 and isinstance(authorised[0], str):
        return authorised[0]
    proposed = [op.get("target_id") for op in proposed_ops]
    if len(proposed) == 1 and isinstance(proposed[0], str):
        return proposed[0]
    if proposed:
        return None
    occupied = {item.get("id") for name in ("travelers", "groups", "legs") for item in data.get(name, [])}
    occupied.update(op.get("target_id") for op in operations)
    for number in range(1, 31):
        target = f"new-stay-{number}"
        if target not in occupied:
            return target
    return None


def ground_intent_operations(before_state: Any, operations: list, text: str,
                             pending_context: dict | None = None) -> tuple[list[dict], dict]:
    """Return copied operations and owned notes/questions/context, never commit.

    pending_context may be a complete pending_question or its flattened context
    including field/target_id. Metadata unknowns are trusted human questions;
    the host must not grant that trust to arbitrary model unknowns.
    """
    data = before_state.model_dump(mode="json") if hasattr(before_state, "model_dump") else deepcopy(before_state)
    copied = [op.model_dump(mode="json") if hasattr(op, "model_dump") else deepcopy(op) for op in operations]
    meta = {"notes": [], "unknowns": [], "pending_context": None,
            "pending_context_clear": [], "replace_geo_unknowns": False}
    if not isinstance(data, dict) or not isinstance(text, str) or not 0 < len(text) <= 20_000:
        return copied, meta
    pending = _pending(pending_context)
    stale_pending = ("state_version" in pending and (type(pending["state_version"]) is not int
        or pending["state_version"] != data.get("state_version")))
    facts_pending = {} if stale_pending else pending
    facts = _facts(data, text, facts_pending)
    affirmative = _key(text).rstrip(".!") in {"да", "верно", "подтверждаю", "да меняем", "да выбираем"}
    proposal = pending.get("proposed_destination")
    if (affirmative and not stale_pending and pending.get("field") == "destination.city"
            and pending.get("confirmation_required") is True and pending.get("proposal_field") == "destination.city"
            and isinstance(proposal, dict) and proposal.get("kind") == "city" and _city(proposal.get("name"))):
        facts["destination"].add(_city(proposal["name"])); facts.update(replace=True, choice=True, current=True)
    origins, destinations, countries = facts["origin"], facts["destination"], facts["country"]
    origin = next(iter(origins)) if len(origins) == 1 else None
    destination = next(iter(destinations)) if len(destinations) == 1 and not facts["alternative"] else None
    country = next(iter(countries)) if len(countries) == 1 else None
    target = _target(data, copied, facts, facts_pending) if destination else None

    def note(code: str, **fields):
        meta["notes"].append({"code": code, **fields})

    def ask(message: str, field: str, context: dict | None = None, target_id=None):
        meta["unknowns"].append(message)
        meta["pending_context"] = {**(meta["pending_context"] or {}), **(context or {})}
        if meta["pending_context"] is None or "field" not in meta["pending_context"]:
            meta["pending_context"] = {**(meta["pending_context"] or {}), **(context or {}), "field": field, "target_id": target_id}
        meta["replace_geo_unknowns"] = True

    unsupported = _declared_unknown_destination(text, copied)
    if unsupported:
        ask(f"Город «{unsupported}» не удалось однозначно распознать в текущем каталоге. Уточните страну и город назначения.",
            "destination.city", {"requested_destination": parse_destination(unsupported)["requested_destination"]})
        meta["pending_context_clear"] = list(_CLEAR_GEO)
        note("declared_unknown_destination_requires_clarification", city=unsupported)
    if len(origins) > 1:
        ask("Из какого одного города вы отправляетесь? Уточните город отправления.", "origin.city")
    if facts["alternative"] or len(destinations) > 1 and not facts["multicity"]:
        ask("Выберите один город назначения или явно опишите последовательность остановок.", "destination.city")
    expected = pending.get("requested_destination")
    expected_country = expected.get("country_code") if isinstance(expected, dict) else None
    blocked = False
    if destination:
        place = parse_destination(destination)["requested_destination"]
        actual_country = place["country_code"]
        mismatched_country = country and country != actual_country
        stale_choice = expected_country and expected_country != actual_country and not facts["replace"] and not (country == actual_country and facts["choice"])
        if mismatched_country or stale_choice:
            blocked = True
            requested = parse_destination(country or expected_country)["requested_destination"]
            actual_name = parse_destination(actual_country)["requested_destination"]["name"]
            ask(f"Город «{destination}» находится в стране «{actual_name}». Вы подтверждаете выбор этого города и смену страны назначения?",
                "destination.city", {"requested_destination": requested, "proposed_destination": place,
                "confirmation_required": True, "proposal_field": "destination.city"}, target)
            note("destination_country_confirmation_required", city=destination, country=actual_country)
            meta["pending_context_clear"] = list(_CLEAR_GEO)
        elif target is None:
            blocked = True
            ask("Какую именно остановку нужно изменить? Укажите её точный идентификатор или существующий город и новый город назначения.", "destination.city")
            note("destination_target_ambiguous", city=destination)
        else:
            meta["pending_context"] = {**(meta["pending_context"] or {}), "requested_destination": place}
            meta["pending_context_clear"] = list(_CLEAR_GEO)
            meta["replace_geo_unknowns"] = True
    elif country and facts["current"]:
        blocked = True
        place = parse_destination(country)["requested_destination"]
        ask(f"Какой город в стране «{place['name']}» вы выбираете для поездки?", "destination.city", {"requested_destination": place})
        meta["pending_context_clear"] = list(_CLEAR_GEO)
    elif affirmative and pending.get("field") == "destination.city":
        requested = pending.get("requested_destination")
        country_name = requested.get("name") if isinstance(requested, dict) and requested.get("kind") == "country" else None
        ask(f"Какой город в стране «{country_name}» вы выбираете для поездки?" if country_name else
            "Какой город назначения вы выбираете? Старое предложение не подтверждено.", "destination.city")
        meta["pending_context_clear"] = list(_CLEAR_GEO)

    result = []
    has_origin, has_destination = False, False
    existing = {stay.get("id"): stay for stay in data.get("stays", [])}
    for op in copied:
        kind, value = op.get("type"), op.get("value")
        if not isinstance(value, dict):
            result.append(op); continue
        if kind == "set_origin":
            proposed = _city(value.get("city", value.get("origin")))
            if origin:
                result.append({**op, "value": {"city": origin}}); has_origin = True
                if proposed != origin:
                    note("origin_corrected_from_current_literal", before=proposed, after=origin)
            elif proposed == _city(data.get("origin")) and proposed is not None:
                result.append(op)
            else:
                note("unanchored_origin_suppressed", proposed=proposed)
                if not data.get("origin") and facts["current"]:
                    ask("Из какого города вы отправляетесь? Город отправления пока не указан.", "origin.city")
            continue
        if kind == "upsert_stay" and "city" in value:
            old = existing.get(op.get("target_id"), {})
            proposed = _city(value.get("city"))
            # A unique explicit replacement owns the existing stay; never
            # create a second stay merely because the model invented an ID.
            if destination and not blocked and (op.get("target_id") == target or proposed == destination):
                op = {**op, "target_id": target, "value": {**value, "city": destination}}
                result.append(op); has_destination = True
                continue
            if proposed == _city(old.get("city")) and proposed is not None:
                result.append(op)
            elif len(destinations) > 1 and not facts["alternative"] and proposed in destinations:
                result.append(op)  # Preserve explicit multi-city proposals; no synthesis/first choice.
            else:
                note("unanchored_destination_suppressed", proposed=proposed, target_id=op.get("target_id"))
                if old:
                    remainder = {key: val for key, val in value.items() if key != "city"}
                    if remainder: result.append({**op, "value": remainder})
            continue
        result.append(op)
    if origin and not has_origin and origin != _city(data.get("origin")) and len(result) < 30:
        result.append({"type": "set_origin", "value": {"city": origin}})
        note("origin_grounded_from_current_literal", after=origin)
    if destination and not blocked and not has_destination and len(result) < 30:
        result.append({"type": "upsert_stay", "target_id": target, "value": {"city": destination}})
        note("destination_grounded_from_current_literal", after=destination, target_id=target)
    return result, meta


_AUX_ANCHORS = {
    "breakfast": re.compile(r"\b(?:завтрак\w*|breakfast)\b"),
    "refundable": re.compile(r"\b(?:возврат\w*|невозврат\w*|refundable|nonrefundable)\b|\bбесплатн\w*\s+отмен\w*|\bотмен\w*\s+(?:брон\w*|тариф\w*|отел\w*)"),
    "pets_allowed": re.compile(r"\b(?:питом\w*|животн\w*|собак\w*|кошк\w*|пес|пса|pets?)\b"),
}
_AUX_ANCHORS["pets"] = _AUX_ANCHORS["pets_allowed"]
_AUX_ANCHORS["nonstop"] = re.compile(r"\b(?:прям\w*|пересад\w*|техпосад\w*|nonstop|direct\s+flight)\b")
_AUX_ACTION = re.compile(r"\b(?:добав\w*|включ\w*|хочу|нужен|нужна|нужны|обязател\w*|требу\w*|установ\w*|постав\w*|остав\w*)\b")


def _aux_clauses(text: str) -> list[str]:
    result, hypothetical = [], False
    for match in re.finditer(r"[^,;.!?\n]+(?:[,;.!?\n]|$)", _unquote(text)):
        raw = match.group().strip(); clause = raw.rstrip(",;.!?\n ")
        if _HYPOTHETICAL.search(clause):
            hypothetical = True
        question = raw.endswith("?") or _QUESTION.search(clause)
        if (not hypothetical and not _HISTORY.search(clause) and (not question or _AUX_ACTION.search(clause))
                and not re.search(r"\b(?:только обсужд\w*|пока не применя\w*|не применя\w*)\b", clause)):
            result.append(clause)
        if raw[-1:] in {";", ".", "!", "?", "\n"}:
            hypothetical = False
    return result


def _hotel_selection(clauses: list[str]) -> bool:
    head = r"(?:отел\w*|гостиниц\w*|hotel|план\w*|вариант\w*)"
    for clause in clauses:
        if re.search(r"\bне\s+(?:выбира\w*|выбер\w*|меня\w*|замен\w*|покуп\w*|закреп\w*)", clause):
            continue
        if (re.search(r"\b(?:выбираю|выбираем|выбери|возьми|берем|замени|поменяй|смени|закрепи|оставь)\s+(?:\w+\s+){0,3}" + head + r"\b", clause)
                or re.search(r"\b" + head + r"\s+(?:\w+\s+){0,2}(?:выбираю|выбираем|берем|оставляем)\b", clause)):
            return True
    return False


def ground_auxiliary_operations(before_state: Any, operations: list, text: str) -> tuple[list[dict], list[dict]]:
    """Suppress unanchored auxiliary proposals; never invent a value/choice.

    This narrower companion is independent of geography. Caller may run it after
    ground_intent_operations and append notes to the same checked-operation log.
    Removal operations and unrelated fields are left to their existing guards.
    """
    data = before_state.model_dump(mode="json") if hasattr(before_state, "model_dump") else deepcopy(before_state)
    copied = [op.model_dump(mode="json") if hasattr(op, "model_dump") else deepcopy(op) for op in operations]
    if not isinstance(data, dict) or not isinstance(text, str) or not 0 < len(text) <= 20_000:
        return copied, []
    clauses = _aux_clauses(text)
    selection = _hotel_selection(clauses)
    existing = {stay.get("id"): stay for stay in data.get("stays", [])}
    result, notes = [], []
    for op in copied:
        kind, value = op.get("type"), op.get("value")
        if not isinstance(value, dict):
            result.append(op); continue
        key = value.get("key")
        if kind == "set_constraint" and key in _AUX_ANCHORS:
            anchors = [clause for clause in clauses if _AUX_ANCHORS[key].search(clause)]
            code = None
            if not anchors:
                code = "unrequested_aux_constraint_suppressed"
            elif all(re.search(r"\b(?:удали|убери|сними|очисти|не заказывал|не заказывала|не просил|не просила)\b", clause) for clause in anchors):
                code = "constraint_set_cannot_replace_requested_removal"
            elif value.get("hard", True) is True and any(re.search(r"\b(?:желательн\w*|не обязател\w*|не принципиал\w*|предпоч\w*)\b", clause) for clause in anchors):
                code = "constraint_cannot_strengthen_explicit_soft_wish"
            elif key not in {"pets", "nonstop"} and type(value.get("value")) is bool:
                subject = {"breakfast": r"завтрак\w*", "refundable": r"(?:возврат\w*|бесплатн\w*\s+отмен\w*)",
                           "pets_allowed": r"(?:питом\w*|животн\w*|собак\w*|кошк\w*|pets?)"}[key]
                negative = any(re.search(r"\b(?:без|не нужен|не нужна|не нужны|не хочу|не надо|не требуется)\s+(?:\w+\s+){0,2}" + subject + r"\b", clause)
                    or re.search(r"\b" + subject + r"\s+(?:\w+\s+){0,2}(?:не нужен|не нужна|не нужны|не надо|не требуется|нет)\b", clause)
                    or key == "refundable" and re.search(r"\b(?:невозврат\w*|nonrefundable)\b", clause) for clause in anchors)
                positive = any(re.search(r"\b(?:нужен|нужна|нужны|хочу|включ\w*|добав\w*|с)\s+(?:\w+\s+){0,2}" + subject + r"\b", clause)
                    or re.search(r"\b" + subject + r"\s+(?:\w+\s+){0,2}обязател\w*\b", clause) for clause in anchors)
                if negative and value["value"] is True or positive and not negative and value["value"] is False:
                    code = "constraint_value_conflicts_explicit_polarity"
            if code:
                notes.append({"field": "constraints", "key": key, "code": code})
                continue
        if kind == "upsert_stay" and isinstance(value.get("hotel_id"), str) and value["hotel_id"]:
            prior = existing.get(op.get("target_id"), {}).get("hotel_id")
            if value["hotel_id"] != prior and not selection:
                notes.append({"field": "stay.hotel_id", "target_id": op.get("target_id"), "code": "unrequested_hotel_choice_suppressed"})
                remainder = {name: val for name, val in value.items() if name != "hotel_id"}
                if remainder:
                    result.append({**op, "value": remainder})
                continue
        result.append(op)
    return result, notes
