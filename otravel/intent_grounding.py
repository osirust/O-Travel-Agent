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
from .geography import _AMBIGUOUS, _COUNTRY_ALIASES, parse_destination
from .place_refs import CityRefSnapshot
from .provider_cities import available_search_cities
from .route_intake import _FORMS as _BASE_FORMS


def _key(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").replace("–", "-").replace("—", "-").split())


_FORMS = dict(_BASE_FORMS)
for _city, _aliases in {
    "Москва": ("moskva", "moskvy"), "Казань": ("kazani",),
    "Стамбул": ("stambul", "stambula"), "Анталья": ("antaliyu", "antalyu"),
    "Санкт-Петербург": ("peterburga", "petersburg", "sankt-peterburga"),
    "Тбилиси": ("тбилиси", "tbilisi"),
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
_END_BEFORE = re.compile(r"\b(?:заканчива\w*|заверша\w*|финиширу\w*|"
    r"(?:завершение|окончание)(?:\s+(?:поездки|маршрута))?|конец\s+(?:поездки|маршрута)|ends?|ending)\s*$")
_TO = re.compile(rf"\b(?:в|во|to|v|vo)\s+(?:город\s+)?(?P<city>{_NAMES})(?![\w-])")
_NEXT_DESTINATION = re.compile(rf"\b(?:затем|потом|далее)\s+(?:(?:в|во)\s+)?(?P<city>{_NAMES})(?![\w-])")
_NO_LODGING_IN_CITY = re.compile(rf"\b(?:отел\w*|гостиниц\w*|проживан\w*)\s+(?:в|во)\s+(?P<city>{_NAMES})(?![\w-]).{{0,25}}\bне\s+(?:нуж\w*|требу\w*)")
_ONTO = re.compile(rf"\bна\s+(?P<city>{_NAMES})(?![\w-])")
_CHOICE = re.compile(rf"\b(?:выбираю|выбираем|выбрали|назначение|направление|город назначения)\s*[:=-]?\s*(?:именно\s+)?(?P<city>{_NAMES})(?![\w-])")
_ORIGIN_FIELD = re.compile(rf"\b(?:отправление|город отправления|город вылета)\s*(?:теперь\s*)?[:=-]?\s*(?P<city>{_NAMES})(?![\w-])")
_COUNTRY_TO = re.compile(rf"\b(?:в|во|to|v|vo)\s+(?P<qualifier>страну\s+)?(?P<country>{_COUNTRY_NAMES})(?![\w-])")
_QUALIFIED_CITY_PAIR = re.compile(rf"(?<![\w-])(?P<city>{_NAMES})\s+(?:в|во|in)\s+(?P<country>{_COUNTRY_NAMES})(?![\w-])")
_QUALIFIED_CITY_CONFIRMATION = re.compile(rf"(?:(?:да|верно|правильно)\s+)?(?:именно\s+)?"
    rf"(?P<city>{_NAMES})\s+(?:в|во|in)\s+(?P<country>{_COUNTRY_NAMES})")
_AMBIGUOUS_NAMES = "|".join(re.escape(name) for name in sorted(_AMBIGUOUS, key=len, reverse=True))
_AMBIGUOUS_TO = re.compile(rf"\b(?:в|во|to|in|направление|назначение|выбираю)\s+(?P<name>{_AMBIGUOUS_NAMES})(?![\w-])")
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


def _selected_ref(data: dict, role: str, target: str | None = None) -> CityRefSnapshot | None:
    if role == "origin":
        name, place_id = data.get("origin"), data.get("origin_place_id")
    else:
        stay = next((stay for stay in data.get("stays", []) if stay.get("id") == target), {})
        name, place_id = stay.get("city"), stay.get("place_id")
    if not isinstance(place_id, str):
        return None
    try:
        ref = CityRefSnapshot.model_validate((data.get("location_refs") or {}).get(place_id))
    except ValueError:
        return None
    return ref if ref.place_id == place_id and ref.canonical_name == name else None


def _same_ref_name(value: Any, ref: CityRefSnapshot) -> bool:
    if not isinstance(value, str):
        return False
    if _key(value) == _key(ref.canonical_name):
        return True
    # An alias is a name-only echo of the CURRENT role identity, never a
    # country lookup or permission to bind another same-named place.
    canonical = _FORMS.get(_key(ref.canonical_name), _city(ref.canonical_name))
    return canonical is not None and _FORMS.get(_key(value), _city(value)) == canonical


def _current_ref_echo(data: dict, operation: dict) -> bool:
    kind, value = operation.get("type"), operation.get("value")
    if kind not in {"set_origin", "upsert_stay"} or not isinstance(value, dict):
        return False
    ref = _selected_ref(data, "origin" if kind == "set_origin" else "destination", operation.get("target_id"))
    return (ref is not None and _same_ref_name(value.get("city", value.get("origin")), ref)
        and ("place_id" not in value or value["place_id"] == ref.place_id))


def _origin_ref_country_conflict(text: str, ref: CityRefSnapshot) -> str | None:
    """Bound an explicit origin qualifier, not the following destination.

    The reviewed Russian case is ``из Москвы в России``; ``в Сербию`` is a
    destination and must not be attached to the departure city. Broader role
    extraction still belongs to NLU/coordinator confirmation.
    """
    canonical = _FORMS.get(_key(ref.canonical_name), _city(ref.canonical_name))
    aliases = {_key(ref.canonical_name), *[name for name, city in _FORMS.items() if canonical is not None and city == canonical]}
    names = "|".join(re.escape(name) for name in sorted(aliases, key=len, reverse=True))
    pattern = re.compile(rf"\b(?:из|from)\s+(?:города\s+)?(?:{names})(?![\w-])\s+(?P<prep>в|in)\s+(?P<country>{_COUNTRY_NAMES})(?![\w-])")
    for match in pattern.finditer(_unquote(text)):
        literal = match["country"]
        if match["prep"] == "в" and not literal.endswith(("е", "и")):
            continue
        code = _COUNTRIES[literal]
        if code != ref.country_iso2:
            return code
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


def resolve_country_destination_answer(before_state: Any, pending_context: dict | None, text: str) -> dict:
    """Bind an explicit country answer to an owned destination slot, no model.

    Intended before generic geography QA: answering the active destination
    question is not the same as asking whether a country exists. No city,
    inventory coverage or other change is inferred from the country.
    """
    unhandled = {"handled": False, "operations": []}
    data = before_state.model_dump(mode="json") if hasattr(before_state, "model_dump") else before_state
    if not isinstance(data, dict) or not isinstance(text, str) or not 0 < len(text) <= 20_000:
        return unhandled
    pending = _pending(pending_context)
    if pending.get("field") != "destination.city":
        return unhandled
    if "state_version" in pending and (type(pending["state_version"]) is not int
            or pending["state_version"] != data.get("state_version")):
        return unhandled
    target = pending.get("target_id") or pending.get("stay_id")
    if target is not None and (not isinstance(target, str)
            or sum(stay.get("id") == target for stay in data.get("stays", [])) != 1):
        return unhandled
    normalized = _key(text)
    match = re.fullmatch(r"(?:(?:это\s+)?страна(?: назначения)?|(?:я\s+|мы\s+)?"
        r"(?:имею|имел|имела|имели|имеем)\s+в\s+виду\s+страну|(?:я\s+|мы\s+)?выбираю\s+страну)"
        rf"\s*[:=-]?\s+(?P<country>{_COUNTRY_NAMES})(?![\w-])(?P<tail>.*)", normalized)
    if match is None:
        return unhandled
    # A bounded disambiguation / coverage disclaimer is not another edit. All
    # other compound tails return to the normal interpreter rather than vanish.
    tail = match["tail"].strip(" ,.!\n")
    tail = re.sub(r"^не американский штат(?:\s*[,.;!]\s*|$)", "", tail).strip(" ,.!\n")
    if tail and not re.fullmatch(r"(?:если (?:ее|его|их) нет в поиске,? (?:так и скажи|скажи об этом)"
            r"(?:,? город за меня не выбирай)?|город за меня не выбирай|город пока не (?:выбрал|выбрала|выбрали|выбирай))", tail):
        return unhandled
    code = _COUNTRIES[match["country"]]
    place = parse_destination(code, available_cities=available_search_cities(data))["requested_destination"]
    return {"handled": True, "operations": [],
            "question": f"Понял: страна «{place['name']}». Какой город в этой стране вы выбираете для поездки?",
            "pending_context": {"field": "destination.city", "target_id": target, "requested_destination": place},
            "pending_context_clear": list(_CLEAR_GEO)}


def _declared_unknown_destination(text: str, operations: list[dict], *, outside_catalogue: bool = False,
                                  available_cities=None) -> str | None:
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
                or not 1 <= len(name.strip()) <= 120):
            continue
        city = _city(name)
        coverage = available_search_cities({}) if available_cities is None else available_cities
        if (outside_catalogue and (city is None or city in coverage)
                or not outside_catalogue and (city is not None
                    or parse_destination(name)["requested_destination"]["kind"] != "unknown")):
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



def _negated_role_preposition(clause: str, start: int) -> bool:
    """Negation belongs to this preposition, not every later city clause."""
    return re.search(r"\b(?:не|not)\s*$", clause[:start]) is not None


def _mask_owned_checkout_origin_cues(data: dict, normalized: str, operations) -> str:
    """A literal checkout with an owned date operation is not a new origin.

    Only removes the server's extra FROM-role inference. Creates no date,
    stay, identity or operation, and cannot make a model target owned.
    A separate explicit departure/origin cue remains in the original text.
    """
    pattern = re.compile(rf"\b(?:дата\s+)?выезд(?:а)?\s+(?P<cue>из)\s+"
        rf"(?P<city>{_NAMES})(?![\w-])\s*(?:[-:=]\s*)?"
        r"(?P<date>\d{1,2}\.\d{1,2}\.\d{4})(?!\d)")
    chars = list(normalized)
    stays = {s.get("id"): s for s in data.get("stays", []) if isinstance(s, dict)}
    for match in pattern.finditer(normalized):
        day, month, year = map(int, match["date"].split("."))
        literal_date = f"{year:04d}-{month:02d}-{day:02d}"
        city = _FORMS[match["city"]]
        owned_date = False
        for operation in operations:
            if not isinstance(operation, dict) or operation.get("type") != "upsert_stay":
                continue
            value = operation.get("value")
            stay = stays.get(operation.get("target_id"))
            if (not isinstance(value, dict) or not isinstance(stay, dict)
                    or _city(stay.get("city")) != city
                    or value.get("check_out") != literal_date
                    or "city" in value and _city(value["city"]) != city):
                continue
            owned_date = True
        if owned_date:
            chars[match.start("cue"):match.end("cue")] = " " * len(match["cue"])
    return "".join(chars)


def _facts(data: dict, text: str, pending: dict, operations=()) -> dict:
    result = {"origin": set(), "destination": set(), "country": set(), "ambiguous": set(), "old": set(),
              "targets": set(), "replace": False, "choice": False, "alternative": False, "multicity": False, "current": False}
    hypothetical = False
    bare_origins, bare_destinations = set(), set()
    normalized = _mask_owned_checkout_origin_cues(data, _unquote(text), operations)
    no_lodging = {_FORMS[m["city"]] for m in _NO_LODGING_IN_CITY.finditer(normalized)}
    policy = data.get("route_policy") if isinstance(data.get("route_policy"), dict) else {}
    terminal = _city(policy.get("end_city")) if policy.get("kind") == "open_jaw" else None
    for match in re.finditer(r"[^,;.!?\n]+(?:[,;.!?\n]|$)", normalized):
        raw = match.group().strip()
        stop = raw[-1:] in {";", ".", "!", "?", "\n"}
        clause = raw.rstrip(",;.!?\n ")
        if (_HYPOTHETICAL.search(clause) or _HISTORY.search(clause) or _NEGATIVE.search(clause)
                or _NEGATIVE_START.search(clause)):
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
                    if (not _RETURN_OR_LATER.search(clause[:found.start()])
                            and not _negated_role_preposition(clause, found.start())):
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
                    if (not _negated_role_preposition(clause, found.start())
                            and not re.search(r"(?:живу|живем|прожива\w*|работа\w*)\s*$", prefix)
                            and not _RETURN.search(prefix) and not _START_BEFORE.search(prefix) and not _END_BEFORE.search(prefix)
                            and _FORMS[found["city"]] not in no_lodging
                            and not (terminal == _FORMS[found["city"]] and _RETURN_OR_LATER.search(prefix))):
                        result["destination"].add(_FORMS[found["city"]])
                        if re.search(r"\b(?:потом|затем|далее|после|через)\b", prefix):
                            result["multicity"] = True
                for found in _NEXT_DESTINATION.finditer(clause):
                    city = _FORMS[found["city"]]
                    if city not in no_lodging and city != terminal and not _RETURN.search(clause[:found.start()]):
                        result["destination"].add(city)
                        result["multicity"] = True
                for found in _CHOICE.finditer(clause):
                    result["choice"] = True
                    role = "origin" if (pending.get("field") == "origin.city" or re.search(r"отправлен|город вылета", clause)) and not re.search(r"направлен|назначен", clause) else "destination"
                    result[role].add(_FORMS[found["city"]])
                for found in _COUNTRY_TO.finditer(clause):
                    if (not _negated_role_preposition(clause, found.start())
                            and not re.search(r"(?:живу|живем|прожива\w*|работа\w*)\s*$", clause[:found.start()])):
                        if not found["qualifier"] and parse_destination(found["country"])["requested_destination"]["kind"] == "ambiguous":
                            result["ambiguous"].add(found["country"])
                        else:
                            result["country"].add(_COUNTRIES[found["country"]])
                for found in _AMBIGUOUS_TO.finditer(clause):
                    if not re.search(r"(?:живу|живем|прожива\w*|работа\w*)\s*$", clause[:found.start()]):
                        result["ambiguous"].add(found["name"])
                # Destination-before-origin compound: 'Белград в Сербии из Москвы'.
                leading = _CITY.match(clause)
                if leading and (_FROM.search(clause[leading.end():]) or _COUNTRY_TO.search(clause[leading.end():])):
                    result["destination"].add(_FORMS[leading["city"]])
                qualified = _QUALIFIED_CITY_CONFIRMATION.fullmatch(clause)
                if qualified:
                    role = "origin" if pending.get("field") == "origin.city" else "destination"
                    result[role].add(_FORMS[qualified["city"]])
                    result["choice"] = True
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
                    if parse_destination(bare)["requested_destination"]["kind"] == "ambiguous":
                        result["ambiguous"].add(bare)
                    else:
                        result["country"].add(_COUNTRIES[bare])
                if bare in _AMBIGUOUS and pending.get("field") == "destination.city":
                    result["ambiguous"].add(bare)
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
    result["current"] = bool(result["origin"] or result["destination"] or result["country"] or result["ambiguous"] or result["alternative"])
    return result


def current_city_country_hint(state, text, role, query):
    """One current literal city-country pair, only for its exact query/role.

    A hint filters a future verified source query; it never admits a CityRef,
    selects a candidate or borrows a country from history/model output.
    """
    if role not in {"origin", "destination"} or not isinstance(text, str) or not 0 < len(text) <= 4000:
        return None
    city = _city(query)
    if city is None:
        return None
    data = state.model_dump(mode="json") if hasattr(state, "model_dump") else state
    if not isinstance(data, dict):
        return None
    facts = _facts(data, text, {"field": role + ".city"})
    if (facts[role] != {city} or len(facts["country"]) != 1 or facts["ambiguous"]
            or facts["alternative"] or facts["multicity"]):
        return None
    country = next(iter(facts["country"]))
    pairs = {(_FORMS[match["city"]], _COUNTRIES[match["country"]])
             for match in _QUALIFIED_CITY_PAIR.finditer(_unquote(text))}
    return country if pairs == {(city, country)} else None


def _target(data: dict, operations: list[dict], facts: dict, pending: dict) -> str | None:
    stays = data.get("stays") or []
    if facts["targets"]:
        return next(iter(facts["targets"])) if len(facts["targets"]) == 1 else None
    if pending.get("field") in {"destination.city", "stay.city"}:
        target = pending.get("target_id") or pending.get("stay_id")
        if target is not None:
            existing = isinstance(target, str) and sum(s.get("id") == target for s in stays) == 1
            pending_new = (not stays and isinstance(target, str)
                and re.fullmatch(r"(?:new-)?stay-(?:[1-9]|[12][0-9]|30)", target) is not None)
            return target if existing or pending_new else None
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


def _compound_proposal_answer(text: str) -> int:
    """Only a direct yes/no followed by an independent non-geographic edit.

    This never chooses a proposal or parses the other edit. The caller still
    requires a fresh, owned destination proposal and validates all operations.
    A geographic observation, veto, question or hypothetical is not consent.
    """
    normalized = _key(text)
    match = re.fullmatch(r"(?P<answer>да меняем|да выбираем|подтверждаю|не меняем|не надо|да|нет)"
        r"(?:\s*[,;.!]\s*|\s+)(?P<tail>.+)", normalized)
    if not match:
        return 0
    tail = match["tail"]
    if ("?" in tail or _HYPOTHETICAL.search(tail) or _HISTORY.search(tail) or _NEGATIVE.search(tail)
            or _CITY.search(tail) or re.search(rf"(?<![\w-])(?:{_COUNTRY_NAMES})(?![\w-])", tail)
            or re.search(r"\b(?:стран\w*|город\w*|назначен\w*|маршрут\w*)\b", tail)
            or re.match(r"(?:(?:и|но|а)\s+)?(?:добавь|убери|удали|измени|исправь|сократи|увеличь|уменьши|установи|оставь|перенеси|поменяй)\b", tail) is None):
        return 0
    return -1 if match["answer"] in {"нет", "не меняем", "не надо"} else 1


def _one_typo(left: str, right: str) -> bool:
    """One insertion/deletion/substitution or adjacent transposition only."""
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        different = [index for index, pair in enumerate(zip(left, right)) if pair[0] != pair[1]]
        if len(different) == 1:
            return True
        return (len(different) == 2 and different[1] == different[0] + 1
                and left[different[0]] == right[different[1]] and left[different[1]] == right[different[0]])
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    return any(longer[:index] + longer[index + 1:] == shorter for index in range(len(longer)))


def _typo_place_proposals(text: str) -> list[dict]:
    """Suggest a unique curated place from an explicit role cue; never commit.

    Unknown places remain unknown. Country names, residence/transit, quoted
    material, questions and non-current clauses cannot authorise a suggestion.
    """
    proposals = []
    hypothetical = False
    patterns = [
        (re.compile(r"(?<![\w-])(?P<cue>из|from|iz|в|во|to|v|vo)\s+(?:город[ау]?\s+)?"
                    r"(?P<literal>[а-яa-z][а-яa-z-]{3,59})(?![\w-])"), None),
        (re.compile(r"\b(?:отправление|город отправления|город вылета)\s*[:=-]?\s*"
                    r"(?:(?:теперь|сейчас)\s+)?(?P<literal>[а-яa-z][а-яa-z-]{3,59})(?![\w-])"), 'origin.city'),
        (re.compile(r"\b(?:назначение|направление|город назначения)\s*[:=-]?\s*"
                    r"(?:(?:теперь|сейчас)\s+)?(?P<literal>[а-яa-z][а-яa-z-]{3,59})(?![\w-])"), 'destination.city'),
    ]
    for item in re.finditer(r"[^,;.!?\n]+(?:[,;.!?\n]|$)", _unquote(text)):
        raw = item.group().strip()
        stop = raw[-1:] in {";", ".", "!", "?", "\n"}
        clause = raw.rstrip(",;.!?\n ")
        if _HYPOTHETICAL.search(clause) or _HISTORY.search(clause) or _NEGATIVE.search(clause):
            hypothetical = True
        skip = (hypothetical or _QUESTION.search(clause) or raw.endswith("?") or _NO_LODGING.search(clause)
                or re.search(r"\b(?:или|либо)\b", clause)
                or re.search(r"\b(?:пересад\w*|транзит\w*|проездом)\b", clause))
        if not skip:
            for pattern, explicit_field in patterns:
                for match in pattern.finditer(clause):
                    literal = match["literal"]
                    prefix = clause[:match.start()]
                    if (literal in _FORMS or literal in _COUNTRIES or literal in _AMBIGUOUS
                            or re.search(r"(?:живу|живем|прожива\w*|работа\w*)\s*$", prefix)
                            or _RETURN.search(prefix) or _END_BEFORE.search(prefix)):
                        continue
                    candidates = {city for alias, city in _FORMS.items() if len(alias) >= 4 and _one_typo(literal, alias)}
                    if len(candidates) == 1:
                        place = parse_destination(next(iter(candidates)))["requested_destination"]
                        field = explicit_field or ('origin.city' if match['cue'] in {'из', 'from', 'iz'} else 'destination.city')
                        proposal = {"field": field, "literal": literal, "place": place}
                        if proposal not in proposals:
                            proposals.append(proposal)
        if stop:
            hypothetical = False
    return proposals


def _existing_stay_city_references(data: dict, operations: list[dict], text: str, facts: dict, pending: dict) -> bool:
    """Multiple scoped stay references do not propose a new route choice."""
    stays = {stay.get('id'): stay for stay in data.get('stays', []) if isinstance(stay, dict)}
    cities = {_city(stay.get('city')) for stay in stays.values()}
    cities.discard(None)
    destinations = facts['destination']
    if (len(cities) < 2 or not destinations or not destinations.issubset(cities)
            or facts['alternative'] or facts['country'] or facts['ambiguous'] or facts['old']
            or facts['choice'] or facts['multicity'] or pending.get('field') in {'origin.city', 'destination.city'}
            or facts['origin'] - {_city(data.get('origin'))}):
        return False
    if any(sum(_city(stay.get('city')) == city for stay in stays.values()) != 1 for city in destinations):
        return False  # A city shared by several stays is not an owned target.
    for operation in operations:
        kind, value, target = operation.get('type'), operation.get('value'), operation.get('target_id')
        if not isinstance(value, dict):
            return False
        if kind == 'remove_stay':
            return False
        if kind == 'upsert_stay' and (target not in stays or 'city' in value and _city(value['city']) != _city(stays[target].get('city'))):
            return False
        if kind == 'set_origin' and _city(value.get('city', value.get('origin'))) != _city(data.get('origin')):
            return False
        if kind == 'set_route_policy' and any(value.get(key) != (data.get('route_policy') or {}).get(key)
                                             for key in value):
            return False
    normalized = _unquote(text)
    route_object = re.search(r'\b(?:измени|поменяй|перестрой|составь|замени|добавь|убери|удали|выбери)\s+'
        r'(?:(?:весь|всю|новый|новую|еще|одну|один)\s+){0,3}(?:маршрут\w*|направлен\w*|поездк\w*|остановк\w*|город\w*)\b', normalized)
    travel_wish = re.search(r'\b(?:(?:хочу|хотим)(?:\s+(?:поехать|полететь|отправиться))?|еду|едем|поедем|лечу|летим|полетим)'
        rf'\s+(?:в|во)\s+(?:{_NAMES})(?![\w-])', normalized)
    if route_object or travel_wish or _REPLACE.search(normalized) or _INSTEAD.search(normalized):
        return False
    scoped = set()
    for match in re.finditer(r'[^,;.!?\n]+(?:[,;.!?\n]|$)', normalized):
        clause = match.group().strip()
        references = {_FORMS[found['city']] for found in _TO.finditer(clause)}.intersection(destinations)
        if not references:
            continue
        if (clause.endswith('?') or _HYPOTHETICAL.search(clause) or _HISTORY.search(clause)
                or not re.search(r'\b(?:отел\w*|гостиниц\w*|номер\w*|комнат\w*|завтрак\w*|питан\w*|тариф\w*|'
                                 r'проживан\w*|расселен\w*|заезд\w*|выезд\w*|отмен\w*|питом\w*)\b', clause)):
            return False
        scoped.update(references)
    return scoped == destinations


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
    facts = _facts(data, text, facts_pending, copied)
    country_answer = resolve_country_destination_answer(data, facts_pending, text)
    if country_answer["handled"]:
        facts["country"].add(country_answer["pending_context"]["requested_destination"]["country_code"])
        facts["current"] = True
    scoped_city_names = None
    if _existing_stay_city_references(data, copied, text, facts, facts_pending):
        scoped_city_names = sorted(facts['destination'])
        # These places address current components. Do not send them through
        # either single-city target selection or initial multi-city intake.
        # Raw operations and all non-destination facts retain their own guards.
        facts['destination'] = set()
    compound_answer = _compound_proposal_answer(text)
    affirmative = _key(text).rstrip(".!") in {"да", "верно", "подтверждаю", "да меняем", "да выбираем"}
    proposal = pending.get("proposed_destination")
    owned_proposal = (not stale_pending and pending.get("field") == "destination.city"
            and pending.get("confirmation_required") is True and pending.get("proposal_field") == "destination.city"
            and isinstance(proposal, dict) and proposal.get("kind") == "city" and _city(proposal.get("name")))
    declined_proposal = owned_proposal and compound_answer == -1
    if owned_proposal and (affirmative or compound_answer == 1):
        facts["destination"].add(_city(proposal["name"])); facts.update(replace=True, choice=True, current=True)
    origins, destinations, countries = facts["origin"], facts["destination"], facts["country"]
    origin = next(iter(origins)) if len(origins) == 1 else None
    destination = next(iter(destinations)) if len(destinations) == 1 and not facts["alternative"] else None
    country = next(iter(countries)) if len(countries) == 1 else None
    target = _target(data, copied, facts, facts_pending) if destination else None
    previous_destination = facts_pending.get('requested_destination')
    serbian_spelling = (facts_pending.get('field') == 'destination.city'
        and isinstance(previous_destination, dict) and previous_destination.get('country_code') == 'RS'
        and destination == 'Белгород' and not countries and not facts['alternative']
        and not re.search(r'\b(?:именно|точно)\s+(?:в\s+)?белгород\b', _key(text)))

    def note(code: str, **fields):
        meta["notes"].append({"code": code, **fields})

    def ask(message: str, field: str, context: dict | None = None, target_id=None):
        meta["unknowns"].append(message)
        meta["pending_context"] = {**(meta["pending_context"] or {}), **(context or {})}
        if meta["pending_context"] is None or "field" not in meta["pending_context"]:
            meta["pending_context"] = {**(meta["pending_context"] or {}), **(context or {}), "field": field, "target_id": target_id}
        meta["replace_geo_unknowns"] = True

    def selected_country_conflict(role, ref, target_id, requested_country):
        field = "origin.city" if role == "origin" else "destination.city"
        direction = "отправления" if role == "origin" else "назначения"
        # Current snapshot identity outranks an inferred seed-country binding.
        meta["unknowns"] = []
        meta["pending_context"] = {"field": field, "target_id": target_id,
            "selected_place_conflict": {"role": role, "current_place_id": ref.place_id,
                "current_country_iso2": ref.country_iso2, "requested_country_iso2": requested_country}}
        meta["pending_context_clear"] = list(dict.fromkeys([*meta["pending_context_clear"], *_CLEAR_GEO, "requested_destination"]))
        ask(f"В поездке выбран город «{ref.canonical_name}» ({ref.country_iso2}), а сейчас указана страна {requested_country}. Какое точное место {direction} выбираем?",
            field, target_id=target_id)
        note("selected_place_country_qualifier_conflict", role=role, place_id=ref.place_id, country_iso2=requested_country)

    def preserve_selected_destination_metadata(ref):
        context = meta.get("pending_context") or {}
        if any(isinstance(context.get(key), dict) and context[key].get("kind") == "city"
                and _same_ref_name(context[key].get("name"), ref)
                for key in ("requested_destination", "proposed_destination")):
            meta["pending_context"] = None
            meta["unknowns"] = []
            meta["pending_context_clear"] = list(dict.fromkeys([*meta["pending_context_clear"], *_CLEAR_GEO, "requested_destination"]))
            note("selected_place_seed_country_context_suppressed", place_id=ref.place_id)

    if scoped_city_names:
        meta['replace_geo_unknowns'] = True
        note('existing_stay_city_scopes_preserved', city_names=scoped_city_names, route_changed=False)
    unresolved_proposals = [op for op in copied if not _current_ref_echo(data, op)]
    unsupported = _declared_unknown_destination(text, unresolved_proposals)
    if unsupported:
        ask(f"Город «{unsupported}» не удалось однозначно распознать в текущем каталоге. Уточните страну и город назначения.",
            "destination.city", {"requested_destination": parse_destination(unsupported)["requested_destination"]})
        meta["pending_context_clear"] = list(_CLEAR_GEO)
        note("declared_unknown_destination_requires_clarification", city=unsupported)
    catalogue_limited = _declared_unknown_destination(text, unresolved_proposals, outside_catalogue=True,
        available_cities=available_search_cities(data))
    if catalogue_limited and not serbian_spelling:
        place = parse_destination(catalogue_limited)["requested_destination"]
        country_name = parse_destination(place["country_code"])["requested_destination"]["name"]
        ask(f"{place['name']} — город в стране «{country_name}». В текущем поисковом каталоге проекта его нет; адаптеры поиска его не покрывают. Обсудим направление без поиска или выберем другой город?",
            "destination.city", {"requested_destination": place})
        meta["pending_context_clear"] = list(_CLEAR_GEO)
        note("known_destination_outside_search_catalogue", city=place["name"])
    if len(origins) > 1:
        ask("Из какого одного города вы отправляетесь? Уточните город отправления.", "origin.city")
    if facts["alternative"] or len(destinations) > 1 and not facts["multicity"]:
        ask("Выберите один город назначения или явно опишите последовательность остановок.", "destination.city")
    expected = facts_pending.get("requested_destination")
    expected_country = expected.get("country_code") if isinstance(expected, dict) else None
    blocked = False
    typo_proposals = _typo_place_proposals(text)
    typo = typo_proposals[0] if len(typo_proposals) == 1 else None
    if typo is not None:
        field, place = typo["field"], typo["place"]
        alias_facts = {**facts, "destination": {place["name"]}}
        proposal_target = _target(data, copied, alias_facts, facts_pending) if field == "destination.city" else None
        direction = "отправления" if field == "origin.city" else "назначения"
        actual_name = parse_destination(place["country_code"])["requested_destination"]["name"]
        ask(f"Похоже, в названии города {direction} опечатка. Вы имеете в виду город «{place['name']}», {actual_name}?",
            field, {"proposed_destination": place, "confirmation_required": True, "proposal_field": field}, proposal_target)
        meta["pending_context_clear"] = list(_CLEAR_GEO)
        note("geographic_typo_confirmation_required", field=field, original=typo["literal"], city=place["name"])
    if serbian_spelling:
        blocked = True
        place = parse_destination('Белград')['requested_destination']
        ask('Белгород — город в России. В контексте Сербии вы, возможно, имеете в виду Белград. Выбираем Белград в Сербии?',
            'destination.city', {'requested_destination': previous_destination, 'proposed_destination': place,
            'confirmation_required': True, 'proposal_field': 'destination.city'}, target)
        meta['pending_context_clear'] = list(_CLEAR_GEO)
        note('serbian_city_spelling_requires_confirmation', original='Белгород', proposed='Белград')
    elif declined_proposal:
        requested = pending.get("requested_destination")
        country_name = requested.get("name") if isinstance(requested, dict) and requested.get("kind") == "country" else None
        from .clarifications import human_question
        retained_city = (isinstance(requested, dict) and requested.get("kind") == "city"
                         and parse_destination(requested.get("name", ""))["requested_destination"] == requested)
        retained_question = human_question("destination.city", data, {"requested_destination": requested})
        if retained_city and requested['name'] in available_search_cities(data):
            country_label = parse_destination(requested['country_code'])['requested_destination']['name']
            retained_question = f"Город «{requested['name']}», {country_label}, остаётся выбранным направлением. Продолжим уточнять условия для поиска?"
        ask(retained_question if retained_city else
            f"Какой город в стране «{country_name}» вы выбираете для поездки?" if country_name else
            "Какой город назначения вы выбираете? Предложенный город отклонён.", "destination.city",
            {"requested_destination": requested} if isinstance(requested, dict) else None,
            pending.get("target_id"))
        meta["pending_context_clear"] = list(_CLEAR_GEO)
        note("destination_proposal_declined_with_independent_edit")
    elif typo is not None and typo["field"] == "destination.city":
        blocked = True
    elif facts["ambiguous"]:
        blocked = True
        if len(facts["ambiguous"]) == 1:
            location = parse_destination(next(iter(facts["ambiguous"])))
            ask(location["clarification"], "destination.city", {"requested_destination": location["requested_destination"]})
        else:
            ask("Уточните, какую страну или регион и какой город назначения вы имеете в виду.", "destination.city")
        meta["pending_context_clear"] = list(_CLEAR_GEO)
        note("ambiguous_destination_requires_clarification")
    elif catalogue_limited and _HYPOTHETICAL.search(_key(text)):
        # The exact declared command may say "only if supported". Knowing the
        # city's identity is not proof of search coverage and is not permission
        # to apply that conditional addition when coverage is absent.
        blocked = True
    elif destination:
        place = parse_destination(destination)["requested_destination"]
        actual_country = place["country_code"]
        mismatched_country = country and country != actual_country
        stale_choice = expected_country and expected_country != actual_country and not facts["replace"] and not (country == actual_country and facts["choice"])
        if mismatched_country or stale_choice:
            blocked = True
            requested = parse_destination(country or expected_country,
                available_cities=available_search_cities(data))["requested_destination"]
            # An unconfirmed alternative in the same requested country does
            # not discard the user's already chosen (possibly unsupported)
            # city. A genuinely different explicit country still supersedes
            # that old city; rejection must not resurrect an obsolete country.
            if (isinstance(expected, dict) and expected.get("kind") == "city"
                    and expected.get("country_code") == requested.get("country_code")
                    and parse_destination(expected.get("name", ""))["requested_destination"] == expected):
                requested = deepcopy(expected)
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
            if destination not in available_search_cities(data):
                country_name = parse_destination(actual_country)["requested_destination"]["name"]
                ask(f"{destination} — город в стране «{country_name}». В текущем поисковом каталоге проекта его нет; адаптеры поиска его не покрывают. Обсудим направление без поиска или выберем другой город?",
                    "destination.city", {"requested_destination": place}, target)
                note("known_destination_outside_search_catalogue", city=destination)
    elif country and facts["current"]:
        blocked = True
        place = parse_destination(country, available_cities=available_search_cities(data))["requested_destination"]
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
    blocked_ref_roles = set()
    existing = {stay.get("id"): stay for stay in data.get("stays", [])}
    for op in copied:
        kind, value = op.get("type"), op.get("value")
        if not isinstance(value, dict):
            result.append(op); continue
        if kind == "set_origin" or kind == "upsert_stay" and ("city" in value or "place_id" in value):
            role = "origin" if kind == "set_origin" else "destination"
            ref = _selected_ref(data, role, op.get("target_id"))
            supplied_name = value.get("city", value.get("origin"))
            echo = ref is not None and _same_ref_name(supplied_name, ref)
            literal = origin if role == "origin" else destination
            different_literal = literal is not None and ref is not None and not _same_ref_name(literal, ref)
            if echo and not different_literal and ("place_id" not in value or value["place_id"] == ref.place_id):
                country_conflict = _origin_ref_country_conflict(text, ref) if role == "origin" else None
                if role == "destination" and destination and _same_ref_name(destination, ref) and country and country != ref.country_iso2:
                    country_conflict = country
                if country_conflict is not None:
                    blocked_ref_roles.add(role)
                    remainder = {key: val for key, val in value.items() if key not in {"city", "origin", "place_id"}}
                    if kind == "upsert_stay" and remainder:
                        result.append({**op, "value": remainder})
                    selected_country_conflict(role, ref, op.get("target_id"), country_conflict)
                    continue
                kept = {**value, "city": ref.canonical_name, "place_id": ref.place_id}
                kept.pop("origin", None)
                result.append({**op, "value": kept})
                if role == "origin":
                    has_origin = True
                else:
                    has_destination = True
                    preserve_selected_destination_metadata(ref)
                note("selected_place_identity_echo_preserved", role=role, place_id=ref.place_id)
                continue
            if "place_id" in value and (value["place_id"] is not None or ref is not None):
                # New/other refs require an owned directory selection. Drop the
                # identity as one pair while preserving independent stay data.
                remainder = {key: val for key, val in value.items() if key not in {"city", "origin", "place_id"}}
                if kind == "upsert_stay" and remainder:
                    result.append({**op, "value": remainder})
                blocked_ref_roles.add(role)
                ask("Уточните точное место и подтвердите его выбор; географическая идентичность пока не изменена.",
                    "origin.city" if role == "origin" else "destination.city", target_id=op.get("target_id"))
                note("unowned_place_identity_change_suppressed", role=role, target_id=op.get("target_id"))
                continue
            if "place_id" in value:
                op = {**op, "value": {key: val for key, val in value.items() if key != "place_id"}}
                value = op["value"]
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
                    remainder = {key: val for key, val in value.items() if key not in {"city", "place_id"}}
                    if remainder: result.append({**op, "value": remainder})
                elif (blocked and facts["current"] and op.get("target_id") == target
                        and (meta.get("pending_context") or {}).get("field") == "destination.city"
                        and (meta.get("pending_context") or {}).get("confirmation_required") is True):
                    # The city is unresolved, but the validated dates/night
                    # requirement belongs to this exact new stop. Retain a
                    # city-less draft instead of losing independent facts.
                    remainder = {key: value[key] for key in ("check_in", "check_out", "requested_nights") if key in value}
                    if remainder:
                        result.append({**op, "value": remainder})
                        note("unresolved_destination_dates_retained", target_id=target, fields=sorted(remainder))
            continue
        result.append(op)
    if origin and not has_origin and "origin" not in blocked_ref_roles and origin != _city(data.get("origin")) and len(result) < 30:
        result.append({"type": "set_origin", "value": {"city": origin}})
        note("origin_grounded_from_current_literal", after=origin)
    if destination and not blocked and not has_destination and "destination" not in blocked_ref_roles and len(result) < 30:
        ref = _selected_ref(data, "destination", target)
        if ref is not None and _same_ref_name(destination, ref) and country and country != ref.country_iso2:
            selected_country_conflict("destination", ref, target, country)
        else:
            value = ({"city": ref.canonical_name, "place_id": ref.place_id}
                if ref is not None and _same_ref_name(destination, ref) else {"city": destination})
            if ref is not None and _same_ref_name(destination, ref):
                preserve_selected_destination_metadata(ref)
            result.append({"type": "upsert_stay", "target_id": target, "value": value})
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
