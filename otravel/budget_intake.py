"""Complete an explicit current budget literal omitted by a validated model.

This is intentionally narrower than general NLU: affirmative money command or
standalone budget declaration, one current financial clause, deterministic
scalar grounding, no new currency inference and no change on questions.
"""
from __future__ import annotations

import re

from .budget_intent import _FRESH, _NON_CURRENT, _QUOTED, guard_budget_prohibitions
from .money import (
    _KOPEKS,
    _MARKER,
    _MONEY,
    _PERCENT,
    _RUNS,
    _USD_MONEY,
    _budget_guard_context,
    _get,
    ground_budget_operations,
)

FINDING_ID = "HARNESS-004-EXPLICIT-BUDGET-OMISSION-001"
_MODIFIERS = r"(?:(?:общий|обший|общему|мой|наш|новый|текущий|весь|именно|только|пожалуйста|теперь|сейчас|к)\s*)*"
_WHOLE_PROHIBITION = re.compile(r"\b(?:ничего|все)\s+(?:пока\s+)?не\s+(?:меняй|изменяй|трогай|применяй)\b")
_SUB_BUDGET = re.compile(r"\bбюджет\w*\s+(?:на\s+)?(?:отел\w*|проживан\w*|перелет\w*|билет\w*|трансфер\w*)\b")
_COMMAND = re.compile(r"\b(?:установи(?:те)?|поставь(?:те)?|задай(?:те)?|измени(?:те)?|меняю|"
    r"увеличь(?:те)?|уменьши(?:те)?|сократи(?:те)?|снизь(?:те)?|урежь(?:те)?|"
    r"повысь(?:те)?|подними(?:те)?|добавь(?:те)?|прибавь(?:те)?|верни(?:те)?)\b")
_INTRO = r"\s*(?:(?:тогда|ладно|хорошо|давай|пожалуйста|теперь|сейчас|ну|нет|да)\s*[,—:-]?\s*)*"
_NON_COMMITTING = re.compile(r"\bне\s+(?:примен\w*|выполн\w*|решил\w*|реша\w*|выбрал\w*|готов\w*)\b|"
    r"\b(?:только|лишь)\s+(?:мо[еия]\s+|это\s+)?(?:предложен\w*|обсужден\w*|пример)\b|"
    r"\b(?:пока\s+обсуждаем|предложени\w*\s*,?\s*не\s+команд\w*|гипотез\w*|"
    r"мо[еия]\s+предложен\w*|возможн\w*\s+вариант|еще\s+подума\w*)\b")
_OTHER_SUBJECT = r"(?:дат\w*|заезд\w*|выезд\w*|отел\w*|гостиниц\w*|город\w*|номер\w*|состав\w*|участник\w*|перелет\w*|рейс\w*|гражданств\w*|размещен\w*)"
_OTHER_NONCOMMIT = re.compile(rf"\b{_OTHER_SUBJECT}\s+(?:(?:я|мы|еще|пока|окончательно)\s+){{0,4}}"
    rf"не\s+(?:выбрал\w*|решил\w*|определил\w*|готов\w*|примен\w*|выполн\w*)\b|"
    rf"\bне\s+(?:выбрал\w*|решил\w*|определил\w*|примен\w*|выполн\w*)\s+(?:еще\s+)?{_OTHER_SUBJECT}\b")


def _budget_noncommit(text: str) -> bool:
    # Unknown dates/hotel selection do not withdraw an independently stated
    # budget. An unscoped 'do not apply / still undecided' remains a veto.
    clean = _OTHER_NONCOMMIT.sub(' ', _QUOTED.sub(' ', text))
    return _NON_COMMITTING.search(clean) is not None


def _unbalanced_quotes(text: str) -> bool:
    stack = []
    for index, char in enumerate(text):
        if char == "'" and index and index + 1 < len(text) and text[index - 1].isalnum() and text[index + 1].isalnum():
            continue
        if char in {'"', "'", '`'}:
            if stack and stack[-1] == char:
                stack.pop()
            else:
                stack.append(char)
        elif char in {'«', '„'}:
            stack.append('»' if char == '«' else '“')
        elif char == '“':
            if stack and stack[-1] == '“':
                stack.pop()
            else:
                stack.append('”')
        elif char in {'»', '”'} and (not stack or stack.pop() != char):
            return True
    return bool(stack)


def _whole_trip_scope(clause: str, marker) -> bool:
    tail = clause[marker.end():]
    first = _RUNS.search(tail)
    if first is None:
        return False
    prefix = _COMMAND.sub(' ', tail[:first.start()])
    prefix = re.sub(r"\bна\s+(?:всю\s+поездку|целую\s+поездку|всех|все|семью)\b", ' ', prefix)
    prefix = re.sub(r"\b(?:теперь|сейчас|составляет|равен|будет|это|на|до|не|больше|более|выше|максимум|пожалуйста|еще)\b", ' ', prefix)
    if prefix.strip(' ,:—-'):
        return False
    for scope in re.finditer(r"\b(?:на|для)\s+", clause):
        rest = clause[scope.end():]
        if _RUNS.match(rest):
            continue
        if not re.match(r"(?:всю\s+поездку|целую\s+поездку|поездку\s+целиком|всех|все|семью)(?!\w)", rest):
            return False
    return True


