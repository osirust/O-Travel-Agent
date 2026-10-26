"""Conservative scalar grounding for an ALREADY proposed set_budget intent.

This module never initiates NLU operations. Ambiguous money and history are not
guessed. Explicit USD literals only repair already proposed USD amounts, without
conversion. Unit scopes suppress an unsafe proposed whole-trip budget. All
arithmetic is Decimal/int; fractional minor units require clarification.
"""
import re
from copy import deepcopy
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

_VALUES = {
    "ноль": 0, "нуль": 0, "один": 1, "одна": 1, "одно": 1, "две": 2, "два": 2,
    "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9,
    "десять": 10, "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
    "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19,
    "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60,
    "семьдесят": 70, "восемьдесят": 80, "девяносто": 90, "сто": 100, "двести": 200,
    "триста": 300, "четыреста": 400, "пятьсот": 500, "шестьсот": 600, "семьсот": 700,
    "восемьсот": 800, "девятьсот": 900,
}
_SCALES = {"тысяча": 1000, "тысячи": 1000, "тысяч": 1000, "тыс": 1000,
           "тыща": 1000, "тыщи": 1000, "тыщ": 1000, "к": 1000, "k": 1000,
           "миллион": 1_000_000, "миллиона": 1_000_000, "миллионов": 1_000_000, "млн": 1_000_000}
_WORDS = "|".join(sorted((*_VALUES, *_SCALES, "полмиллиона"), key=len, reverse=True))
_DIGITS = r"(?:\d{1,3}(?: \d{3})+(?:[,.]\d{1,2})?|\d+(?:[,.]\d{1,2})?)"
_TOKEN = rf"(?:{_DIGITS}|{_WORDS})"
_SEQUENCE = rf"{_TOKEN}(?:[ -]*{_TOKEN})*"
_RUNS = re.compile(rf"(?<!\w)(?P<number>{_SEQUENCE})(?!\w)", re.IGNORECASE)
_RUB = r"(?:рублей|рубля|рубль|руб\.?|rub|rur|₽)"
_MONEY = re.compile(rf"(?<!\w)(?P<number>{_SEQUENCE})\s*(?P<currency>{_RUB})(?!\w)", re.IGNORECASE)
_LEXER = re.compile(_TOKEN, re.IGNORECASE)
_MARKER = re.compile(r"\b(?:(?:бюджет|бьюджет|бютжет)\w*|предел\w*|лимит\w*|потолок\w*|budget)\b", re.IGNORECASE)
_CAP_CONTEXT = re.compile(r"\b(?:не\s+(?:дороже|более|больше|выше)|максимум|до)\b", re.IGNORECASE)
_TOTAL_SCOPE = re.compile(r"\b(?:общий|весь)\s+бюджет\b|\bна\s+(?:(?:всю|целую)\s+поездку|всех|семью|все)\b|\btotal\s+budget\b|\bfor\s+the\s+whole\s+trip\b", re.IGNORECASE)
_FOREIGN = re.compile(r"[$€£¥]|\b(?:usd|eur|gbp|aed|cny|try|byn|uah|kzt|доллар\w*|евро|дирхам\w*|тенге|грив\w*)\b", re.IGNORECASE)
_HISTORY = re.compile(r"\b(?:был\w*|вчера\w*|раньше|ранее|прежде|изначально|историческ\w*|прошл\w*)\b", re.IGNORECASE)
_UNIT_PRICE = re.compile(r"\b(?:за|в)\s+(?:ночь|сутки|день|неделю|месяц|номер|человека|гостя)\b|\bна\s+(?:человека|одного|каждого)\b|\bper\s+(?:night|person|room|day)\b", re.IGNORECASE)
_DURATION_PRICE = re.compile(r"\bна\s+(?:ночь|сутки|день|неделю)\b", re.IGNORECASE)
_LODGING = re.compile(r"\b(?:номер\w*|отел\w*|гостиниц\w*|проживани\w*)\b", re.IGNORECASE)
_REDUCE = re.compile(r"\b(?:сократ\w*|уменьш\w*|сниз\w*|сниж\w*|уреж\w*)\b", re.IGNORECASE)
_INCREASE = re.compile(r"\b(?:увелич\w*|повысь|повыс\w*|подними|добав\w*|прибав\w*)\b", re.IGNORECASE)
_RANGE = re.compile(rf"\b(?:от|между)\s*{_SEQUENCE}\s*(?:до|и)\s*{_SEQUENCE}(?!\w)", re.IGNORECASE)
_PERCENT = re.compile(rf"(?<!\w)(?P<number>{_SEQUENCE})\s*(?:%|процент(?:а|ов)?\b)", re.IGNORECASE)
_KOPEKS = re.compile(rf"\s+(?:и\s+)?(?P<number>{_SEQUENCE})\s*коп(?:еек|ейки|ейка|\.)?(?!\w)", re.IGNORECASE)
_USD_MONEY = re.compile(rf"(?<!\w)(?P<number>{_SEQUENCE})\s*(?:usd|доллар(?:ов|а|ы)?)(?!\w)", re.IGNORECASE)
_TRANSACTION_PURPOSE = re.compile(
    r"\b(?:оплат\w*|оплач\w*|запла[тч]\w*|платеж\w*|платить|предоплат\w*|депозит\w*|залог\w*|аванс\w*|"
    r"оформл\w*|бронирован\w*|бронь|брони|покуп\w*|купить|куплю|счет\w*)\b|"
    r"\b(?:цена|стоимость)\b.{0,65}\b(?:вариант\w*|предложен\w*|тариф\w*|корзин\w*|отел\w*)\b", re.IGNORECASE)
