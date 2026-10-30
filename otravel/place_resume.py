"""Durable server-only continuation after an explicit place resume.

The Store creates the queue row in the control transaction. This worker helper
does not execute client-supplied typed markers or refresh expired authority.
"""
from __future__ import annotations

import json
from copy import deepcopy
from uuid import UUID

from .domain import DomainError, TripState

PREFIX = "\x1eOTRAVEL_TYPED_V1:"
KIND = "resume_place"
OWNER = "place_run.deferred.v1"
STALE_TEXT = "Выбор места устарел или изменился. Укажите направление заново; остальные условия поездки сохранены."


def resume_marker(trip_id: str, resume_id: str, run_id: str) -> str:
    return PREFIX + json.dumps({"kind": KIND, "trip_id": trip_id, "resume_id": resume_id,
        "resume_run_id": run_id}, sort_keys=True, separators=(",", ":"))


def read_resume_marker(text) -> dict | None:
    try:
        if not isinstance(text, str) or not text.startswith(PREFIX) or len(text) > 1024:
            return None
        def unique_object(pairs):
            if len({key for key, _ in pairs}) != len(pairs):
                raise ValueError
            return dict(pairs)
        value = json.loads(text[len(PREFIX):], object_pairs_hook=unique_object)
        if (not isinstance(value, dict) or set(value) != {"kind", "trip_id", "resume_id", "resume_run_id"}
                or value["kind"] != KIND or not isinstance(value["trip_id"], str) or not 0 < len(value["trip_id"]) <= 128):
            return None
        for key in ("resume_id", "resume_run_id"):
            if not isinstance(value[key], str) or str(UUID(value[key])) != value[key]:
                return None
        return value
    except (ValueError, TypeError, KeyError):
        return None


def stale_place_question(version: int, identifier: str) -> dict:
    return {"question_id": identifier + "-place-stale", "state_version": version,
        "text": STALE_TEXT, "questions": [STALE_TEXT], "field": "place.city_query",
        "target_id": None, "reason": "clarification", "context": {}}


async def resume_deferred_place_run(harness, session, run, state) -> None:
    """Dispatch exactly the current server-created continuation, without NLU."""
    from .place_resolution import OWNER as RESOLUTION_OWNER
    from .place_run import _context, _now, _rebase_owned_pending, finish_place_run

    proof = await harness.store.get_place_resume_marker(run["run_id"])
    if proof is None or proof["session_id"] != session["session_id"] or run["session_id"] != session["session_id"]:
        raise DomainError("invalid_place_resume", "Продолжение выбора места не подтверждено сервером.")
    if proof["status"] == "completed":
        return
    marker = proof["marker"]
    state = TripState.model_validate(state)
    if state.trip_id != session["state"]["trip_id"]:
        raise DomainError("invalid_place_resume", "Продолжение относится к другой поездке.")
    pending = session.get("pending_question")
    context = _context(pending)
    deferred = context.get("place_deferred_action")
    current = (isinstance(deferred, dict) and deferred.get("owner") == OWNER
        and deferred.get("trip_id") == state.trip_id == marker["trip_id"]
        and deferred.get("resume_id") == marker["resume_id"]
        and deferred.get("resume_run_id") == run["run_id"] == marker["resume_run_id"])
    if not current:
        # A newer control/action superseded this queue item; never use its data.
        await harness.store.finish_run(run["run_id"], session["state_version"], None,
            session["status"], "Сохранённое продолжение уже заменено; текущие условия не изменены.",
            pending_question=pending, waiting_reason=session.get("waiting_reason"),
            result={"response_source": "canonical_place_resume", "place_resolution": {"action": "superseded"}})
        return
    try:
        rebound = _rebase_owned_pending(pending, state, state, _now(harness))
        deferred = rebound["context"]["place_deferred_action"]
    except (ValueError, TypeError, KeyError):
        await harness.store.finish_run(run["run_id"], session["state_version"], None,
            "WAITING_USER", STALE_TEXT,
            pending_question=stale_place_question(session["state_version"], run["run_id"]),
            waiting_reason="clarification", result={"response_source": "canonical_place_resume",
                "error": {"code": "PLACE_DEFERRED_STALE", "message": STALE_TEXT, "retryable": False}})
        return
    await harness._ensure_current_run(session, run)
    resumed = deepcopy(session)
    if deferred["action"] == "select":
        resumed["pending_question"] = deepcopy(deferred["pending_question"])
    else:
        # The approved request may contain an owned country fact whose former
        # question was deliberately replaced by the durable deferred wrapper.
        authority = deferred["request"].get("country_ref")
        resumed["pending_question"] = deepcopy(pending)
        if authority is not None:
            resumed["pending_question"]["context"] = {"place_resolution": {
                "owner": RESOLUTION_OWNER, "country_ref": deepcopy(authority)}}
    if type(context.get("children_known")) is bool:
        resumed["pending_question"].setdefault("context", {})["children_known"] = context["children_known"]
    await finish_place_run(harness, resumed, run, state,
        request=deferred.get("request") if deferred["action"] == "resolve" else None,
        selection=deferred.get("selection") if deferred["action"] == "select" else None,
        remaining_requests=deferred.get("remaining_requests", ()))
