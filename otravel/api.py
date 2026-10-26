"""Backend API; no frontend. Every trip resource requires a session Bearer token."""

from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import socket
from pathlib import Path
import secrets
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import text as sql_text
from sqlalchemy.exc import SQLAlchemyError

from .config import Settings, provider_status
from .domain import DomainError, Operation, TripState, validate_trip
from .harness import Harness, TYPED_PREFIX, encode_command, exact_control
from .inventory import reprice_plan, validate_plan
from .planning import assemble_plan, validate_quote
from .providers import LLMGateway, ProviderError, TravelGateway
from .fx import CBRFxClient, FxUnavailable
from .media import MEDIA_CSP_SOURCES, MediaResolver
from .store import Conflict, NotFound, Store, StoreError


_PRIVATE = {"owner_token_hash", "input", "input_text", "request_hash", "active_run_id", "active_run_started_at", "settings", "database_url", "authorization", "api_key", "apikey", "api_token", "secret", "password", "access_token", "refresh_token", "headers", "raw_response", "raw_request", "request_url"}


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items() if key.casefold().replace("-", "_") not in _PRIVATE and not key.casefold().endswith(("_api_key", "_api_secret", "_access_token", "_api_token"))}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    if isinstance(value, str):
        # Never echo configured credentials, even inside an otherwise public string.
        for name in ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY", "LITEAPI_API_KEY", "DUFFEL_ACCESS_TOKEN", "SERPAPI_API_KEY", "HOTELBEDS_API_KEY", "HOTELBEDS_API_SECRET", "RATEHAWK_API_KEY", "TRAVELPAYOUTS_API_TOKEN", "ZAI_API_KEY", "COHERE_API_KEY", "CLOUDFLARE_API_TOKEN", "NVIDIA_API_KEY"):
            secret = os.getenv(name)
            if secret:
                value = value.replace(secret, "[REDACTED]")
    return value


def _session_public(row: dict) -> dict:
    return _clean({"session_id": row["session_id"], "trip_id": row["trip_id"], "state_version": row["state_version"], "trip_version": row["state_version"], "status": row["status"], "waiting_reason": row.get("waiting_reason"), "pending_question": row.get("pending_question"), "trip": row["state"], "available_actions": ["commands", "demo", "search", "control", "checkout/preview"]})


def _run_public(row: dict) -> dict:
    response = row.get("result") or {}
    metadata = (response.get("result") or {}).get("llm") or (row.get("error") or {}).get("llm")
    return _clean({"run_id": row["run_id"], "session_id": row["session_id"], "status": row["status"], "based_version": row["based_version"], "result": row.get("result"), "error": row.get("error"), "llm": metadata, "created_at": row.get("created_at"), "finished_at": row.get("finished_at"), "available_actions": ["commands", "demo", "search", "control"]})


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateSession(Body):
    initial_trip: TripState | None = None


class Message(Body):
    client_message_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1)
    expected_version: int | None = Field(default=None, ge=0, strict=True)


class Commands(Body):
    client_message_id: str = Field(min_length=1, max_length=200)
    expected_version: int = Field(ge=0, strict=True)
    operations: list[Operation] = Field(max_length=30)
    search: StrictBool = False


class Action(Body):
    action: Literal["pause", "resume", "undo", "cancel"]
    client_message_id: str | None = Field(default=None, min_length=1, max_length=200)
    expected_version: int | None = Field(default=None, ge=0, strict=True)


class Search(Body):
    client_message_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=200)
    expected_version: int | None = Field(default=None, ge=0, strict=True)
    mode: Literal["DEMO", "HYBRID"] | None = None


class LiveSearch(Body):
    client_message_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=200)
    expected_version: int | None = Field(default=None, ge=0, strict=True)
    kind: Literal["hotels", "flights", "weather"]
    provider: str = Field(min_length=1, max_length=40)
    request: dict[str, Any]
    demo_fallback: StrictBool = False


