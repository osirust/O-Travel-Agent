"""Bounded replies from saved trip facts, never from model prose or prices.

This is not a general conversation/NLU engine. The caller supplies only plans
actually shown by completed runs in the authenticated session. Preview is a
suggestion, never a quote, reservation, payment, or availability guarantee.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any


def _key(text: str) -> str:
    return " ".join(text.casefold().replace("ё", "е").split())


def request_kinds(text: str) -> set[str]:
    key = _key(text)
    kinds = set()
    if ("состав" in key and any(word in key for word in ("напомни", "покажи", "текущий", "наш"))
            or re.search(r"\bкто (?:сейчас )?(?:едет|поедет)\b", key)
            or re.search(r"\b(?:будут|поедут|едут|есть) (?:ли )?(?:(?:с нами|с вами) )?дети\b", key)
            or re.search(r"\bсколько\s+(?:взрослых|детей|участников|человек)\b", key)):
        kinds.add("party")
    if (re.search(r"\bкаки[хе] (?:данных|данные|сведений|сведения).*не хватает\b", key)
            or re.search(r"\b(?:что|чего) (?:еще )?(?:не хватает|нужно уточнить|надо уточнить|осталось уточнить)\b", key)):
        kinds.add("missing_data")
    if ("ребен" in key and re.search(r"\bесли\b", key)
            and re.search(r"\b(?:пока не добавляй|не добавляй пока)\b", key)):
        kinds.add("hypothetical_child")
    if (any(word in key for word in ("демонстрацион", "демо")) or re.search(r"\bdemo\b", key)
            or "источник" in key and any(word in key for word in ("план", "вариант", "корзин", "цен", "отел"))):
        kinds.add("source")
    if "корзин" in key and any(word in key for word in ("посмотр", "покаж", "показ", "откр", "можно", "тестов", "без покупк")):
        kinds.add("cart")
    return kinds


def explicit_search_request(text: str) -> bool:
    key = _key(text)
    if (re.search(r"\b(?:если|не ищи|не подбирай|не пересобирай|пока не|не надо)\b", key)
            or re.search(r"\bне\s+(?:иск|ищ|подбира|подобра|пересобира|пересобра|обнов|поиска|най)\w*", key)):
        return False
    imperative = re.search(r"(?:^|[.!]\s*)(?:пожалуйста[, ]+)?(?:найди|поищи|подбери|пересобери|обнови (?:варианты|поиск))\b", key)
    polite = re.search(r"(?:^|[.!]\s*)(?:а\s+)?(?:(?:ты|вы)\s+)?(?:можешь|можете|можно)(?:\s+ли)?\s+"
        r"(?:пожалуйста[, ]+)?(?:найти|поискать|искать|подобрать|пересобрать|обновить)\b", key)
    future = re.search(r"(?:^|[.!]\s*)(?:(?:ты|вы)\s+)?(?:подберешь|подберете|поищешь|поищете|найдешь|найдете|пересоберешь|пересоберете|обновишь|обновите)\b", key)
    travel_target = re.search(r"\b(?:вариант|поездк|путешеств|маршрут|отел|гостиниц|билет|рейс|поиск)\w*", key)
    operative = polite or future
    ellipsis = operative and re.fullmatch(r"\s*(?:еще)?\s*[?!.]*", key[operative.end():])
    return bool(imperative or operative and (travel_target or ellipsis))


def information_noop(text: str, speech_act: str) -> bool:
    return bool(request_kinds(text) or "?" in text or speech_act in {"ASK", "PROPOSE"})


def should_search(text: str, *, changed: bool, action: str, response_source: str,
                  speech_act: str, undo: bool = False) -> bool:
    if undo:
        return False
    if changed or action == "resume" or response_source == "contextual_slot":
        return True
    if explicit_search_request(text):
        return True
    return action == "continue" and not information_noop(text, speech_act)


def _data(state: Any) -> dict:
    return state.model_dump(mode="json") if hasattr(state, "model_dump") else state if isinstance(state, dict) else {}


def _unanswered(field: str, target: str | None, data: dict, context: dict) -> bool:
    people = data.get("travelers") or []
    if field == "party.adults":
        return not any(person.get("type") == "adult" for person in people)
    if field == "party.children":
        return not context.get("children_known") and not any(person.get("type") == "child" for person in people)
    if field == "traveler.age":
        return any(person.get("type") == "child" and person.get("age") is None and not person.get("birth_date")
            and (target is None or person.get("id") == target) for person in people)
    if field == "origin.city":
        return not data.get("origin")
    if field == "destination.city":
        return not data.get("stays") or any(not stay.get("city") for stay in data["stays"])
    if field == "budget.amount_minor":
        return data.get("budget") is None
    if field == "search.guest_nationality":
        return not data.get("search_policy", {}).get("guest_nationality")
    if field == "route.kind":
        return data.get("route_policy", {}).get("kind") == "undecided"
    if field == "route.end_city":
        route = data.get("route_policy", {})
        return route.get("kind") == "open_jaw" and not route.get("end_city")
    if field == "party.removal":
        return context.get("force_clarification") is True
    if field == "group.membership":
        return (context.get("force_clarification") is True and context.get("family_separation_required") is True
                or any(group.get("keep_separate") and not group.get("traveler_ids")
                       and (target is None or group.get("id") == target) for group in data.get("groups", [])))
    if field.startswith(("stay.", "leg.")):
        collection = data.get("stays" if field.startswith("stay.") else "legs") or []
        key = field.split(".", 1)[1]
        return any(not item.get(key) and (target is None or item.get("id") == target) for item in collection)
    return False


def information_question(text: str, state: Any, unknowns: list[str], context: dict | None = None) -> dict | None:
    """Keep one unanswered model question only when the user asked that topic.

    A roster reminder is a summary, not permission to start intake. The explicit
    'who goes/are there children' and missing-data queries may retain a real
    bound clarification; unrelated/stale fields and deferred child ideas may not.
    Server-owned clarification packets are handled separately by the caller.
    """
    from .clarifications import derive_pending_field, human_question

    key, data = _key(text), _data(state)
    if "hypothetical_child" in request_kinds(text) or re.search(r"\b(?:пока не добавляй|не добавляй пока)\b", key):
        return None
    if "missing_data" in request_kinds(text):
        allowed = None
    elif re.search(r"\bкто (?:сейчас )?(?:едет|поедет)\b", key):
        allowed = {"party.adults", "party.children", "traveler.age"}
    elif re.search(r"\b(?:будут|поедут|едут|есть) (?:ли )?(?:(?:с нами|с вами) )?дети\b|\bсколько\s+детей\b", key):
        allowed = {"party.children", "traveler.age"}
    elif re.search(r"\bсколько\s+взрослых\b", key):
        allowed = {"party.adults"}
    else:
        return None
    saved = deepcopy(context) if isinstance(context, dict) else {}
    for raw in unknowns:
        bound = derive_pending_field([{"text": raw, "context": saved}], data)
        field = bound["field"]
        if (not field or allowed is not None and field not in allowed
                or not _unanswered(field, bound["target_id"], data, bound["context"])):
            continue
        return {**bound, "question": human_question({"field": field, "target_id": bound["target_id"]}, data, bound["context"])}
    return None


def current_plans(state: Any, plans: Any) -> list[dict]:
    """Retain displayed order; never relabel a later valid item as 'first'."""
    data = _data(state)
    version = data.get("state_version")
    if type(version) is not int or version < 0 or not isinstance(plans, list) or not plans:
        return []
    for plan in plans:
        if (not isinstance(plan, dict) or not isinstance(plan.get("plan_id"), str) or not plan["plan_id"]
                or not isinstance(data.get("trip_id"), str) or plan.get("trip_id") != data["trip_id"]
                or type(plan.get("state_version")) is not int or plan["state_version"] != version
                or plan.get("historical_only") or plan.get("not_bookable")
                or not isinstance(plan.get("offers"), list) or not plan["offers"]
                or any(not isinstance(offer, dict) for offer in plan["offers"])):
            return []
    return deepcopy(plans)


def _plural(number: int, forms: tuple[str, str, str]) -> str:
    position = 2 if 11 <= number % 100 <= 14 else 0 if number % 10 == 1 else 1 if 2 <= number % 10 <= 4 else 2
    return f"{number} {forms[position]}"


def _party(state: dict) -> str:
    people = state.get("travelers") or []
    adults = sum(person.get("type") == "adult" for person in people if isinstance(person, dict))
    children = [person for person in people if isinstance(person, dict) and person.get("type") == "child"]
    if not adults and not children:
        return "Состав поездки пока не указан."
    parts = [_plural(adults, ("взрослый", "взрослых", "взрослых"))]
    if children:
        ages = [_plural(person["age"], ("год", "года", "лет")) if type(person.get("age")) is int
                else "возраст не указан" for person in children]
        parts.append(_plural(len(children), ("ребёнок", "ребёнка", "детей")) + " (" + ", ".join(ages) + ")")
    else:
        parts.append("дети в сохранённом составе не указаны")
    return "Текущий состав: " + "; ".join(parts) + "."


def _selected_plan(text: str, plans: list[dict]) -> dict | None:
    key = _key(text)
    words = ("перв", "втор", "трет", "четверт", "пят", "шест", "седьм", "восьм", "девят", "десят")
    endings = r"(?:ый|ой|ий|ого|ому|ым|ом|ая|ую|ое|ые|ых|ыми|ьего|ьему|ьим|ьем|ья|ью|ье|ьи|ьих|ьими)"
    head = r"(?:план|вариант)(?:а|у|ом|е|ы|ов|ам|ами|ах)?"
    ordinal = r"(?:" + "|".join(word + endings for word in words) + r")"
    if re.search(r"\b(?:не|кроме)\s+(?:(?:" + ordinal + r"|[+-]?[0-9]+(?:-?й)?)\s+)?" + head + r"\b", key):
        return None  # A negative choice is not consent to another plan.
    # An ordinal belongs to the adjacent plan/variant, never to a child, room,
    # date, or other participant elsewhere in the message. Coordinated ordinals
    # may share one local noun: 'первый или второй план' remains ambiguous.
    references = re.findall(r"\b(" + ordinal + r")\s+" + head + r"\b", key)
    coordinated = re.findall(r"\b(" + ordinal + r"(?:\s*(?:,|или|и|либо)\s*" + ordinal + r")+)\s+" + head + r"\b", key)
    for group in coordinated:
        references.extend(re.findall(ordinal, group))
    indices = {index for index, word in enumerate(words)
               if any(re.fullmatch(word + endings, reference) for reference in references)}
    numeric = re.findall(r"\b" + head + r"\s*№?\s*([+-]?[0-9]+(?:[.,][0-9]+)?)", key)
    numeric += re.findall(r"(?<!\w)([+-]?[0-9]+(?:[.,][0-9]+)?)(?:-?(?:й|ый|ой|ий))?\s+" + head + r"\b", key)
    if any(not value.isdecimal() or int(value) < 1 for value in numeric):
        return None
    indices.update(int(value) - 1 for value in numeric)
    descriptions = {"этот", "тот", "наш", "мой", "такой", "текущий", "единственный", "выбранный", "актуальный",
                    "демонстрационный", "тестовый", "готовый", "предложенный", "показанный", "сохраненный", "доступный", "оценочный"}
    for previous in re.findall(r"\b([а-я-]+)\s+" + head + r"\b", key):
        if (re.search(endings + r"$", previous) and previous not in descriptions
                and not any(re.fullmatch(word + endings, previous) for word in words)):
            return None  # An unsupported explicit ordinal must not mean 'first'.
    if len(indices) == 1:
        index = indices.pop()
        return plans[index] if index < len(plans) else None
    return plans[0] if len(plans) == 1 and not indices else None


def _cart_action_requested(text: str) -> bool:
    """A capability/hypothesis/refusal is not a request to open this cart."""
    key = _key(text)
    if re.search(r"\b(?:если|допустим|предположим)\b", key):
        return False
    if (re.search(r"\bне\s+(?:откр|показ|покаж|смотр|просмотр)\w*", key)
            or re.search(r"\bне\s+(?:надо|нужно|стоит|хочу|хотим)\s+(?:\w+\s+){0,2}(?:откр|показ|смотр|просмотр)\w*", key)
            or re.search(r"\bбез\s+(?:открытия|просмотра)\b", key)
            or re.search(r"\b(?:откр|показ|смотр|просмотр)\w*\s+(?:\w+\s+){0,2}не\s+(?:надо|нужно|стоит)\b", key)):
        return False
    return bool(re.search(r"\b(?:посмотр|покаж|показ|откр|просмотр|смотр)\w*", key))


def _is_demo(plan: dict) -> bool:
    return plan.get("source_type") == "DEMO" and all(
        offer.get("source_type") == "DEMO" and offer.get("is_demo") is True
        and offer.get("price_kind") == "synthetic" for offer in plan["offers"])


def render_response(text: str, state: Any, plans: Any, *, changed: bool = False,
                    party_changed: bool = False) -> dict | None:
    kinds = request_kinds(text)
    data = _data(state)
    parts = []
    if "hypothetical_child" in kinds and not changed:
        parts.append("Состав и поездка не изменены: предложенное добавление ребёнка пока не применялось. Если позже добавите его, поездку можно будет пересобрать. Цены и наличие для нового состава сейчас не проверялись.")
    if kinds.intersection({"party", "missing_data"}) or party_changed:
        parts.append(_party(data))
    action = None
    if kinds.intersection({"source", "cart"}):
        valid = current_plans(data, plans)
        selected = _selected_plan(text, valid)
        cart_action = "cart" in kinds and _cart_action_requested(text)
        if not valid:
            parts.append("Актуального показанного плана для этой версии поездки нет; его источник, цены и наличие сейчас не подтверждены.")
            if cart_action:
                parts.append("Для просмотра корзины сначала нужен новый поиск и выбор варианта.")
        elif selected is None:
            if "source" in kinds:
                parts.append("Показанные актуальные планы демонстрационные: цены и наличие вымышлены."
                    if all(_is_demo(plan) for plan in valid) else
                    "Показанные планы включают компоненты других или неуказанных источников; нельзя считать их все демонстрационными. Цены и наличие требуют новой проверки.")
            if cart_action:
                parts.append("Уточните, какой из показанных планов открыть. Предварительный просмотр корзины не оформляет заказ и не выполняет оплату.")
            elif "cart" in kinds and "source" not in kinds:
                parts.append("Предварительный просмотр корзины возможен после явного выбора актуального плана; сейчас открытие не предлагается. Просмотр не оформляет заказ и не выполняет оплату.")
        else:
            demo = _is_demo(selected)
            if demo:
                parts.append("Этот план демонстрационный: цены и наличие вымышлены.")
            else:
                parts.append("Источник каждого компонента указан в плане; весь вариант нельзя считать демонстрационным. Цены и наличие требуют новой проверки.")
            if cart_action:
                parts.append("Можно открыть тестовую корзину через предварительный просмотр, без покупки и реального бронирования."
                    if demo else "Можно запросить предварительный просмотр без оформления заказа и оплаты.")
                action = {"type": "preview_cart", "plan_id": selected["plan_id"], "state_version": data["state_version"]}
            elif "cart" in kinds and "source" not in kinds:
                parts.append("Предварительный просмотр можно запросить после явного выбора; сейчас открытие не предлагается.")
    if not parts:
        return None
    answer = {"reply": " ".join(parts), "kinds": sorted(kinds | ({"party"} if party_changed else set()))}
    if action is not None:
        answer["suggested_action"] = action
    return answer