_CURRENT_OFFER = re.compile(r"\b(?:выбранн\w*|текущ\w*|перв\w*|втор\w*|этот|этого)\s+(?:\w+\s+){0,2}"
    r"(?:вариант\w*|предложен\w*|тариф\w*|отел\w*)\b", re.IGNORECASE)
_BUDGET_COMMAND = re.compile(r"\b(?:подтвержда\w*|установ\w*|постав\w*|зада\w*|измени\w*|меняю|"
    r"увелич\w*|уменьш\w*|сократ\w*|сниз\w*|повы\w*|добав\w*|прибав\w*|верни)\b", re.IGNORECASE)


def _purpose_spans(text: str) -> list[re.Match]:
    matches = list(_TRANSACTION_PURPOSE.finditer(text))
    for offer in _CURRENT_OFFER.finditer(text):
        tail = re.split(r"[;.!?\n]", text[offer.end():], maxsplit=1)[0][:100]
        # A bare mention of a 'second hotel' is not a quoted price (037:T6).
        if _MONEY.search(tail) or any(_scaled(run["number"]) and run["number"] not in {"к", "k"}
                                     for run in _RUNS.finditer(tail)):
            matches.append(offer)
    return sorted(matches, key=lambda match: match.start())


def _budget_part_in_transaction(text: str) -> str | bool | None:
    """Separate an explicit budget clause from quote/payment/deposit statements.

    AFTER80-MONEY-QUOTE-001: a proposed set_budget is not permission to turn a
    selected price into a spending limit. Confirmation alone is not a reason
    to reject a genuine 'подтверждаю бюджет' instruction. No money intent is made.
    """
    if not _purpose_spans(text):
        return None
    selected = []
    for clause in re.split(r"(?<=[;.!?])\s+|[;\n]|,\s+(?!\d)", text):
        markers = list(_MARKER.finditer(clause)) or list(_TOTAL_SCOPE.finditer(clause))
        if not markers or "?" in clause:
            continue
        if (re.search(r"\b(?:не\s+(?:меняй|меняю|изменяй|трогай)|оставь|сохрани|сохранен\w*|записан\w*|"
                      r"остается|остаются|прежн\w*|в рамках|в пределах|если|например)\b", clause)
                or _HISTORY.search(clause)):
            continue
        marker = markers[-1]
        # Only values following this budget marker qualify. A prior offer price
        # elsewhere in the clause cannot supply an omitted budget amount.
        tail = clause[marker.end():]
        if not any(run.start() >= marker.end() and _number(run["number"]) is not None
                   for run in _RUNS.finditer(clause)):
            continue
        start = marker.start()
        for command in _BUDGET_COMMAND.finditer(clause[:marker.start()]):
            if re.fullmatch(r"\s*(?:(?:общий|общему|новый|текущий|мой|наш|именно|только|к)\s*)*",
                            clause[command.end():marker.start()]):
                start = command.start()
        other_purposes = _purpose_spans(tail)
        end = marker.end() + other_purposes[0].start() if other_purposes else len(clause)
        selected.append(clause[start:end].strip())
    return " ".join(selected) if selected else False


