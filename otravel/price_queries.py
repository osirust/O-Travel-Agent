"""Whole-trip price questions from persisted facts, without invented quotes."""
from __future__ import annotations

import re
from datetime import UTC, datetime

from .conversational import current_plans


def whole_trip_price_question(text: str) -> bool:
    if not isinstance(text, str) or not 0 < len(text) <= 180:
        return False
    key = " ".join(text.casefold().replace("ё", "е").split()).strip(" .!?")
    return bool(re.fullmatch(
        r"(?:а |ну )?(?:сколько (?:это |все это |наша поездка |поездка )?(?:будет стоить|стоит)"
        r"|(?:какая|какова) (?:общая |итоговая )?(?:цена|стоимость)(?: поездки| путешествия)?"
        r"|во сколько обойдется(?: поездка| путешествие| все это)?)", key))


def saved_price_answer(state, plans, *, now: datetime | None = None) -> str | None:
    """Explain stored comparable totals; never certify price/availability now."""
    from .trip_answers import _money

    now = now or datetime.now(UTC)
    valid = current_plans(state, plans)
    if not valid:
        return None
    lines = []
    for index, plan in enumerate(valid, 1):
        for offer in plan["offers"]:
            if offer.get("source_type") == "DEMO":
                continue
            try:
                expiry = datetime.fromisoformat(offer["expires_at"])
                if expiry.tzinfo is None or expiry <= now:
                    return None
            except (KeyError, TypeError, ValueError, AttributeError):
                return None
        amount = _money(plan.get("total_minor"), plan.get("currency"))
        if amount is None:
            return None  # A subtotal in one currency is not a whole-trip price.
        source = plan.get("source_type")
        label = {"DEMO": "демонстрационный расчёт", "SANDBOX": "тестовые тарифы API",
            "MIXED": "смешанный расчёт с отдельно отмеченными источниками",
            "LIVE_INDICATIVE": "ориентировочная цена из поиска",
            "LIVE_BOOKABLE": "предложение поставщика, требуется перепроверка"}.get(source)
        if label is None:
            return None
        lines.append(f"Вариант {index}: {amount} — {label}.")
    answer = "По сохранённым результатам поиска:\n" + "\n".join(lines)
    if any(plan.get("fx_reference_only") for plan in valid):
        answer += " Пересчёт валют справочный; курс оплаты и комиссии могут отличаться."
    answer += " Это сохранённая оценка: перед тестовым оформлением заново проверяем тарифы и наличие. Реальная бронь не создана."
    return answer
