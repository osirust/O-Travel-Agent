"""Bound read-only answers from canonical saved facts; no clock, LLM or tools.

This is deliberately not free-form NLG. Mixed edits, search/booking requests,
hypotheses and multi-topic questions remain with the caller's normal pipeline.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date
from decimal import Decimal, localcontext
from typing import Any

from .conversational import _selected_plan, current_plans, explicit_search_request
from .domain import DomainError, canonical_city

_KINDS = {"budget", "dates", "route", "why_plan"}
_MODIFIERS = r"(?:(?:у нас|у меня|сейчас|наш|мой|сохраненный|текущий|общий|заданный|нашей|моей)\s+)*"
_TRIP = r"(?:\s+(?:поездки|путешествия|на поездку|на всю поездку))?"
_REMINDER = re.compile(
    r"^(?:(?:ты |вы )?(?:можешь|можете)(?: ли)?\s+(?:напомнить|показать|сказать)|напомни|покажи|скажи)\s+"
)
_BUDGET_QUESTION = re.compile(
    rf"(?:какой|каков)\s+{_MODIFIERS}бюджет{_TRIP}(?:\s+(?:сейчас|у нас|у меня|сохранен|задан))?"
)
_DATE_QUESTIONS = (
    re.compile(rf"какие\s+{_MODIFIERS}даты{_TRIP}(?:\s+у нас)?"),
    re.compile(rf"на какие даты\s+{_MODIFIERS}(?:поездка|путешествие)"),
    re.compile(r"когда\s+(?:у нас\s+)?(?:заезд и выезд|заезд|выезд|начало и конец поездки)"),
    re.compile(r"когда (?:мы )?(?:едем|поедем|возвращаемся|уезжаем)"),
    re.compile(r"(?:на )?сколько ночей(?: у нас| мы выбрали| выбрали| поездка| планируем)?"),
)
_ROUTE_QUESTIONS = (
    re.compile(rf"какой\s+{_MODIFIERS}маршрут{_TRIP}"),
    re.compile(r"(?:куда|откуда) (?:мы )?(?:едем|поедем|отправляемся)"),
    re.compile(r"какие города (?:у нас )?(?:в поездке|в маршруте|в плане)"),
)
_HEAD = r"(?:план|вариант)(?:а|у|ом|е|ы|ов)?"
_REF_WORD = r"(?:этот|наш|текущий|выбранный|предложенный|показанный|[а-я-]+(?:ый|ой|ий|ого|ому|ым|ом))"
_REFERENCE = rf"(?:(?:именно\s+)?{_REF_WORD}(?:\s*(?:,|и|или|либо)\s*{_REF_WORD})*\s+|[+-]?[0-9]+(?:[.,][0-9]+)?-?(?:й|ый|ой|ий)?\s+)?{_HEAD}(?:\s*№?\s*[+-]?[0-9]+(?:[.,][0-9]+)?)?"
_WHY = re.compile(
    rf"(?:почему\s+(?:(?:ты|вы)\s+)?(?:(?:выбрал|выбрала|выбрали|выбран|предложил|предложила|предложили|предложен|подобрал|подобрали)\s+)?"
    rf"{_REFERENCE}(?:\s+(?:подходит(?: под наши пожелания)?|лучше|выгоднее|самый дешевый))?"
    rf"|(?:объясни|поясни|расскажи про|расскажи о)\s+{_REFERENCE})"
)
_HYPOTHESIS = re.compile(r"\b(?:если|бы|допустим|предположим|возможно|может)\b")
_ZERO_DECIMAL = {"JPY", "KRW", "VND", "CLP", "ISK"}
_THREE_DECIMAL = {"BHD", "JOD", "KWD", "OMR", "TND"}
_TWO_DECIMAL = {"RUB", "USD", "EUR", "GBP", "TRY", "AED", "CNY", "CHF", "BYN", "KZT", "INR", "THB"}


def _key(text: str) -> str:
    return " ".join(text.casefold().replace("ё", "е").split()).strip()


def classify_saved_question(text: Any) -> str | None:
    """Recognize one complete bounded reminder/question, never a partial match."""
    if not isinstance(text, str) or not 0 < len(text) <= 500:
        return None
    key = _key(text).rstrip(" .?!")
    key = re.sub(r"^(?:а|ну)\s+", "", key, count=1)
    key = re.sub(r"^пожалуйста[, ]+", "", key, count=1)
    key = re.sub(r",?\s+пожалуйста$", "", key, count=1)
    if _HYPOTHESIS.search(key) or explicit_search_request(text):
        return None
    reminder = _REMINDER.match(key)
    query = key[reminder.end() :] if reminder else key
    query = re.sub(r"^(?:пожалуйста[, ]+)", "", query, count=1)
    if _BUDGET_QUESTION.fullmatch(query) or reminder and re.fullmatch(rf"{_MODIFIERS}бюджет{_TRIP}", query):
        return "budget"
    if (
        any(pattern.fullmatch(query) for pattern in _DATE_QUESTIONS)
        or reminder
        and re.fullmatch(rf"{_MODIFIERS}(?:даты|даты заезда и выезда){_TRIP}", query)
    ):
        return "dates"
    if (
        any(pattern.fullmatch(query) for pattern in _ROUTE_QUESTIONS)
        or reminder
        and re.fullmatch(rf"{_MODIFIERS}маршрут{_TRIP}", query)
    ):
        return "route"
    if _WHY.fullmatch(key):
        return "why_plan"
    return None


def _data(state: Any) -> dict:
    return (
        state.model_dump(mode="json")
        if hasattr(state, "model_dump")
        else state
        if isinstance(state, dict)
        else {}
    )


def _money(minor: Any, currency: Any) -> str | None:
    if (
        type(minor) is not int
        or minor < 0
        or not isinstance(currency, str)
        or currency not in _ZERO_DECIMAL | _THREE_DECIMAL | _TWO_DECIMAL
    ):
        return None
    places = 0 if currency in _ZERO_DECIMAL else 3 if currency in _THREE_DECIMAL else 2
    with localcontext() as ctx:
        ctx.prec = max(28, len(str(minor)) + places + 2)
        value = Decimal(minor).scaleb(-places)
        return f"{value:,.{places}f}".replace(",", " ").replace(".", ",") + " " + currency


def _day(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _date_text(value: Any) -> str:
    day = _day(value)
    return day.strftime("%d.%m.%Y") if day else "не указан"


def _city(value: Any) -> str:
    try:
        return canonical_city(value)
    except DomainError:
        return "город не указан"


def _plural(number: int, forms: tuple[str, str, str]) -> str:
    index = 2 if 11 <= number % 100 <= 14 else 0 if number % 10 == 1 else 1 if 2 <= number % 10 <= 4 else 2
    return f"{number} {forms[index]}"


def _child_age(person: dict, day: date | None) -> str:
    if person.get("birth_date") is not None:
        birth = _day(person["birth_date"])
        if not birth or not day or birth > day:
            return "возраст на поездку не определён"
        years = day.year - birth.year - ((day.month, day.day) < (birth.month, birth.day))
    elif type(person.get("age")) is int and 0 <= person["age"] < 18:
        years = person["age"]
    else:
        return "возраст не указан"
    return _plural(years, ("год", "года", "лет"))


def _budget(data: dict) -> str:
    budget = data.get("budget")
    if not isinstance(budget, dict):
        return "Бюджет поездки пока не указан."
    amount = _money(budget.get("amount_minor"), budget.get("currency"))
    if amount is None or budget.get("amount_minor") == 0:
        return "Сохранённая сумма бюджета не подтверждена."
    scope = " на всю поездку" if budget.get("scope", "whole_trip") == "whole_trip" else ""
    return f"Сохранённый бюджет{scope}: {amount}." + (
        " Область применения бюджета не подтверждена." if not scope else ""
    )


def _dates(data: dict) -> str:
    stays, legs = data.get("stays") or [], data.get("legs") or []
    if not stays and not legs:
        return "Даты поездки пока не указаны."
    children = [p for p in data.get("travelers", []) if isinstance(p, dict) and p.get("type") == "child"]
    parts = []
    for index, stay in enumerate(stays, 1):
        start, end = _day(stay.get("check_in")), _day(stay.get("check_out"))
        line = f"Остановка {index}, {_city(stay.get('city'))}: заезд {_date_text(stay.get('check_in'))}, выезд {_date_text(stay.get('check_out'))}"
        if start and end and end > start:
            line += "; " + _plural((end - start).days, ("ночь", "ночи", "ночей"))
        elif start and end:
            line += "; порядок дат требует уточнения"
        elif type(stay.get("requested_nights")) is int and stay["requested_nights"] > 0:
            line += "; запрошено " + _plural(stay["requested_nights"], ("ночь", "ночи", "ночей"))
        if children:
            line += ". Дети на дату заезда: " + ", ".join(_child_age(p, start) for p in children)
        parts.append(line + ".")
    for index, leg in enumerate(legs, 1):
        parts.append(
            f"Переезд {index}, {_city(leg.get('origin'))} → {_city(leg.get('destination'))}: "
            f"отправление {_date_text(leg.get('departure_date'))}."
        )
    return " ".join(parts)


def _return_date(data: dict) -> str:
    legs = data.get("legs") or []
    if not data.get("origin") or not legs or legs[-1].get("destination") != data["origin"]:
        return (
            "Обратное плечо к месту отправления не задано; дату возвращения не определяю по выезду из отеля."
        )
    day = _day(legs[-1].get("departure_date"))
    if day is None:
        return "Дата отправления по обратному плечу не указана. Дата прибытия домой отдельно не сохранена."
    return f"По обратному плечу сохранена дата отправления {day.strftime('%d.%m.%Y')}. Дата прибытия домой отдельно не сохранена."


def _route(data: dict) -> str:
    legs = data.get("legs") or []
    if legs:
        pairs = [(_city(leg.get("origin")), _city(leg.get("destination"))) for leg in legs]
        if all(pairs[index - 1][1] == pair[0] for index, pair in enumerate(pairs) if index):
            return "Сохранённый маршрут: " + " → ".join([pairs[0][0], *[p[1] for p in pairs]]) + "."
        return (
            "Сохранённые плечи: "
            + "; ".join(f"{a} → {b}" for a, b in pairs)
            + ". Связность требует проверки."
        )
    origin = _city(data.get("origin"))
    cities = [_city(stay.get("city")) for stay in data.get("stays", [])]
    if not data.get("origin") and not cities:
        return "Маршрут поездки пока не указан."
    return (
        f"Отправление: {origin}. Остановки: "
        + (" → ".join(cities) if cities else "пока не указаны")
        + ". Транспортные плечи и возвращение ещё не заданы."
    )


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _source(plan: dict) -> str:
    known = {"DEMO", "SANDBOX", "LIVE_INDICATIVE", "LIVE_BOOKABLE"}
    counts = Counter(
        source if isinstance(source := offer.get("source_type"), str) and source in known else "UNKNOWN"
        for offer in plan["offers"]
    )
    parts = []
    if len(counts) > 1 or plan.get("source_type") == "MIXED":
        parts.append("План смешанный.")
    if "DEMO" in counts:
        parts.append(f"DEMO-компонентов: {counts['DEMO']}; их цены и наличие вымышлены.")
    if "SANDBOX" in counts:
        parts.append(f"SANDBOX-компонентов: {counts['SANDBOX']}; это ответы тестового API.")
    if "LIVE_INDICATIVE" in counts:
        parts.append(
            f"Поисковых оценок LIVE_INDICATIVE: {counts['LIVE_INDICATIVE']}; это не подтверждение доступности."
        )
    if "LIVE_BOOKABLE" in counts:
        parts.append(
            f"Предложений API LIVE_BOOKABLE: {counts['LIVE_BOOKABLE']}; перед оформлением нужна новая проверка."
        )
    if any(source not in known for source in counts):
        parts.append("Происхождение части компонентов не указано.")
    return " ".join(parts)


def _explain(plan: dict, index: int, data: dict) -> str:
    parts = [f"Вариант {index}: объясняю сохранённые факты.", _route(data), _dates(data)]
    total = _money(plan.get("total_minor"), plan.get("currency"))
    parts.append(
        f"Сохранённая расчётная сумма: {total}."
        if total
        else "Полная расчётная сумма в сохранённом плане не указана."
    )
    budget = data.get("budget")
    if total and isinstance(budget, dict) and budget.get("scope", "whole_trip") == "whole_trip":
        if budget.get("currency") == plan.get("currency") and type(budget.get("amount_minor")) is int:
            difference = budget["amount_minor"] - plan["total_minor"]
            label = "Остаток до общего бюджета" if difference >= 0 else "Превышение общего бюджета"
            parts.append(f"{label}: {_money(abs(difference), plan['currency'])}.")
        else:
            parts.append("Сравнение с бюджетом в одной валюте не подтверждено; пересчёт валют не выполнялся.")
    coverage = plan.get("coverage")
    if (
        isinstance(coverage, dict)
        and isinstance(coverage.get("required"), list)
        and isinstance(coverage.get("covered"), list)
        and all(isinstance(item, str) for item in coverage["required"] + coverage["covered"])
    ):
        required, covered = set(coverage["required"]), set(coverage["covered"])
        parts.append(
            f"В сохранённом покрытии: {len(required.intersection(covered))} из {len(required)} компонентов."
        )
    else:
        parts.append("Полнота покрытия компонентов отдельным полем не подтверждена.")
    parts.append(_source(plan))
    hotels = [
        offer
        for offer in plan["offers"]
        if _mapping(offer.get("details")).get("kind", offer.get("kind")) == "hotel"
    ]
    for condition in data.get("constraints", []):
        if not isinstance(condition, dict) or condition.get("hard") is not True:
            continue
        key = condition.get("key")
        label = {
            "breakfast": "завтрак",
            "refundable": "возвратность тарифа",
            "pets_allowed": "размещение с животными",
        }.get(key)
        if not label:
            continue
        relevant = [
            o
            for o in hotels
            if condition.get("stay_id") is None
            or o.get("component_id", _mapping(_mapping(o.get("details")).get("binding")).get("stay_id"))
            == condition["stay_id"]
        ]
        facts = [
            o.get(key)
            if o.get("source_type") == "DEMO"
            else _mapping(_mapping(o.get("details")).get("constraint_evidence")).get(key)
            for o in relevant
        ]
        confirmed = facts and all(
            type(v) is type(condition.get("value")) and v == condition.get("value") for v in facts
        )
        parts.append(
            f"Условие «{label}»: "
            + (
                "совпадает с сохранёнными фактами компонентов."
                if confirmed
                else "соблюдение не подтверждено сохранёнными фактами."
            )
        )
    parts.append(
        "Ранжирование и минимум по всему рынку этими данными не доказаны. Свежие цены и наличие этим ответом не проверяются."
    )
    return " ".join(parts)


def render_saved_answer(
    kind: str, text: str, state: Any, plans: Any, context: dict | None = None
) -> str | None:
    """Render one classified saved topic; never select a first plan by default.

    context is reserved for caller-owned metadata. No implicit clock or pending
    slot mutation is performed here. Current means this trip/revision, not a new
    supplier availability check.
    """
    if kind not in _KINDS or classify_saved_question(text) != kind:
        return None
    data = _data(state)
    if kind == "budget":
        return _budget(data)
    if kind == "dates":
        return _dates(data) + (" " + _return_date(data) if "возвращ" in _key(text) else "")
    if kind == "route":
        return _route(data)
    valid = current_plans(data, plans)
    if not valid:
        return "Актуального сохранённого плана для этой версии поездки нет. Его состав, сумма и источники сейчас не подтверждены."
    selected = _selected_plan(text, valid)
    if selected is None:
        return (
            "Какой вариант объяснить? Укажите один номер из показанных: "
            + ", ".join(str(i) for i in range(1, len(valid) + 1))
            + "."
        )
    return _explain(selected, valid.index(selected) + 1, data)