def _budget_guard_context(text: str) -> tuple[str, bool]:
    """Bind ambiguity/prohibitions to the financial clause, not other edits.

    CF10-10 explicitly reduces the budget while forbidding hotel/person edits.
    An affirmative budget command can stand beside unrelated history, ranges or
    'не меняй'. Its own negative/historical wording still blocks grounding.
    """
    clauses = re.split(r"(?<=[;.!?])\s+|[;\n]|,\s+(?!\d)", text)
    financial, positive = [], []
    for clause in clauses:
        markers = list(_MARKER.finditer(clause)) or list(_TOTAL_SCOPE.finditer(clause))
        if not markers:
            continue
        marker = markers[-1]
        affirmative = any(not _negated_literal(clause, command.start()) and re.fullmatch(
            r"\s*(?:(?:общий|общему|новый|текущий|мой|наш|именно|только|к)\s*)*",
            clause[command.end():marker.start()]) for command in _BUDGET_COMMAND.finditer(clause[:marker.start()]))
        if affirmative:
            # Do not split competing budget verbs ('уменьши и увеличь бюджет').
            # Only a clearly different object/action ends this budget clause.
            clause = re.split(r"\s+(?:и|а|но)\s+(?=(?:продли|перенеси|пересели)\b|"
                r"(?:(?:первое|последнее|всех|остальных|других)\s+)*(?:отел|проживан|участник|дет|дат|город|маршрут)\w*\b)",
                clause, maxsplit=1)[0]
            positive.append(clause)
        financial.append(clause)
    # A second current budget declaration can contradict the command. Retain
    # it for amount/range conflict checks; only unrelated historical budgets
    # yield to an affirmative current command.
    selected = [clause for clause in financial if not _HISTORY.search(clause) or clause in positive] if positive else financial
    return (" ".join(selected), bool(positive)) if selected else (text, False)


def _suppressed(result: list, *, reason: str, question: str | None = None, noop: bool = False) -> tuple[list, list[dict]]:
    note = {"field": "budget.amount_minor", "status": "no_op" if noop else "clarification_required",
            "code": "retained_budget" if noop else "budget_amount_ambiguous",
            "reason": reason, "operation_suppressed": True}
    if question:
        note["question"] = question
    return [op for op in result if _get(op, "type") != "set_budget"], [note]


def _negated_literal(text: str, start: int) -> bool:
    return bool(re.search(r"\b(?:не|not)\s*$", text[max(0, start - 20):start]))


def _direction(prefix: str) -> int:
    """An amount-changing verb must belong to this budget, not an earlier child."""
    boundary = max(prefix.rfind(c) for c in ",;.!?\n")
    local = prefix[boundary + 1:]
    markers = list(_MARKER.finditer(local))
    if not markers:
        return 0
    marker = markers[-1]
    head = local[:marker.start()]
    if _REDUCE.search(head) and _INCREASE.search(head):
        return 0
    directions = []
    for pattern, direction in ((_REDUCE, -1), (_INCREASE, 1)):
        for verb in pattern.finditer(head):
            between = head[verb.end():]
            if re.fullmatch(r"\s*(?:(?:к|общий|общему|весь|наш|мой|на|whole|total)\s*)*", between):
                directions.append(direction)
    return directions[0] if len(directions) == 1 else 0


