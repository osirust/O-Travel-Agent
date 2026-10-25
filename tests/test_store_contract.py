"""Repository contract; real-Postgres cases require explicit TEST_DATABASE_URL.

No database is contacted during collection or by the SQL compilation checks.
Every integration case creates its own sessions; no global cleanup or DROP.
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.dialects import postgresql
from sqlalchemy import func, select
from sqlalchemy.schema import CreateTable

from otravel.store import (
    REAL_MODEL_PROVIDERS, LOCAL_MODEL_PROVIDERS, Conflict, NotFound, StaleQuote, Store, StoreError,
    events, metadata,
)


def test_postgresql_schema_can_compile_without_a_database():
    ddl = "\n".join(str(CreateTable(table).compile(dialect=postgresql.dialect()))
                    for table in metadata.sorted_tables)
    assert "JSONB" in ddl
    assert "UNIQUE (quote_id)" in ddl
    assert "UNIQUE (session_id, client_message_id)" in ddl
    assert "UNIQUE (session_id, idempotency_key)" in ddl


def test_constructor_rejects_a_non_postgresql_backend():
    with pytest.raises(StoreError) as exc:
        Store("sqlite+aiosqlite:///:memory:")
    assert exc.value.code == "invalid_database_url"


@pytest_asyncio.fixture
async def store():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured; no database contacted")
    repository = Store(url)
    initialization_error = None
    try:
        await repository.initialize()
        await repository.initialize()  # bootstrap must be idempotent
    except Exception as exc:
        # Pytest's long asyncpg traceback renders ConnectionParameters/password.
        # Do not print driver frames, exception arguments or the connection URL.
        initialization_error = type(exc).__name__
    if initialization_error is not None:
        await repository.close()
        pytest.fail(f"PostgreSQL initialization failed ({initialization_error}); connection details redacted", pytrace=False)
    try:
        yield repository
    finally:
        await repository.close()


async def new_session(store):
    return await store.create_session({"destination": "Казань", "budget": 180000})


@pytest.mark.asyncio
async def test_session_survives_repository_recreation(store):
    session = await store.create_session({"destination": "Казань"}, owner_token_hash="a" * 64)
    another = Store(os.environ["TEST_DATABASE_URL"])
    try:
        restored = await another.get_session(session["session_id"])
        assert restored["state"] == session["state"]
        assert restored["state_version"] == restored["state"]["state_version"] == 0
        assert restored["trip_id"] == restored["state"]["trip_id"]
        assert restored["owner_token_hash"] == "a" * 64
        assert restored["status"] == "NEW"
    finally:
        await another.close()


@pytest.mark.asyncio
async def test_intake_idempotency_checks_the_payload(store):
    session = await new_session(store)
    args = (session["session_id"], "client-1", "Хочу в Казань", 0)
    first, duplicate = await store.intake_message(*args)
    repeated, duplicate_again = await store.intake_message(*args)
    assert not duplicate and duplicate_again
    assert first["id"] == repeated["id"]
    with pytest.raises(Conflict) as exc:
        await store.intake_message(session["session_id"], "client-1", "Другой текст", 0)
    assert exc.value.code == "idempotency_mismatch"
    with pytest.raises(Conflict):
        await store.intake_message(session["session_id"], "client-2", "Текст", 9)
    assert len(await store.list_messages(session["session_id"])) == 1


@pytest.mark.asyncio
async def test_only_one_claim_wins_and_only_one_run_is_active(store):
    session = await new_session(store)
    first, _ = await store.intake_message(session["session_id"], "first", "Первый", 0)
    second, _ = await store.intake_message(session["session_id"], "second", "Второй", 0)
    winners = await asyncio.gather(*(store.claim_run(first["id"]) for _ in range(3)))
    assert winners.count(True) == 1
    assert not await store.claim_run(second["id"])
    response = await store.finish_run(first["id"], 0, {"budget": 140000}, "OPTIONS_READY", "Готово")
    assert response["state_version"] == response["state"]["state_version"] == 1
    assert response["run"]["status"] == "completed"
    assert await store.claim_run(second["id"])
    await store.fail_run(second["id"], {"code": "test_complete"})


@pytest.mark.asyncio
async def test_pause_invalidates_inflight_result_without_losing_state(store):
    session = await new_session(store)
    run, _ = await store.intake_message(session["session_id"], "flight", "Начни поиск", 0)
    assert await store.claim_run(run["id"])
    paused = await store.set_control(session["session_id"], "pause", "Жду дополнения")
    assert paused["state_version"] == 1
    assert paused["state"]["budget"] == 180000
    with pytest.raises(Conflict):
        await store.finish_run(run["id"], 0, {"budget": 1}, "OPTIONS_READY", "Устаревшее")
    current = await store.get_session(session["session_id"])
    assert current["status"] == "WAITING_USER" and current["waiting_reason"] == "user_pause"
    assert current["state"]["budget"] == 180000
    assert current["active_run_id"] is None
    assert (await store.get_run(run["id"]))["status"] == "conflict"
    assert "Устаревшее" not in [m["text"] for m in await store.list_messages(session["session_id"])]
    resumed = await store.set_control(session["session_id"], "resume")
    assert resumed["state_version"] == 2 and resumed["waiting_reason"] is None


@pytest.mark.asyncio
async def test_paused_input_is_durable_but_cannot_claim_tools(store):
    session = await new_session(store)
    await store.set_control(session["session_id"], "pause")
    run, _ = await store.intake_message(session["session_id"], "extra", "Добавь ребёнка", 1)
    assert not await store.claim_run(run["id"])
    assert (await store.list_messages(session["session_id"]))[0]["text"] == "Добавь ребёнка"
    assert run["id"] in [item["id"] for item in await store.list_pending_runs()]


@pytest.mark.asyncio
async def test_quote_confirmation_is_atomic_idempotent_and_bound(store):
    session = await new_session(store)
    quote = await store.create_quote(session["session_id"], 0, {"total_rub": 10000})
    args = (session["session_id"], quote["id"], quote["quote_version"], "confirm-1", True, 0)
    first, repeated = await asyncio.gather(store.confirm_quote(*args), store.confirm_quote(*args))
    assert first["id"] == repeated["id"]
    assert first["is_demo"] and not first["booking_created_at_supplier"]
    assert len(await store.list_orders(session["session_id"])) == 1
    assert (await store.get_session(session["session_id"]))["state_version"] == 1
    with pytest.raises(Conflict) as exc:
        await store.confirm_quote(session["session_id"], quote["id"], 1, "confirm-2", True, 0)
    assert exc.value.code == "quote_already_confirmed"
    with pytest.raises(Conflict) as exc:
        await store.confirm_quote(session["session_id"], quote["id"], 2, "confirm-1", True, 0)
    assert exc.value.code == "idempotency_mismatch"
    with pytest.raises(Conflict):
        await store.confirm_quote(session["session_id"], quote["id"], 1, "confirm-3", 1, 1)


@pytest.mark.asyncio
async def test_quote_cannot_cross_sessions_or_survive_domain_changes(store):
    first = await new_session(store)
    second = await new_session(store)
    quote = await store.create_quote(first["session_id"], 0, {"total_rub": 9000})
    with pytest.raises(NotFound):
        await store.confirm_quote(second["session_id"], quote["id"], 1, "x", True, 0)
    run, _ = await store.intake_message(first["session_id"], "modify", "Добавь гостя", 0)
    assert await store.claim_run(run["id"])
    await store.finish_run(run["id"], 0, {"adults": 3}, "TRIP_DRAFT", "Изменено")
    with pytest.raises(StaleQuote):
        await store.confirm_quote(first["session_id"], quote["id"], 1, "y", True, 1)
    assert await store.list_orders(first["session_id"]) == []


@pytest.mark.asyncio
async def test_expiry_is_checked_without_sleep(store):
    session = await new_session(store)
    quote = await store.create_quote(session["session_id"], 0, {"total_rub": 100}, expires_seconds=1)
    later = datetime.fromisoformat(quote["expires_at"]) + timedelta(seconds=1)
    with patch("otravel.store._now", return_value=later), pytest.raises(StaleQuote):
        await store.confirm_quote(session["session_id"], quote["id"], 1, "expired", True, 0)


@pytest.mark.asyncio
async def test_error_cleanup_preserves_user_pause(store):
    session = await new_session(store)
    run, _ = await store.intake_message(session["session_id"], "error", "Поиск", 0)
    assert await store.claim_run(run["id"])
    await store.set_control(session["session_id"], "pause")
    result = await store.fail_run(run["id"], {"code": "model_timeout"}, preserve_session=False)
    assert result["waiting_reason"] == "user_pause" and result["status"] == "WAITING_USER"
    assert (await store.get_session(session["session_id"]))["active_run_id"] is None


@pytest.mark.asyncio
async def test_recovery_does_not_replay_and_events_redact_secrets(store):
    session = await new_session(store)
    run, _ = await store.intake_message(session["session_id"], "recover", "Поиск", 0)
    assert await store.claim_run(run["id"])
    event = await store.save_event(session["session_id"], run["id"], "TOOL_STARTED", {
        "nested": {"api_key": "private", "Authorization": "Bearer private"},
        "error": "https://example.invalid/?api_key=private",
    })
    assert "private" not in str(event["payload"])
    recovered = await store.recover_stale_runs(timeout_seconds=0)
    assert run["id"] in [item["id"] for item in recovered]
    current = await store.get_session(session["session_id"])
    assert current["active_run_id"] is None and current["state_version"] == 0
    assert current["state"]["budget"] == 180000
    assert (await store.get_run(run["id"]))["status"] == "interrupted"
    assert await store.list_orders(session["session_id"]) == []
    assert run["id"] not in [item["id"] for item in await store.list_pending_runs()]


@pytest.mark.asyncio
async def test_model_call_budget_is_atomic_global_and_survives_new_store(store):
    session = await new_session(store)
    run, _ = await store.intake_message(session["session_id"], "budget", "Запрос", 0)
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    async with store.engine.connect() as conn:
        before = (await conn.execute(select(func.count()).select_from(events).where(
            events.c.event_type == "MODEL_CALL_STARTED", events.c.created_at >= start,
            events.c.created_at < start + timedelta(days=1),
            events.c.payload["provider"].astext.not_in(sorted(REAL_MODEL_PROVIDERS | LOCAL_MODEL_PROVIDERS)),
        ))).scalar_one()
        live_before = (await conn.execute(select(func.count()).select_from(events).where(
            events.c.event_type == "MODEL_CALL_STARTED", events.c.created_at >= start,
            events.c.created_at < start + timedelta(days=1),
            events.c.payload["provider"].astext.in_(sorted(REAL_MODEL_PROVIDERS)),
        ))).scalar_one()
    attempts = await asyncio.gather(*(
        store.reserve_model_call(session["session_id"], run["id"], before + 2, "test_provider")
        for _ in range(3)
    ))
    assert attempts.count(True) == 2 and attempts.count(False) == 1
    another = Store(os.environ["TEST_DATABASE_URL"])
    try:
        assert not await another.reserve_model_call(session["session_id"], run["id"], before + 2, "test_other_provider")
    finally:
        await another.close()
    async with store.engine.connect() as conn:
        live_after = (await conn.execute(select(func.count()).select_from(events).where(
            events.c.event_type == "MODEL_CALL_STARTED", events.c.created_at >= start,
            events.c.created_at < start + timedelta(days=1),
            events.c.payload["provider"].astext.in_(sorted(REAL_MODEL_PROVIDERS)),
        ))).scalar_one()
    assert live_after == live_before
    with pytest.raises(StoreError) as exc:
        await store.reserve_model_call(session["session_id"], run["id"], 40, "openrouter_typo")
    assert exc.value.code == "invalid_provider"


async def _finish_edit(store, session, amount):
    run, _ = await store.intake_message(session["session_id"], str(uuid4()), "Изменить бюджет", session["state_version"])
    assert await store.claim_run(run["id"])
    state = {**session["state"], "budget": amount}
    return await store.finish_run(run["id"], session["state_version"], state, "TRIP_DRAFT", "Изменено",
                                  changes=[{"operation": "set_budget", "field": "budget"}])


async def _finish_undo(store, session):
    prepared = await store.prepare_undo(session["session_id"], session["state_version"])
    run, _ = await store.intake_message(session["session_id"], str(uuid4()), "Undo", session["state_version"])
    assert await store.claim_run(run["id"])
    result = await store.finish_run(run["id"], session["state_version"], prepared["state"], "TRIP_DRAFT", "Отменена правка",
                                    changes=prepared["changes"], result={"undo": prepared["undo"]})
    return result, run


@pytest.mark.asyncio
async def test_undo_without_domain_history_changes_nothing(store):
    session = await new_session(store)
    with pytest.raises(Conflict) as caught:
        await store.prepare_undo(session["session_id"], 0)
    assert caught.value.code == "nothing_to_undo"
    assert (await store.get_session(session["session_id"]))["state"] == session["state"]


@pytest.mark.asyncio
async def test_repeated_undo_consumes_edits_without_toggling_or_reviving_quotes(store):
    session = await new_session(store)
    first = await _finish_edit(store, session, 140000)
    second = await _finish_edit(store, first, 120000)
    quote = await store.create_quote(session["session_id"], 2, {"total_rub": 120000})
    undone, run = await _finish_undo(store, second)
    assert undone["state_version"] == 3 and undone["state"]["budget"] == 140000
    replay = await store.finish_run(run["id"], 2, {}, "TRIP_DRAFT", "Повтор")
    assert replay["state_version"] == 3
    again, _ = await _finish_undo(store, undone)
    assert again["state_version"] == 4 and again["state"]["budget"] == 180000
    assert (await store.get_quote(quote["id"]))["status"] == "stale"
    with pytest.raises(StaleQuote):
        await store.confirm_quote(session["session_id"], quote["id"], 1, "old-consent", True, 4)
    with pytest.raises(Conflict) as caught:
        await store.prepare_undo(session["session_id"], 4)
    assert caught.value.code == "nothing_to_undo"
    assert sum(event["event_type"] == "STATE_UNDONE" for event in await store.list_events(session["session_id"])) == 2


@pytest.mark.asyncio
async def test_undo_skips_control_order_and_failed_runs_without_changing_orders(store):
    session = await new_session(store)
    edited = await _finish_edit(store, session, 140000)
    quote = await store.create_quote(session["session_id"], 1, {"total_rub": 140000})
    order = await store.confirm_quote(session["session_id"], quote["id"], 1, "order-before-undo", True, 1)
    await store.set_control(session["session_id"], "pause")
    current = await store.set_control(session["session_id"], "resume")
    failed, _ = await store.intake_message(session["session_id"], "failed-edit", "Ошибка", current["state_version"])
    assert await store.claim_run(failed["id"])
    await store.fail_run(failed["id"], {"code": "synthetic_failure"})
    current = await store.get_session(session["session_id"])
    undone, _ = await _finish_undo(store, current)
    assert undone["state"]["budget"] == 180000
    assert undone["result"]["undo"]["undone_revision"] == edited["state_version"]
    assert (await store.list_orders(session["session_id"]))[0] == order


@pytest.mark.asyncio
async def test_prepared_undo_is_guarded_by_finish_cas(store):
    session = await new_session(store)
    first = await _finish_edit(store, session, 140000)
    prepared = await store.prepare_undo(session["session_id"], 1)
    undo_run, _ = await store.intake_message(session["session_id"], "stale-undo", "Undo", 1)
    second = await _finish_edit(store, first, 120000)
    assert await store.claim_run(undo_run["id"])
    with pytest.raises(Conflict):
        await store.finish_run(undo_run["id"], 1, prepared["state"], "TRIP_DRAFT", "Не применять", changes=prepared["changes"])
    assert (await store.get_session(session["session_id"]))["state"]["budget"] == 120000
    with pytest.raises(Conflict) as caught:
        await store.prepare_undo(session["session_id"], 1)
    assert caught.value.code == "stale_state"


@pytest.mark.asyncio
async def test_cancel_blocks_late_commit_and_is_not_undo_or_order_cancellation(store):
    session = await new_session(store)
    quote = await store.create_quote(session["session_id"], 0, {"total_rub": 180000})
    run, _ = await store.intake_message(session["session_id"], "cancel-target", "Начать поиск", 0)
    assert await store.claim_run(run["id"])
    cancelled = await store.cancel_processing(session["session_id"], 0, target_run_id=run["id"])
    current = cancelled["session"]
    assert current["state_version"] == 1 and current["state"]["budget"] == 180000
    assert current["waiting_reason"] is None
    assert (await store.get_run(run["id"]))["status"] == "cancelled"
    with pytest.raises(Conflict):
        await store.finish_run(run["id"], 0, {"budget": 1}, "OPTIONS_READY", "Поздний ответ")
    replay = await store.fail_run(run["id"], {"code": "late_model_error"}, preserve_session=False)
    assert replay["run"]["status"] == "cancelled"
    assert (await store.get_quote(quote["id"]))["status"] == "stale"
    with pytest.raises(Conflict) as caught:
        await store.prepare_undo(session["session_id"], 1)
    assert caught.value.code == "nothing_to_undo"
    assert await store.list_orders(session["session_id"]) == []


@pytest.mark.asyncio
async def test_cancel_pending_during_pause_preserves_pause_and_ignores_future_inputs(store):
    session = await new_session(store)
    await store.set_control(session["session_id"], "pause")
    older, _ = await store.intake_message(session["session_id"], "older", "Дополнение", 1)
    control, _ = await store.intake_message(session["session_id"], "cancel-control", "Cancel", 1)
    newer, _ = await store.intake_message(session["session_id"], "newer", "Новое дополнение", 1)
    result = await store.cancel_processing(session["session_id"], 1, control_run_id=control["id"])
    assert result["cancelled_run_id"] == older["id"]
    assert result["session"]["waiting_reason"] == "user_pause"
    assert (await store.get_run(newer["id"]))["status"] == "pending"
    no_target = await store.cancel_processing(session["session_id"], 2, control_run_id=control["id"])
    assert no_target["cancelled_run_id"] is None
    assert no_target["session"]["state_version"] == 2
