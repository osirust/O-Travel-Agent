"""Current, explicit budget prohibitions; no money parsing or new operations.

This bounded guard is independent of scalar grounding. A model's proposed
amount cannot grant permission to change a budget. Unrelated operations retain
their original types and values, unless the user's own instructions conflict.
"""
import re
from copy import deepcopy

_BUDGET = r"(?:бюджет(?:а|у|ом|е)?|бьюджет(?:а|у|ом|е)?|budget)"
_MOD = r"(?:(?:общий|общего|общему|общим|весь|всего|всему|мой|моего|наш|нашего|текущий|текущего|новый|нового|прежний|прежнего|именно|только|пожалуйста|денежный|сейчас|теперь|к|на)\s+)*"
_NEG_VERB = r"(?:меняй|меняйте|изменяй|изменяйте|трогай|трогайте|увеличивай|увеличивайте|уменьшай|уменьшайте|сокращай|сокращайте|снижай|снижайте|поднимай|поднимайте|уменьши|уменьшите|увеличь|увеличьте|сократи|сократите|снизь|снизьте|урежь|урежьте|подними|поднимите|повысь|повысьте)"
_KEEP_VERB = r"(?:оставь|оставьте|сохрани|сохраните)"
_SAME = r"(?:прежн(?:ий|им)|текущ(?:ий|им)|тот\s+же|таким\s+же|без\s+изменений)"
_CHANGE_VERB = r"(?:измени|измените|меняю|установи|установите|поставь|поставьте|задай|задайте|увеличь|увеличьте|уменьши|уменьшите|сократи|сократите|снизь|снизьте|подними|поднимите|повысь|повысьте|добавь|добавьте|прибавь|прибавьте|верни|верните)"
_PROHIBIT = re.compile(
    rf"\bне\s+{_NEG_VERB}\s+{_MOD}{_BUDGET}\b|"
    rf"\b{_BUDGET}\s+(?:пожалуйста\s+)?не\s+{_NEG_VERB}\b|"
    rf"\b{_KEEP_VERB}\s+{_MOD}{_BUDGET}\s+{_SAME}\b|"
    rf"\b{_KEEP_VERB}\s+{_SAME}\s+{_MOD}{_BUDGET}\b|"
    rf"\b{_BUDGET}\s+{_KEEP_VERB}\s+{_SAME}\b", re.IGNORECASE)
_CHANGE = re.compile(
    rf"\b{_CHANGE_VERB}\s+{_MOD}{_BUDGET}\b|"
    rf"\b{_BUDGET}\s+(?:пожалуйста\s+)?{_CHANGE_VERB}\b|"
    rf"\bподтверждаю\s+(?:именно\s+)?новый\s+{_BUDGET}\b", re.IGNORECASE)
_NON_CURRENT = re.compile(
    r"\b(?:если|допустим|предположим|например|гипотетически|бы|раньше|ранее|вчера|позавчера|"
    r"изначально|когда-то|говорил[аи]?|писал[аи]?|просил[аи]?|сказал[аи]?|цитирую)\b|"
    r"\bв\s+прошл\w*\b", re.IGNORECASE)
_QUOTED = re.compile(r'«[^»]*»|“[^”]*”|„[^“]*“|"[^"\n]*"|\'[^\'\n]*\'|`[^`]*`', re.DOTALL)
_FRESH = re.compile(r"(?:\b(?:но|а|однако)\s+|,\s*)(?:теперь|сейчас)\b", re.IGNORECASE)


def _current_parts(text: str):
    if text.lstrip().startswith(">"):
        return
    unquoted = _QUOTED.sub(" ", text.casefold().replace("ё", "е"))
    for sentence in re.split(r"[.!?;\n]+", unquoted):
        # An explicit new 'now' branch may follow a historical/conditional one.
        for branch in _FRESH.split(sentence):
            if _NON_CURRENT.search(branch):
                continue
            yield branch


def guard_budget_prohibitions(ops: list, text: str) -> tuple[list, dict]:
    """Return copied ops and server-owned ``notes``/human ``conflicts`` lists.

    A prohibition alone removes only set_budget. A simultaneous affirmative
    budget change blocks the entire batch with a clarification. The function
    does not infer permission from a model amount or a prior budget value.
    """
    copied = deepcopy(ops)
    metadata = {"notes": [], "conflicts": []}
    if not isinstance(text, str):
        return copied, metadata
    prohibited = changed = False
    # Split with delimiters so a question containing an imperative quotation
    # cannot become an authoritative instruction when '?' is discarded.
    for piece in re.finditer(r"[^.!?;\n]+(?:[.!?;\n]|$)", text):
        if piece.group().rstrip().endswith("?"):
            continue
        for part in _current_parts(piece.group()):
            prohibited = prohibited or bool(_PROHIBIT.search(part))
            for command in _CHANGE.finditer(part):
                if not re.search(r"\bне\s*$", part[:command.start()]):
                    changed = True
    if not prohibited:
        return copied, metadata
    if changed:
        metadata["notes"].append({"code": "budget_instruction_conflict", "status": "clarification_required"})
        metadata["conflicts"].append(
            "Вы одновременно просите изменить бюджет и оставить его прежним. Бюджет нужно изменить или сохранить? До уточнения изменения не применены.")
        return [], metadata
    kept = [op for op in copied if (op.get("type") if isinstance(op, dict) else getattr(op, "type", None)) != "set_budget"]
    if len(kept) != len(copied):
        metadata["notes"].append({"code": "budget_change_prohibited", "status": "suppressed",
            "operation": "set_budget", "suppressed_count": len(copied) - len(kept)})
    return kept, metadata