def _closed_financial_tail(clause: str, marker) -> bool:
    """The amount may end only in whole-trip scope or a short polite tag.

    Arbitrary prose after a number is not authority: it may be a proposal,
    quoted instruction, advice question, or a component budget.
    """
    first = _RUNS.search(clause, marker.end())
    if first is None:
        return False
    end = first.end()
    for pattern in (_MONEY, _USD_MONEY, _PERCENT):
        for match in pattern.finditer(clause):
            if match.start() == first.start() and match.end() >= end:
                end = match.end()
    kopeks = _KOPEKS.match(clause, end)
    if kopeks:
        end = kopeks.end()
    tail = clause[end:]
    tail = re.sub(r"^\s*(?:на|для)\s+(?:всю\s+поездку|целую\s+поездку|поездку\s+целиком|всех|все|семью)(?!\w)", '', tail)
    if clause.rstrip().endswith('?'):
        return re.fullmatch(r"\s*,?\s*(?:хорошо|ладно|ок|окей|пожалуйста)\s*\?\s*", tail) is not None
    return not tail.strip(' \t\r\n.,!;')


def _sentences(text: str):
    clean = _QUOTED.sub(" ", text)
    clean = "\n".join(line for line in clean.splitlines() if not line.lstrip().startswith(">"))
    start = 0
    for separator in re.finditer(r"[!?;\n]|(?<!\d)\.|\.(?!\d)", clean):
        yield clean[start:separator.end()]
        start = separator.end()
    if clean[start:].strip():
        yield clean[start:]


def _current_parts(text: str):
    # Decimal points remain inside the financial clause. Quotes were removed
    # from the whole text before sentence splitting, including quoted dots.
    for branch in _FRESH.split(text):
        if not _NON_CURRENT.search(branch):
            yield branch


def _command_binds_budget(clause: str, marker) -> bool:
    head, tail = clause[:marker.start()], clause[marker.end():]
    before = any(re.fullmatch(r"\s*" + _MODIFIERS, clause[command.end():marker.start()])
        and re.fullmatch(_INTRO, clause[:command.start()])
        and not re.search(r"\bне\s*$", clause[:command.start()])
        for command in _COMMAND.finditer(head))
    after = any(re.fullmatch(r"\s*" + _MODIFIERS, tail[:command.start()])
        for command in _COMMAND.finditer(tail)) and re.fullmatch(_INTRO + _MODIFIERS, head) is not None
    return before or after


def explicit_budget_operation(state, text: str) -> tuple[dict | None, dict | None]:
    if not isinstance(text, str) or not 0 < len(text) <= 20_000:
        return None, None
    normalized = text.casefold().replace("ё", "е").replace("\u00a0", " ").replace("\u202f", " ")
    normalized = re.sub(r"\b(теперь|сейчас|на|до)(?=\d)", r"\1 ", normalized)
    normalized = re.sub(r"\b(бюджет|бьюджет|бютжет|лимит|потолок)(?=\d)", r"\1 ", normalized)
    normalized = re.sub(r"\b((?:установи|поставь|задай|измени|увеличь|уменьши|сократи|снизь|урежь|повысь|подними|добавь|прибавь|верни)(?:те)?)(?=\d)", r"\1 ", normalized)
    if _unbalanced_quotes(normalized):
        return None, None
    current_unquoted = _QUOTED.sub(' ', normalized)
    if _WHOLE_PROHIBITION.search(current_unquoted) or _budget_noncommit(current_unquoted):
        return None, None
    selected = []
    for sentence in _sentences(normalized):
        for clause in _current_parts(sentence):
            clause = re.split(r"\s+(?:и|а|но)\s+(?=(?:напомни|скажи|сверь|перечисли|назови)\b)", clause, maxsplit=1)[0]
            markers = list(_MARKER.finditer(clause))
            if len(markers) != 1 or _SUB_BUDGET.search(clause):
                continue
            marker = markers[0]
            command = _command_binds_budget(clause, marker)
            if sentence.rstrip().endswith("?") and not command:
                continue
            if not _whole_trip_scope(clause, marker) or not _closed_financial_tail(clause, marker):
                continue
            head, tail = clause[:marker.start()], clause[marker.end():]
            declaration = (re.fullmatch(r"\s*" + _MODIFIERS, head) is not None
                and re.match(r"\s*(?:(?:теперь|сейчас|составляет|равен|будет|это)\s+)*", tail) is not None
                and _RUNS.match(re.sub(r"^\s*(?:(?:теперь|сейчас|составляет|равен|будет|это)\s+)*", "", tail)) is not None)
            if command or declaration:
                context, _ = _budget_guard_context(clause)
                selected.append(context)
    if len(selected) != 1:
        return None, None
    literal = selected[0]
    currency = "USD" if _USD_MONEY.search(literal) else "RUB"
    budget = _get(state, "budget")
    if currency == "RUB" and not _MONEY.search(literal) and _get(budget, "currency") not in {"RUB", "RUR"}:
        return None, None
    seed = {"type": "set_budget", "value": {"amount_minor": 0, "currency": currency}}
    allowed, prohibitions = guard_budget_prohibitions([seed], normalized)
    if not allowed or prohibitions["conflicts"]:
        return None, None
    operations, notes = ground_budget_operations(state, allowed, literal)
    if (len(operations) != 1 or not notes or any(note.get("operation_suppressed") for note in notes)
            or type(operations[0]["value"].get("amount_minor")) is not int
            or operations[0]["value"]["amount_minor"] <= 0):
        return None, None
    return operations[0], {"code": "explicit_budget_literal_completed", "finding_id": FINDING_ID,
        "status": "grounded", "source": "current_asserted_financial_clause", "currency": currency,
        "amount_minor": operations[0]["value"]["amount_minor"]}


