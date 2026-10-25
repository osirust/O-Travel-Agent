"""Human questions and bounded answers to an already established typed slot.

This is not an intent extractor. It never chooses a slot from an answer, expands
the search catalogue, or applies operations. Unsupported places stay metadata.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from .domain import CITIES, MVP_LIMITS
from .geography import parse_destination


_ALIASES = {
    "origin": "origin.city", "origin_city": "origin.city", "origin.city": "origin.city",
    "missing_origin": "origin.city", "город отправления": "origin.city", "город вылета": "origin.city",
    "destination": "destination.city", "destination_city": "destination.city", "destination.city": "destination.city",
    "city": "destination.city", "missing_city": "destination.city", "missing_stays": "destination.city",
    "stay.city": "destination.city", "город назначения": "destination.city",
    "party": "party.adults", "travelers": "party.adults", "adults": "party.adults",
    "party.adults": "party.adults", "party_adults": "party.adults", "missing_travelers": "party.adults",
    "adult_count": "party.adults", "children": "party.children", "party.children": "party.children",
    "children_count": "party.children", "party_children": "party.children",
    "child_age": "traveler.age", "traveler.age": "traveler.age", "missing_child_age": "traveler.age",
    "check_in": "stay.check_in", "checkin": "stay.check_in", "stay.check_in": "stay.check_in",
    "missing_check_in": "stay.check_in", "дата заезда": "stay.check_in",
    "check_out": "stay.check_out", "checkout": "stay.check_out", "stay.check_out": "stay.check_out",
    "missing_check_out": "stay.check_out", "дата выезда": "stay.check_out",
    "departure_date": "leg.departure_date", "leg.departure_date": "leg.departure_date",
    "дата вылета": "leg.departure_date", "дата отправления": "leg.departure_date",
    "rooms": "stay.rooms", "stay.rooms": "stay.rooms", "missing_rooms": "stay.rooms",
    "budget": "budget.amount_minor", "budget.amount_minor": "budget.amount_minor",
    "budget_amount": "budget.amount_minor", "бюджет": "budget.amount_minor",
}
_COUNTRY_NAMES = {"RS": "Сербия", "RU": "Россия", "TR": "Турция", "AE": "ОАЭ"}
_ORIGIN_FORMS = {"москвы": "Москва", "санкт-петербурга": "Санкт-Петербург",
                 "сочи": "Сочи", "казани": "Казань", "стамбула": "Стамбул",
                 "антальи": "Анталья", "дубая": "Дубай", "белграда": "Белград", "нови-сада": "Нови-Сад",
                 "белгорода": "Белгород"}
_YES = {"да", "верно", "правильно", "подтверждаю", "да выбираем", "да меняем"}
_NO = {"нет", "не надо", "не меняем"}
_AGE_WORDS = {
    "ноль": 0, "нуль": 0, "один": 1, "два": 2, "три": 3, "четыре": 4,
    "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
    "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17,
}
_AGE_ANSWER = re.compile(
    r"(?:(?:ему|ей|ребенку) )?(?:будет )?"
    r"(?P<age>[0-9]{1,2}|" + "|".join(sorted(_AGE_WORDS, key=len, reverse=True)) + r")"
    r"(?:\s*(?:лет|год|года))?(?: на дату (?:поездки|выезда))?"
    r"(?:(?:[.!] *|, *| )это единственное уточнение)?"
)


def _data(value: Any) -> dict:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else deepcopy(value) if isinstance(value, dict) else {}


def _key(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split()).strip(" .!?,:;«»\"'")


def _age_answer(text: str) -> int | None:
    """A closed answer grammar, used only for an already bound child-age slot.

    Full-match deliberately excludes extra edits, another named child, negation,
    hypotheticals and ranges. A question is not an asserted age. The optional
    acknowledgement does not expand the grammar into general intent extraction.
    """
    if "?" in text:
        return None
    match = _AGE_ANSWER.fullmatch(_key(text))
    if match is None:
        return None
    value = match["age"]
    age = int(value) if value.isascii() and value.isdigit() else _AGE_WORDS[value]
    return age if 0 <= age < 18 else None


def _field(raw: Any) -> str | None:
    if isinstance(raw, dict):
        for name in ("field", "code", "text", "message"):
            result = _field(raw.get(name))
            if result:
                return result
        return None
    if not isinstance(raw, str):
        return None
    key = _key(raw)
    if key in _ALIASES:
        return _ALIASES[key]
    # Render known paths even when surrounded by a short model explanation.
    for alias in sorted(_ALIASES, key=len, reverse=True):
        if ("." in alias or "_" in alias) and re.search(r"(?<![\w.])" + re.escape(alias) + r"(?![\w.])", key):
            return _ALIASES[alias]
    if re.search(r"из какого города|откуда (?:выезжа|вылета|отправля)|город.*отправления", key):
        return "origin.city"
    if re.search(r"сколько лет|возраст.*ребен|ребен.*возраст", key):
        return "traveler.age"
    if re.search(r"кто (?:поедет|едет)|сколько.*взросл|состав.*(?:групп|участник)", key):
        return "party.adults"
    if re.search(r"сколько.*дет|поедут ли.*дет|дети.*поедут", key):
        return "party.children"
    if re.search(r"какой город|какие города|куда (?:хотите|поед|ед)|город.*назначения", key):
        return "destination.city"
    if re.search(r"дат[ауые].*(?:заезд|прибыт)|когда.*заез", key):
        return "stay.check_in"
    if re.search(r"дат[ауые].*(?:выезд|возвращ)|когда.*возвращ", key):
        return "stay.check_out"
    if re.search(r"дат[ауые].*(?:вылет|отправл)", key):
        return "leg.departure_date"
    if re.search(r"сколько.*номер|количество.*номер", key):
        return "stay.rooms"
    if re.search(r"бюджет|денежный лимит", key):
        return "budget.amount_minor"
    return None


def question_field(raw: Any) -> str | None:
    """Public slot classifier; no answer inference or mutation."""
    return _field(raw)


def _context(raw: Any, extra: dict | None = None) -> dict:
    result = {}
    if isinstance(raw, dict):
        if isinstance(raw.get("context"), dict):
            result.update(deepcopy(raw["context"]))
        if isinstance(raw.get("details"), dict):
            result.update(deepcopy(raw["details"]))
        for name in ("target_id", "stay_id", "traveler_id", "leg_id", "requested_destination", "requested_origin",
                     "proposed_destination", "confirmation_required", "children_known", "children_count", "force_clarification", "contextual_correction"):
            if name in raw:
                result[name] = deepcopy(raw[name])
    if isinstance(extra, dict):
        result.update(deepcopy(extra))
    return result


def _target(field: str | None, state: dict, context: dict) -> str | None:
    explicit = context.get("target_id") or context.get("traveler_id" if field == "traveler.age" else "leg_id" if field == "leg.departure_date" else "stay_id")
    if explicit:
        return str(explicit)
    if field == "traveler.age":
        candidates = [person for person in state.get("travelers", []) if person.get("type") == "child" and person.get("age") is None and not person.get("birth_date")]
    elif field == "leg.departure_date":
        candidates = [leg for leg in state.get("legs", []) if not leg.get("departure_date")]
    elif field == "destination.city":
        candidates = [stay for stay in state.get("stays", []) if not stay.get("city")]
        if not candidates and len(state.get("stays", [])) == 1:
            candidates = state["stays"]
    elif field and field.startswith("stay."):
        key = field.split(".")[1]
        candidates = [stay for stay in state.get("stays", []) if not stay.get(key)]
    else:
        candidates = []
    return candidates[0]["id"] if len(candidates) == 1 else None


def human_question(rawfield: Any, state: Any, context: dict | None = None) -> str:
    """Render a complete Russian question; machine field paths never reach UI."""
    data, field = _data(state), _field(rawfield)
    ctx = _context(rawfield, context)
    target = _target(field, data, ctx)
    if field == "origin.city":
        return "Из какого города вы отправляетесь?"
    if field == "destination.city":
        destination = ctx.get("requested_destination", {})
        if isinstance(destination, dict) and destination.get("kind") == "country":
            country = destination.get("name") or _COUNTRY_NAMES.get(destination.get("country_code"))
            if country:
                return f"Какой город в стране «{country}» вы выбираете для поездки?"
        if isinstance(destination, dict) and destination.get("kind") == "city" and isinstance(destination.get("name"), str):
            known = parse_destination(destination["name"])["requested_destination"]
            if known["kind"] == "city" and known["name"] not in CITIES:
                return _unsupported_city_question(known, ctx)
        return "В какой город вы хотите поехать?"
    if field == "party.adults":
        return "Сколько взрослых поедет в поездку?"
    if field == "party.children":
        return "Поедут ли с вами дети? Если да, сколько их будет?"
    if field == "traveler.age":
        children = [p for p in data.get("travelers", []) if p.get("type") == "child"]
        if len(children) > 1 and target:
            index = next((i + 1 for i, p in enumerate(children) if p["id"] == target), None)
            if index:
                return f"Сколько лет будет ребёнку №{index} на дату поездки?"
        return "Сколько лет будет ребёнку на дату поездки?"
    if field in {"stay.check_in", "stay.check_out", "stay.rooms"}:
        stay = next((s for s in data.get("stays", []) if s["id"] == target), {})
        place = f" в городе «{stay['city']}»" if stay.get("city") else ""
        return {"stay.check_in": f"На какую дату планируете заезд{place}?",
                "stay.check_out": f"На какую дату планируете выезд{place}?",
                "stay.rooms": f"Сколько гостиничных номеров вам нужно{place}?"}[field]
    if field == "leg.departure_date":
        leg = next((x for x in data.get("legs", []) if x["id"] == target), {})
        route = f" по маршруту «{leg['origin']} — {leg['destination']}»" if leg.get("origin") and leg.get("destination") else ""
        return f"На какую дату планируете отправление{route}?"
    if field == "budget.amount_minor":
        return "Какой общий бюджет поездки в рублях вы хотите установить?"
    text = rawfield.get("message", rawfield.get("text", "")) if isinstance(rawfield, dict) else rawfield
    # Existing natural-language questions may contain details this module cannot infer.
    if isinstance(text, str) and "?" in text and len(text.split()) >= 3 and re.search("[А-Яа-яЁё]", text) and not re.search(r"\b\w+[._]\w+\b", text):
        return text.strip()
    return "Что нужно уточнить о поездке, чтобы продолжить?"


def derive_pending_field(raw_questions: Any, state: Any) -> dict:
    """Bind the first unanswered known question to a stable slot and exact ID.

    No target is invented if two children/stays could match. Explicit metadata
    wins over question wording. An exhausted/stale question list has field=None.
    """
    data = _data(state)
    questions = raw_questions if isinstance(raw_questions, (list, tuple)) else [raw_questions]
    for raw in questions:
        field, ctx = _field(raw), _context(raw)
        if not field:
            continue
        if field == "origin.city" and data.get("origin"):
            continue
        if field == "budget.amount_minor" and data.get("budget") is not None and ctx.get("force_clarification") is not True:
            continue
        if field == "party.adults" and any(p.get("type") == "adult" for p in data.get("travelers", [])):
            if ctx.get("children_known") or any(p.get("type") == "child" for p in data.get("travelers", [])):
                continue
            field = "party.children"
        if field == "party.children" and ctx.get("children_known"):
            continue
        target = _target(field, data, ctx)
        collection, key = ("travelers", "age") if field == "traveler.age" else ("legs", "departure_date") if field == "leg.departure_date" else ("stays", "city" if field == "destination.city" else field.split(".")[-1])
        item = next((x for x in data.get(collection, []) if x["id"] == target), None)
        if field == "traveler.age" and item and (item.get("age") is not None or item.get("birth_date") or item.get("type") != "child"):
            continue
        if field == "destination.city" and item and item.get(key) and not ctx.get("requested_destination"):
            continue
        if field.startswith(("stay.", "leg.")) and item and item.get(key):
            continue
        return {"field": field, "target_id": target, "context": ctx}
    return {"field": None, "target_id": None, "context": {}}


def _answer(operations=None, *, question=None, context=None, notice=None) -> dict:
    result = {"handled": True, "operations": operations or []}
    if question:
        result["question"] = question
    if context is not None:
        result["pending_context"] = context
    if notice:
        result["notice"] = notice
    return result


def _new_id(prefix: str, items: list[dict]) -> str:
    used = {item["id"] for item in items}
    index = 1
    while f"{prefix}-{index}" in used:
        index += 1
    return f"{prefix}-{index}"


def _one_edit(left: str, right: str) -> bool:
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) == 1
    if len(left) > len(right):
        left, right = right, left
    return any(right[:i] + right[i + 1:] == left for i in range(len(right)))


def _answer_place(text: str, field: str) -> dict:
    candidate = text
    if field == "origin.city":
        match = re.fullmatch(r"из\s+(.+)", _key(text))
        if match:
            candidate = _ORIGIN_FORMS.get(match[1], match[1])
    return parse_destination(candidate)["requested_destination"]


def _asserts_proposed_geography(text: str, proposed: dict) -> bool:
    """Only a named proposed city + a recognised country, not arbitrary chat."""
    match = re.fullmatch(r"(.+?)\s+(?:это(?:\s+же)?|находится в|в)\s+(.+)", _key(text))
    if not match or proposed.get("kind") != "city":
        return False
    city = parse_destination(match[1])["requested_destination"]
    country = parse_destination(match[2])["requested_destination"]
    return city.get("kind") == "city" and city.get("name") == proposed.get("name") and country.get("kind") == "country"


def _country_change_question(place: dict, field: str, ctx: dict) -> str:
    country = _COUNTRY_NAMES.get(place.get("country_code"), place.get("country_code"))
    previous = ctx.get("requested_origin" if field == "origin.city" else "requested_destination")
    old_country = (_COUNTRY_NAMES.get(previous.get("country_code"), previous.get("name"))
                   if isinstance(previous, dict) else None)
    direction = "отправления" if field == "origin.city" else "назначения"
    earlier = f" Ранее обсуждали страну «{old_country}»." if old_country else ""
    distinction = (" Есть похожее название «Белград» — это город в стране «Сербия»."
                   if field == "destination.city" and place["name"] == "Белгород" and isinstance(previous, dict) and previous.get("country_code") == "RS" else "")
    return (f"Город «{place['name']}» находится в стране «{country}».{distinction}{earlier} "
            f"Меняем страну {direction} на «{country}» и выбираем этот город?")


def _contextual_notice(place: dict, context: dict) -> str | None:
    correction = context.get("contextual_correction")
    if isinstance(correction, dict) and correction.get("interpreted_city") == place.get("name") and correction.get("country_code") == place.get("country_code"):
        country = _COUNTRY_NAMES.get(place["country_code"], place["country_code"])
        return f"Понял как «{place['name']}», {country}."
    return None


def _unsupported_city_question(place: dict, context: dict | None = None) -> str:
    country = _COUNTRY_NAMES.get(place.get("country_code"), place.get("country_code"))
    notice = _contextual_notice(place, context or {})
    prefix = f"{notice} " if notice else ""
    return (f"{prefix}Город «{place['name']}» находится в стране «{country}», но пока не входит в каталог поиска. "
            "Сохраняем это направление без поиска или вы хотите его изменить?")


def _city_answer(data: dict, field: str, target: str | None, ctx: dict, text: str) -> dict:
    requested_key = "requested_origin" if field == "origin.city" else "requested_destination"
    proposed = ctx.get("proposed_destination")
    if ctx.get("confirmation_required") and isinstance(proposed, dict):
        if _key(text) in _YES:
            place = proposed
            ctx = {**ctx, requested_key: proposed, "confirmation_required": False}
        elif _key(text) in _NO:
            clean = {k: v for k, v in ctx.items() if k not in {"proposed_destination", "confirmation_required"}}
            return _answer(question=human_question(field, data, clean), context={**clean, "field": field, "target_id": target})
        else:
            if _asserts_proposed_geography(text, proposed):
                # A factual observation/question is not consent to change a route.
                verified = parse_destination(proposed.get("name", ""))["requested_destination"]
                return _answer(question=_country_change_question(verified, field, ctx),
                               context={**ctx, "field": field, "target_id": target})
            place = _answer_place(text, field)
            if place["kind"] == "unknown":
                return {"handled": False, "operations": []}
    else:
        place = _answer_place(text, field)
    requested = ctx.get(requested_key)
    # Owner policy v2: only a bare name inside an explicit Serbian destination
    # context may be read as the similarly named Serbian city. This deliberately
    # does not change the geographic registry or rewrite explicit Russian input.
    contextual_correction = (field == "destination.city" and isinstance(requested, dict)
                             and requested.get("country_code") == "RS"
                             and requested.get("kind") in {"country", "city"}
                             and text.strip().casefold() == "белгород")
    if contextual_correction:
        place = parse_destination("Белград")["requested_destination"]
        ctx = {**ctx, "contextual_correction": {"original_text": text, "interpreted_city": "Белград",
                                               "country_code": "RS", "basis": "requested_country"}}
    elif "contextual_correction" in ctx:
        ctx = {key: value for key, value in ctx.items() if key != "contextual_correction"}
    if place["kind"] == "country":
        return _answer(question=human_question(field, data, {"requested_destination": place}),
                       context={**ctx, "field": field, "target_id": target, requested_key: place})
    if place["kind"] != "city":
        suggestions = requested.get("suggested_cities", []) if isinstance(requested, dict) else []
        if field == "destination.city":
            suggestions = ctx.get("suggested_cities", suggestions)
        candidates = []
        for suggestion in suggestions if isinstance(suggestions, list) else []:
            name = suggestion.get("name") if isinstance(suggestion, dict) else suggestion
            if isinstance(name, str) and len(_key(text)) >= 4 and _one_edit(_key(text), _key(name)):
                candidate = parse_destination(name)["requested_destination"]
                if candidate["kind"] == "city" and candidate not in candidates:
                    candidates.append(candidate)
        if len(candidates) == 1:
            place = candidates[0]
            return _answer(question=f"Вы имеете в виду город «{place['name']}»?",
                           context={**ctx, "field": field, "target_id": target, "proposed_destination": place, "confirmation_required": True})
        if place["kind"] == "unknown":
            return {"handled": False, "operations": []}
        return _answer(question=human_question(field, data, ctx), context={**ctx, "field": field, "target_id": target})
    old_country = requested.get("country_code") if isinstance(requested, dict) else None
    new_country = place.get("country_code")
    if (old_country and old_country != new_country) or text.rstrip().endswith("?"):
        question = (_country_change_question(place, field, ctx)
                    if old_country and old_country != new_country else f"Выбираем город «{place['name']}» для поездки?")
        return _answer(question=question, context={**ctx, "field": field, "target_id": target,
                       "proposed_destination": place, "confirmation_required": True})
    if place["name"] not in CITIES:
        # A new explicit city supersedes an earlier proposed alternative. Keep
        # the choice as metadata, without inventing coverage or forcing a route.
        clean = {key: value for key, value in ctx.items() if key not in {"proposed_destination", "confirmation_required"}}
        return _answer(question=_unsupported_city_question(place, clean),
                       context={**clean, "field": field, "target_id": target, requested_key: place},
                       notice=_contextual_notice(place, clean))
    if field == "origin.city":
        return _answer([{"type": "set_origin", "value": {"city": place["name"]}}])
    stays = data.get("stays", [])
    if target is not None and not any(s["id"] == target for s in stays):
        if stays:
            return _answer(question="Для какой остановки нужно изменить город назначения?", context={**ctx, "field": field})
    elif target is None:
        if len(stays) > 1:
            return _answer(question="Для какой остановки нужно изменить город назначения?", context={**ctx, "field": field})
        target = stays[0]["id"] if stays else _new_id("stay", stays)
    # A committed destination replaces display metadata as well as the stay.
    # Return only metadata here: the next question must derive its own binding.
    clean = {key: value for key, value in ctx.items()
             if key not in {"field", "target_id", "stay_id", "traveler_id", "leg_id",
                            "proposed_destination", "confirmation_required", "suggested_cities"}}
    return _answer([{"type": "upsert_stay", "target_id": target, "value": {"city": place["name"]}}],
                   context={**clean, "requested_destination": place})


def resolve_short_answer(state: Any, pending_question: Any, text: str) -> dict:
    """Resolve only unambiguous short answers to a supplied pending slot.

    Return operations for the caller to validate atomically. ``pending_context``
    is clarification metadata, never a TripState patch. No implicit booking,
    dates, ages of adults, country-to-city mapping or unrelated edits are made.
    """
    unhandled = {"handled": False, "operations": []}
    if not isinstance(text, str) or not text.strip() or len(text) > 100 or ";" in text or "\n" in text:
        return unhandled
    data, field = _data(state), _field(pending_question)
    ctx = _context(pending_question)
    if not field:
        return unhandled
    if field != "traveler.age" and len(text.split()) > 7:
        return unhandled
    target, key = _target(field, data, ctx), _key(text)
    if field in {"origin.city", "destination.city"}:
        if re.search(r"\d|бюджет|потом|подожди|отмен|заброни|добавь", key):
            return unhandled
        return _city_answer(data, field, target, ctx, text.strip())
    if field == "traveler.age":
        age = _age_answer(text)
        if age is None:
            return unhandled
        # A supplied answer never chooses a person, even when only one age is
        # missing. The persisted question must already carry its stable target.
        target = ctx.get("target_id") or ctx.get("traveler_id")
        child = next((p for p in data.get("travelers", []) if p["id"] == target and p.get("type") == "child"), None)
        if child is None or child.get("birth_date") or child.get("age") is not None:
            return _answer(question=human_question(field, data, ctx), context={**ctx, "field": field, "target_id": target})
        operations = [{"type": "update_traveler", "target_id": target, "value": {"age": age}}]
        remaining = [p for p in data.get("travelers", []) if p["id"] != target and p.get("type") == "child" and p.get("age") is None and not p.get("birth_date")]
        if remaining:
            next_context = {"field": field, "target_id": remaining[0]["id"], "children_known": True}
            return _answer(operations, question=human_question(field, data, next_context), context=next_context)
        return _answer(operations)
    if field not in {"party.adults", "party.children"}:
        return unhandled
    adults = field == "party.adults"
    if adults and key in {"я взрослый", "я взрослая", "я один", "я одна", "один", "одна"}:
        count = 1
    elif not adults and key in _NO | {"без детей", "детей нет", "не будет", "нет детей"}:
        count = 0
    elif re.fullmatch(r"\d{1,2}", key):
        count = int(key)
    else:
        return unhandled
    limit = MVP_LIMITS["travelers"]
    if count < (1 if adults else 0) or count > limit:
        return _answer(question=human_question(field, data, ctx), context={**ctx, "field": field})
    kind = "adult" if adults else "child"
    travelers = deepcopy(data.get("travelers", []))
    existing = [p for p in travelers if p.get("type") == kind]
    if len(existing) > count or len(travelers) + count - len(existing) > limit:
        return _answer(question="Состав участников уже сохранён. Кого именно нужно добавить или убрать?", context={**ctx, "field": field})
    operations = []
    for _ in range(count - len(existing)):
        person = {"id": _new_id("traveler-" + kind, travelers), "type": kind}
        travelers.append(person)
        operations.append({"type": "add_traveler", "value": person})
    children = [p for p in travelers if p.get("type") == "child"]
    unknown_age = [p for p in children if p.get("age") is None and not p.get("birth_date")]
    if unknown_age:
        next_context = {"field": "traveler.age", "target_id": unknown_age[0]["id"], "children_known": True, "children_count": len(children)}
        return _answer(operations, question=human_question("traveler.age", {**data, "travelers": travelers}, next_context), context=next_context)
    if adults and not children and not ctx.get("children_known"):
        next_context = {"field": "party.children", "children_known": False}
        return _answer(operations, question=human_question("party.children", data), context=next_context)
    return _answer(operations, context={"children_known": True, "children_count": len(children)})
