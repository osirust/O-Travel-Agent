"""Bounded explanation of current HYBRID search evidence; no tools or mutation.

Raw provider/model messages and identifiers never become user text. Missing
evidence is not sold-out inventory. Source labels do not prove a real booking.
"""
from __future__ import annotations

import re
from collections import Counter
from copy import deepcopy
from typing import Any

from .domain import DomainError, TripState, canonical_city

_SOURCE_LABELS = {"DEMO": "демонстрационный расчёт (DEMO)", "SANDBOX": "тестовые тарифы (SANDBOX)",
                  "LIVE_INDICATIVE": "справочные цены поиска (LIVE_INDICATIVE)", "UNVERIFIED": "данные с неподтверждённым типом источника"}
_KIND_LABELS = {"hotel": "проживание", "flight": "перелёты", "train": "поезда", "transfer": "трансферы", "other": "прочие компоненты"}
_TEXT = {
    "source_rate_limited": "источник временно ограничил запросы; время восстановления не подтверждено",
    "source_failure": "получить проверяемый ответ источника не удалось",
    "source_not_configured": "источник недоступен в текущей конфигурации",
    "source_request_unsupported": "источник не поддержал направление или условия этого запроса",
    "search_limit_reached": "достигнут предел обращений в этом поиске",
    "hard_condition_unverified": "ответ источника не подтвердил обязательное условие поездки",
    "fees_unverified": "в ответе не подтверждены все обязательные сборы",
    "allocation_unverified": "источник не подтвердил распределение группы по указанным номерам",
    "capacity_unverified": "вместимость по ответу источника не подтверждена; это не означает отсутствия мест",
    "group_fare_unverified": "источник не подтвердил общую цену для всей группы",
    "price_not_comparable_or_unpriced": "источник не подтвердил сопоставимую цену в валюте бюджета; конвертация не выполнялась",
    "component_total_unverified": "ответ не подтвердил общую цену компонента для заданных дат и состава",
    "snapshot_requires_refresh": "срок или актуальность снимка не подтверждены; нужна перепроверка",
    "identity_unverified": "не подтверждено соответствие предложения выбранному отелю или маршруту",
    "no_source_offers": "источник не вернул предложений для этого запроса; отсутствие мест этим не доказано",
    "source_data_unverified": "данных источника недостаточно, чтобы включить предложение в план",
}
_REASONS = {
    "unpriced_or_fx_required": "price_not_comparable_or_unpriced", "fx_required": "price_not_comparable_or_unpriced",
    "mandatory_fees_unknown": "fees_unverified", "allocation_unverified": "allocation_unverified",
    "capacity_unverified": "capacity_unverified", "group_quote_unverified": "group_fare_unverified",
    "hard_constraint_unverified": "hard_condition_unverified", "not_component_total": "component_total_unverified",
    "stale_source_snapshot": "snapshot_requires_refresh", "invalid_source_lifetime": "snapshot_requires_refresh",
    "source_binding_mismatch": "identity_unverified", "unrequested_hotel_id": "identity_unverified",
    "provider_hotel_id_unknown": "identity_unverified", "source_mismatch": "identity_unverified",
    "invalid_shape": "source_data_unverified", "unknown_source_type": "source_data_unverified",
    "no_matches": "no_source_offers",
}
_ERRORS = {
    **{code: "source_rate_limited" for code in ("RATE_LIMITED", "SOURCE_COOLDOWN")},
    **{code: "source_failure" for code in ("HTTP_ERROR", "REQUEST_TIMEOUT", "NETWORK_ERROR", "PROVIDER_FAILURE", "PROVIDER_RESPONSE_ERROR", "INVALID_RESPONSE")},
    **{code: "source_not_configured" for code in ("CONFIG_MISSING", "CONFIG_BLOCKED")},
    **{code: "source_request_unsupported" for code in ("UNSUPPORTED_REQUEST", "UNSUPPORTED_CITY", "UNSUPPORTED_PROVIDER", "unsupported_provider_geography")},
    "TOOL_BUDGET_EXHAUSTED": "search_limit_reached", "NO_MATCHES": "no_source_offers",
    **_REASONS,
}
_PRIORITY = {code: index for index, code in enumerate(_TEXT)}


