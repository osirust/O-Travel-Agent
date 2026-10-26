"""One bounded omitted soft preference; never general text-to-state parsing."""
import re
from copy import deepcopy

_COMMAND = re.compile(
    r"^(?:пожалуйста[, ]+)?(?:ищи|найди|поищи|подбери|выбирай)\s+"
    r"(?:(?:варианты?\s+с\s+|поездку\s+с\s+))?"
    r"(?:миним\w*\s+(?:цен\w*|стоим\w*)|(?:сам\w*\s+)?дешев\w*(?:\s+вариант\w*)?|подешевле)\b"
)


def explicit_lowest_cost(text: str) -> bool:
    if not isinstance(text, str) or not 0 < len(text) <= 20_000:
        return False
    # Quoted examples and a conditional/history preamble never authorize edits.
    key = re.sub(r'[«“\"][^»”\"]*[»”\"]', '', text.casefold().replace('ё', 'е'))
    for clause in re.split(r'[.!?;\n]', key):
        clause = ' '.join(clause.split())
        if re.search(r'\b(?:если|допустим|раньше|прежде|цитата|например)\b', clause):
            return False
        if _COMMAND.search(clause):
            return True
    return False


def price_preference_operation() -> dict:
    return {"type": "set_preference", "target_id": None,
            "value": {"key": "price_priority", "value": "lowest_total"}}


def ground_lowest_cost(operations: list, text: str) -> tuple[list[dict], list[dict]]:
    copied = [op.model_dump(mode='json') if hasattr(op, 'model_dump') else deepcopy(op) for op in operations]
    if not explicit_lowest_cost(text):
        return copied, []
    if not any(op.get('type') == 'set_preference' and op.get('value', {}).get('key') == 'price_priority'
               and op.get('value', {}).get('value') == 'lowest_total' for op in copied):
        copied.append(price_preference_operation())
    return copied, [{"finding_id": "AFTER80-PRICE-INTAKE-001", "field": "preferences.price_priority",
                     "source": "current_message_literal", "status": "explicit_lowest_cost"}]
