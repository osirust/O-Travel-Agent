"""Ground one missing question for a fresh, explicit travel-planning wish."""
import re

from .clarifications import derive_pending_field, human_question
from .domain import TripState, validate_trip


def explicit_planning_wish(text: str) -> bool:
    if not isinstance(text, str) or not 0 < len(text) <= 600:
        return False
    key = " ".join(text.casefold().replace("ё", "е").split())
    if re.search(r"\b(?:если|бы|допустим|предположим|цитата|не спрашивай|не задавай|без вопросов)\b", key):
        return False
    return bool(re.match(
        r"^(?:(?:хочу|хотим)\s+(?:отдохнуть|поехать|путешествовать|в тепло|в отпуск|на море|в горы|поездку|тур)"
        r"|(?:планирую|планируем)\s+(?:поездку|отпуск|путешествие)"
        r"|(?:собираюсь|собираемся)\s+(?:отдохнуть|поехать|путешествовать)"
        r"|(?:мне|нам)\s+нужна\s+поездка)\b", key))


def initial_planning_question(state: dict, pending, text: str) -> dict | None:
    # An established trip, user pause, question, proposal or existing target
    # retains its own lifecycle. This does not infer a destination or budget.
    if (pending or state.get("origin") or state.get("travelers") or state.get("stays")
            or not explicit_planning_wish(text)):
        return None
    current = TripState.model_validate(state)
    for issue in validate_trip(current):
        if not issue["code"].startswith("missing_"):
            continue
        binding = derive_pending_field([issue], current)
        if binding.get("field"):
            question = human_question(issue, current, binding.get("context"))
            return {"question": question, "context": {**binding.get("context", {}),
                "field": binding["field"], "target_id": binding.get("target_id")}}
    return None