def _city(value: Any) -> str | None:
    try:
        return canonical_city(value) if isinstance(value, str) else None
    except DomainError:
        return None


def _component_labels(state: dict) -> dict[str, dict]:
    candidates = []
    for kind, collection in (("stay", "stays"), ("leg", "legs")):
        rows = state.get(collection)
        for index, item in enumerate(rows if isinstance(rows, list) else [], 1):
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            if kind == "stay":
                city = _city(item.get("city"))
                label = f"Проживание: {city}" if city else f"Проживание №{index}"
            else:
                origin, destination = _city(item.get("origin")), _city(item.get("destination"))
                mode = "Поезд" if item.get("mode") == "train" else "Перелёт"
                label = f"{mode}: {origin} → {destination}" if origin and destination else f"{mode} №{index}"
            candidates.append((item["id"], {"kind": kind, "index": index, "label": label}))
    counts = Counter(key for key, _ in candidates)
    return {key: value for key, value in candidates if counts[key] == 1}


def _current(plan: Any, state: dict) -> bool:
    return (isinstance(plan, dict) and isinstance(state.get("trip_id"), str)
        and bool(state["trip_id"]) and plan.get("trip_id") == state["trip_id"]
        and type(state.get("state_version")) is int and type(plan.get("state_version")) is int
        and plan["state_version"] == state["state_version"])


def _error_reasons(error: dict) -> list[str]:
    code = error.get("code")
    if not isinstance(code, str):
        return ["source_data_unverified"]
    if code == "NO_VERIFIED_COMPONENT":
        # Orchestrator emits these controlled tokens in its diagnostic message.
        # Recognise only complete known tokens; never copy the diagnostic text.
        message = error.get("message")
        message = message[:2000] if isinstance(message, str) else ""
        reasons = [target for token, target in _REASONS.items()
                   if re.search(r"(?<![a-zA-Z0-9_])" + re.escape(token) + r"(?![a-zA-Z0-9_])", message)]
        return list(dict.fromkeys(reasons)) or ["source_data_unverified"]
    return [_ERRORS.get(code, "source_data_unverified")]