def _retained_increment(text: str) -> bool:
    retained = re.search(r"\b(?:прибавк\w*|добавк\w*|увеличени\w*|сокращени\w*)\b.{0,90}\b(?:оставь|сохрани|сохранить)\b", text)
    if not retained:
        return False
    # A new explicit budget command is not cancelled by a separate reminder.
    return not re.search(r"\b(?:установи|поставь|задай|увеличь|уменьши|сократи|прибавь|добавь|подними|повысь|снизь|измени|верни)\b.{0,30}\b(?:бюджет\w*|лимит\w*)\b", text)


def _kopeck_literals(text: str) -> tuple[str, bool]:
    """Resolve explicit major+minor parts once; never round a fractional kopeck."""
    replacements = []
    for match in _MONEY.finditer(text):
        tail = _KOPEKS.match(text, match.end())
        if tail is None:
            continue
        major, minor = _number(match["number"]), _number(tail["number"])
        if (major is None or minor is None or major != major.to_integral_value()
                or minor != minor.to_integral_value() or not 0 <= minor <= 99):
            return text, False
        replacements.append((match.start(), tail.end(), f"{int(major)}.{int(minor):02d} {match['currency']}"))
    for start, end, replacement in reversed(replacements):
        text = text[:start] + replacement + text[end:]
    return text, True


def _percent_budget(state: Any, result: list, value: dict, text: str) -> tuple[list, list[dict]] | None:
    matches = list(_PERCENT.finditer(text))
    if not matches:
        return None
    if len(matches) != 1:
        return _suppressed(result, reason="multiple_percentages", question="На сколько процентов изменить общий бюджет поездки?")
    match = matches[0]
    prefix, near = _near_budget(text, match.start(), match.end())
    direction = _direction(prefix)
    if near and direction and prefix.rstrip().endswith(("-", "+")):
        return _suppressed(result, reason="signed_percentage", question="На сколько положительных процентов изменить общий бюджет?")
    if not near or not direction or not re.search(r"\bна\s*$", prefix):
        return None
    budget = _get(state, "budget")
    base = _get(budget, "amount_minor")
    pct = _number(match["number"])
    if (type(base) is not int or _get(budget, "currency") not in {"RUB", "RUR"}
            or _get(budget, "scope", "whole_trip") != "whole_trip"):
        return _suppressed(result, reason="missing_percent_base", question="Какой исходный общий бюджет в рублях нужно изменить?")
    for literal in _MONEY.finditer(text):
        if _negated_literal(text, literal.start()):
            continue
        amount = _number(literal["number"])
        if literal.end() >= match.start() or amount is None or amount * 100 != base:
            return _suppressed(result, reason="multiple_money_values", question="Изменить бюджет на процент или установить указанную итоговую сумму?")
    if pct is None or pct < 0 or direction < 0 and pct >= 100:
        return _suppressed(result, reason="invalid_percentage", question="На сколько процентов уменьшить бюджет, сохранив положительную сумму?")
    with localcontext() as arithmetic:
        arithmetic.prec = max(40, len(str(abs(base))) + 30)
        minor = Decimal(base) * (Decimal(100) + direction * pct) / 100
    if minor != minor.to_integral_value():
        return _suppressed(result, reason="fractional_minor_unit", question="Изменение даёт долю копейки. Какую точную итоговую сумму бюджета установить?")
    before = value.get("amount_minor")
    value["amount_minor"] = int(minor)
    if before == int(minor) and type(before) is int:
        return result, []
    return result, [{"field": "budget.amount_minor", "before": before, "after": int(minor),
        "mode": "relative_percent_decrease" if direction < 0 else "relative_percent_increase",
        "literal": match["number"], "currency": "RUB", "source": "explicit_budget_percentage"}]


