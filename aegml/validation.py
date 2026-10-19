"""Проверки пользовательского ввода без догадок о датах и бюджете."""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .contracts import TripRequest


@dataclass
class ValidationResult:
    request: TripRequest | None = None
    questions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def validate_request(data: Any) -> ValidationResult:
    result = ValidationResult()
    if not isinstance(data, dict):
        result.errors.append("Запрос должен быть JSON-объектом.")
        return result

    allowed = {"destination", "check_in", "check_out", "adults", "max_total_rub"}
    unknown = sorted(set(data) - allowed)
    if unknown:
        result.errors.append("Неизвестные поля: " + ", ".join(unknown) + ".")

    prompts = {
        "destination": "В какой город вы хотите поехать?",
        "check_in": "Какая дата заезда? Укажите ГГГГ-ММ-ДД.",
        "check_out": "Какая дата выезда? Укажите ГГГГ-ММ-ДД.",
        "adults": "Сколько взрослых поедет?",
    }
    missing = {
        key for key in prompts
        if key not in data or data[key] is None or data[key] == ""
    }
    result.questions.extend(prompts[key] for key in prompts if key in missing)

    destination = data.get("destination")
    if "destination" not in missing:
        if not isinstance(destination, str) or not destination.strip():
            result.errors.append("destination должен быть непустой строкой с городом.")

    dates: dict[str, date] = {}
    for key in ("check_in", "check_out"):
        if key in missing:
            continue
        value = data[key]
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            result.errors.append(f"{key}: ожидается дата в формате ГГГГ-ММ-ДД.")
            continue
        try:
            dates[key] = date.fromisoformat(value)
        except ValueError:
            result.errors.append(f"{key}: такой календарной даты не существует.")
    if len(dates) == 2 and dates["check_out"] <= dates["check_in"]:
        result.errors.append("Дата выезда должна быть позже даты заезда.")

    adults = data.get("adults")
    if "adults" not in missing and (type(adults) is not int or adults <= 0):
        result.errors.append("adults должен быть целым положительным числом.")

    budget = data.get("max_total_rub")
    if budget is not None and (type(budget) is not int or budget <= 0):
        result.errors.append("max_total_rub должен быть целым положительным числом рублей.")

    if not result.questions and not result.errors:
        result.request = TripRequest(
            destination=destination.strip(),
            check_in=dates["check_in"],
            check_out=dates["check_out"],
            adults=adults,
            max_total_rub=budget,
        )
    return result
