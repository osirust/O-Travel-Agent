"""Детерминированный публичный сценарий; LLM и бронирование не подключены."""

from dataclasses import asdict
from typing import Any

from .config import Config, create_language_model
from .demo import DemoHotelProvider, UnsupportedDestination
from .validation import validate_request


def prepare_demo(data: Any, config: Config) -> tuple[dict[str, Any], int]:
    # Проверяем выбранную интеграцию даже в структурированном сценарии.
    # Неподключённый local/api не может незаметно стать mock.
    create_language_model(config)
    base: dict[str, Any] = {
        "is_demo": True,
        "source": "fictional_static_catalog",
        "notice": "DEMO: отели вымышлены; цены и наличие не реальные. Бронь не создаётся.",
        "llm": {"mode": config.llm_mode, "called": False},
        "booking": {"status": "not_supported", "created": False},
    }
    validation = validate_request(data)
    if validation.errors or validation.questions:
        return {
            **base,
            "status": "invalid_request" if validation.errors else "needs_clarification",
            "errors": validation.errors,
            "questions": validation.questions,
            "offers": [],
        }, 2 if validation.errors else 0

    request = validation.request
    assert request is not None
    try:
        offers = DemoHotelProvider().search(request)
    except UnsupportedDestination as exc:
        return {
            **base,
            "status": "needs_clarification",
            "questions": [str(exc)],
            "offers": [],
        }, 0

    normalized = asdict(request)
    normalized["check_in"] = request.check_in.isoformat()
    normalized["check_out"] = request.check_out.isoformat()
    return {
        **base,
        "status": "completed_demo" if offers else "no_demo_matches",
        "request": normalized,
        "questions": [],
        "offers": [asdict(offer) for offer in offers],
        "price_basis": "fictional_total_for_all_adults_for_the_entire_stay",
        "message": (
            "Показаны вымышленные предложения без ранжирования."
            if offers else "В DEMO-каталоге нет примеров в заданном бюджете."
        ),
    }, 0