def _get(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _number(phrase: str) -> Decimal | None:
    phrase = phrase.strip().lower().replace("пол миллиона", "полмиллиона")
    if phrase == "полмиллиона":
        return Decimal(500_000)
    tokens, cursor = [], 0
    for match in _LEXER.finditer(phrase):
        if phrase[cursor:match.start()].strip(" -"):
            return None
        tokens.append(match.group().lower())
        cursor = match.end()
    if phrase[cursor:].strip(" -") or not tokens:
        return None
    total, group, last_scale, last_rank = Decimal(0), Decimal(0), 10**12, 4
    group_has_digit = False
    group_has_word = False
    for token in tokens:
        if token in _SCALES:
            scale = _SCALES[token]
            if scale >= last_scale:
                return None
            total += (group if group_has_digit or group_has_word else Decimal(1)) * scale
            group, last_scale, last_rank = Decimal(0), scale, 4
            group_has_digit = group_has_word = False
        elif token in _VALUES:
            value = _VALUES[token]
            rank = 3 if value >= 100 else 2 if value >= 20 else 1
            if group_has_digit or rank >= last_rank or (last_rank == 2 and value >= 10):
                return None
            group += value
            last_rank, group_has_word = rank, True
        else:
            if group_has_digit or group_has_word:
                return None
            try:
                group = Decimal(token.replace(" ", "").replace(",", "."))
            except InvalidOperation:
                return None
            group_has_digit = True
    result = total + group
    return result if result.is_finite() and 0 <= result <= Decimal(1000000000000) else None


def _scaled(phrase: str) -> bool:
    return any(token in _SCALES or token == "полмиллиона" for token in _LEXER.findall(phrase))


def _duration_price_scopes(text: str) -> list[re.Match]:
    """Recognize lodging amounts followed by a duration, not trip duration.

    A bare 'budget for a week' is a whole-trip duration. Require a local lodging
    subject followed by a monetary literal, with no newer budget subject, before
    treating 'на ночь/неделю' as a component price. This never creates an intent.
    """
    result = []
    for scope in _DURATION_PRICE.finditer(text):
        boundary = max(text.rfind(symbol, 0, scope.start()) for symbol in ";!?\n")
        prefix = text[max(boundary + 1, scope.start() - 120):scope.start()]
        products = list(_LODGING.finditer(prefix))
        if not products:
            continue
        product = products[-1]
        if any(marker.start() > product.start() for marker in _MARKER.finditer(prefix)):
            continue
        amount = prefix[product.end():]
        if _MONEY.search(amount) or any(_scaled(run.group("number")) for run in _RUNS.finditer(amount)):
            result.append(scope)
    return result


def _near_budget(text: str, start: int, end: int, *, scaled_context: bool = False) -> tuple[str, bool]:
    markers = list(_MARKER.finditer(text))
    if scaled_context:
        markers += list(_CAP_CONTEXT.finditer(text)) + list(_TOTAL_SCOPE.finditer(text))
        markers.sort(key=lambda marker: marker.start())
        bare = text.strip(" .!")
        run = _RUNS.fullmatch(bare)
        if run and _scaled(run.group("number")):
            return "", True
    for marker in reversed(markers):
        if marker.end() <= start:
            between = text[marker.end():start]
        elif marker.start() >= end:
            between = text[end:marker.start()]
        else:
            continue
        if len(between) > 100 or re.search(r"[.;!?\n]", between):
            continue
        if re.search(r"\b(?:стоит|цена|стоимость|предоплата|неизвест\w*|не\s+знаю)\b", between):
            continue
        if re.search(r"\b(?:от|между|или|либо)\b", between):
            continue
        # Return the local sentence prefix for relative-vs-absolute parsing.
        boundary = max(text.rfind(c, 0, start) for c in ";!?\n")
        return text[boundary + 1:start], True
    return "", False


def ground_budget_operations(state: Any, operations: list, text: str) -> tuple[list, list[dict]]:
    """Copy operations and correct one unambiguous proposed budget scalar.

    Bare unscaled amounts require an existing RUB budget. Shorthand scales can
    use an explicitly proposed RUB currency without inventing an operation.
    Relative changes require a canonical whole-trip RUB base. Explicit USD is
    supported only for a matching already-proposed absolute USD amount. Simple
    nonpositive delta results still reach domain validation; percentages that
    zero a budget or require rounding instead ask for an exact amount.
    """
    result = deepcopy(operations)
    if not isinstance(text, str) or len(text) > 20_000 or not isinstance(result, list):
        return result, []
    proposed = [op for op in result if _get(op, "type") == "set_budget"]
    if not proposed:
        return result, []
    normalized = text.lower().replace("ё", "е").replace("\u00a0", " ").replace("\u202f", " ").replace("пол миллиона", "полмиллиона")
    normalized = re.sub(r"\b(тыс|млн)\.(?=\s|\d|руб|$)", r"\1", normalized)
    normalized = re.sub(r"\b(на|до)(?=[0-9])", r"\1 ", normalized)
    budget_part = _budget_part_in_transaction(normalized)
    if budget_part is False:
        unchanged, notes = _suppressed(result, reason="quote_payment_or_deposit_not_budget", noop=True)
        notes[0].update(code="budget_not_requested", finding_id="AFTER80-MONEY-QUOTE-001")
        return unchanged, notes
    if isinstance(budget_part, str):
        normalized = budget_part
    if len(proposed) != 1:
        return result, []
    value = _get(proposed[0], "value")
    if (not isinstance(value, dict) or not isinstance(value.get("currency", "RUB"), str)
            or value.get("currency", "RUB").upper() not in {"RUB", "RUR", "USD"} or value.get("scope", "whole_trip") != "whole_trip"):
        return result, []
    def suppress_scope(reason: str) -> tuple[list, list[dict]]:
        return [op for op in result if _get(op, "type") != "set_budget"], [{
            "field": "budget.amount_minor", "status": "clarification_required",
            "code": "budget_scope_ambiguous", "reason": reason, "operation_suppressed": True}]

    unit_scopes = [scope for scope in list(_UNIT_PRICE.finditer(normalized)) + _duration_price_scopes(normalized)
                   if not _negated_literal(normalized, scope.start())]
    if unit_scopes:
        return suppress_scope("mixed_scope" if _TOTAL_SCOPE.search(normalized) or len(unit_scopes) > 1 else "unit_price_scope")
    if _retained_increment(normalized):
        return _suppressed(result, reason="retained_previous_delta", noop=True)
    guard_context, affirmative_budget = _budget_guard_context(normalized)
    if (_HISTORY.search(guard_context) or _RANGE.search(guard_context)
            or re.search(r"\b(?:если|например|допустим|предположим)\b", normalized)
            or re.search(r"\bне\s+(?:меняй|меняю|изменяй|трогай|увеличивай|уменьшай|уменьшать|уменьш|сокращай|снижай)\b", guard_context)):
        return result, []
    if affirmative_budget:
        normalized = guard_context
    if value.get("currency", "RUB").upper() == "USD":
        # No FX: only repair an explicitly requested, already proposed USD sum.
        matches = list(_USD_MONEY.finditer(normalized))
        if len(matches) != 1 or _MONEY.search(normalized):
            return result, []
        match = matches[0]
        prefix, near = _near_budget(normalized, match.start(), match.end(), scaled_context=True)
        amount = _number(match["number"])
        if (not near or _direction(prefix) or amount is None
                or amount * 100 != (amount * 100).to_integral_value()):
            return result, []
        before, minor = value.get("amount_minor"), int(amount * 100)
        value["amount_minor"] = minor
        return (result, [] if type(before) is int and before == minor else [{
            "field": "budget.amount_minor", "before": before, "after": minor,
            "mode": "absolute", "literal": match["number"], "currency": "USD", "source": "explicit_usd_literal"}])
    if _FOREIGN.search(normalized):
        return result, []
    if _PERCENT.search(normalized) or "%" in normalized:
        return _percent_budget(state, result, value, normalized) or (result, [])
    normalized, valid_minor_parts = _kopeck_literals(normalized)
    if not valid_minor_parts:
        return _suppressed(result, reason="invalid_minor_parts", question="Укажите точную сумму бюджета: целые рубли и от 0 до 99 копеек?")
    bare = _RUNS.fullmatch(normalized.strip(" .!"))
    if not (_MARKER.search(normalized) or _CAP_CONTEXT.search(normalized) or _TOTAL_SCOPE.search(normalized)
            or bare and _scaled(bare.group("number"))):
        return result, []
    budget = _get(state, "budget")
    base = _get(budget, "amount_minor")
    current_currency = _get(budget, "currency", "")
    current_rub = isinstance(current_currency, str) and current_currency.upper() in {"RUB", "RUR"}
    proposed_rub = isinstance(value.get("currency"), str) and value["currency"].upper() in {"RUB", "RUR"}
    mentions = [(m.start(), m.end(), m.group("number"), True) for m in _MONEY.finditer(normalized)
                if not _negated_literal(normalized, m.start())]
    dates = [(m.start(), m.end()) for m in re.finditer(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]\d{2,4})\b", normalized)]
    for run in _RUNS.finditer(normalized):
        if any(run.start() < end and run.end() > start for start, end, _, _ in mentions):
            continue
        phrase = run.group("number")
        if phrase in {"к", "k"} or _negated_literal(normalized, run.start()):
            continue  # A Russian preposition or a rejected alternate is not money.
        scaled = _scaled(phrase)
        prefix, near = _near_budget(normalized, run.start(), run.end(), scaled_context=scaled)
        if (any(run.start() < end and run.end() > start for start, end in dates)
                or re.match(r"\s*(?:лет|год\w*|дн\w*|день|ноч\w*|взросл\w*|дет\w*|ребен\w*|номер\w*|комнат\w*|человек\w*|гост\w*|запрос\w*|октябр\w*|ноябр\w*|декабр\w*)\b", normalized[run.end():])):
            continue
        expense = bool(re.search(r"(?:стоит|цена|стоимость|предоплата|(?:отель|билет|перелет|трансфер)\s+за)\s*$", normalized[max(0, run.start()-30):run.start()]))
        marker_matches = list(_MARKER.finditer(prefix))
        tail = prefix[marker_matches[-1].end():].strip(" :—-") if marker_matches else None
        adjacent_budget = tail is not None and bool(re.fullmatch(r"(?:(?:общий|на|всю|всех|поездку|семью|не|более|больше|выше|до|максимум)\s*)*", tail))
        if scaled or current_rub and near and adjacent_budget or expense:
            mentions.append((run.start(), run.end(), phrase, False))
    if len(mentions) > 1:
        amounts = [_number(phrase) for _, _, phrase, _ in mentions]
        if (amounts[0] is not None and all(amount == amounts[0] for amount in amounts)
                and all(_near_budget(normalized, start, end, scaled_context=True)[1] for start, end, _, _ in mentions)):
            mentions = mentions[:1]  # Equivalent written+digit declarations, not two edits.
    if len(mentions) != 1:
        return result, []
    start, end, phrase, explicit_rub = mentions[0]
    scaled = _scaled(phrase)
    prefix, near = _near_budget(normalized, start, end, scaled_context=scaled or explicit_rub)
    if not near or not explicit_rub and not (current_rub or scaled and proposed_rub):
        return result, []
    if prefix.rstrip().endswith(("-", "+")):
        # Do not turn a signed/invalid budget into a positive feasible one.
        return result, []
    amount = _number(phrase)
    if amount is None or amount * 100 != (amount * 100).to_integral_value():
        return result, []
    minor = int(amount * 100)
    mode = "absolute"
    if re.search(r"\b(?:до|не\s+дороже|не\s+больше|не\s+выше|не\s+более|максимум)\s*$", prefix):
        pass
    elif re.search(r"\bна\s*$", prefix) or _direction(prefix) and re.search(r"\b(?:бюджет\w*|лимит\w*)\s*(?:еще)?\s*$", prefix):
        direction = _direction(prefix)
        if not direction or type(base) is not int or not current_rub:
            return result, []
        if _get(budget, "scope", "whole_trip") != "whole_trip":
            return suppress_scope("mixed_scope")
        minor = base + direction * minor
        mode = "relative_decrease" if direction < 0 else "relative_increase"
    elif _direction(prefix) or re.search(r"\b(?:меньше|больше)\b", prefix):
        return result, []
    before = value.get("amount_minor")
    if type(before) is int and before == minor:
        return result, []
    value["amount_minor"] = minor
    return result, [{"field": "budget.amount_minor", "before": before, "after": minor,
                     "mode": mode, "literal": phrase, "currency": "RUB",
                     "source": "explicit_rub_literal" if explicit_rub else "existing_rub_budget_context" if current_rub else "proposed_rub_budget_context"}]
