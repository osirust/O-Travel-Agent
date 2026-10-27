"""Current-message permission for the two implemented soft preferences.

This model-proposal guard never creates preferences, hotel filters or departure
hours. Unknown keys/values remain the normal schema/domain validator's concern.
"""
from __future__ import annotations

import math
import re
from copy import deepcopy
from decimal import Decimal

from .budget_intent import _FRESH, _NON_CURRENT
from .domain import Preference
from .intent_grounding import _facts
from .operation_aliases import _without_quoted_spans
from .preference_intake import explicit_lowest_cost

FINDING_ID = "HARNESS-004-UNREQUESTED-PREFERENCE-001"
_FAMILY = {"price_priority": "price_priority", "departure_time": "departure_time", "departure_window": "departure_time"}
_DEFAULT_WEIGHT = Preference.model_fields["weight"].default
_AFFIRM = re.compile(r"\b(?:ищи|найди|поищи|подбери|выбирай|хочу|хотим|нужен|нужна|нужно|"
    r"предпочитаю|предпочитаем|предпочтительн\w*|желател\w*|поставь|установи|добавь|включи|choose|find|prefer|want|set)\b")
_PRICE = re.compile(r"\b(?:миним\w*\s+(?:цен\w*|стоим\w*)|миним\w*\s+общ\w*\s+стоим\w*|"
    r"сам\w*\s+дешев\w*|подешевле|price_priority|lowest_total|lowest\s+(?:cost|price)|cheapest)\b")
_BARE_PRICE = re.compile(r"(?:миним\w*\s+(?:общ\w*\s+)?(?:цен\w*|стоим\w*)|подешевле)")
_MORNING = re.compile(r"\b(?:рано\s+утром|ранн\w*\s+утр\w*|утренн\w*\s+(?:вылет\w*|рейс\w*)|early\s+morning)\b")
_TIME_SUBJECT = re.compile(r"\b(?:вылет\w*|перелет\w*|рейс\w*|departure_time|departure_window|flight|departure)\b")
_TIME_REFERENCE = re.compile(r"\b(?:утр\w*|ранн\w*\s+(?:вылет\w*|рейс\w*)|время\s+(?:вылета|отправления)|departure_time|departure_window|morning)\b")
_ARRIVAL = re.compile(r"\b(?:заезд\w*|выезд\w*|заселен\w*|выселен\w*|check.?in|check.?out)\b")
_REMOVE = re.compile(r"\b(?:сними|убери|удали|отмени|очисти|перестань\s+учитывать|не\s+учитывай|remove|clear|drop|delete)\b")
_KEEP = re.compile(r"\b(?:не\s+(?:снимай|убирай|удаляй|отменяй|очищай)|сохрани|оставь\s+прежн\w*|keep|don't\s+remove)\b")
_NEGATED_REQUEST = re.compile(r"\b(?:не|not)\s+(?:хочу|хотим|нужен|нужна|нужно|ищи|найди|выбирай|добавляй|"
    r"устанавливай|ставь|миним\w*|дешев\w*|рано|ранн\w*|prefer|want|set)\b")
_DECLINED = re.compile(r"\b(?:не\s+(?:нужен|нужна|нужно|нужны|важен|важна|важно|принципиален|принципиальна)|больше\s+не)\b")
_FREE_TIME = re.compile(r"\b(?:время\s+(?:вылета|отправления)\s+(?:теперь\s+)?(?:люб\w*|свобод\w*|не\s+важн\w*)|"
    r"(?:люб\w*|свобод\w*)\s+время\s+(?:вылета|отправления)|(?:вылет|рейс)\s+в\s+любое\s+время)\b")
_DISCUSSION = re.compile(r"\b(?:только\s+обсужда\w*|ничего\s+не\s+меняй|пока\s+не\s+(?:применяй|добавляй|ставь)|"
    r"гипотетическ\w*|hypothetically|do\s+not\s+apply)\b")
_WEIGHT = re.compile(r"\b(?:вес\s+(?:(?:этого|данного)\s+)?(?:пожелания|предпочтения|departure_time|departure_window|price_priority)|"
    r"weight)\s*[:=]?\s*(0(?:[.,]\d{1,6})?|1(?:[.,]0{1,6})?)(?![\w.,])")


def _key(text):
    return " ".join(text.casefold().replace("ё", "е").split())


def _clauses(text):
    unquoted = _without_quoted_spans(re.sub(r"`[^`]*`", " ", text))
    if unquoted is None or "`" in unquoted:
        return []
    result = []
    pieces = re.split(r"([!?;\n]|(?<!\d)\.|\.(?!\d))", unquoted)
    blocked = False
    for index in range(0, len(pieces), 2):
        sentence = _key(pieces[index])
        end = pieces[index+1] if index+1 < len(pieces) else ""
        if end == "?" or sentence.lstrip().startswith(">"):
            continue
        for branch_index, branch in enumerate(_FRESH.split(sentence)):
            if branch_index > 0 or re.match(r"^(?:теперь|сейчас)\b", branch):
                blocked = False
            if _NON_CURRENT.search(branch) or re.search(r"\b(?:if|would|maybe|previously|earlier|yesterday|suppose)\b", branch):
                blocked = True
                continue
            if blocked:
                continue
            result.extend(part.strip() for part in re.split(r"(?<!\d),|,(?!\d)|\b(?:а|но)\b", branch) if part.strip())
    return [] if any(_DISCUSSION.search(clause) for clause in result) else result


