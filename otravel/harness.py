"""One durable worker per session. PostgreSQL locks never span provider HTTP."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import hashlib
import json
import re
from time import perf_counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .config import Settings
from .domain import DomainError, Operation, TripState, apply_operations, sample_trip, validate_trip
from .inventory import DemoInventory
from .providers import LLMGateway, ProviderError
from .store import Conflict, StoreError


TYPED_PREFIX = "\x1eOTRAVEL_TYPED_V1:"
PAUSE_PHRASES = {"подожди", "подожди, я ещё дополню", "подожди, я еще дополню", "подожди, я ещё допишу", "подожди, я еще допишу", "пауза"}
RESUME_PHRASES = {"продолжай", "продолжить"}


def exact_control(text: str) -> str | None:
    value = text.strip().casefold().rstrip(".! ")
    return "pause" if value in PAUSE_PHRASES else "resume" if value in RESUME_PHRASES else None


def state_fingerprint(state: dict) -> str:
    value = {key: item for key, item in state.items() if key != "state_version"}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def encode_command(kind: str, session: dict, **fields: Any) -> str:
    return TYPED_PREFIX + json.dumps({"kind": kind, "base_state_hash": state_fingerprint(session["state"]), **fields}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class _Control(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["continue", "wait", "resume", "request_checkout", "confirm", "none"]


class ModelResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operations: list[Operation] = Field(max_length=30)
    control: _Control
    reply: str
    unknowns: list[str]
    conflicts: list[str]


OPERATION_GUIDE = {
    "instruction": "Извлекай изменения только из current_message. История — контекст, её старые изменения повторно не применять. Ничего не бронировать. Отсутствующее поле не означает false. Все цены и бюджет в integer amount_minor (копейки RUB).",
    "supported_operations": {
        "set_origin": {"value": {"city": "Казань"}},
        "set_budget": {"value": {"amount_minor": 50000000, "currency": "RUB", "scope": "whole_trip"}},
        "add_traveler": {"value": {"type": "child", "age": 6, "group_id": "EXISTING_GROUP_ID"}},
        "update_traveler": {"target_id": "EXISTING_TRAVELER_ID", "value": {"age": 7}},
        "remove_traveler": {"target_id": "EXISTING_TRAVELER_ID", "value": {}},
        "upsert_stay": {"target_id": "EXISTING_OR_NEW_STAY_ID", "value": {"city": "Стамбул", "check_in": "2026-11-06", "check_out": "2026-11-09"}},
        "remove_stay": {"target_id": "EXISTING_STAY_ID", "value": {}},
        "replace_hotel": {"target_id": "EXISTING_STAY_ID", "value": {"hotel_id": "SAVED_HOTEL_ID"}},
        "shift_return": {"value": {"days": 2}},
        "set_constraint": {"value": {"key": "breakfast", "value": True, "hard": True}},
        "clear_constraint": {"value": {"key": "breakfast"}},
    },
    "room_shape": {"id": "STABLE_ROOM_ID", "traveler_ids": ["EXISTING_TRAVELER_ID"]},
    "known_hard_constraints": ["breakfast", "refundable", "pets_allowed", "pets"],
    "do_not_invent": ["city", "date", "age", "budget", "hotel_id", "consent"],
}


def _problem(code: str, message: str, retryable: bool = False) -> dict:
    return {"code": code, "message": message, "retryable": retryable}


def _can_switch_model(error: Exception) -> bool:
    """A backup must not circumvent account/key/workspace or unknown quota limits."""
    if isinstance(error, ValidationError):
        return True
    if not isinstance(error, ProviderError):
        return False
    details = error.details if isinstance(error.details, dict) else {}
    limit_source = details.get("limit_source")
    if limit_source and limit_source != "upstream_provider_shared_pool":
        return False
    if error.code == "RATE_LIMITED":
        return limit_source == "upstream_provider_shared_pool"
    if error.code == "HTTP_ERROR":
        status = details.get("http_status")
        return type(status) is int and 500 <= status < 600
    return error.code in {"NETWORK_ERROR", "REQUEST_TIMEOUT", "INVALID_LLM_RESPONSE", "INVALID_RESPONSE"}


def _requested_model(gateway: Any, provider: str) -> str:
    config = getattr(gateway, "settings", {})
    if isinstance(config, dict) and provider in {"gemini", "openrouter"}:
        return str(config.get("GEMINI_MODEL" if provider == "gemini" else "OPENROUTER_MODEL", ""))
    return str(getattr(gateway, "last_call", {}).get("model", "controlled_test_adapter"))


class Harness:
    def __init__(self, settings: Settings, store: Any, llm: Any, travel: Any):
        self.settings, self.store, self.llm, self.travel = settings, store, llm, travel
        self.inventory = DemoInventory()
        self.tasks: dict[str, asyncio.Task] = {}
        self.maintenance: asyncio.Task | None = None
        self.closed = False

    async def start(self) -> None:
        await self.store.recover_stale_runs()
        await self.schedule_pending()
        self.maintenance = asyncio.create_task(self._maintenance())

    async def close(self) -> None:
        self.closed = True
        if self.maintenance:
            self.maintenance.cancel()
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, *([self.maintenance] if self.maintenance else []), return_exceptions=True)

    async def _maintenance(self) -> None:
        while True:
            await asyncio.sleep(5)
            try:
                await self.store.recover_stale_runs()
                await self.schedule_pending()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Do not log driver URLs, credentials, or raw provider exceptions.
                continue

    async def schedule_pending(self) -> None:
        for run in await self.store.list_pending_runs():
            self.schedule(run["session_id"])

    def schedule(self, session_id: str) -> None:
        if self.closed:
            return
        current = self.tasks.get(session_id)
        if current is None or current.done():
            self.tasks[session_id] = asyncio.create_task(self._drain(session_id))
            # Retrieve task failures without logging DSNs or raw HTTP exceptions.
            self.tasks[session_id].add_done_callback(lambda task: None if task.cancelled() else task.exception())

    async def control(self, session_id: str, action: str, *, schedule: bool = True, write_reply: bool = True) -> dict:
        # This transaction increments the CAS version before cancellation.
        reply = ("Пауза сохранена. Жду явного «продолжай»." if action == "pause" else "Продолжаю с сохранённой поездкой.") if write_reply else ""
        session = await self.store.set_control(session_id, action, reply)
        if action == "pause":
            task = self.tasks.get(session_id)
            if task and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        elif schedule:
            self.schedule(session_id)
        return session

    async def control_message(self, run: dict, action: str) -> dict:
        session = await self.control(run["session_id"], action, schedule=False, write_reply=False)
        # A control message has already been durably accepted. It is not sent to NLU.
        result = await self.store.finish_run(run["run_id"], session["state_version"], None,
            "WAITING_USER" if action == "pause" else "TRIP_DRAFT",
            "Жду явного «продолжай»." if action == "pause" else "Продолжаю.",
            waiting_reason="user_pause" if action == "pause" else None,
            result={"control": action, "available_actions": ["commands", "demo", "resume"]})
        if action == "resume":
            self.schedule(run["session_id"])
        return result

    async def _drain(self, session_id: str) -> None:
        while not self.closed:
            session = await self.store.get_session(session_id)
            if session.get("waiting_reason") == "user_pause":
                return
            pending = [run for run in await self.store.list_pending_runs() if run["session_id"] == session_id]
            if not pending:
                return
            run_id = pending[0]["run_id"]
            if not await self.store.claim_run(run_id):
                return
            try:
                await self._process(await self.store.get_run(run_id))
            except asyncio.CancelledError:
                with suppress(Exception):
                    await asyncio.shield(self.store.fail_run(run_id, _problem("run_interrupted", "Выполнение остановлено; поездка сохранена.", True), preserve_session=True))
                raise
            except Conflict as exc:
                with suppress(Exception):
                    await self.store.fail_run(run_id, _problem(exc.code, exc.message, True), preserve_session=True)
            except (DomainError, ProviderError, ValidationError) as exc:
                code = getattr(exc, "code", "invalid_model_output")
                message = getattr(exc, "message", "Предложенные изменения не прошли проверку.")
                problem = _problem(code, message, getattr(exc, "retryable", False))
                if getattr(exc, "llm_meta", None):
                    problem["llm"] = exc.llm_meta
                if isinstance(exc, ProviderError) and isinstance(exc.details, dict):
                    problem["details"] = {key: exc.details[key] for key in ("limit_source", "provider_name", "http_status") if key in exc.details}
                with suppress(Exception):
                    await self.store.fail_run(run_id, problem, "Не удалось завершить обработку. Поездка сохранена; доступны формы и явный DEMO-сценарий.", preserve_session=False)
            except Exception:
                with suppress(Exception):
                    await self.store.fail_run(run_id, _problem("processing_error", "Ошибка обработки; состояние сохранено.", True), "Состояние сохранено. Можно повторить действие или воспользоваться формой.", preserve_session=False)

    async def _extract(self, session: dict, run: dict) -> tuple[ModelResult, dict]:
        history = await self.store.list_messages(session["session_id"])
        input_row = next((message for message in history if message["id"] == run["input_message_id"]), None)
        sequence = input_row["sequence"] if input_row else float("inf")
        preceding = [message for message in history if message["sequence"] <= sequence]
        shown = []
        seen = set()
        offer_fields = {"offer_id", "hotel_id", "component_id", "kind", "name", "city", "origin", "destination", "check_in", "check_out", "rate_type", "breakfast", "refundable", "total_minor", "currency", "source_type"}
        for message in reversed(preceding):
            prior_id = message.get("run_id")
            if message["role"] != "assistant" or not prior_id or prior_id in seen:
                continue
            seen.add(prior_id)
            prior = await self.store.get_run(prior_id)
            response = prior.get("result") or {}
            plans = (response.get("result") or {}).get("plans", [])
            if plans:
                shown.append({"run_id": prior_id, "plans": [{"plan_id": plan["plan_id"], "state_version": plan["state_version"], "total_minor": plan["total_minor"], "currency": plan["currency"], "offers": [{key: value for key, value in offer.items() if key in offer_fields} for offer in plan["offers"]]} for plan in plans[:3]]})
            if len(shown) >= 2:
                break
        history = [{"role": message["role"], "text": message["text"]} for message in preceding if not message["text"].startswith(TYPED_PREFIX)][-12:]
        context = {"current_message": run["input_text"], "trip": session["state"], "pending_question": session.get("pending_question"), "recent_messages": history, "shown_plans": shown, "reference_now_utc": datetime.now(timezone.utc).isoformat(), "default_timezone": "Europe/Moscow", "operation_contract": OPERATION_GUIDE}
        gateway = self.llm
        provider = self.settings.llm_mode if isinstance(gateway, LLMGateway) else "injected_test_adapter"
        fallback = None
        if isinstance(gateway, LLMGateway):
            config = gateway.settings
            primary = config.get("LLM_PROVIDER", self.settings.llm_mode)
            gemini_ready = bool(config.get("GEMINI_API_KEY")) and str(config.get("GEMINI_FREE_TIER_CONFIRMED", False)).lower() in {"true", "1", "yes"}
            router_ready = bool(config.get("OPENROUTER_API_KEY")) and str(config.get("OPENROUTER_MODEL", "")).endswith(":free")
            if primary == "gemini" and router_ready:
                fallback = LLMGateway({**config, "LLM_PROVIDER": "openrouter"})
            if primary == "gemini" and not gemini_ready:
                if fallback is None:
                    raise ProviderError("CONFIG_MISSING", "LLM API/подтверждённый бесплатный режим не настроены.")
                gateway, provider, fallback = fallback, "openrouter", None
            elif primary == "openrouter" and not router_ready:
                raise ProviderError("CONFIG_MISSING", "Бесплатная LLM API не настроена.")
            else:
                provider = primary
            # No backup is selected by default. Only an explicitly configured,
            # distinct :free model may be the second OpenRouter attempt.
            backup = str(config.get("OPENROUTER_BACKUP_MODEL", "") or "").strip()
            selected = str(getattr(gateway, "settings", {}).get("OPENROUTER_MODEL", ""))
            if provider == "openrouter" and router_ready and backup != selected and backup.endswith(":free") and re.fullmatch(r"[\w./:-]+", backup):
                fallback = LLMGateway({**config, "LLM_PROVIDER": "openrouter", "OPENROUTER_MODEL": backup})
        attempts = max(0, min(2, self.settings.max_llm_calls_per_run))
        temporary = gateway is not self.llm
        attempt_meta: list[dict] = []
        try:
            for attempt in range(attempts):
                # Only explicitly injected non-HTTP test adapters may use a test
                # budget; the real gateway always has the production ceiling.
                daily_limit = min(40, self.settings.max_llm_calls_per_day) if isinstance(self.llm, LLMGateway) else self.settings.max_llm_calls_per_day
                if not await self.store.reserve_model_call(session["session_id"], run["run_id"], daily_limit, provider):
                    raise ProviderError("MODEL_BUDGET_EXHAUSTED", "Дневной лимит вызовов LLM исчерпан.")
                requested_model = _requested_model(gateway, provider)
                started = perf_counter()
                try:
                    extracted = ModelResult.model_validate(await gateway.extract_operations(context))
                    last_call = getattr(gateway, "last_call", {})
                    actual = {"provider": str(last_call.get("provider", provider)), "model": str(last_call.get("model", requested_model)), "latency_ms": max(0, int((perf_counter() - started) * 1000))}
                    attempt_meta.append({**actual, "outcome": "response"})
                    await self.store.save_event(session["session_id"], run["run_id"], "MODEL_RESPONSE_RECEIVED", {**actual, "operations": len(extracted.operations), "attempt": attempt + 1})
                    return extracted, {**actual, "attempts": attempt_meta}
                except (ProviderError, ValidationError) as exc:
                    details = exc.details if isinstance(exc, ProviderError) and isinstance(exc.details, dict) else {}
                    failed = {"provider": provider, "model": requested_model, "code": getattr(exc, "code", "INVALID_LLM_RESPONSE"), "latency_ms": max(0, int((perf_counter() - started) * 1000)), **{key: details[key] for key in ("limit_source", "provider_name") if key in details}}
                    attempt_meta.append({**failed, "outcome": "failed"})
                    await self.store.save_event(session["session_id"], run["run_id"], "MODEL_CALL_FAILED", failed)
                    exc.llm_meta = {"provider": provider, "model": requested_model, "attempts": attempt_meta}
                    if attempt + 1 >= attempts:
                        raise
                    if fallback is not None and _can_switch_model(exc):
                        if temporary:
                            await gateway.close()
                        gateway, provider, temporary, fallback = fallback, "openrouter", True, None
                    elif not isinstance(exc, ValidationError) and not (isinstance(exc, ProviderError) and exc.code in {"INVALID_LLM_RESPONSE", "INVALID_RESPONSE"} and _can_switch_model(exc)):
                        raise
            raise ProviderError("MODEL_BUDGET_EXHAUSTED", "Вызовы LLM для этой обработки отключены.")
        except (ProviderError, ValidationError) as exc:
            if attempt_meta and not getattr(exc, "llm_meta", None):
                last = attempt_meta[-1]
                exc.llm_meta = {"provider": last["provider"], "model": last["model"], "attempts": attempt_meta}
            raise
        finally:
            if temporary:
                await gateway.close()
            if fallback is not None:
                await fallback.close()

    async def _process(self, run: dict) -> None:
        session = await self.store.get_session(run["session_id"])
        version = session["state_version"]
        if session.get("waiting_reason") == "user_pause":
            raise Conflict("Работа поставлена на паузу.", "user_paused")
        state = TripState.model_validate(session["state"])
        new_state: TripState | None = None
        changes: list[dict] = []
        questions: list[str] = []
        result: dict = {}
        model_meta: dict | None = None
        waiting_reason = None
        status = "TRIP_DRAFT"
        search = False
        # Explicit resume is newer execution control than additions accumulated
        # during that pause. Apply their data, not an obsolete request to wait.
        control_events = [event for event in await self.store.list_events(session["session_id"]) if event["event_type"] in {"USER_PAUSED", "USER_RESUMED"}]
        resumed_after_input = bool(control_events and control_events[-1]["event_type"] == "USER_RESUMED" and control_events[-1]["created_at"] > run["created_at"])
        if run["input_text"].startswith(TYPED_PREFIX):
            command = json.loads(run["input_text"][len(TYPED_PREFIX):])
            if command["base_state_hash"] != state_fingerprint(session["state"]):
                raise Conflict("Поездка изменилась после отправки формы.", "stale_state")
            kind = command["kind"]
            if kind == "commands":
                candidate, changes = apply_operations(state, command["operations"])
                new_state = candidate if changes else None
                search = command["search"]
            elif kind == "demo":
                new_state = sample_trip()
                new_state.trip_id = state.trip_id
                changes = [{"field": "trip", "before": state.model_dump(mode="json"), "after": new_state.model_dump(mode="json"), "operation": "load_explicit_demo"}]
                search = True
            elif kind == "search":
                search = True
            elif kind == "live_search":
                result = await self._live_search(session, run, command, state)
                questions = result.get("questions", [])
                status = "WAITING_USER" if questions else "OPTIONS_READY" if result.get("live_offers") or result.get("weather") or result.get("plans") else "TRIP_DRAFT"
                waiting_reason = "clarification" if questions else None
            else:
                raise DomainError("invalid_command", "Неизвестная структурированная команда.")
        else:
            extracted, model_meta = await self._extract(session, run)
            try:
                candidate, changes = apply_operations(state, extracted.operations)
            except DomainError as exc:
                exc.llm_meta = model_meta
                raise
            new_state = candidate if changes else None
            questions = extracted.conflicts + extracted.unknowns
            if extracted.control.action == "wait" and not resumed_after_input:
                status, waiting_reason = "WAITING_USER", "user_pause"
            elif questions:
                status, waiting_reason = "WAITING_USER", "clarification"
            elif extracted.control.action in {"request_checkout", "confirm"}:
                result["available_actions"] = ["checkout/preview", "checkout/confirm"]
                result["notice"] = "Выбор и текстовое «да» не создают заказ. Нужны preview и явное подтверждение его версии."
            else:
                search = True
        current = new_state or state
        if new_state is not None:
            current.state_version = version + 1
        if search and waiting_reason != "user_pause" and not questions:
            result = self.inventory.search_trip(current)
            questions = result["questions"]
            status = "OPTIONS_READY" if result["plans"] else "WAITING_USER" if questions else "TRIP_DRAFT"
            waiting_reason = "clarification" if questions else None
        elif not result and waiting_reason != "user_pause":
            questions = questions or [issue["message"] for issue in validate_trip(current) if issue["code"].startswith("missing_")]
            if questions:
                status, waiting_reason = "WAITING_USER", "clarification"
        if waiting_reason == "user_pause":
            reply = "Изменения сохранены. Жду явного «продолжай»; поиск и оформление остановлены."
        elif questions:
            reply = questions[0]
        elif result.get("plans"):
            reply = f"Подобрано DEMO-планов: {len(result['plans'])}. Все цены и наличие вымышлены; бронь не создана."
        elif result.get("live_offers"):
            reply = "Получены компоненты поставщика. Их статус указан отдельно; полная поездка и бронирование не подтверждены."
        elif result.get("weather"):
            reply = "Получен прогноз Open-Meteo на ближайшие 7 дней. Это не гарантия погоды в даты поездки."
        elif result.get("violations"):
            reply = result["violations"][0]["message"]
        elif result.get("source_type") == "DEMO" and "plans" in result:
            reply = "В конечном DEMO-каталоге нет подходящих вариантов. Бюджет и обязательные условия не ослаблены."
        else:
            reply = result.get("notice") or "Изменения сохранены. Доступны структурированные формы и явный DEMO-поиск."
        pending = {"question_id": f"{run['run_id']}-question", "text": questions[0], "questions": questions, "state_version": current.state_version, "reason": "clarification"} if questions and waiting_reason != "user_pause" else None
        if model_meta is not None:
            result["llm"] = model_meta
        await self.store.finish_run(run["run_id"], version, current.model_dump(mode="json") if new_state is not None else None, status, reply, pending_question=pending, waiting_reason=waiting_reason, changes=changes, result=result)

    async def _live_search(self, session: dict, run: dict, command: dict, state: TripState) -> dict:
        kind, provider, request = command["search_kind"], command["provider"], command["request"]
        await self.store.save_event(session["session_id"], run["run_id"], "TOOL_CALL_STARTED", {"kind": kind, "provider": provider})
        try:
            if kind == "hotels":
                offers = await self.travel.search_hotels(request, provider)
            elif kind == "flights":
                offers = await self.travel.search_flights(request, provider)
            else:
                weather = await self.travel.weather(request["city"], request["latitude"], request["longitude"])
                await self.store.save_event(session["session_id"], run["run_id"], "TOOL_CALL_COMPLETED", {"kind": kind, "provider": "open_meteo"})
                return {"weather": weather, "source_type": weather["source_type"], "plans": []}
            await self.store.save_event(session["session_id"], run["run_id"], "TOOL_CALL_COMPLETED", {"kind": kind, "provider": provider, "count": len(offers)})
            return {"live_offers": offers, "source_type": offers[0].get("source_type", "UNKNOWN") if offers else "NO_MATCHES", "plans": [], "warnings": ["Это отдельные компоненты, не проверенная многогородская корзина. Supplier orders не создаются."]}
        except ProviderError as exc:
            await self.store.save_event(session["session_id"], run["run_id"], "TOOL_CALL_FAILED", {"kind": kind, "provider": provider, "code": exc.code})
            if not command.get("demo_fallback"):
                raise
            result = self.inventory.search_trip(state)
            result["provider_error"] = _problem(exc.code, exc.message, exc.retryable)
            result["warnings"].append("Поставщик недоступен. По явному запросу показана DEMO-замена; это не реальные цены.")
            return result
