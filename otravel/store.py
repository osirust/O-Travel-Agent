"""Durable PostgreSQL repository; no model/tool HTTP calls belong in this module.

Schema version 1 uses idempotent create_all during the initial scaffold. Domain
updates and pause/resume invalidate optimistic versions; every lock is confined
to a short transaction. External effects are not claimed to be exactly-once.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    BigInteger, CheckConstraint, Column, DateTime, ForeignKey, Identity,
    Index, Integer, MetaData, String, Table, Text, UniqueConstraint,
    func, insert, select, update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.ext.asyncio import create_async_engine


SCHEMA_VERSION = 1
REAL_MODEL_PROVIDERS = frozenset({"gemini", "openrouter", "groq", "cloudflare", "zai", "gigachat"})
INDEPENDENT_REMOTE_PROVIDERS = frozenset({"groq", "cloudflare", "zai", "gigachat"})
SINGLE_ATTEMPT_REMOTE_PROVIDERS = frozenset({"cloudflare", "zai", "gigachat"})
LOCAL_MODEL_PROVIDERS = frozenset({"ollama"})
metadata = MetaData()
schema_versions = Table(
    "schema_versions", metadata,
    Column("version", Integer, primary_key=True),
    Column("installed_at", DateTime(timezone=True), nullable=False),
)
sessions = Table(
    "sessions", metadata,
    Column("session_id", String(36), primary_key=True),
    Column("trip_id", String(64), nullable=False, unique=True),
    Column("state_version", Integer, nullable=False),
    Column("state", JSONB, nullable=False),
    Column("status", String(40), nullable=False),
    Column("waiting_reason", Text),
    Column("pending_question", JSONB),
    Column("owner_token_hash", String(64)),
    Column("active_run_id", String(36)),
    Column("active_run_started_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("state_version >= 0", name="session_version_nonnegative"),
)
messages = Table(
    "messages", metadata,
    Column("sequence", BigInteger, Identity(), primary_key=True),
    Column("id", String(36), nullable=False, unique=True),
    Column("session_id", ForeignKey("sessions.session_id"), nullable=False),
    Column("run_id", String(36)),
    Column("client_message_id", String(200)),
    Column("role", String(16), nullable=False),
    Column("text", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("session_id", "client_message_id", name="uq_client_message"),
    Index("ix_messages_session_sequence", "session_id", "sequence"),
)
runs = Table(
    "agent_runs", metadata,
    Column("id", String(36), primary_key=True),
    Column("session_id", ForeignKey("sessions.session_id"), nullable=False),
    Column("input_message_id", ForeignKey("messages.id"), nullable=False),
    Column("client_message_id", String(200), nullable=False),
    Column("input_text", Text, nullable=False),
    Column("request_expected_version", Integer),
    Column("based_version", Integer, nullable=False),
    Column("status", String(32), nullable=False),
    Column("result", JSONB),
    Column("error", JSONB),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    UniqueConstraint("session_id", "client_message_id", name="uq_run_client_message"),
    Index("ix_runs_status_created", "status", "created_at"),
    Index("ix_runs_session", "session_id"),
)
events = Table(
    "agent_events", metadata,
    Column("sequence", BigInteger, Identity(), primary_key=True),
    Column("event_id", String(36), nullable=False, unique=True),
    Column("session_id", ForeignKey("sessions.session_id"), nullable=False),
    Column("run_id", ForeignKey("agent_runs.id")),
    Column("event_type", String(100), nullable=False),
    Column("state_version", Integer, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Index("ix_events_session_sequence", "session_id", "sequence"),
    Index("ix_events_type_created", "event_type", "created_at"),
)
revisions = Table(
    "state_revisions", metadata,
    Column("id", String(36), primary_key=True),
    Column("session_id", ForeignKey("sessions.session_id"), nullable=False),
    Column("state_version", Integer, nullable=False),
    Column("state", JSONB, nullable=False),
    Column("session_status", String(40), nullable=False),
    Column("run_id", ForeignKey("agent_runs.id")),
    Column("changes", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("session_id", "state_version", name="uq_session_revision"),
)
quotes = Table(
    "quotes", metadata,
    Column("id", String(36), primary_key=True),
    Column("session_id", ForeignKey("sessions.session_id"), nullable=False),
    Column("quote_version", Integer, nullable=False),
    Column("state_version", Integer, nullable=False),
    Column("plan", JSONB, nullable=False),
    Column("status", String(24), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Index("ix_quotes_session", "session_id"),
)
demo_orders = Table(
    "demo_orders", metadata,
    Column("id", String(36), primary_key=True),
    Column("session_id", ForeignKey("sessions.session_id"), nullable=False),
    Column("quote_id", ForeignKey("quotes.id"), nullable=False, unique=True),
    Column("quote_version", Integer, nullable=False),
    Column("state_version", Integer, nullable=False),
    Column("idempotency_key", String(200), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("plan", JSONB, nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("session_id", "idempotency_key", name="uq_demo_order_operation"),
    Index("ix_orders_session_created", "session_id", "created_at"),
)


class StoreError(Exception):
    code = "store_error"

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class NotFound(StoreError):
    code = "not_found"


class Conflict(StoreError):
    code = "conflict"


class StaleQuote(Conflict):
    code = "stale_quote"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: Any) -> Any:
    """Detach caller objects, reject NaN and non-JSON values before any write."""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise StoreError("Ожидаются корректные JSON-данные.", "invalid_json") from exc


def _safe_payload(value: Any) -> Any:
    sensitive = {
        "api_key", "apikey", "x_api_key", "token", "access_token", "refresh_token",
        "authorization", "password", "secret", "client_secret", "database_url",
        "gemini_api_key", "openrouter_api_key", "serpapi_api_key", "liteapi_api_key",
        "duffel_access_token", "owner_token_hash", "url", "request_url", "http_url",
    }
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.casefold().replace("-", "_") in sensitive
            else _safe_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_safe_payload(item) for item in value]
    if isinstance(value, str):
        return re.sub(r"https?://\S+", "[URL REDACTED]", value)
    return value


def _public(row: Any) -> dict:
    def convert(value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return {key: convert(item) for key, item in value.items()}
        if isinstance(value, list):
            return [convert(item) for item in value]
        return value
    return convert(dict(row))


def _run(row: Any) -> dict:
    result = _public(row)
    result.update(run_id=result["id"], current_status=result["status"], input=result["input_text"])
    return result


def _quote(row: Any) -> dict:
    result = _public(row)
    result["quote_id"] = result["id"]
    return result


def _order(row: Any) -> dict:
    result = _public(row)
    result.pop("request_hash", None)
    result.pop("idempotency_key", None)
    result.update(order_id=result["id"], is_demo=True, booking_created_at_supplier=False)
    return result


def _version(value: int | None) -> None:
    if value is not None and (type(value) is not int or value < 0):
        raise StoreError("Версия должна быть целым неотрицательным числом.", "invalid_version")


def _key(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise StoreError("Требуется непустой ключ длиной до 200 символов.", "invalid_key")


def _stamped(state: dict, version: int) -> dict:
    if not isinstance(state, dict):
        raise StoreError("Состояние должно быть JSON-объектом.", "invalid_state")
    result = _json(state)
    result["state_version"] = version
    return result


def _pending_after_control(session: dict, version: int) -> dict | None:
    """Carry a fresh clarification across a control-only revision, not an edit."""
    pending = copy.deepcopy(session.get("pending_question"))
    before = session["state_version"]
    if (not isinstance(pending, dict) or type(pending.get("state_version")) is not int
            or pending["state_version"] != before):
        return None
    pending["state_version"] = version
    context = pending.get("context")
    if isinstance(context, dict):
        owners = {"date_proposal": "date_slots.v1", "duration_proposal": "date_slots.duration.v1",
                  "leg_mode_proposal": "leg_mode_intent_v1"}
        for name, owner in owners.items():
            packet = context.get(name)
            if (isinstance(packet, dict) and packet.get("owner") == owner
                    and packet.get("trip_id") == session["trip_id"]
                    and type(packet.get("based_state_version")) is int and packet["based_state_version"] == before):
                packet["based_state_version"] = version
        if type(context.get("based_state_version")) is int and context["based_state_version"] == before:
            context["based_state_version"] = version
    return pending


def _require_checkout_ready(session: dict) -> None:
    if session.get("active_run_id") is not None:
        raise Conflict("Дождитесь завершения обработки поездки.", "processing_in_progress")
    if session.get("waiting_reason") == "user_pause":
        raise Conflict("Поездка на паузе. Сначала явно продолжите работу с поездкой.", "user_paused")
    if session.get("waiting_reason") == "clarification" and session.get("pending_question") is not None:
        raise Conflict("Сначала уточните условия поездки.", "clarification_required")
    if session.get("status") == "RECOVERABLE_ERROR":
        raise Conflict("После ошибки проверьте условия и получите новый план.", "trip_requires_recovery")


class Store:
    def __init__(self, database_url: str):
        if database_url.startswith("postgres://"):
            database_url = "postgresql+asyncpg://" + database_url[len("postgres://"):]
        elif database_url.startswith("postgresql://"):
            database_url = "postgresql+asyncpg://" + database_url[len("postgresql://"):]
        if not database_url.startswith(("postgresql+asyncpg://", "postgresql+psycopg://")):
            raise StoreError("Нужен async PostgreSQL URL.", "invalid_database_url")
        self.engine = create_async_engine(
            database_url, pool_pre_ping=True, echo=False, hide_parameters=True,
        )

    async def initialize(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
            await conn.execute(pg_insert(schema_versions).values(
                version=SCHEMA_VERSION, installed_at=_now()
            ).on_conflict_do_nothing(index_elements=[schema_versions.c.version]))

    async def close(self) -> None:
        await self.engine.dispose()

    async def _session(self, conn, session_id: str, *, lock: bool = False) -> dict:
        query = select(sessions).where(sessions.c.session_id == session_id)
        row = (await conn.execute(query.with_for_update() if lock else query)).mappings().first()
        if row is None:
            raise NotFound("Сессия не найдена.")
        return dict(row)

    async def _run(self, conn, run_id: str, *, lock: bool = False) -> dict:
        query = select(runs).where(runs.c.id == run_id)
        row = (await conn.execute(query.with_for_update() if lock else query)).mappings().first()
        if row is None:
            raise NotFound("Обработка сообщения не найдена.")
        return dict(row)

    async def _event(self, conn, session: dict, run_id: str | None,
                     event_type: str, payload: dict, *, timestamp: datetime | None = None) -> dict:
        row = (await conn.execute(insert(events).values(
            event_id=str(uuid4()), session_id=session["session_id"], run_id=run_id,
            event_type=event_type, state_version=session["state_version"],
            payload=_safe_payload(_json(payload)), created_at=timestamp if timestamp is not None else _now(),
        ).returning(events))).mappings().one()
        return _public(row)

    async def _revision(self, conn, session: dict, run_id: str | None,
                        changes: list | None = None) -> None:
        await conn.execute(insert(revisions).values(
            id=str(uuid4()), session_id=session["session_id"],
            state_version=session["state_version"], state=session["state"],
            session_status=session["status"], run_id=run_id,
            changes=_json(changes or []), created_at=_now(),
        ))

    async def _assistant(self, conn, session_id: str, run_id: str | None, text: str) -> dict:
        row = (await conn.execute(insert(messages).values(
            id=str(uuid4()), session_id=session_id, run_id=run_id,
            role="assistant", text=text, created_at=_now(),
        ).returning(messages))).mappings().one()
        return _public(row)

    async def _invalidate_quotes(self, conn, session_id: str) -> None:
        await conn.execute(update(quotes).where(
            quotes.c.session_id == session_id, quotes.c.status == "active"
        ).values(status="stale"))

    async def create_session(self, state: dict, owner_token_hash: str | None = None) -> dict:
        if owner_token_hash is not None and (
            len(owner_token_hash) != 64 or any(c not in "0123456789abcdef" for c in owner_token_hash)
        ):
            raise StoreError("Некорректный hash токена сессии.", "invalid_owner_hash")
        now = _now()
        values = dict(
            session_id=str(uuid4()), trip_id=str(uuid4()), state_version=0,
            state=_stamped(state, 0), status="NEW", waiting_reason=None,
            pending_question=None, owner_token_hash=owner_token_hash,
            active_run_id=None, active_run_started_at=None, created_at=now, updated_at=now,
        )
        values["state"]["trip_id"] = values["trip_id"]
        async with self.engine.begin() as conn:
            await conn.execute(insert(sessions).values(**values))
            await self._revision(conn, values, None)
            await self._event(conn, values, None, "SESSION_CREATED", {})
        return _public(values)

    async def get_session(self, session_id: str) -> dict:
        # owner_token_hash is internal: the HTTP layer must strip it from responses.
        async with self.engine.connect() as conn:
            return _public(await self._session(conn, session_id))

    async def list_messages(self, session_id: str) -> list[dict]:
        async with self.engine.connect() as conn:
            await self._session(conn, session_id)
            rows = (await conn.execute(select(messages).where(
                messages.c.session_id == session_id
            ).order_by(messages.c.sequence))).mappings().all()
            return [_public(row) for row in rows]

    async def list_events(self, session_id: str, after: int = 0) -> list[dict]:
        _version(after)
        async with self.engine.connect() as conn:
            await self._session(conn, session_id)
            rows = (await conn.execute(select(events).where(
                events.c.session_id == session_id, events.c.sequence > after
            ).order_by(events.c.sequence))).mappings().all()
            return [_public(row) for row in rows]

    async def intake_message(self, session_id: str, client_message_id: str,
                             text: str, expected_version: int | None = None) -> tuple[dict, bool]:
        _key(client_message_id)
        _version(expected_version)
        if not isinstance(text, str) or not text.strip():
            raise StoreError("Текст сообщения пуст.", "invalid_message")
        async with self.engine.begin() as conn:
            session = await self._session(conn, session_id, lock=True)
            existing = (await conn.execute(select(runs).where(
                runs.c.session_id == session_id, runs.c.client_message_id == client_message_id
            ))).mappings().first()
            if existing is not None:
                if existing["input_text"] != text or existing["request_expected_version"] != expected_version:
                    raise Conflict("Этот client_message_id уже связан с другим запросом.", "idempotency_mismatch")
                return _run(existing), True
            if expected_version is not None and expected_version != session["state_version"]:
                raise Conflict("Состояние поездки изменилось.", "stale_state")
            run_id, message_id, now = str(uuid4()), str(uuid4()), _now()
            await conn.execute(insert(messages).values(
                id=message_id, session_id=session_id, run_id=run_id,
                client_message_id=client_message_id, role="user", text=text, created_at=now,
            ))
            row = (await conn.execute(insert(runs).values(
                id=run_id, session_id=session_id, input_message_id=message_id,
                client_message_id=client_message_id, input_text=text,
                request_expected_version=expected_version, based_version=session["state_version"],
                status="pending", created_at=now,
            ).returning(runs))).mappings().one()
            await self._event(conn, session, run_id, "USER_MESSAGE_RECEIVED", {"message_id": message_id})
            return _run(row), False

    async def get_run(self, run_id: str) -> dict:
        async with self.engine.connect() as conn:
            return _run(await self._run(conn, run_id))

    async def prepare_undo(self, session_id: str, expected_version: int) -> dict:
        """Read an undo candidate; finish_run CAS must commit it as a new revision.

        Control/order revisions and already undone edits are skipped. Only the
        domain snapshot is returned: session phase, quotes and orders never rewind.
        """
        _version(expected_version)
        async with self.engine.connect() as conn:
            session = await self._session(conn, session_id)
            if session["state_version"] != expected_version:
                raise Conflict("Поездка изменилась перед undo.", "stale_state")
            rows = (await conn.execute(select(revisions).where(
                revisions.c.session_id == session_id
            ).order_by(revisions.c.state_version))).mappings().all()

        def core(state: dict) -> dict:
            return {key: value for key, value in state.items() if key not in {"state_version", "trip_id"}}

        consumed = set()
        for row in rows:
            for change in row["changes"] or []:
                if isinstance(change, dict) and change.get("operation") == "undo" and type(change.get("undone_revision")) is int:
                    consumed.add(change["undone_revision"])
        for index in range(len(rows) - 1, 0, -1):
            row, previous = rows[index], rows[index - 1]
            undo_row = any(isinstance(change, dict) and change.get("operation") == "undo" for change in row["changes"] or [])
            if not row["run_id"] or undo_row or row["state_version"] in consumed or core(row["state"]) == core(previous["state"]):
                continue
            restored = copy.deepcopy(previous["state"])
            # These are not domain wishes and must never revive consent/cache IDs.
            for field in ("cart_quote_id", "quote_id", "quote_version", "consent", "confirmed", "selected_offer_ids"):
                restored.pop(field, None)
            restored.update(trip_id=session["trip_id"], state_version=expected_version)
            marker = {"undone_revision": row["state_version"], "restored_from_revision": previous["state_version"]}
            changes = [{"operation": "undo", "field": key, "before": copy.deepcopy(session["state"].get(key)), "after": copy.deepcopy(restored.get(key)), **marker}
                       for key in sorted(set(session["state"]) | set(restored)) if key not in {"state_version", "trip_id"} and session["state"].get(key) != restored.get(key)]
            if not changes:
                changes = [{"operation": "undo", "field": "trip", "before": core(session["state"]), "after": core(restored), **marker}]
            return {"state": restored, "changes": changes, "undo": marker}
        raise Conflict("Нет успешной правки, которую можно отменить.", "nothing_to_undo")

    async def cancel_processing(self, session_id: str, expected_version: int,
                                control_run_id: str | None = None,
                                target_run_id: str | None = None,
                                result: dict | None = None) -> dict:
        """Cancel one in-flight/oldest pending run; never delete state or orders."""
        _version(expected_version)
        async with self.engine.begin() as conn:
            session = await self._session(conn, session_id, lock=True)
            if session["state_version"] != expected_version:
                raise Conflict("Поездка изменилась перед отменой обработки.", "stale_state")
            if control_run_id:
                control = await self._run(conn, control_run_id)
                if control["session_id"] != session_id:
                    raise NotFound("Команда этой сессии не найдена.")
                control_sequence = (await conn.execute(select(messages.c.sequence).where(messages.c.id == control["input_message_id"]))).scalar_one()
            else:
                control_sequence = None
            target_id = target_run_id or session["active_run_id"]
            if target_id == control_run_id:
                target_id = None
            if target_id and control_sequence is not None:
                target_row = await self._run(conn, target_id)
                target_sequence = (await conn.execute(select(messages.c.sequence).where(messages.c.id == target_row["input_message_id"]))).scalar_one()
                if target_sequence >= control_sequence:
                    target_id = None
            if target_id is None:
                query = select(runs).join(messages, messages.c.id == runs.c.input_message_id).where(
                    runs.c.session_id == session_id, runs.c.status == "pending"
                )
                if control_run_id:
                    query = query.where(runs.c.id != control_run_id, messages.c.sequence < control_sequence)
                candidate = (await conn.execute(query.order_by(messages.c.sequence).limit(1))).mappings().first()
                target_id = candidate["id"] if candidate else None
            if target_id is None:
                await self._event(conn, session, control_run_id, "CANCEL_NO_ACTIVE_RUN", {})
                return {"session": _public(session), "cancelled_run_id": None, "was_active": False}
            target = await self._run(conn, target_id, lock=True)
            if target["session_id"] != session_id:
                raise NotFound("Обработка этой сессии не найдена.")
            if target["status"] not in {"pending", "running"}:
                return {"session": _public(session), "cancelled_run_id": None, "was_active": False}
            was_active = session["active_run_id"] == target_id
            version = session["state_version"] + 1
            paused = session["waiting_reason"] == "user_pause"
            values = dict(state_version=version, state=_stamped(session["state"], version),
                          status="WAITING_USER" if paused else "TRIP_DRAFT",
                          waiting_reason="user_pause" if paused else None,
                          pending_question=None, updated_at=_now())
            if was_active:
                values.update(active_run_id=None, active_run_started_at=None)
            await conn.execute(update(sessions).where(sessions.c.session_id == session_id).values(**values))
            session.update(values)
            await self._invalidate_quotes(conn, session_id)
            await self._revision(conn, session, control_run_id, [{"op": "cancel_processing", "run_id": target_id}])
            problem = {"code": "user_cancelled", "message": "Текущая обработка отменена; поездка и заказы сохранены.", "retryable": False}
            message = await self._assistant(conn, session_id, target_id, problem["message"])
            response = self._response(session, target_id, problem["message"], message, [], problem,
                                      {"cancelled": True, **_json(result or {})})
            response["run"] = {**_run(target), "status": "cancelled", "current_status": "cancelled", "result": None}
            await conn.execute(update(runs).where(runs.c.id == target_id).values(
                status="cancelled", error=problem, result=response, finished_at=_now()))
            await self._event(conn, session, target_id, "RUN_CANCELLED", {"orders_unchanged": True})
            return {"session": _public(session), "cancelled_run_id": target_id, "was_active": was_active}

    async def list_pending_runs(self) -> list[dict]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(select(runs).join(
                messages, messages.c.id == runs.c.input_message_id
            ).where(
                runs.c.status == "pending"
            ).order_by(messages.c.sequence))).mappings().all()
            return [_run(row) for row in rows]

    async def claim_run(self, run_id: str) -> bool:
        async with self.engine.begin() as conn:
            run = await self._run(conn, run_id)
            session = await self._session(conn, run["session_id"], lock=True)
            run = await self._run(conn, run_id, lock=True)
            if run["status"] != "pending" or session["active_run_id"] is not None:
                return False
            if session["waiting_reason"] == "user_pause":
                return False
            now = _now()
            await conn.execute(update(sessions).where(
                sessions.c.session_id == session["session_id"]
            ).values(active_run_id=run_id, active_run_started_at=now, updated_at=now))
            await conn.execute(update(runs).where(runs.c.id == run_id).values(
                status="running", started_at=now, based_version=session["state_version"]
            ))
            await self._event(conn, session, run_id, "RUN_STARTED", {})
            return True

    @staticmethod
    def _response(session: dict, run_id: str, reply: str, message: dict | None,
                  changes: list | None, error: dict | None, result: dict | None) -> dict:
        visible = _public(session)
        return {
            **visible, "session": visible, "run_id": run_id, "reply": reply,
            "message": message, "assistant_message": message, "changes": changes or [],
            "error": error, "result": result,
        }

    async def finish_run(self, run_id: str, expected_version: int, state: dict | None,
                         session_status: str, reply: str, pending_question: dict | None = None,
                         waiting_reason: str | None = None, changes: list | None = None,
                         error: dict | None = None, result: dict | None = None) -> dict:
        _version(expected_version)
        stale: Conflict | None = None
        response: dict = {}
        async with self.engine.begin() as conn:
            run = await self._run(conn, run_id)
            session = await self._session(conn, run["session_id"], lock=True)
            run = await self._run(conn, run_id, lock=True)
            if run["status"] == "completed":
                return copy.deepcopy(run["result"])
            if run["status"] not in {"pending", "running"}:
                raise Conflict("Обработка уже завершена или прервана.", "run_not_active")
            owns_lease = session["active_run_id"] == run_id
            if session["state_version"] != expected_version or (
                run["status"] == "running" and run["based_version"] != expected_version
            ) or (
                session["active_run_id"] is not None and not owns_lease
            ):
                problem = {"code": "stale_state", "message": "Результат относится к устаревшей версии."}
                await conn.execute(update(runs).where(runs.c.id == run_id).values(
                    status="conflict", error=problem, finished_at=_now()
                ))
                if owns_lease:
                    await conn.execute(update(sessions).where(
                        sessions.c.session_id == session["session_id"]
                    ).values(active_run_id=None, active_run_started_at=None, updated_at=_now()))
                await self._event(conn, session, run_id, "RUN_CONFLICT", problem)
                stale = Conflict(problem["message"], problem["code"])
            else:
                new_version = expected_version + (state is not None)
                values = dict(
                    state=_stamped(session["state"] if state is None else state, new_version),
                    state_version=new_version, status=session_status,
                    pending_question=_json(pending_question), waiting_reason=waiting_reason,
                    active_run_id=None, active_run_started_at=None, updated_at=_now(),
                )
                # A demo template or model patch must not replace session identity.
                values["state"]["trip_id"] = session["trip_id"]
                updated = (await conn.execute(update(sessions).where(
                    sessions.c.session_id == session["session_id"],
                    sessions.c.state_version == expected_version,
                ).values(**values).returning(sessions))).mappings().one()
                session = dict(updated)
                if state is not None:
                    await self._invalidate_quotes(conn, session["session_id"])
                    await self._revision(conn, session, run_id, changes)
                    undo = next((change for change in changes or [] if isinstance(change, dict) and change.get("operation") == "undo"), None)
                    if undo:
                        await self._event(conn, session, run_id, "STATE_UNDONE", {key: undo[key] for key in ("undone_revision", "restored_from_revision")})
                message = await self._assistant(conn, session["session_id"], run_id, reply)
                clean_error = _safe_payload(_json(error))
                response = self._response(session, run_id, reply, message, _json(changes), clean_error, _json(result))
                response["run"] = {**_run(run), "status": "completed", "current_status": "completed", "result": None}
                await conn.execute(update(runs).where(runs.c.id == run_id).values(
                    status="completed", result=response, error=clean_error, finished_at=_now()
                ))
                await self._event(conn, session, run_id, "RUN_COMPLETED", {
                    "domain_changed": state is not None, "error": clean_error,
                })
        if stale is not None:
            raise stale
        return response

    async def fail_run(self, run_id: str, error: dict, reply: str = "",
                       preserve_session: bool = True) -> dict:
        clean_error = _safe_payload(_json(error))
        async with self.engine.begin() as conn:
            run = await self._run(conn, run_id)
            session = await self._session(conn, run["session_id"], lock=True)
            run = await self._run(conn, run_id, lock=True)
            if run["status"] == "completed":
                return copy.deepcopy(run["result"])
            if run["status"] in {"failed", "interrupted", "conflict", "cancelled"} and run["result"]:
                return copy.deepcopy(run["result"])
            values: dict = {"updated_at": _now()}
            if session["active_run_id"] == run_id:
                values.update(active_run_id=None, active_run_started_at=None)
            if not preserve_session and session["waiting_reason"] != "user_pause":
                values["status"] = "RECOVERABLE_ERROR"
            await conn.execute(update(sessions).where(
                sessions.c.session_id == session["session_id"]
            ).values(**values))
            session.update(values)
            message = await self._assistant(conn, session["session_id"], run_id, reply) if reply else None
            response = self._response(session, run_id, reply, message, [], clean_error, None)
            status = run["status"] if run["status"] in {"interrupted", "conflict", "cancelled"} else "failed"
            response["run"] = {**_run(run), "status": status, "current_status": status, "result": None}
            await conn.execute(update(runs).where(runs.c.id == run_id).values(
                status=status, error=clean_error, result=response, finished_at=_now()
            ))
            await self._event(conn, session, run_id, "RUN_FAILED", clean_error)
            return response

    async def set_control(self, session_id: str, action: str = "pause", reply: str = "") -> dict:
        if action not in {"pause", "resume"}:
            raise StoreError("Допустимы pause или resume.", "invalid_control")
        async with self.engine.begin() as conn:
            session = await self._session(conn, session_id, lock=True)
            version = session["state_version"] + 1
            pending = _pending_after_control(session, version)
            values = dict(
                state_version=version, state=_stamped(session["state"], version),
                status="WAITING_USER" if action == "pause" or pending else "TRIP_DRAFT",
                waiting_reason="user_pause" if action == "pause" else "clarification" if pending else None,
                pending_question=pending, updated_at=_now(),
            )
            await conn.execute(update(sessions).where(
                sessions.c.session_id == session_id
            ).values(**values))
            session.update(values)
            await self._invalidate_quotes(conn, session_id)
            await self._revision(conn, session, None, [{"op": action}])
            await self._event(conn, session, None, "USER_PAUSED" if action == "pause" else "USER_RESUMED", {})
            if reply:
                if action == "resume" and pending and isinstance(pending.get("text"), str):
                    reply += " " + pending["text"]
                await self._assistant(conn, session_id, None, reply)
            return _public(session)

    async def create_quote(self, session_id: str, expected_version: int, plan: dict,
                           expires_seconds: int = 600) -> dict:
        _version(expected_version)
        if type(expires_seconds) is not int or expires_seconds <= 0:
            raise StoreError("Срок действия должен быть положительным целым.", "invalid_expiry")
        if not isinstance(plan, dict):
            raise StoreError("План должен быть JSON-объектом.", "invalid_plan")
        async with self.engine.begin() as conn:
            session = await self._session(conn, session_id, lock=True)
            if session["state_version"] != expected_version:
                raise Conflict("Состояние поездки изменилось.", "stale_state")
            if session["waiting_reason"] == "user_pause":
                raise Conflict("Поездка поставлена на паузу.", "user_paused")
            _require_checkout_ready(session)
            now = _now()
            expires_at = now + timedelta(seconds=min(600, expires_seconds))
            if plan.get("fx_snapshot") is not None:
                from .fx import FxUnavailable, validate_snapshot
                try:
                    pinned_fx = validate_snapshot(plan["fx_snapshot"], now)
                    expires_at = min(expires_at, datetime.fromisoformat(pinned_fx["expires_at"]).astimezone(timezone.utc))
                except FxUnavailable as error:
                    raise StoreError(error.message, error.code) from None
            snapshots = plan.get("source_snapshots", [])
            if not isinstance(snapshots, list):
                raise StoreError("Некорректные снимки источников корзины.", "invalid_source_snapshots")
            for snapshot in snapshots:
                if not isinstance(snapshot, dict):
                    raise StoreError("Некорректный снимок источника.", "invalid_source_snapshots")
                if snapshot.get("source_type") == "DEMO":
                    continue
                try:
                    external_expiry = datetime.fromisoformat(snapshot["expires_at"].replace("Z", "+00:00"))
                    if external_expiry.tzinfo is None:
                        raise ValueError
                except (KeyError, ValueError, TypeError, AttributeError):
                    raise StoreError("Для внешнего компонента нужен expires_at с timezone.", "invalid_source_expiry") from None
                expires_at = min(expires_at, external_expiry.astimezone(timezone.utc))
            if expires_at <= now:
                raise StaleQuote("Источник корзины уже устарел; нужен новый поиск.")
            # A later preview supersedes every prior active consent atomically.
            await self._invalidate_quotes(conn, session_id)
            row = (await conn.execute(insert(quotes).values(
                id=str(uuid4()), session_id=session_id, quote_version=1,
                state_version=expected_version, plan=_json(plan), status="active",
                expires_at=expires_at, created_at=now,
            ).returning(quotes))).mappings().one()
            await self._event(conn, session, None, "QUOTE_CREATED", {"quote_id": row["id"]})
            return _quote(row)

    async def begin_quote_preview(self, session_id: str, expected_version: int) -> None:
        """Revoke old consent before external reprice, including failed attempts."""
        _version(expected_version)
        async with self.engine.begin() as conn:
            session = await self._session(conn, session_id, lock=True)
            if session["state_version"] != expected_version:
                raise Conflict("Состояние поездки изменилось.", "stale_state")
            if session["waiting_reason"] == "user_pause":
                raise Conflict("Поездка поставлена на паузу.", "user_paused")
            _require_checkout_ready(session)
            await self._invalidate_quotes(conn, session_id)
            await self._event(conn, session, None, "QUOTE_PREVIEW_STARTED", {"state_version": expected_version})

    async def get_quote(self, quote_id: str) -> dict:
        async with self.engine.connect() as conn:
            row = (await conn.execute(select(quotes).where(quotes.c.id == quote_id))).mappings().first()
            if row is None:
                raise NotFound("Корзина не найдена.")
            return _quote(row)

    async def current_selected_plan(self, session_id: str, expected_version: int) -> dict | None:
        """Read an owned caller's latest UI selection in one SQL snapshot.

        The latest row is chosen before status/expiry checks: a later failed,
        revoked or ordered selection must not resurrect an older active row.
        No row locks, history writes, prices or consent are returned.
        """
        _version(expected_version)
        from .selected_plan import project_current_selection

        latest_id = (select(quotes.c.id).where(quotes.c.session_id == sessions.c.session_id)
            .order_by(quotes.c.created_at.desc(), quotes.c.quote_version.desc())
            .limit(1).correlate(sessions).scalar_subquery())
        query = (select(sessions.c.session_id, sessions.c.trip_id, sessions.c.state_version, sessions.c.state,
                quotes.c.session_id.label("quote_session_id"), quotes.c.state_version.label("quote_state_version"),
                quotes.c.status.label("quote_status"), quotes.c.expires_at, quotes.c.plan)
            .select_from(sessions.outerjoin(quotes, quotes.c.id == latest_id))
            .where(sessions.c.session_id == session_id).limit(1))
        async with self.engine.connect() as conn:
            row = (await conn.execute(query)).mappings().first()
        if row is None:
            raise NotFound("Сессия не найдена.")
        session = {key: row[key] for key in ("session_id", "trip_id", "state_version", "state")}
        quote = {"session_id": row["quote_session_id"], "state_version": row["quote_state_version"],
            "status": row["quote_status"], "expires_at": row["expires_at"], "plan": row["plan"]}
        return project_current_selection(session, quote, expected_version, _now())

    async def confirm_quote(self, session_id: str, quote_id: str, quote_version: int,
                            idempotency_key: str, confirmed: bool,
                            expected_state_version: int) -> dict:
        _key(idempotency_key)
        _version(expected_state_version)
        _version(quote_version)
        if confirmed is not True:
            raise Conflict("Нужно явное подтверждение этой корзины.", "confirmation_required")
        request_hash = hashlib.sha256(json.dumps({
            "session_id": session_id, "quote_id": quote_id, "quote_version": quote_version,
            "expected_state_version": expected_state_version, "confirmed": confirmed,
        }, sort_keys=True).encode()).hexdigest()
        async with self.engine.begin() as conn:
            session = await self._session(conn, session_id, lock=True)
            previous = (await conn.execute(select(demo_orders).where(
                demo_orders.c.session_id == session_id,
                demo_orders.c.idempotency_key == idempotency_key,
            ))).mappings().first()
            if previous is not None:
                if previous["request_hash"] != request_hash:
                    raise Conflict("Ключ подтверждения использован с другими параметрами.", "idempotency_mismatch")
                return _order(previous)
            _require_checkout_ready(session)
            quote = (await conn.execute(select(quotes).where(
                quotes.c.id == quote_id, quotes.c.session_id == session_id
            ).with_for_update())).mappings().first()
            if quote is None:
                raise NotFound("Корзина этой сессии не найдена.")
            existing_order = (await conn.execute(select(demo_orders.c.id).where(
                demo_orders.c.quote_id == quote_id
            ))).first()
            if existing_order is not None:
                raise Conflict("Для этой корзины уже создан тестовый заказ.", "quote_already_confirmed")
            if session["waiting_reason"] == "user_pause":
                raise Conflict("Сначала продолжите работу с поездкой.", "user_paused")
            if session["state_version"] != expected_state_version:
                raise StaleQuote("Версия поездки изменилась.")
            if (quote["quote_version"] != quote_version or quote["state_version"] != expected_state_version
                    or quote["status"] != "active" or quote["expires_at"] <= _now()):
                raise StaleQuote("Корзина устарела: необходимы перепроверка и новое подтверждение.")
            version = session["state_version"] + 1
            row = (await conn.execute(insert(demo_orders).values(
                id=str(uuid4()), session_id=session_id, quote_id=quote_id,
                quote_version=quote_version, state_version=version,
                idempotency_key=idempotency_key, request_hash=request_hash,
                plan=_json(quote["plan"]), status="BOOKED_DEMO", created_at=_now(),
            ).returning(demo_orders))).mappings().one()
            values = dict(state_version=version, state=_stamped(session["state"], version),
                          status="BOOKED_DEMO", waiting_reason=None, pending_question=None, updated_at=_now())
            await conn.execute(update(sessions).where(sessions.c.session_id == session_id).values(**values))
            session.update(values)
            await self._invalidate_quotes(conn, session_id)
            await conn.execute(update(quotes).where(quotes.c.id == quote_id).values(status="ordered"))
            await self._revision(conn, session, None, [{"op": "demo_order", "order_id": row["id"]}])
            await self._event(conn, session, None, "DEMO_ORDER_CREATED", {
                "order_id": row["id"], "quote_id": quote_id, "is_demo": True,
            })
            return _order(row)

    async def list_orders(self, session_id: str) -> list[dict]:
        async with self.engine.connect() as conn:
            await self._session(conn, session_id)
            rows = (await conn.execute(select(demo_orders).where(
                demo_orders.c.session_id == session_id
            ).order_by(demo_orders.c.created_at))).mappings().all()
            return [_order(row) for row in rows]

    async def orders(self, session_id: str) -> list[dict]:
        return await self.list_orders(session_id)

    async def get_order(self, order_id: str) -> dict:
        async with self.engine.connect() as conn:
            row = (await conn.execute(select(demo_orders).where(
                demo_orders.c.id == order_id
            ))).mappings().first()
            if row is None:
                raise NotFound("Тестовый заказ не найден.")
            return _order(row)

    async def save_event(self, session_id: str, run_id: str | None,
                         event_type: str, payload: dict) -> dict:
        async with self.engine.begin() as conn:
            session = await self._session(conn, session_id)
            if run_id is not None:
                run = await self._run(conn, run_id)
                if run["session_id"] != session_id:
                    raise Conflict("Run принадлежит другой сессии.", "session_mismatch")
            return await self._event(conn, session, run_id, event_type, payload)

    async def select_model_role(self, session_id: str, run_id: str, expected_version: int, role: str) -> None:
        """Fix a server-owned role before inference, without a network lock."""
        _version(expected_version)
        if role != "readonly_answer_v1":
            raise StoreError("Неизвестная серверная роль модели.", "invalid_model_role")
        async with self.engine.begin() as conn:
            await conn.execute(select(func.pg_advisory_xact_lock(0x4F545241)))
            session = await self._session(conn, session_id, lock=True)
            run = await self._run(conn, run_id, lock=True)
            if (run["session_id"] != session_id or run["status"] != "running"
                    or session["active_run_id"] != run_id or session["state_version"] != expected_version
                    or run["based_version"] != expected_version or session["waiting_reason"] == "user_pause"):
                raise Conflict("Изменились версия или управление поездкой.", "processing_control_changed")
            previous = (await conn.execute(select(events.c.payload).where(
                events.c.run_id == run_id, events.c.event_type == "MODEL_ROLE_SELECTED"))).scalars().all()
            if previous:
                if any(value.get("role") != role or value.get("based_state_version") != expected_version for value in previous):
                    raise Conflict("Роль этого запуска уже выбрана.", "model_role_changed")
                return
            started = (await conn.execute(select(func.count()).select_from(events).where(
                events.c.run_id == run_id, events.c.event_type == "MODEL_CALL_STARTED"))).scalar_one()
            if started:
                raise Conflict("Нельзя менять роль после вызова модели.", "model_role_changed")
            await self._event(conn, session, run_id, "MODEL_ROLE_SELECTED", {
                "role": role, "based_state_version": expected_version, "max_calls": 1})

    async def reserve_model_call(self, session_id: str, run_id: str,
                                 limit: int, provider: str) -> bool:
        """Reserve an actual attempt in remote/local/test durable budget buckets.

        This is an application budget, not a query of the provider's allowance.
        Gemini/OpenRouter share accounting and a 2/run bucket. Gemini is capped
        at40/day; OpenRouter may explicitly opt up from caller-default40 to50/day
        for its free request allowance, without resetting existing usage. Groq has
        an independent default40/day pool, explicitly raisable up to1000/day.
        These are engineering ceilings, not vendor quota promises. Cloudflare and Z.ai have separate
        default 40/day caller limits; dedicated evaluation may explicitly raise
        them up to 2000/day. Cloudflare uses its own explicit setting, so raising
        the generic Z.ai limit does not expand it. Both are 1/run, with no remote attempt
        allowed before or after them in that run. Ollama has
        an independent hard 500/day and 1/run local bucket; it is not a test
        adapter. Caller limits can reduce but never increase these ceilings.
        Injected test adapters share a third bucket excluding both actual buckets.
        Unknown provider names fail closed rather than bypassing the limits.
        The canonical MODEL_CALL_STARTED event is written here exactly once per
        reservation; callers must not duplicate it in their normal event logging.
        """
        if type(limit) is not int or limit < 0:
            raise StoreError("Лимит должен быть целым неотрицательным.", "invalid_budget_limit")
        if not isinstance(provider, str) or not (
            provider in REAL_MODEL_PROVIDERS or provider in LOCAL_MODEL_PROVIDERS or provider == "injected_test_adapter"
            or provider.startswith("test_")
        ):
            raise StoreError("Нужен разрешённый реальный или явно тестовый провайдер.", "invalid_provider")
        async with self.engine.begin() as conn:
            # One project-wide, transaction-scoped lock. Never held over HTTP.
            await conn.execute(select(func.pg_advisory_xact_lock(0x4F545241)))
            session = await self._session(conn, session_id)
            run = await self._run(conn, run_id)
            if run["session_id"] != session_id:
                raise Conflict("Run принадлежит другой сессии.", "session_mismatch")
            reserved_at = _now().astimezone(timezone.utc)
            day_start = reserved_at.replace(hour=0, minute=0, second=0, microsecond=0)
            event_provider = events.c.payload["provider"].astext
            if provider in REAL_MODEL_PROVIDERS:
                # Independent accounts retain their own conservative daily pool.
                # Shared accounting is retained; caller defaults remain40.
                provider_scope = (event_provider == provider if provider in INDEPENDENT_REMOTE_PROVIDERS else
                    event_provider.in_(sorted(REAL_MODEL_PROVIDERS - INDEPENDENT_REMOTE_PROVIDERS)))
                effective_limit = min(limit, 2000 if provider in {"zai", "cloudflare"} else 1000 if provider in {"groq", "gigachat"} else 50 if provider == "openrouter" else 40)
                per_run_limit = 1 if provider in SINGLE_ATTEMPT_REMOTE_PROVIDERS else 2
            elif provider in LOCAL_MODEL_PROVIDERS:
                provider_scope = event_provider.in_(sorted(LOCAL_MODEL_PROVIDERS))
                effective_limit, per_run_limit = min(limit, 500), 1
            else:
                provider_scope = event_provider.not_in(sorted(REAL_MODEL_PROVIDERS | LOCAL_MODEL_PROVIDERS))
                effective_limit, per_run_limit = limit, None
            roles = (await conn.execute(select(events.c.payload).where(
                events.c.run_id == run_id, events.c.event_type == "MODEL_ROLE_SELECTED"))).scalars().all()
            readonly = any(value.get("role") == "readonly_answer_v1" for value in roles)
            if readonly:
                if (run["status"] != "running" or session.get("active_run_id") != run_id
                        or session.get("waiting_reason") == "user_pause"
                        or any(value.get("based_state_version") != session["state_version"] for value in roles)
                        or run["based_version"] != session["state_version"]):
                    raise Conflict("Изменились версия или управление поездкой.", "processing_control_changed")
                per_run_limit = 1
            if per_run_limit is not None:
                run_scope = (True if readonly else event_provider.in_(sorted(REAL_MODEL_PROVIDERS))
                             if provider in REAL_MODEL_PROVIDERS else provider_scope)
                run_count = (await conn.execute(select(func.count()).select_from(events).where(
                    events.c.event_type == "MODEL_CALL_STARTED", events.c.run_id == run_id,
                    run_scope,
                ))).scalar_one()
                if run_count >= per_run_limit:
                    return False
                if provider in REAL_MODEL_PROVIDERS and provider not in SINGLE_ATTEMPT_REMOTE_PROVIDERS and run_count:
                    prior_single_attempt = (await conn.execute(select(func.count()).select_from(events).where(
                        events.c.event_type == "MODEL_CALL_STARTED", events.c.run_id == run_id,
                        event_provider.in_(sorted(SINGLE_ATTEMPT_REMOTE_PROVIDERS)),
                    ))).scalar_one()
                    if prior_single_attempt:
                        return False
            count = (await conn.execute(select(func.count()).select_from(events).where(
                events.c.event_type == "MODEL_CALL_STARTED",
                events.c.created_at >= day_start,
                events.c.created_at < day_start + timedelta(days=1),
                provider_scope,
            ))).scalar_one()
            if count >= effective_limit:
                return False
            await self._event(conn, session, run_id, "MODEL_CALL_STARTED", {"provider": provider}, timestamp=reserved_at)
            return True

    async def recover_stale_runs(self, timeout_seconds: int = 120) -> list[dict]:
        if type(timeout_seconds) is not int or timeout_seconds < 0:
            raise StoreError("Некорректный срок восстановления.", "invalid_timeout")
        cutoff = _now() - timedelta(seconds=timeout_seconds)
        recovered = []
        async with self.engine.begin() as conn:
            candidates = (await conn.execute(select(sessions).where(
                sessions.c.active_run_id.is_not(None), sessions.c.active_run_started_at < cutoff
            ).order_by(sessions.c.session_id).with_for_update(skip_locked=True))).mappings().all()
            for candidate in candidates:
                session = dict(candidate)
                run = await self._run(conn, session["active_run_id"], lock=True)
                problem = {"code": "run_interrupted", "message": "Обработка прервана; состояние сохранено."}
                await conn.execute(update(sessions).where(
                    sessions.c.session_id == session["session_id"]
                ).values(active_run_id=None, active_run_started_at=None, updated_at=_now()))
                session.update(active_run_id=None, active_run_started_at=None)
                response = self._response(session, run["id"], "", None, [], problem, None)
                updated = (await conn.execute(update(runs).where(runs.c.id == run["id"]).values(
                    status="interrupted", error=problem, result=response, finished_at=_now()
                ).returning(runs))).mappings().one()
                await self._event(conn, session, run["id"], "RUN_INTERRUPTED", problem)
                recovered.append(_run(updated))
        return recovered

    async def model_availability(self, provider: str, model: str,
                                 now: datetime | None = None) -> dict:
        """Durable routing evidence; this performs no call, reservation or write.

        Known per-model upstream/network failures cool down for ten minutes.
        Account/auth/credit/unknown-429 limits block the entire provider until
        UTC midnight or a later confirmed MODEL_RESPONSE_RECEIVED. A metadata
        GET is not such evidence. After rotating a key, explicitly verify it;
        this method cannot infer account identity from a provider label.

        All events are project-global rather than session-scoped. Existing
        event_type/created_at indexes keep these short JSON-filtered reads small
        under the bounded daily call budget. Test-provider namespaces use the
        same classification, so fixtures do not contaminate real provider data.
        """
        if not isinstance(provider, str) or not (
            provider in REAL_MODEL_PROVIDERS or provider in LOCAL_MODEL_PROVIDERS or provider == "injected_test_adapter"
            or provider.startswith("test_")
        ):
            raise StoreError("Нужен разрешённый провайдер модели.", "invalid_provider")
        if not isinstance(model, str) or not model.strip() or len(model) > 300:
            raise StoreError("Нужен явный ID модели длиной до 300 символов.", "invalid_model")
        instant = _now() if now is None else now
        if not isinstance(instant, datetime) or instant.tzinfo is None or instant.utcoffset() is None:
            raise StoreError("Для cooldown нужно время с часовым поясом.", "invalid_clock")
        instant = instant.astimezone(timezone.utc)
        day_start = instant.replace(hour=0, minute=0, second=0, microsecond=0)
        next_day = day_start + timedelta(days=1)

        def classify(payload: dict) -> tuple[str | None, str]:
            code = payload.get("code")
            status = payload.get("http_status")
            source = payload.get("limit_source")
            if source and source != "upstream_provider_shared_pool":
                return "provider", "provider_limit_blocked"
            if code == "RATE_LIMITED" or type(status) is int and status == 429:
                return ("model", "upstream_pool_cooldown") if source == "upstream_provider_shared_pool" else ("provider", "provider_quota_or_unknown_429")
            if type(status) is int and status in {401, 403}:
                return "provider", "provider_auth_blocked"
            if type(status) is int and status == 402:
                return "provider", "provider_credit_blocked"
            if code == "HTTP_ERROR":
                if type(status) is not int:
                    return "provider", "unclassified_http_error"
                if 500 <= status < 600:
                    return "model", "upstream_http_cooldown"
            if code in {"NETWORK_ERROR", "REQUEST_TIMEOUT"}:
                return "model", "network_cooldown" if code == "NETWORK_ERROR" else "timeout_cooldown"
            return None, "not_an_availability_failure"

        event_types = ("MODEL_CALL_FAILED", "MODEL_RESPONSE_RECEIVED")
        columns = (events.c.event_type, events.c.payload, events.c.created_at, events.c.sequence)
        common = (events.c.event_type.in_(event_types), events.c.payload["provider"].astext == provider,
                  events.c.created_at <= instant)
        lookback = instant - timedelta(days=1) if provider in INDEPENDENT_REMOTE_PROVIDERS else instant - timedelta(minutes=10)
        async with self.engine.connect() as conn:
            # One statement snapshot for model cooldown and provider-wide limits:
            # a second query must not miss a concurrent account block seen by one.
            rows = (await conn.execute(select(*columns).where(
                *common, events.c.created_at >= min(day_start, lookback)
            ).order_by(events.c.created_at.desc(), events.c.sequence.desc()))).mappings().all()
        provider_rows = [row for row in rows if row["created_at"] >= day_start or provider in INDEPENDENT_REMOTE_PROVIDERS]
        model_rows = [row for row in rows if row["payload"].get("model") == model and row["created_at"] >= instant - timedelta(minutes=10)]

        def answer(available: bool, reason: str, scope: str, until: datetime | None = None,
                   allow_fallback: bool = False) -> dict:
            return {"available": available, "reason": reason, "scope": scope,
                    "retry_after": max(0, math.ceil((until - instant).total_seconds())) if until else None,
                    "retry_at": until.isoformat() if until else None,
                    "allow_fallback": allow_fallback, "provider": provider, "model": model}

        for row in provider_rows:
            if row["event_type"] == "MODEL_RESPONSE_RECEIVED":
                if isinstance(row["payload"].get("model"), str) and row["payload"]["model"].strip():
                    break
                continue
            payload = row["payload"]
            hint = payload.get("retry_after_seconds")
            if (provider in {"zai", "cloudflare"} and payload.get("code") == "RATE_LIMITED"
                    and type(payload.get("http_status")) is int and payload["http_status"] == 429):
                from .rate_failures import classify_rate_failure
                business = payload.get("business_code")
                facts = classify_rate_failure(provider, 429, {"error": {"code": business}})
                if facts["daily_quota_confirmed"]:
                    until = row["created_at"].replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
                    if until > instant:
                        return answer(False, "confirmed_daily_quota", "provider", until)
                    continue
                if facts["failure_kind"] in {"billing", "usage_quota"}:
                    if row["created_at"] >= day_start:
                        return answer(False, "provider_billing_or_usage_limit_reset_unconfirmed", "provider", next_day)
                    continue
                if facts["failure_kind"] in {"unknown", "request_rate", "capacity"}:
                    # Unknown429 is not evidence of a daily quota. This is a
                    # server wait interval, never a vendor-reset prediction or
                    # an automatic HTTP retry. It also handles legacy rows.
                    has_hint = type(hint) is int and 1 <= hint <= 86400
                    wait = hint if has_hint else 300
                    until = row["created_at"] + timedelta(seconds=wait)
                    if until > instant:
                        return answer(False, "provider_retry_after" if has_hint else "unconfirmed_429_engineering_wait", "provider", until)
                    continue
            if (provider == "groq" and payload.get("code") == "RATE_LIMITED"
                    and type(payload.get("http_status")) is int and payload["http_status"] == 429
                    and payload.get("limit_source") in {None, "unknown"}):
                facts = payload.get("rate_limits")
                facts = facts if isinstance(facts, dict) else {}
                limit, requested = facts.get("token_limit_from_error"), facts.get("tokens_requested_from_error")
                consumed = facts.get("tokens_used_from_error", 0)
                confirmed_day_limit = (facts.get("token_window") == "TPD"
                    and type(limit) is int and 0 <= limit <= 10**12
                    and type(requested) is int and 0 < requested <= 10**12
                    and type(consumed) is int and 0 <= consumed <= 10**12
                    and consumed + requested > limit)
                if confirmed_day_limit:
                    if row["created_at"] >= day_start:
                        return answer(False, "provider_usage_limit_reset_unconfirmed", "provider", next_day)
                    continue
                # Unknown/token-minute 429 is not evidence of an exhausted
                # daily quota. This is a durable engineering wait only. A new
                # explicit request still faces the provider's own limits.
                has_hint = type(hint) is int and 1 <= hint <= 86400
                until = row["created_at"] + timedelta(seconds=hint if has_hint else 300)
                if until > instant:
                    return answer(False, "provider_retry_after" if has_hint else "unconfirmed_429_engineering_wait", "provider", until)
                continue
            if (provider in INDEPENDENT_REMOTE_PROVIDERS and payload.get("code") == "RATE_LIMITED" and type(payload.get("http_status")) is int and payload.get("http_status") == 429
                    and payload.get("limit_source") in {None, "unknown"}
                    and type(hint) is int and 1 <= hint <= 86400):
                until = row["created_at"] + timedelta(seconds=hint)
                if until > instant:
                    return answer(False, "provider_retry_after", "provider", until)
                continue
            if row["created_at"] < day_start:
                continue
            scope, reason = classify(row["payload"])
            if scope == "provider":
                return answer(False, reason, "provider", next_day)
        for row in model_rows:
            if row["event_type"] == "MODEL_RESPONSE_RECEIVED":
                return answer(True, "confirmed_model_response", "model")
            scope, reason = classify(row["payload"])
            until = row["created_at"] + timedelta(minutes=10)
            if scope == "model" and until > instant:
                return answer(False, reason, "model", until, allow_fallback=provider not in SINGLE_ATTEMPT_REMOTE_PROVIDERS)
        return answer(True, "no_active_cooldown", "model")