class Preview(Body):
    plan_id: str = Field(min_length=1, max_length=120)
    state_version: int = Field(ge=0, strict=True)


class Confirm(Body):
    quote_id: str = Field(min_length=1, max_length=100)
    quote_version: int = Field(ge=0, strict=True)
    state_version: int = Field(ge=0, strict=True)
    idempotency_key: str = Field(min_length=1, max_length=200)
    confirmed: StrictBool


def create_app(settings: Settings | None = None, store: Any = None, llm: Any = None, travel: Any = None, fx: Any = None, media: Any = None) -> FastAPI:
    settings = settings or Settings.from_env()
    repository = store if store is not None else Store(settings.database_url)
    model = llm if llm is not None else LLMGateway(settings.provider_settings())
    gateway = travel if travel is not None else TravelGateway(settings.provider_settings())
    rates = fx if fx is not None else CBRFxClient() if settings.reference_fx_enabled else None
    pictures = media if media is not None else MediaResolver(gateway)
    harness = Harness(settings, repository, model, gateway, fx=rates)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        try:
            await repository.initialize()
            await harness.start()
        except Exception:
            await repository.close()
            await model.close()
            await gateway.close()
            if rates is not None:
                await rates.close()
            raise RuntimeError("Backend startup failed: PostgreSQL initialization unavailable; connection details redacted.") from None
        try:
            yield
        finally:
            await harness.close()
            await model.close()
            await gateway.close()
            if rates is not None:
                await rates.close()
            await repository.close()

    app = FastAPI(title="O!Travel Agent Backend", version="0.1.0", lifespan=lifespan,
                  description="Сессии защищены Bearer-токеном, возвращаемым один раз при создании. DEMO не создаёт реальную бронь. POST сообщения возвращает run_id для polling.")
    app.state.store, app.state.harness = repository, harness
    app.state.llm, app.state.travel, app.state.settings = model, gateway, settings
    app.state.fx = rates
    app.state.media = pictures
    origins = [value.strip().rstrip("/") for value in os.getenv("CORS_ORIGINS", "").split(",") if value.strip() and value.strip() != "*"]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"], allow_credentials=False)

    def error(code: str, message: str, status: int, retryable: bool = False) -> JSONResponse:
        return JSONResponse({"error": {"code": code, "message": _clean(message), "retryable": retryable}}, status_code=status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _exc):
        return error("invalid_request", "Некорректный формат запроса; проверьте схему OpenAPI.", 422)

    @app.exception_handler(HTTPException)
    async def http_error(_request, exc):
        return error("access_denied" if exc.status_code in {401, 403} else "request_error", str(exc.detail), exc.status_code)

    @app.exception_handler(StoreError)
    async def store_error(_request, exc):
        return error(exc.code, exc.message, 404 if isinstance(exc, NotFound) else 409 if isinstance(exc, Conflict) else 400)

    @app.exception_handler(DomainError)
    async def domain_error(_request, exc):
        return error(exc.code, exc.message, 422)

    @app.exception_handler(ProviderError)
    async def provider_error(_request, exc):
        return error(exc.code, exc.message, 503, exc.retryable)

    @app.exception_handler(FxUnavailable)
    async def fx_error(_request, exc):
        return error(exc.code, exc.message, 503, True)

    @app.exception_handler(SQLAlchemyError)
    @app.exception_handler(ConnectionError)
    @app.exception_handler(TimeoutError)
    @app.exception_handler(socket.gaierror)
    async def database_error(_request, _exc):
        return error("database_unavailable", "Хранилище недоступно; команда не считается долговременно принятой.", 503, True)

    def bearer(authorization: str | None) -> str:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "Нужен Bearer-токен сессии.")
        token = authorization[7:].strip()
        if not token or len(token) > 512:
            raise HTTPException(401, "Некорректный токен сессии.")
        return token

    async def owned(session_id: str, authorization: str | None) -> dict:
        token = bearer(authorization)
        row = await repository.get_session(session_id)
        expected = row.get("owner_token_hash") or "0" * 64
        actual = hashlib.sha256(token.encode()).hexdigest()
        if not hmac.compare_digest(actual, expected):
            raise HTTPException(403, "Ресурс принадлежит другой сессии.")
        return row

    def no_credentials(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = key.casefold().replace("-", "_")
                if normalized in {"api_key", "apikey", "token", "authorization", "password", "secret", "headers", "url", "endpoint"} or normalized.endswith(("_api_key", "_access_token", "_api_secret")):
                    raise HTTPException(400, "Ключи и адреса HTTP задаются только серверной конфигурацией.")
                no_credentials(item)
        elif isinstance(value, list):
            for item in value:
                no_credentials(item)

    async def submit(session: dict, client_id: str, expected_version: int | None, kind: str, *, schedule: bool = True, **fields: Any) -> dict:
        no_credentials(fields)
        # Reuse the original server-side fingerprint on an identical replay.
        incoming = {"kind": kind, **fields}
        encoded = None
        for message in reversed(await repository.list_messages(session["session_id"])):
            if message.get("client_message_id") == client_id and message.get("run_id"):
                previous = await repository.get_run(message["run_id"])
                previous_text = previous["input_text"]
                if previous_text.startswith(TYPED_PREFIX):
                    payload = json.loads(previous_text[len(TYPED_PREFIX):])
                    payload.pop("base_state_hash", None)
                    if payload == incoming:
                        encoded = previous_text
                break
        encoded = encoded or encode_command(kind, session, **fields)
        if len(encoded) > max(16_000, settings.max_message_chars * 4):
            raise HTTPException(413, "Структурированная команда слишком велика.")
        run, duplicate = await repository.intake_message(session["session_id"], client_id, encoded, expected_version)
        if not duplicate and schedule:
            harness.schedule(session["session_id"])
        return {"session_id": session["session_id"], "run_id": run["run_id"], "status": run["status"], "duplicate": duplicate, "poll_url": f"/api/v1/runs/{run['run_id']}"}

    @app.get("/")
    async def root():
        return {"service": "O!Travel Agent backend", "frontend": "provided_by_separate_team_member", "docs": "/docs", "health": "/health"}

    @app.get("/health")
    async def health():
        try:
            async with repository.engine.connect() as connection:
                await connection.execute(sql_text("SELECT 1"))
        except Exception:
            return error("database_unavailable", "PostgreSQL readiness не подтверждена.", 503, True)
        return {"status": "ok", "database": "ready", "application": "backend", "supplier_booking": False}

    @app.get("/api/v1/providers/status")
    async def providers_status():
        return _clean(provider_status())

    @app.get("/debug", include_in_schema=False)
    async def debug_chat():
        return FileResponse(Path(__file__).parent / "static" / "debug.html", media_type="text/html",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                     "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: " + " ".join(MEDIA_CSP_SOURCES) + "; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"})

    @app.get("/debug/assets/{asset}", include_in_schema=False)
    async def debug_asset(asset: Literal["chat.css", "chat.js"]):
        return FileResponse(Path(__file__).parent / "static" / asset,
            media_type="text/css" if asset.endswith(".css") else "text/javascript",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @app.post("/api/v1/sessions", status_code=201)
    async def create_session(body: CreateSession | None = None):
        state = body.initial_trip if body and body.initial_trip else TripState()
        if "search_policy" not in state.model_fields_set and settings.default_search_mode == "HYBRID":
            state.search_policy = type(state.search_policy)(mode="HYBRID",
                hotel_provider=settings.default_hotel_provider, flight_provider=settings.default_flight_provider)
        issues = validate_trip(state, final=False)
        if issues:
            raise DomainError(issues[0]["code"], issues[0]["message"])
        token = secrets.token_urlsafe(32)
        row = await repository.create_session(state.model_dump(mode="json"), owner_token_hash=hashlib.sha256(token.encode()).hexdigest())
        return {**_session_public(row), "session_token": token}

    @app.get("/api/v1/sessions/{session_id}")
    async def get_session(session_id: str, authorization: str | None = Header(default=None)):
        return _session_public(await owned(session_id, authorization))

    @app.get("/api/v1/sessions/{session_id}/messages")
    async def get_messages(session_id: str, authorization: str | None = Header(default=None)):
        await owned(session_id, authorization)
        rows = await repository.list_messages(session_id)
        for row in rows:
            if row["text"].startswith(TYPED_PREFIX):
                row["text"], row["kind"] = "Структурированная команда из формы.", "command"
        return {"messages": _clean(rows)}

    @app.get("/api/v1/sessions/{session_id}/events")
    async def get_events(session_id: str, after: int = Query(default=0, ge=0), authorization: str | None = Header(default=None)):
        await owned(session_id, authorization)
        rows = await repository.list_events(session_id, after)
        return {"events": _clean(rows), "next_cursor": rows[-1]["sequence"] if rows else after}

    @app.get("/api/v1/runs/{run_id}")
    async def get_run(run_id: str, authorization: str | None = Header(default=None)):
        bearer(authorization)
        row = await repository.get_run(run_id)
        await owned(row["session_id"], authorization)
        return _run_public(row)

    @app.post("/api/v1/sessions/{session_id}/messages", status_code=202)
    async def message(session_id: str, body: Message, authorization: str | None = Header(default=None)):
        session = await owned(session_id, authorization)
        if len(body.text) > settings.max_message_chars:
            raise HTTPException(413, "Сообщение слишком длинное.")
        if "OTRAVEL_TYPED_V1:" in body.text:
            raise HTTPException(400, "Служебный формат сообщений зарезервирован для /commands.")
        run, duplicate = await repository.intake_message(session_id, body.client_message_id, body.text, body.expected_version)
        action = exact_control(body.text)
        if not duplicate and action:
            await harness.control_message(run, action)
        elif not duplicate and session.get("waiting_reason") != "user_pause":
            harness.schedule(session_id)
        return {"session_id": session_id, "run_id": run["run_id"], "status": (await repository.get_run(run["run_id"]))["status"], "duplicate": duplicate, "poll_url": f"/api/v1/runs/{run['run_id']}"}

    @app.post("/api/v1/sessions/{session_id}/commands", status_code=202)
    async def commands(session_id: str, body: Commands, authorization: str | None = Header(default=None)):
        session = await owned(session_id, authorization)
        return await submit(session, body.client_message_id, body.expected_version, "commands", operations=[operation.model_dump(mode="json") for operation in body.operations], search=body.search)

    @app.post("/api/v1/sessions/{session_id}/control")
    async def control(session_id: str, body: Action, authorization: str | None = Header(default=None)):
        session = await owned(session_id, authorization)
        if body.action in {"undo", "cancel"}:
            if body.client_message_id is None or body.expected_version is None:
                raise HTTPException(422, "Для undo/cancel нужны client_message_id и expected_version.")
            accepted = await submit(session, body.client_message_id, body.expected_version, body.action, schedule=body.action != "cancel")
            if body.action == "cancel" and not accepted["duplicate"]:
                await harness.cancel_control(await repository.get_run(accepted["run_id"]))
                accepted["status"] = (await repository.get_run(accepted["run_id"]))["status"]
            return JSONResponse(_clean(accepted), status_code=202)
        return _session_public(await harness.control(session_id, body.action))

    @app.post("/api/v1/sessions/{session_id}/demo", status_code=202)
    async def demo(session_id: str, body: Search | None = None, authorization: str | None = Header(default=None)):
        session = await owned(session_id, authorization)
        body = body or Search()
        return await submit(session, body.client_message_id, body.expected_version, "demo")

    @app.post("/api/v1/sessions/{session_id}/search", status_code=202)
    async def search(session_id: str, body: Search | None = None, authorization: str | None = Header(default=None)):
        session = await owned(session_id, authorization)
        body = body or Search()
        if body.mode is not None and body.mode != session["state"].get("search_policy", {}).get("mode", "DEMO"):
            raise Conflict("Режим поиска отличается от сохранённой политики поездки. Сначала измените политику поиска.", "search_policy_mismatch")
        return await submit(session, body.client_message_id, body.expected_version, "search")

    @app.post("/api/v1/sessions/{session_id}/live/search", status_code=202)
    async def live_search(session_id: str, body: LiveSearch, authorization: str | None = Header(default=None)):
        session = await owned(session_id, authorization)
        if body.kind == "weather" and not {"city", "latitude", "longitude"}.issubset(body.request):
            raise HTTPException(422, "Для погоды нужны city, latitude и longitude.")
        return await submit(session, body.client_message_id, body.expected_version, "live_search", search_kind=body.kind, provider=body.provider, request=body.request, demo_fallback=body.demo_fallback)

    async def saved_plan(session: dict, plan_id: str, *, include_partial: bool = False) -> dict:
        checked = set()
        for message in reversed(await repository.list_messages(session["session_id"])):
            run_id = message.get("run_id")
            if not run_id or run_id in checked:
                continue
            checked.add(run_id)
            run = await repository.get_run(run_id)
            response = run.get("result") or {}
            inventory = response.get("result") or {}
            candidates = list(inventory.get("plans", []))
            if include_partial and isinstance(inventory.get("partial_plan"), dict):
                candidates.append(inventory["partial_plan"])
            for plan in candidates:
                if plan.get("plan_id") == plan_id:
                    if plan.get("trip_id") != session["trip_id"] or plan.get("state_version") != session["state_version"]:
                        raise Conflict("Выбранный план устарел; нужен новый поиск.", "stale_plan")
                    return plan
        raise NotFound("План этой сессии не найден.")

    @app.get("/api/v1/sessions/{session_id}/media")
    async def get_plan_media(session_id: str, plan_id: str = Query(min_length=1, max_length=120),
                             state_version: int | None = Query(default=None, ge=0),
                             authorization: str | None = Header(default=None)):
        session = await owned(session_id, authorization)
        if state_version is not None and state_version != session["state_version"]:
            raise Conflict("Версия поездки изменилась; обновите карточки.", "stale_state")
        plan = await saved_plan(session, plan_id, include_partial=True)
        # Static presentation is deliberately outside the priced snapshot. The
        # resolver only sees hotel identities already saved in this own plan.
        presentation = await pictures.resolve_plan(deepcopy(plan))
        latest = await repository.get_session(session_id)
        if latest["state_version"] != session["state_version"]:
            raise Conflict("Поездка изменилась во время загрузки фотографий.", "stale_state")
        return _clean({"session_id": session_id, "state_version": session["state_version"],
                       "plan_id": plan_id, **presentation})

    @app.post("/api/v1/sessions/{session_id}/checkout/preview")
    async def preview(session_id: str, body: Preview, authorization: str | None = Header(default=None)):
        session = await owned(session_id, authorization)
        if body.state_version != session["state_version"]:
            raise Conflict("Версия поездки изменилась.", "stale_state")
        plan = await saved_plan(session, body.plan_id)
        state = TripState.model_validate(session["state"])
        # Failed refresh also revokes any old preview; it must not leave reusable
        # consent for a price/availability that could not be checked this time.
        await repository.begin_quote_preview(session_id, body.state_version)
        if isinstance(plan.get("components"), list):
            issues = validate_quote(plan, state, now=datetime.now(timezone.utc))
            if issues:
                issue = next((item for item in issues if item["code"] in {"stale_source_snapshot", "invalid_source_lifetime"}), issues[0])
                raise Conflict(issue["message"], issue["code"])
            fresh_fx = None
            if plan.get("fx_snapshot") is not None:
                if rates is None:
                    raise Conflict("Источник курса недоступен; старая корзина отозвана, исходные предложения сохранены.", "fx_unavailable")
                # Refresh rates before creating a new consent, never in confirm.
                fresh_fx = await rates.fetch_snapshot(now=datetime.now(timezone.utc))
            refreshed = []
            for old in plan["offers"]:
                if old.get("source_type") == "DEMO":
                    refreshed.extend(reprice_plan({"source_type": "DEMO", "offers": [deepcopy(old)]})["offers"])
                    continue
                new = await gateway.reprice(deepcopy(old))
                if (not isinstance(new, dict) or new.get("provider") != old.get("provider")
                        or new.get("offer_id") != old.get("offer_id") or not isinstance(new.get("details"), dict)):
                    raise Conflict("Перепроверка изменила идентичность предложения; нужен новый выбор.", "reprice_identity_changed")
                prior_details, details = old.get("details", {}), deepcopy(new["details"])
                if details.get("kind") != prior_details.get("kind"):
                    raise Conflict("Перепроверка изменила тип компонента.", "reprice_identity_changed")
                for key in ("native_offer_id", "hotel_id"):
                    if key in prior_details and details.get(key) != prior_details[key]:
                        raise Conflict("Перепроверка не подтвердила исходный provider ID.", "reprice_identity_changed")
                if ("search_request" not in prior_details or details.get("search_request") != prior_details["search_request"]):
                    raise Conflict("Перепроверка не связана с исходным поисковым запросом.", "reprice_request_changed")
                if "binding" in details and details["binding"] != prior_details.get("binding"):
                    raise Conflict("Перепроверка относится к другим компонентам поездки.", "reprice_binding_changed")
                # Reattach only the verified request association. Economic facts
                # such as fees/allocation/constraints MUST come from the new reply.
                details["binding"] = deepcopy(prior_details["binding"])
                new = deepcopy(new)
                new["details"] = details
                refreshed.append(new)
            updated = assemble_plan(state, refreshed, previous_plan=plan, now=datetime.now(timezone.utc), fx_snapshot=fresh_fx)
            issues = validate_quote(updated, state, current_offers=refreshed, now=datetime.now(timezone.utc))
            if not issues and not updated["ready_for_simulated_checkout"]:
                issues = updated["blockers"] or [{"code": "checkout_not_ready", "message": "Корзина не готова к имитации оформления."}]
            updated["repriced"] = True
            updated["reprice_kind"] = "component_refresh_simulated_not_supplier_booking"
        else:
            if plan.get("source_type") != "DEMO":
                raise Conflict("Полная live-корзина не проверена. Поисковая цена не подтверждает бронирование.", "live_plan_not_validated")
            updated = reprice_plan(plan)
            issues = validate_plan(state, updated)
        if issues:
            raise DomainError(issues[0]["code"], issues[0]["message"])
        return _clean(await repository.create_quote(session_id, body.state_version, updated))

    @app.post("/api/v1/sessions/{session_id}/checkout/confirm")
    async def confirm(session_id: str, body: Confirm, authorization: str | None = Header(default=None)):
        await owned(session_id, authorization)
        return _clean(await repository.confirm_quote(session_id, body.quote_id, body.quote_version, body.idempotency_key, body.confirmed, body.state_version))

    @app.get("/api/v1/sessions/{session_id}/orders")
    async def orders(session_id: str, authorization: str | None = Header(default=None)):
        await owned(session_id, authorization)
        return {"orders": _clean(await repository.list_orders(session_id))}

    def openapi():
        if app.openapi_schema is None:
            schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
            schema.setdefault("components", {}).setdefault("securitySchemes", {})["SessionBearer"] = {"type": "http", "scheme": "bearer", "description": "Токен возвращается только при POST /sessions и даёт доступ только к этой сессии."}
            for path, methods in schema["paths"].items():
                if path.startswith(("/api/v1/sessions/", "/api/v1/runs/")):
                    for method, operation in methods.items():
                        if method in {"get", "post"}:
                            operation["security"] = [{"SessionBearer": []}]
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi
    return app


app = create_app()
