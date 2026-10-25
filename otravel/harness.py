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
from .domain import CITIES, DomainError, Operation, TripState, apply_operations, canonical_city, sample_trip, validate_trip
from .inventory import DemoInventory
from .orchestrator import TripSearch
from .history import plan_history
from .geography import known_cities, parse_destination
from .money import ground_budget_operations
from .llm_contract import build_operation_schema
from .clarifications import PENDING_CONTEXT_CLEAR_KEYS, derive_pending_field, human_question, question_field, resolve_short_answer
from .date_slots import resolve_date_answer
from .observability import model_snapshot
from .providers import LLMGateway, ProviderError
from .store import Conflict, StoreError


TYPED_PREFIX = "\x1eOTRAVEL_TYPED_V1:"
PAUSE_PHRASES = {"подожди", "подожди, я ещё дополню", "подожди, я еще дополню", "подожди, я ещё допишу", "подожди, я еще допишу", "пауза"}
RESUME_PHRASES = {"продолжай", "продолжить"}
CANCEL_PHRASES = {"отмени обработку", "отмени текущий поиск", "останови поиск"}


def exact_control(text: str) -> str | None:
    value = text.strip().casefold().rstrip(".! ")
    return "pause" if value in PAUSE_PHRASES else "resume" if value in RESUME_PHRASES else "cancel" if value in CANCEL_PHRASES else None


def state_fingerprint(state: dict) -> str:
    value = {key: item for key, item in state.items() if key != "state_version"}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def explicit_pause_hint(text: str) -> bool:
    value = text.casefold().replace("ё", "е")
    return bool(re.search(r"\b(подожди|приостанови|пауза|поставь на паузу|допишу|дополню|не продолжай|не запускай поиск|ничего не ищи|пока не ищи|пока не подбирай|пока ничего не подбирай)\b", value))


def explicit_no_children(text: str) -> bool:
    # A complete explicit clause can establish zero children inside a compound
    # request. Broader/negated/conditional language still belongs to the model.
    clauses = re.split(r"[,;.!\n]", text.casefold().replace("ё", "е"))
    return any(" ".join(part.split()) in {"детей нет", "без детей", "дети не едут"} for part in clauses)