_REPORTED_BUDGET = re.compile(
    r"\b(?:друг|подруга|коллега|турагент|агент|он|она|они|мне|нам)\s+"
    r"(?:советует|советуют|рекомендует|рекомендуют)\s*[:,—-]?\s*"
    r"(?:(?:установи(?:ть|те)?|постав(?:ить|ь|ьте)|зада(?:ть|й|йте)|увеличи(?:ть|вай)|уменьши(?:ть|те)?)\s+)?"
    + _MODIFIERS + _MARKER.pattern)
_REPORTED_BUDGET_TAIL = re.compile(
    r"\b(?:это\s+)?(?:совет|рекомендация)\s+(?:друга|подруги|коллеги|турагента|агента)\b")
_COMPONENT_BUDGET_SCOPE = re.compile(
    r"\b(?:на|для)\s+(?:(?:оплату|оплаты|покупку|покупки|бронирование|бронирования)\s+)?"
    r"(?:страховк\w*|питани\w*|еду|еды|отел\w*|гостиниц\w*|проживан\w*|"
    r"перелет\w*|билет\w*|трансфер\w*|экскурс\w*|багаж\w*|номер\w*)\b")


def _negative_budget_evidence(text: str) -> list[str]:
    """Explicit contrary evidence only; an unparsed literal is not a veto."""
    reasons = set()
    for sentence in _sentences(text):
        for clause in _current_parts(sentence):
            markers = list(_MARKER.finditer(clause))
            if not markers:
                continue
            if _REPORTED_BUDGET.search(clause) or (_REPORTED_BUDGET_TAIL.search(clause)
                    and not any(_command_binds_budget(clause, marker) for marker in markers)):
                reasons.add('reported_advice')
            for marker in markers:
                # Scope belongs to this financial clause, not a following
                # independent instruction such as 'и подбери отель ...'.
                tail = re.split(r"\s+(?:и|а|но)\s+", clause[marker.end():], maxsplit=1)[0]
                if any(not re.search(r"\bне\s*$", tail[:scope.start()])
                       for scope in _COMPONENT_BUDGET_SCOPE.finditer(tail)):
                    reasons.add('component_budget')
    return sorted(reasons)


def guard_budget_question_operations(operations: list, text: str, *, has_asserted_budget: bool):
    """A question about an existing amount cannot supply a new budget fact."""
    if has_asserted_budget or not isinstance(text, str):
        return operations, []
    normalized = text.casefold().replace("ё", "е")
    unbalanced = _unbalanced_quotes(normalized)
    nonapply = bool(_budget_noncommit(normalized) or _WHOLE_PROHIBITION.search(_QUOTED.sub(' ', normalized)))
    only_noncurrent = bool(_MARKER.search(normalized)) and not any(
        _MARKER.search(clause) for sentence in _sentences(normalized) for clause in _current_parts(sentence))
    financial_question = '?' in normalized and _MARKER.search(normalized) is not None
    negative_evidence = _negative_budget_evidence(normalized)
    if not (unbalanced or nonapply or only_noncurrent or financial_question or negative_evidence):
        return operations, []
    copied = [operation for operation in operations if _get(operation, "type") != "set_budget"]
    if len(copied) == len(operations):
        return copied, []
    note = {"code": "budget_question_not_assignment", "finding_id": FINDING_ID,
        "status": "suppressed", "field": "budget.amount_minor"}
    if negative_evidence:
        note['negative_evidence'] = negative_evidence
    if 'component_budget' in negative_evidence:
        note.update(status='clarification_required', question='Вы указали бюджет отдельной части поездки. Какой общий бюджет всей поездки нужно установить?')
    if unbalanced:
        note.update(status='clarification_required', question='Уточните бюджет отдельной командой без незакрытых кавычек; цитату не применяю к поездке.')
    return copied, [note]