def render_search_explanation(state: TripState | dict, search_result: Any, *, max_reasons: int = 4) -> dict:
    """Return code-owned ``text`` and an identifier-free machine ``summary``.

    The caller supplies its current owned TripSearch result, not raw provider
    HTTP. Plans additionally require exact trip/version association here. The
    result is an explanation, never an availability check or checkout consent.
    """
    if type(max_reasons) is not int or not 3 <= max_reasons <= 5:
        raise ValueError("max_reasons must be an integer between 3 and 5")
    data = state.model_dump(mode="json") if isinstance(state, TripState) else state
    summary = {"format": "otravel.hybrid_search_explanation.v1", "applicable": False,
               "status": "not_applicable", "source_counts": {}, "reasons": [], "omitted_reason_count": 0,
               "real_booking_confirmed": False}
    policy = data.get("search_policy") if isinstance(data, dict) else None
    if (not isinstance(policy, dict) or policy.get("mode") != "HYBRID" or not isinstance(search_result, dict)
            or not any(key in search_result for key in ("plans", "partial_plan", "offers", "component_errors"))):
        return {"text": "", "summary": summary}
    summary["applicable"] = True
    candidates = search_result.get("plans")
    candidates = candidates if isinstance(candidates, list) else []
    if not candidates and isinstance(search_result.get("partial_plan"), dict):
        candidates = [search_result["partial_plan"]]
    if not candidates and isinstance(search_result.get("offers"), list):
        candidates = [search_result]
    plans = [plan for plan in candidates if _current(plan, data)]
    if candidates and not plans:
        summary["status"] = "stale_or_foreign"
        return {"text": "Результат не связан с текущей версией этой поездки. Для объяснения нужен новый поиск.", "summary": summary}
    # Explain the first current displayed plan only; alternative plans must not
    # multiply counts or be combined into a nonexistent composite itinerary.
    plan = plans[0] if plans else {}
    offers = plan.get("offers")
    offers = offers if isinstance(offers, list) else []
    labels = _component_labels(data)
    generic = {"kind": "trip", "index": None, "label": "Поездка"}
    grouped: dict[str, list[dict]] = {}

    def add(code: str, identifier: Any = None):
        target = labels.get(identifier, generic) if isinstance(identifier, str) else generic
        targets = grouped.setdefault(code, [])
        if target not in targets:
            targets.append(deepcopy(target))

    errors = search_result.get("component_errors")
    for error in (errors if isinstance(errors, list) else [])[:64]:
        if not isinstance(error, dict):
            add("source_data_unverified")
            continue
        details = error.get("details")
        identifier = details.get("component_id") if isinstance(details, dict) else None
        for code in _error_reasons(error):
            add(code, identifier)

    source_counts: Counter = Counter()
    kinds: dict[str, set[str]] = {}
    for offer in offers[:64]:
        if not isinstance(offer, dict):
            add("source_data_unverified")
            continue
        source = offer.get("source_type")
        source = source if isinstance(source, str) and source in _SOURCE_LABELS else "UNVERIFIED"
        source_counts[source] += 1
        details = offer.get("details")
        details = details if isinstance(details, dict) else {}
        kind = details.get("kind", offer.get("kind"))
        kind = kind if isinstance(kind, str) and kind in _KIND_LABELS else "other"
        kinds.setdefault(kind, set()).add(source)
        if source == "UNVERIFIED":
            add("source_data_unverified")
        if source not in {"SANDBOX", "LIVE_INDICATIVE"}:
            continue
        binding = details.get("binding")
        identifier = binding.get("stay_id") if isinstance(binding, dict) else None
        if kind == "hotel":
            if details.get("mandatory_fees_known") is not True and details.get("fees_included") is not True:
                add("fees_unverified", identifier)
            if details.get("allocation_verified") is not True:
                add("allocation_unverified", identifier)
            if details.get("capacity_verified") is not True:
                add("capacity_unverified", identifier)
    summary.update(source_counts=dict(sorted(source_counts.items())), contains_demo=bool(source_counts["DEMO"]),
        uses_sandbox=bool(source_counts["SANDBOX"]), uses_indicative=bool(source_counts["LIVE_INDICATIVE"]))
    summary["status"] = ("complete" if plan.get("ready_for_simulated_checkout") is True
        and isinstance(plan.get("coverage"), dict) and plan["coverage"].get("complete") is True else "partial" if offers else "empty")
    selected_codes = sorted(grouped, key=lambda code: _PRIORITY[code])[:max_reasons]
    summary["omitted_reason_count"] = max(0, len(grouped) - len(selected_codes))
    summary["reasons"] = [{"code": code, "targets": grouped[code][:10]} for code in selected_codes]
    parts = []
    if kinds:
        sources = [f"{_KIND_LABELS[kind]} — " + ", ".join(_SOURCE_LABELS[s] for s in sorted(kinds[kind]))
                   for kind in _KIND_LABELS if kind in kinds]
        parts.append("В текущем расчёте: " + "; ".join(sources) + ".")
    else:
        parts.append("Полный план пока не собран.")
    for code in selected_codes:
        targets = grouped[code]
        prefix = "; ".join(target["label"] for target in targets[:3])
        if len(targets) > 3:
            prefix += f" и ещё {len(targets) - 3} компонента"
        parts.append(prefix + ": " + _TEXT[code] + ".")
    if summary["omitted_reason_count"]:
        parts.append(f"Есть ещё {summary['omitted_reason_count']} причин, требующих проверки.")
    if offers:
        parts.append("Этот расчёт не подтверждает наличие мест или реальное бронирование у поставщика.")
    return {"text": "\n".join(parts), "summary": summary}