def encode_command(kind: str, session: dict, **fields: Any) -> str:
    return TYPED_PREFIX + json.dumps({"kind": kind, "base_state_hash": state_fingerprint(session["state"]), **fields}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class _Control(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["continue", "wait", "resume", "request_checkout", "confirm", "cancel", "none"]


class ModelResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operations: list[Operation] = Field(max_length=30)
    control: _Control
    reply: str
    unknowns: list[str]
    conflicts: list[str]
    speech_act: Literal["CHANGE", "ASK", "PROPOSE"] = "CHANGE"
    information_subject: str | None = Field(default=None, max_length=80)
    candidate_operations: list[Operation] = Field(default_factory=list, exclude=True)
    destination_request: dict[str, Any] | None = Field(default=None, exclude=True)
    operation_normalizations: list[dict[str, Any]] = Field(default_factory=list, exclude=True)
    pending_context: dict[str, Any] | None = Field(default=None, exclude=True)
    pending_context_clear: list[Literal["proposed_destination", "confirmation_required", "suggested_cities", "contextual_correction", "proposal_field"]] = Field(default_factory=list, max_length=5, exclude=True)
    response_source: str = Field(default="llm", exclude=True)


OPERATION_GUIDE = {
    "instruction": "Извлекай изменения только из current_message. История — контекст, её старые изменения повторно не применять. Ничего не бронировать. Не предлагай противоречивые значения одного поля: сообщи conflicts, тогда весь batch не применяется. Не спрашивай уже известный origin без явного намерения его изменить. Для существующего ребёнка используй его traveler.id, не создавай дубль. Отсутствующее поле не означает false. Все цены и бюджет в integer amount_minor (копейки RUB). Undo — отдельная единственная операция, отменяет последнюю успешную правку; cancel останавливает обработку, не откатывает поездку и не отменяет заказы.",
    "supported_operations": {
        "set_origin": {"value": {"city": "Казань"}},
        "set_budget": {"value": {"amount_minor": 50000000, "currency": "RUB", "scope": "whole_trip"}},
        "set_preference": {"value": {"key": "price_priority", "value": "lowest_total"}},
        "add_traveler": {"value": {"id": "new-traveler-1", "type": "child", "age": 6, "group_id": "EXISTING_GROUP_ID"}},
        "update_traveler": {"target_id": "EXISTING_TRAVELER_ID", "value": {"age": 7}},
        "remove_traveler": {"target_id": "EXISTING_TRAVELER_ID", "value": {}},
        "upsert_stay": {"target_id": "EXISTING_OR_NEW_STAY_ID", "value": {"city": "Стамбул", "check_in": "2026-11-06", "check_out": "2026-11-09"}},
        "remove_stay": {"target_id": "EXISTING_STAY_ID", "value": {}},
        "replace_hotel": {"target_id": "EXISTING_STAY_ID", "value": {"hotel_id": "SAVED_HOTEL_ID"}},
        "shift_return": {"value": {"days": 2}},
        "set_constraint": {"value": {"key": "breakfast", "value": True, "hard": True}},
        "clear_constraint": {"value": {"key": "breakfast"}},
        "undo": {"value": {}},
        "set_search_policy": {"value": {"mode": "HYBRID", "hotel_provider": "liteapi", "flight_provider": "duffel", "demo_fallback": True}},
    },
    "room_shape": {"id": "STABLE_ROOM_ID", "traveler_ids": ["EXISTING_TRAVELER_ID"]},
    "known_hard_constraints": ["breakfast", "refundable", "pets_allowed", "pets"],
    "supported_cities": list(CITIES),
    "known_geographic_cities": list(known_cities()),
    "constraint_contract": {"breakfast": "boolean: включённый завтрак конкретного тарифа", "refundable": "boolean: возвратность конкретного тарифа", "pets_allowed": "boolean: свойство отеля", "pets": {"kind": "dog|cat", "weight_kg": "known positive number"}},
    "do_not_invent": ["city", "date", "age", "budget", "hotel_id", "consent"],
}


def _problem(code: str, message: str, retryable: bool = False) -> dict:
    return {"code": code, "message": message, "retryable": retryable}


def operation_conflicts(state: TripState, operations: list[Operation]) -> list[str]:
    """Preflight contradictory writes before any field can be committed."""
    seen: dict[tuple, Any] = {}
    conflicts: list[str] = []
    items = [item if isinstance(item, Operation) else Operation.model_validate(item) for item in operations]
    if any(item.type == "undo" for item in items):
        if len(items) != 1 or items[0].value or items[0].target_id is not None:
            return ["Undo должен быть отдельной операцией без дополнительных правок и произвольного target."]
        return []

    def write(key: tuple, value: Any) -> None:
        normalized = json.dumps(value, sort_keys=True, ensure_ascii=False)
        if key in seen and seen[key] != normalized:
            conflicts.append("В одной реплике предложены разные значения поля «" + ".".join(str(part) for part in key) + "». Уточните итоговое значение.")
        seen[key] = normalized

    last_stay = state.stays[-1].id if state.stays else None
    for item in items:
        value, target = item.value, item.target_id or item.value.get("id")
        if item.type == "set_origin":
            for key in ("city", "origin"):
                if key in value:
                    write(("trip", "origin"), canonical_city(value[key]))
        elif item.type == "set_budget":
            for key, default in (("amount_minor", None), ("currency", "RUB"), ("scope", "whole_trip")):
                write(("budget", key), value.get(key, default))
        elif item.type == "set_search_policy":
            for key, field in value.items():
                write(("search_policy", key), field)
        elif item.type in {"upsert_stay", "replace_hotel", "remove_stay"}:
            write(("stay", target, "exists"), item.type != "remove_stay")
            for key, field in value.items():
                if key != "id":
                    write(("stay", target, key), canonical_city(field) if key == "city" and field else field)
                    if target == last_stay and key == "check_out":
                        write(("trip", "return_edit_kind"), "absolute")
        elif item.type in {"add_traveler", "update_traveler", "remove_traveler"}:
            if target is not None:
                write(("traveler", target, "exists"), item.type != "remove_traveler")
                for key, field in value.items():
                    if key != "id":
                        write(("traveler", target, key), field)
        elif item.type in {"set_constraint", "clear_constraint"}:
            key = value.get("key") or target
            write(("constraint", value.get("stay_id"), key), {"value": value.get("value"), "hard": value.get("hard", True)} if item.type == "set_constraint" else {"removed": True})
        elif item.type == "shift_return":
            write(("trip", "return_edit_kind"), "relative")
            write(("trip", "return_shift_days"), value.get("days"))
    return list(dict.fromkeys(conflicts))


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
    if isinstance(config, dict) and provider == "ollama":
        return str(config.get("OLLAMA_MODEL", "qwen3.5:4b"))
    if isinstance(config, dict) and provider == "groq":
        return str(config.get("GROQ_MODEL", "qwen/qwen3.8-27b"))
    if isinstance(config, dict) and provider == "cloudflare":
        return str(config.get("CLOUDFLARE_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"))
    if isinstance(config, dict) and provider == "zai":
        return str(config.get("ZAI_MODEL", "glm-4.7-flash"))
    if isinstance(config, dict) and provider in {"gemini", "openrouter"}:
        return str(config.get("GEMINI_MODEL" if provider == "gemini" else "OPENROUTER_MODEL", ""))
    return str(getattr(gateway, "last_call", {}).get("model", "controlled_test_adapter"))


class Harness:
    def __init__(self, settings: Settings, store: Any, llm: Any, travel: Any):
        self.settings, self.store, self.llm, self.travel = settings, store, llm, travel
        self.inventory = DemoInventory()
        self.tasks: dict[str, asyncio.Task] = {}
        self.active: dict[str, str] = {}
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
        if action == "cancel":
            return await self.cancel_control(run)
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

    async def cancel_control(self, run: dict) -> dict:
        session_id = run["session_id"]
        session = await self.store.get_session(session_id)
        expected = run.get("request_expected_version")
        if expected is not None and expected != session["state_version"]:
            await self.store.fail_run(run["run_id"], _problem("stale_state", "Поездка изменилась перед отменой.", True))
            raise Conflict("Поездка изменилась перед отменой.", "stale_state")
        cancellation = await self.store.cancel_processing(session_id, session["state_version"], control_run_id=run["run_id"])
        target = cancellation["cancelled_run_id"]
        task = self.tasks.get(session_id)
        if target and self.active.get(session_id) == target and task and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        current = cancellation["session"]
        result = await self.store.finish_run(run["run_id"], current["state_version"], None,
            current["status"], "Текущая обработка отменена; поездка и заказы сохранены." if target else "Нет текущей обработки для отмены.",
            waiting_reason=current.get("waiting_reason"), pending_question=current.get("pending_question"),
            result={"cancelled_run_id": target, "orders_unchanged": True})
        self.schedule(session_id)
        return result

    async def _search_trip(self, state: TripState, *, session: dict | None = None, run: dict | None = None) -> dict:
        previous = None
        if session is not None:
            seen = set()
            for message in reversed(await self.store.list_messages(session["session_id"])):
                prior = message.get("run_id")
                if not prior or prior in seen or run and prior == run["run_id"]:
                    continue
                seen.add(prior)
                saved = await self.store.get_run(prior)
                plans = ((saved.get("result") or {}).get("result") or {}).get("plans", [])
                if plans:
                    previous = plans[0]
                    break

        async def audit(kind: str, payload: dict) -> None:
            if session is not None and run is not None:
                if kind == "TOOL_CALL_STARTED":
                    await self._ensure_current_run(session, run)
                await self.store.save_event(session["session_id"], run["run_id"], kind, payload)

        return await TripSearch(self.travel).search(state, previous_plan=previous, event_sink=audit)

    async def _ensure_current_run(self, session: dict, run: dict) -> None:
        latest = await self.store.get_session(session["session_id"])
        current_run = await self.store.get_run(run["run_id"])
        if (latest["state_version"] != session["state_version"] or
                latest.get("waiting_reason") == "user_pause" or
                latest.get("active_run_id") != run["run_id"] or current_run["status"] != "running"):
            raise Conflict("Обработка остановлена: изменились версия или управление поездкой.", "processing_control_changed")

    async def _apply_or_undo(self, session: dict, state: TripState, operations: list[Operation]) -> tuple[TripState, list[dict], dict | None]:
        if len(operations) == 1 and operations[0].type == "undo":
            prepared = await self.store.prepare_undo(session["session_id"], session["state_version"])
            return TripState.model_validate(prepared["state"]), prepared["changes"], prepared["undo"]
        candidate, changes = apply_operations(state, operations)
        return candidate, changes, None

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
            self.active[session_id] = run_id
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
            finally:
                if self.active.get(session_id) == run_id:
                    self.active.pop(session_id, None)

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
        archived = await plan_history(self.store, session["session_id"], session["state_version"])
        context["first_shown_plan"] = archived["first_saved_plan"]
        context["__response_schema"] = build_operation_schema(session["state"])
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
            elif primary == "groq":
                if not config.get("GROQ_API_KEY"):
                    raise ProviderError("CONFIG_MISSING", "Ключ Groq не настроен.", provider="groq")
                if str(config.get("GROQ_FREE_TIER_CONFIRMED", False)).lower() not in {"true", "1", "yes"}:
                    raise ProviderError("CONFIG_BLOCKED", "Подтвердите бесплатный аккаунт Groq без billing.", provider="groq")
                provider = "groq"
            elif primary == "cloudflare":
                token = config.get("CLOUDFLARE_API_TOKEN")
                if not isinstance(token, str) or not token.strip():
                    raise ProviderError("CONFIG_MISSING", "Токен Cloudflare не настроен.", provider="cloudflare")
                account = config.get("CLOUDFLARE_ACCOUNT_ID")
                if not isinstance(account, str) or re.fullmatch(r"[0-9a-fA-F]{32}", account) is None:
                    raise ProviderError("CONFIG_BLOCKED", "Нужен корректный Account ID Cloudflare.", provider="cloudflare")
                if str(config.get("CLOUDFLARE_FREE_TIER_CONFIRMED", False)).lower() not in {"true", "1", "yes"}:
                    raise ProviderError("CONFIG_BLOCKED", "Подтвердите Workers Free без billing.", provider="cloudflare")
                provider = "cloudflare"
            elif primary == "zai":
                key = config.get("ZAI_API_KEY")
                if not isinstance(key, str) or not key.strip():
                    raise ProviderError("CONFIG_MISSING", "Ключ Z.ai не настроен.", provider="zai")
                if str(config.get("ZAI_FREE_TIER_CONFIRMED", False)).lower() not in {"true", "1", "yes"}:
                    raise ProviderError("CONFIG_BLOCKED", "Подтвердите бесплатный Standard API Z.ai без billing.", provider="zai")
                model = config.get("ZAI_MODEL", "glm-4.7-flash")
                if not isinstance(model, str) or model not in {"glm-4.7-flash", "glm-4.5-flash"}:
                    raise ProviderError("CONFIG_BLOCKED", "Для Z.ai разрешены только Free Flash модели.", provider="zai")
                provider = "zai"
            else:
                provider = primary
            # No backup is selected by default. Only an explicitly configured,
            # distinct :free model may be the second OpenRouter attempt.
            backup = str(config.get("OPENROUTER_BACKUP_MODEL", "") or "").strip()
            selected = str(getattr(gateway, "settings", {}).get("OPENROUTER_MODEL", ""))
            if provider == "openrouter" and router_ready and backup != selected and backup.endswith(":free") and re.fullmatch(r"[\w./:-]+", backup):
                fallback = LLMGateway({**config, "LLM_PROVIDER": "openrouter", "OPENROUTER_MODEL": backup})
        attempts = max(0, min(2, self.settings.max_llm_calls_per_run))
        if provider in {"ollama", "groq", "cloudflare", "zai"}:
            # Keep local and tightly capped free providers to one generation;
            # a malformed answer does not trigger an immediate full-context repair.
            attempts = min(1, attempts)
        temporary = gateway is not self.llm
        attempt_meta: list[dict] = []
        try:
            if isinstance(gateway, LLMGateway) and provider in {"gemini", "openrouter", "groq", "cloudflare", "zai"}:
                current_model = _requested_model(gateway, provider)
                health = await self.store.model_availability(provider, current_model)
                if not health["available"]:
                    await self.store.save_event(session["session_id"], run["run_id"], "MODEL_SKIPPED_COOLDOWN",
                        {"provider": provider, "model": current_model, "reason": health["reason"], "retry_after": health["retry_after"]})
                    if not health["allow_fallback"] or fallback is None:
                        raise ProviderError("MODEL_COOLDOWN", "Модель или квота временно недоступна. Состояние сохранено.",
                            True, provider, {"retry_after": health["retry_after"], "scope": health["scope"]})
                    backup_model = _requested_model(fallback, "openrouter")
                    backup_health = await self.store.model_availability("openrouter", backup_model)
                    if not backup_health["available"]:
                        raise ProviderError("MODEL_COOLDOWN", "Обе бесплатные модели временно недоступны. Состояние сохранено.",
                            True, "openrouter", {"retry_after": backup_health["retry_after"], "scope": backup_health["scope"]})
                    if temporary:
                        await gateway.close()
                    gateway, provider, temporary, fallback = fallback, "openrouter", True, None
            for attempt in range(attempts):
                await self._ensure_current_run(session, run)
                # Only explicitly injected non-HTTP test adapters may use a test
                # budget; the real gateway always has the production ceiling.
                daily_limit = (min(500, self.settings.max_local_llm_calls_per_day) if provider == "ollama" else
                    min(2000, self.settings.max_llm_calls_per_day) if provider == "zai" else
                    min(40, self.settings.max_llm_calls_per_day) if isinstance(self.llm, LLMGateway) else self.settings.max_llm_calls_per_day)
                if not await self.store.reserve_model_call(session["session_id"], run["run_id"], daily_limit, provider):
                    raise ProviderError("MODEL_BUDGET_EXHAUSTED", "Дневной лимит вызовов LLM исчерпан.")
                requested_model = _requested_model(gateway, provider)
                started = perf_counter()
                try:
                    raw_extraction = await gateway.extract_operations(context)
                    extracted = ModelResult.model_validate(raw_extraction)
                    model_speech_act = extracted.speech_act
                    gateway_settings = getattr(gateway, "settings", {})
                    trace_settings = {**self.settings.provider_settings(), **(gateway_settings if isinstance(gateway_settings, dict) else {})}
                    validated_snapshot = model_snapshot(raw_extraction, trace_settings)
                    if not validated_snapshot.get("captured"):
                        raise ProviderError("INVALID_LLM_RESPONSE", "Структурированный ответ модели превышает допустимый объём.", provider=provider)
                    # Known configured credentials must not reach either stored
                    # replies or state values, even if a provider echoes them.
                    extracted = ModelResult.model_validate(validated_snapshot["value"])
                    await self.store.save_event(session["session_id"], run["run_id"], "MODEL_EXTRACTION_VALIDATED", {
                        "based_state_version": session["state_version"], "provider": provider,
                        "model": requested_model, "structured_contract": validated_snapshot})
                    if extracted.speech_act in {"ASK", "PROPOSE"} and extracted.control.action == "none" and not extracted.conflicts:
                        # Questions/suggestions are not permission to change a
                        # trip. Keep proposed edits as audit data, never execute.
                        extracted.candidate_operations = extracted.operations
                        extracted.operations = []
                        extracted.unknowns = []
                    if not extracted.conflicts and extracted.control.action != "cancel":
                        extracted.operations, extracted.operation_normalizations = ground_budget_operations(
                            TripState.model_validate(session["state"]), extracted.operations, run["input_text"])
                        if any(note.get("status") == "clarification_required" for note in extracted.operation_normalizations):
                            extracted.unknowns = ["Вы указали стоимость за ночь или за человека. Какой общий бюджет всей поездки нужно установить?"]
                            extracted.pending_context = {**(extracted.pending_context or {}), "field": "budget.amount_minor", "budget_scope_ambiguous": True}
                    location = parse_destination(run["input_text"])
                    requested = location["requested_destination"]
                    country_context = requested if requested["kind"] == "country" else None
                    if country_context is not None and extracted.speech_act != "ASK" and extracted.control.action not in {"cancel", "request_checkout", "confirm"}:
                        # This bounded parser matched a country-only request,
                        # not dates/people or a confirmed city. City guesses are
                        # suggestions and must not mutate a concrete route.
                        extracted.operations = []
                        extracted.unknowns = [location["clarification"]]
                        extracted.destination_request = country_context
                    if not extracted.conflicts and extracted.control.action != "cancel" and not any(op.type == "undo" for op in extracted.operations):
                        try:
                            domain_state = TripState.model_validate(session["state"])
                            semantic_conflicts = operation_conflicts(domain_state, extracted.operations)
                            if semantic_conflicts:
                                action = "wait" if extracted.control.action == "wait" and explicit_pause_hint(run["input_text"]) else "none"
                                extracted = ModelResult(operations=[], control={"action": action}, reply=semantic_conflicts[0],
                                    unknowns=[], conflicts=semantic_conflicts)
                            else:
                                apply_operations(domain_state, extracted.operations)
                        except DomainError as error:
                            if error.code in {"unsupported_city", "unsupported_limit", "unsupported_operation", "missing_target", "unknown_target", "missing_hotel", "missing_return_date"}:
                                action = "wait" if extracted.control.action == "wait" and explicit_pause_hint(run["input_text"]) else "none"
                                if error.code == "unsupported_city" and country_context is None:
                                    for operation in extracted.operations:
                                        place = operation.value.get("city", operation.value.get("origin"))
                                        if isinstance(place, str):
                                            candidate_location = parse_destination(place)
                                            if candidate_location["requested_destination"]["kind"] == "country":
                                                country_context = candidate_location["requested_destination"]
                                                location = candidate_location
                                                break
                                question = location["clarification"] if country_context is not None else error.message
                                extracted = ModelResult(operations=[], control={"action": action}, reply=error.message,
                                    unknowns=[question], conflicts=[], destination_request=country_context)
                            else:
                                context["validation_feedback"] = {"domain_code": error.code,
                                    "message": error.message, "operation_shapes": [{"type": op.type, "fields": sorted(op.value)} for op in extracted.operations],
                                    "note": "Исправь схему разрешённых операций, не придумывая факты. Если условия невалидны, верни один conflicts/unknowns вместо изменений."}
                                if isinstance(error.details, list):
                                    fields = {"key", "value", "hard", "stay_id", "amount_minor", "currency", "scope", "id", "type", "age", "birth_date", "group_id", "city", "check_in", "check_out", "rooms", "hotel_id"}
                                    context["validation_feedback"]["errors"] = [{"type": entry.get("type"),
                                        "loc": [part if isinstance(part, int) or part in fields else "<field>" for part in entry.get("loc", [])]}
                                        for entry in error.details if isinstance(entry, dict) and "loc" in entry]
                                raise ProviderError("INVALID_LLM_OPERATION", "Операции LLM не прошли проверку схемы.", provider=provider) from None
                    if country_context is not None and extracted.speech_act != "ASK" and extracted.control.action not in {"cancel", "request_checkout", "confirm"}:
                        extracted.destination_request = country_context
                        if not extracted.operations and not extracted.conflicts:
                            extracted.unknowns = [location["clarification"]]
                    last_call = getattr(raw_extraction, "call_metadata", None) or getattr(gateway, "last_call", {})
                    actual = {"provider": str(last_call.get("provider", provider)), "model": str(last_call.get("model", requested_model)), "latency_ms": max(0, int((perf_counter() - started) * 1000))}
                    for metric in ("usage", "request_metrics"):
                        if isinstance(last_call.get(metric), dict):
                            actual[metric] = last_call[metric]
                    await self.store.save_event(session["session_id"], run["run_id"], "MODEL_OPERATIONS_CHECKED", {
                        "based_state_version": session["state_version"], "provider": provider, "model": requested_model,
                        "structured_contract": model_snapshot(extracted.model_dump(mode="json"), trace_settings),
                        "normalizations": extracted.operation_normalizations})
                    attempt_meta.append({**actual, "outcome": "response"})
                    await self.store.save_event(session["session_id"], run["run_id"], "MODEL_RESPONSE_RECEIVED", {
                        **actual, "operations": len(extracted.operations), "attempt": attempt + 1,
                        "speech_act": model_speech_act, "speech_act_explicit": "speech_act" in raw_extraction})
                    return extracted, {**actual, "attempts": attempt_meta}
                except (ProviderError, ValidationError) as exc:
                    if isinstance(exc, ProviderError) and exc.code == "INVALID_LLM_OPERATION" and exc.details.get("schema_errors"):
                        context["validation_feedback"] = {"errors": exc.details["schema_errors"],
                            "note": "Выбери точный существующий target_id из enum и разрешённую структуру value. Не придумывай типы операций или имена полей."}
                    if isinstance(exc, ValidationError):
                        allowed_locations = {"operations", "control", "action", "type", "target_id", "value", "reply", "unknowns", "conflicts"}
                        context["validation_feedback"] = {
                            "errors": [{"loc": [part if isinstance(part, int) or part in allowed_locations else "<unknown_field>" for part in entry["loc"]], "type": entry["type"]}
                                       for entry in exc.errors(include_input=False, include_context=False, include_url=False)],
                            "expected_contract": {"required": ["operations", "control", "reply", "unknowns", "conflicts"], "note": "Верни весь корректный JSON-объект. Не повторяй невалидную структуру; не меняй известные поля без запроса."},
                        }
                    details = exc.details if isinstance(exc, ProviderError) and isinstance(exc.details, dict) else {}
                    failed = {"provider": provider, "model": requested_model, "code": getattr(exc, "code", "INVALID_LLM_RESPONSE"), "latency_ms": max(0, int((perf_counter() - started) * 1000)), **{key: details[key] for key in ("http_status", "limit_source", "provider_name", "retry_after_seconds", "rate_limits") if key in details}}
                    if isinstance(gateway, LLMGateway):
                        failed.update(gateway.call_metadata_for_current_call())
                    attempt_meta.append({**failed, "outcome": "failed"})
                    await self.store.save_event(session["session_id"], run["run_id"], "MODEL_CALL_FAILED", failed)
                    exc.llm_meta = {"provider": provider, "model": requested_model, "attempts": attempt_meta}
                    if attempt + 1 >= attempts:
                        raise
                    if isinstance(exc, ProviderError) and exc.code == "INVALID_LLM_OPERATION":
                        continue
                    if fallback is not None and _can_switch_model(exc):
                        backup_health = await self.store.model_availability("openrouter", _requested_model(fallback, "openrouter"))
                        if not backup_health["available"]:
                            raise ProviderError("MODEL_COOLDOWN", "Резервная модель временно недоступна. Состояние сохранено.",
                                True, "openrouter", {"retry_after": backup_health["retry_after"], "scope": backup_health["scope"]}) from None
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

    async def _interpret(self, session: dict, run: dict) -> tuple[ModelResult, dict | None]:
        pending = session.get("pending_question")
        if pending and session.get("waiting_reason") == "clarification":
            pending = dict(pending)
            if not pending.get("field"):
                binding = derive_pending_field(pending.get("questions") or [pending.get("text")], session["state"])
                pending.update({key: value for key, value in binding.items() if value is not None})
        resolved = resolve_date_answer(session["state"], pending, run["input_text"], datetime.now(timezone.utc))
        if not resolved["handled"] and pending and session.get("waiting_reason") == "clarification":
            resolved = resolve_short_answer(session["state"], pending, run["input_text"])
        if resolved["handled"]:
            question = resolved.get("question")
            extracted = ModelResult(operations=[Operation.model_validate(op) for op in resolved["operations"]],
                control={"action": "none"}, reply=question or "Ответ сохранён.", unknowns=[question] if question else [], conflicts=[],
                pending_context=resolved.get("pending_context"), pending_context_clear=resolved.get("pending_context_clear", []),
                response_source="contextual_slot")
            await self.store.save_event(session["session_id"], run["run_id"], "CONTEXT_ANSWER_PROPOSED", {
                "based_state_version": session["state_version"], "pending_field": (pending or {}).get("field"),
                "target_id": (pending or {}).get("target_id"), "context_clear": extracted.pending_context_clear,
                "operations": [op.model_dump(mode="json") for op in extracted.operations]})
            return extracted, None
        return await self._extract(session, run)

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
        undo_meta: dict | None = None
        preserve_pending = False
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
                operations = [Operation.model_validate(item) for item in command["operations"]]
                conflicts = operation_conflicts(state, operations)
                if conflicts:
                    raise DomainError("conflicting_operations", conflicts[0], conflicts)
                if any(operation.type == "undo" for operation in operations) and command["search"]:
                    raise DomainError("undo_requires_separate_search", "После undo выполните отдельный новый поиск.")
                candidate, changes, undo_meta = await self._apply_or_undo(session, state, operations)
                new_state = candidate if changes else None
                search = command["search"]
            elif kind == "undo":
                candidate, changes, undo_meta = await self._apply_or_undo(session, state, [Operation(type="undo")])
                new_state = candidate
            elif kind == "cancel":
                await self.cancel_control(run)
                return
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
            extracted, model_meta = await self._interpret(session, run)
            # Explicit conflicts block the *entire* batch, including otherwise
            # valid edits. Unknown fields alone may retain unambiguous changes.
            conflicts = extracted.conflicts or operation_conflicts(state, extracted.operations)
            if conflicts:
                questions = conflicts
                status, waiting_reason = "WAITING_USER", ("user_pause" if extracted.control.action == "wait" and
                    explicit_pause_hint(run["input_text"]) and not resumed_after_input else "clarification")
            elif extracted.control.action == "cancel":
                await self.store.cancel_processing(session["session_id"], version,
                    target_run_id=run["run_id"], result={"llm": model_meta})
                return
            elif (extracted.speech_act == "ASK" or extracted.speech_act == "PROPOSE" and extracted.destination_request is None) and extracted.control.action == "none":
                # No intake questionnaire or search is started merely because
                # someone asked about a place. Preserve any previous exact slot.
                preserve_pending = True
                status, waiting_reason = session["status"], session.get("waiting_reason")
                subject = parse_destination(extracted.information_subject or "")["requested_destination"]
                if extracted.speech_act == "PROPOSE":
                    notice = "Предложение сохранено отдельно; поездка не изменена. Чтобы применить его, сформулируйте изменение явно."
                elif subject.get("kind") == "country":
                    notice = f"Да, {subject['name']} — страна. Могу помочь обсудить маршрут и условия поездки; ваш вопрос сам по себе не меняет поездку."
                elif subject.get("kind") == "city":
                    country = parse_destination(subject["country_code"])["requested_destination"]
                    notice = f"Город «{subject['name']}» находится в стране «{country['name']}»."
                else:
                    notice = "Могу помочь с направлением, маршрутом и условиями поездки. Уточните, что именно хотите узнать."
                result.update(notice=notice, conversation={"speech_act": extracted.speech_act, "subject": subject},
                    candidate_operations=[op.model_dump(mode="json") for op in extracted.candidate_operations])
            else:
                try:
                    candidate, changes, undo_meta = await self._apply_or_undo(session, state, extracted.operations)
                except DomainError as exc:
                    exc.llm_meta = model_meta
                    raise
                new_state = candidate if changes else None
                questions = extracted.unknowns
                if extracted.control.action == "wait" and not resumed_after_input:
                    missing = [issue["message"] for issue in validate_trip(candidate) if issue["code"].startswith("missing_")]
                    if (questions or missing) and not explicit_pause_hint(run["input_text"]):
                        questions = questions or missing
                        status, waiting_reason = "WAITING_USER", "clarification"
                    else:
                        status, waiting_reason = "WAITING_USER", "user_pause"
                elif questions:
                    status, waiting_reason = "WAITING_USER", "clarification"
                elif extracted.control.action in {"request_checkout", "confirm"}:
                    result["available_actions"] = ["checkout/preview", "checkout/confirm"]
                    result["notice"] = "Выбор и текстовое «да» не создают заказ. Нужны preview и явное подтверждение его версии."
                else:
                    search = undo_meta is None and (bool(changes) or extracted.control.action in {"continue", "resume"}
                        or extracted.response_source == "contextual_slot")
                    if undo_meta is None and not search:
                        status = session["status"]
                        waiting_reason = session.get("waiting_reason")
                        preserve_pending = True
                        result.update(no_changes=True, notice="Новых изменений нет. Сохранённая поездка и текущие варианты не изменены.")
                        if not session.get("pending_question"):
                            missing = [issue for issue in validate_trip(candidate) if issue["code"].startswith("missing_")]
                            if missing:
                                questions = [missing[0]["message"]]
                                status, waiting_reason, preserve_pending = "WAITING_USER", "clarification", False
                                result.clear()
        current = new_state or state
        if new_state is not None:
            current.state_version = version + 1
        if undo_meta is not None:
            search = False
            result.update(undo=undo_meta, plans=[], notice="Последняя успешная правка отменена новой ревизией. Цены и согласия не восстановлены; нужен новый поиск. Существующие заказы не изменены.")
        clarification_metadata = dict((session.get("pending_question") or {}).get("context") or {})
        if not run["input_text"].startswith(TYPED_PREFIX):
            if extracted.destination_request is not None:
                clarification_metadata["requested_destination"] = extracted.destination_request
            for key in extracted.pending_context_clear:
                if key in PENDING_CONTEXT_CLEAR_KEYS:
                    clarification_metadata.pop(key, None)
            if extracted.pending_context is not None:
                chosen = extracted.pending_context.get("requested_destination")
                if isinstance(chosen, dict) and chosen.get("kind") == "city" and not extracted.pending_context.get("confirmation_required"):
                    for stale_choice in ("proposed_destination", "confirmation_required", "suggested_cities", "contextual_correction"):
                        clarification_metadata.pop(stale_choice, None)
                clarification_metadata.update(extracted.pending_context)
            if (any(op.type == "upsert_stay" and op.target_id and set(op.value).intersection({"check_in", "check_out"}) for op in extracted.operations)
                    or extracted.pending_context and extracted.pending_context.get("target_id") and extracted.pending_context.get("field", "").startswith("stay.")):
                clarification_metadata.pop("target_selection_required", None)
            if any(op.type == "set_budget" for op in extracted.operations):
                clarification_metadata.pop("budget_scope_ambiguous", None)
            if extracted.speech_act == "CHANGE" and not any(person.type == "child" for person in current.travelers) and explicit_no_children(run["input_text"]):
                clarification_metadata.update(children_known=True, children_count=0)
            clarification_metadata.pop("field", None)
            clarification_metadata.pop("target_id", None)
            for stale_address in ("traveler_id", "stay_id", "leg_id"):
                clarification_metadata.pop(stale_address, None)
            if extracted.conflicts:
                clarification_metadata["force_clarification"] = True
            elif clarification_metadata.get("budget_scope_ambiguous"):
                clarification_metadata["force_clarification"] = True
            else:
                clarification_metadata.pop("force_clarification", None)
            if questions and waiting_reason != "user_pause":
                normalized = []
                for raw in questions:
                    trusted = raw in extracted.unknowns and (
                        extracted.response_source == "contextual_slot" or extracted.destination_request is not None)
                    bound = derive_pending_field([{**clarification_metadata, "text": raw}], current)
                    if question_field(raw) and bound["field"] is None and not trusted and not extracted.conflicts:
                        continue  # This precise slot is already filled.
                    rendered = raw if trusted else human_question({**clarification_metadata, "text": raw}, current, bound)
                    if (not trusted and not extracted.conflicts and bound["field"] is None
                            and rendered == human_question(None, current)):
                        # An unknown model field must not erase the address of
                        # a missing domain fact. Ask a specific validated slot
                        # before accepting its answer; never guess an edit target.
                        for issue in validate_trip(current):
                            if not issue["code"].startswith("missing_"):
                                continue
                            domain_slot = derive_pending_field([issue], current)
                            if domain_slot["field"] is not None:
                                rendered = human_question(issue, current, domain_slot)
                                break
                    normalized.append(rendered)
                questions = list(dict.fromkeys(normalized))[:1]
                if not questions:
                    waiting_reason = None
                    status = "TRIP_DRAFT"
                    search = bool(changes)
        if search and waiting_reason != "user_pause" and not questions:
            result = await self._search_trip(current, session=session, run=run)
            questions = result["questions"]
            status = "OPTIONS_READY" if result["plans"] else "WAITING_USER" if questions else "TRIP_DRAFT"
            waiting_reason = "clarification" if questions else None
        elif not result and waiting_reason != "user_pause":
            if not questions:
                missing = [issue for issue in validate_trip(current) if issue["code"].startswith("missing_")]
                if missing:
                    questions = [human_question(missing[0], current, clarification_metadata)]
                    detail = missing[0].get("details") or {}
                    if detail.get("traveler_id"):
                        clarification_metadata["traveler_id"] = detail["traveler_id"]
            if questions:
                status, waiting_reason = "WAITING_USER", "clarification"
        # Search and typed commands can generate questions after the model/slot
        # phase. Sanitize that path too; raw schema names never enter the chat.
        if questions and waiting_reason != "user_pause":
            trusted = not run["input_text"].startswith(TYPED_PREFIX) and questions[0] in extracted.unknowns and (
                extracted.response_source == "contextual_slot" or extracted.destination_request is not None)
            if not trusted:
                questions = [human_question({**clarification_metadata, "text": questions[0]}, current, clarification_metadata)]
        if waiting_reason == "user_pause":
            reply = "Запрос и текущее состояние сохранены. Жду явного «продолжай»; поиск и оформление остановлены."
            if questions:
                result["deferred_questions"] = questions
        elif questions:
            reply = questions[0]
            if any(change.get("field") == "preferences" for change in changes):
                reply = "Пожелания по поездке сохранены. " + reply
        elif result.get("plans"):
            reply = (f"Подобрано DEMO-планов: {len(result['plans'])}. Все цены и наличие вымышлены; бронь не создана."
                     if result.get("source_type") == "DEMO" else
                     "Собрана оценочная поездка. Источник каждого компонента указан отдельно; оформление только имитируется.")
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
        pending = session.get("pending_question") if preserve_pending else {"question_id": f"{run['run_id']}-question", "text": questions[0], "questions": questions, "state_version": current.state_version, "reason": "clarification"} if questions and waiting_reason != "user_pause" else None
        if pending is not None and not preserve_pending:
            if not run["input_text"].startswith(TYPED_PREFIX) and extracted.pending_context and extracted.pending_context.get("field"):
                binding = {"field": extracted.pending_context["field"], "target_id": extracted.pending_context.get("target_id")}
            else:
                binding = derive_pending_field([{**clarification_metadata, "text": question} for question in questions], current)
            if binding.get("field") == "traveler.age" and binding.get("target_id") is None:
                # Choose whom to ASK, not whose age an ambiguous prior answer
                # meant. The persisted question names its exact target first.
                missing_children = [person for person in current.travelers if person.type == "child" and person.age is None and person.birth_date is None]
                if missing_children:
                    binding["target_id"] = missing_children[0].id
                    clarification_metadata["traveler_id"] = missing_children[0].id
                    text = human_question({"field": "traveler.age", "target_id": missing_children[0].id}, current)
                    pending.update(text=text, questions=[text])
                    reply = text
            if (binding.get("field", "") and binding["field"].startswith(("stay.", "leg.")) and binding.get("target_id") is None
                    and not clarification_metadata.get("target_selection_required")):
                field_name = binding["field"].split(".", 1)[1]
                collection = current.legs if binding["field"].startswith("leg.") else current.stays
                candidates = [item for item in collection if not getattr(item, field_name, None)]
                if candidates:
                    binding["target_id"] = candidates[0].id
                    text = human_question({"field": binding["field"], "target_id": candidates[0].id}, current)
                    pending.update(text=text, questions=[text])
                    reply = text
            pending = {**pending, "field": binding.get("field"), "target_id": binding.get("target_id"), "context": clarification_metadata}
            if clarification_metadata.get("requested_destination") is not None:
                pending["requested_destination"] = clarification_metadata["requested_destination"]
        if not run["input_text"].startswith(TYPED_PREFIX):
            result["response_source"] = extracted.response_source
        if model_meta is not None:
            result["llm"] = model_meta
            result["model_reply"] = extracted.reply
            result["operation_normalizations"] = extracted.operation_normalizations
        await self.store.finish_run(run["run_id"], version, current.model_dump(mode="json") if new_state is not None else None, status, reply, pending_question=pending, waiting_reason=waiting_reason, changes=changes, result=result)

    async def _live_search(self, session: dict, run: dict, command: dict, state: TripState) -> dict:
        kind, provider, request = command["search_kind"], command["provider"], command["request"]
        await self._ensure_current_run(session, run)
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