def _authority(state, text):
    clauses = _clauses(text)
    positive, removal, keep, weights = set(), set(), set(), {}
    named = set()
    unbound_weights = set()
    generic_remove = False
    for clause in clauses:
        anchors = set()
        if _PRICE.search(clause):
            anchors.add("price_priority")
        if _TIME_REFERENCE.search(clause):
            anchors.add("departure_time")
        named.update(anchors)
        if _KEEP.search(clause):
            keep.update(anchors)
        remove = bool(_REMOVE.search(clause) and not _KEEP.search(clause))
        if remove:
            removal.update(anchors)
            if re.search(r"\b(?:все\s+(?:пожелан\w*|предпочтен\w*)|all\s+preferences)\b", clause):
                removal.update({"price_priority", "departure_time"})
            elif re.search(r"\b(?:это\s+(?:пожелан\w*|предпочтен\w*)|this\s+preference)\b", clause):
                generic_remove = True
        if _FREE_TIME.search(clause) and not _KEEP.search(clause):
            removal.add("departure_time")
        if remove or _KEEP.search(clause) or _NEGATED_REQUEST.search(clause) or _DECLINED.search(clause):
            continue
        if _PRICE.search(clause) and (_AFFIRM.search(clause) or _BARE_PRICE.fullmatch(clause) or explicit_lowest_cost(clause)):
            positive.add("price_priority")
        if _MORNING.search(clause) and not (_ARRIVAL.search(clause) and not _TIME_SUBJECT.search(clause)):
            facts = _facts(state, clause, {})
            if _TIME_SUBJECT.search(clause) or _AFFIRM.search(clause) or len(facts["origin"]) == 1:
                positive.add("departure_time")
        for match in _WEIGHT.finditer(clause):
            value = Decimal(match[1].replace(",", "."))
            if not anchors:
                unbound_weights.add(value)
            for family in anchors:
                weights.setdefault(family, set()).add(value)
    if unbound_weights and len(named) == 1:
        weights.setdefault(next(iter(named)), set()).update(unbound_weights)
    if generic_remove and len(named) == 1:
        removal.update(named)
    return positive, removal, keep, weights


def guard_preference_operations(before_state, operations, text):
    """Filter only model preferences; typed commands retain domain validation."""
    data = before_state.model_dump(mode="json") if hasattr(before_state, "model_dump") else deepcopy(before_state)
    copied = [op.model_dump(mode="json") if hasattr(op, "model_dump") else deepcopy(op) for op in operations]
    meta = {"notes": [], "unknowns": [], "conflicts": []}
    if not isinstance(data, dict) or not isinstance(text, str) or not 0 < len(text) <= 20_000:
        return copied, meta
    positive, removal, keep, weights = _authority(data, text)
    ambiguous = positive & removal | removal & keep
    if ambiguous:
        meta["conflicts"].append("Одновременно заданы несовместимые изменения пожелания. Его нужно включить, сохранить или снять?")
    prior = {item.get("key"): item for item in data.get("preferences", []) if isinstance(item, dict)}
    result = []
    for operation in copied:
        kind, value = operation.get("type"), operation.get("value")
        if kind not in {"set_preference", "clear_preference"} or not isinstance(value, dict):
            result.append(operation); continue
        key = value.get("key")
        family = _FAMILY.get(key) if isinstance(key, str) else None
        if family is None:
            result.append(operation)
            meta["notes"].append({"code": "preference_key_outside_grounding_scope", "field": "preferences",
                "status": "normal_validation_required", "finding_id": FINDING_ID})
            continue
        saved = prior.get(key)
        saved_weight = saved.get("weight", _DEFAULT_WEIGHT) if saved else None
        proposed_weight = value.get("weight", saved_weight if saved else _DEFAULT_WEIGHT)
        noop = (kind == "set_preference" and saved is not None and value.get("value") == saved.get("value")
            and proposed_weight == saved_weight)
        if noop and family not in ambiguous:
            result.append(operation); continue
        allowed = family in (positive if kind == "set_preference" else removal)
        if not allowed or family in ambiguous:
            meta["notes"].append({"code": "preference_change_not_authorized", "field": "preferences", "key": key,
                "operation": kind, "status": "suppressed", "finding_id": FINDING_ID})
            continue
        if kind == "set_preference" and type(proposed_weight) in {int, float} and math.isfinite(proposed_weight) and 0 <= proposed_weight <= 1:
            literal_weights = weights.get(family, set())
            if literal_weights and literal_weights != {Decimal(str(proposed_weight))}:
                meta["conflicts"].append("Предложенный вес пожелания не совпадает с указанным. Уточните вес этого пожелания.")
                continue
            if not literal_weights:
                if saved is not None and "weight" in value and value["weight"] != saved_weight:
                    operation["value"] = {name: item for name, item in value.items() if name != "weight"}
                    meta["notes"].append({"code": "existing_preference_weight_preserved", "key": key, "finding_id": FINDING_ID})
                elif saved is None and "weight" in value and value["weight"] != _DEFAULT_WEIGHT:
                    operation["value"] = {**value, "weight": _DEFAULT_WEIGHT}
                    meta["notes"].append({"code": "new_preference_default_weight", "key": key, "finding_id": FINDING_ID})
        result.append(operation)
    meta["conflicts"] = list(dict.fromkeys(meta["conflicts"]))
    return result, meta
