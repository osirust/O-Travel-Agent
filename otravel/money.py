"""Conservative scalar grounding for an ALREADY proposed set_budget intent.

This module never initiates NLU operations. Ambiguous money, history and foreign
currencies are not guessed. Explicit unit scopes suppress an unsafe proposed
whole-trip budget and return clarification metadata. All arithmetic is Decimal/int.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import re
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
_SCALES = {"тысяча": 1000, "тысячи": 1000, "тысяч": 1000, "тыс": 1000, "к": 1000, "k": 1000,
           "миллион": 1_000_000, "миллиона": 1_000_000, "миллионов": 1_000_000, "млн": 1_000_000}
_WORDS = "|".join(sorted((*_VALUES, *_SCALES, "полмиллиона"), key=len, reverse=True))
_DIGITS = r"(?:\d{1,3}(?: \d{3})+(?:[,.]\d{1,2})?|\d+(?:[,.]\d{1,2})?)"
_TOKEN = rf"(?:{_DIGITS}|{_WORDS})"
_SEQUENCE = rf"{_TOKEN}(?:[ -]*{_TOKEN})*"
_RUNS = re.compile(rf"(?<!\w)(?P<number>{_SEQUENCE})(?!\w)", re.I)
_RUB = r"(?:рублей|рубля|рубль|руб\.?|rub|rur|₽)"
_MONEY = re.compile(rf"(?<!\w)(?P<number>{_SEQUENCE})\s*(?P<currency>{_RUB})(?!\w)", re.I)
_LEXER = re.compile(_TOKEN, re.I)
_MARKER = re.compile(r"\b(?:бюджет\w*|предел\w*|лимит\w*|потолок\w*)", re.I)
_CAP_CONTEXT = re.compile(r"\b(?:не\s+(?:дороже|более|больше|выше)|максимум|до)\b", re.I)
_TOTAL_SCOPE = re.compile(r"\b(?:общий|весь)\s+бюджет\b|\bна\s+(?:(?:всю|целую)\s+поездку|всех|семью)\b", re.I)
_FOREIGN = re.compile(r"[$€£¥]|\b(?:usd|eur|gbp|aed|cny|try|byn|uah|kzt|доллар\w*|евро|дирхам\w*|тенге|грив\w*)\b", re.I)
_HISTORY = re.compile(r"\b(?:был\w*|вчера\w*|раньше|ранее|прежде|изначально|историческ\w*|прошл\w*)\b", re.I)
_UNIT_PRICE = re.compile(r"\b(?:за|в)\s+(?:ночь|сутки|день|неделю|месяц|номер|человека|гостя)\b|\bна\s+(?:человека|одного|каждого)\b|\bper\s+(?:night|person|room|day)\b", re.I)
_DURATION_PRICE = re.compile(r"\bна\s+(?:ночь|сутки|день|неделю)\b", re.I)
_LODGING = re.compile(r"\b(?:номер\w*|отел\w*|гостиниц\w*|проживани\w*)\b", re.I)
_REDUCE = re.compile(r"\b(?:сократ\w*|уменьш\w*|сниз\w*|сниж\w*|уреж\w*)\b", re.I)
_INCREASE = re.compile(r"\b(?:увелич\w*|повысь|повыс\w*|подними)\b", re.I)
_RANGE = re.compile(rf"\b(?:от|между)\s*{_SEQUENCE}\s*(?:до|и)\s*{_SEQUENCE}(?!\w)", re.I)


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
    return result if result.is_finite() and 0 <= result <= Decimal("1000000000000") else None


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
    """Copy operations and correct only one unambiguous RUB budget scalar.

    Bare unscaled amounts require an existing RUB budget. Shorthand scales can
    use an explicitly proposed RUB currency without inventing an operation.
    Relative changes require a canonical whole-trip RUB base. Nonpositive
    results remain nonpositive integers so domain validation can reject them;
    this function never invents a feasible budget instead.
    """
    result = deepcopy(operations)
    if not isinstance(text, str) or len(text) > 20_000 or not isinstance(result, list):
        return result, []
    proposed = [op for op in result if _get(op, "type") == "set_budget"]
    if len(proposed) != 1:
        return result, []
    value = _get(proposed[0], "value")
    if (not isinstance(value, dict) or not isinstance(value.get("currency", "RUB"), str)
            or value.get("currency", "RUB").upper() not in {"RUB", "RUR"} or value.get("scope", "whole_trip") != "whole_trip"):
        return result, []
    normalized = text.lower().replace("ё", "е").replace("\u00a0", " ").replace("\u202f", " ").replace("пол миллиона", "полмиллиона")
    normalized = re.sub(r"\b(тыс|млн)\.(?=\s|\d|руб|$)", r"\1", normalized)
    normalized = re.sub(r"\b(на|до)(?=[0-9])", r"\1 ", normalized)
    if (_FOREIGN.search(normalized) or _HISTORY.search(normalized) or _RANGE.search(normalized) or "%" in normalized
            or re.search(r"\b(?:если|например|допустим|предположим)\b", normalized)
            or re.search(r"\bне\s+(?:меняй|меняю|изменяй|трогай|увеличивай|уменьшай|сокращай|снижай)\b", normalized)):
        return result, []

    def suppress_scope(reason: str) -> tuple[list, list[dict]]:
        return [op for op in result if _get(op, "type") != "set_budget"], [{
            "field": "budget.amount_minor", "status": "clarification_required",
            "code": "budget_scope_ambiguous", "reason": reason, "operation_suppressed": True}]

    unit_scopes = list(_UNIT_PRICE.finditer(normalized)) + _duration_price_scopes(normalized)
    if unit_scopes:
        return suppress_scope("mixed_scope" if _TOTAL_SCOPE.search(normalized) or len(unit_scopes) > 1 else "unit_price_scope")
    bare = _RUNS.fullmatch(normalized.strip(" .!"))
    if not (_MARKER.search(normalized) or _CAP_CONTEXT.search(normalized) or _TOTAL_SCOPE.search(normalized)
            or bare and _scaled(bare.group("number"))):
        return result, []
    budget = _get(state, "budget")
    base = _get(budget, "amount_minor")
    current_currency = _get(budget, "currency", "")
    current_rub = isinstance(current_currency, str) and current_currency.upper() in {"RUB", "RUR"}
    proposed_rub = isinstance(value.get("currency"), str) and value["currency"].upper() in {"RUB", "RUR"}
    mentions = [(m.start(), m.end(), m.group("number"), True) for m in _MONEY.finditer(normalized)]
    dates = [(m.start(), m.end()) for m in re.finditer(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]\d{2,4})\b", normalized)]
    for run in _RUNS.finditer(normalized):
        if any(run.start() < end and run.end() > start for start, end, _, _ in mentions):
            continue
        phrase = run.group("number")
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
    elif re.search(r"\bна\s*$", prefix):
        decrease, increase = bool(_REDUCE.search(prefix)), bool(_INCREASE.search(prefix))
        if decrease == increase or type(base) is not int or not current_rub:
            return result, []
        if _get(budget, "scope", "whole_trip") != "whole_trip":
            return suppress_scope("mixed_scope")
        minor = base - minor if decrease else base + minor
        mode = "relative_decrease" if decrease else "relative_increase"
    elif _REDUCE.search(prefix) or _INCREASE.search(prefix) or re.search(r"\b(?:меньше|больше)\b", prefix):
        return result, []
    before = value.get("amount_minor")
    if type(before) is int and before == minor:
        return result, []
    value["amount_minor"] = minor
    return result, [{"field": "budget.amount_minor", "before": before, "after": minor,
                     "mode": mode, "literal": phrase, "currency": "RUB",
                     "source": "explicit_rub_literal" if explicit_rub else "existing_rub_budget_context" if current_rub else "proposed_rub_budget_context"}]
