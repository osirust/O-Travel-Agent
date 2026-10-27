"""One persistent place-resolution transition; no model calls or direct SQL.

The harness owns the lease/deadline/tool budget and dispatches this helper only
for its opt-in server role. Domain changes, question and selected refs commit
through Store.finish_run once against the original session version.
"""
from __future__ import annotations

import inspect
import math
import re
import unicodedata
from copy import deepcopy
from datetime import UTC, datetime

from .clarifications import derive_pending_field, human_question
from .domain import DomainError, Operation, TripState, apply_operations, validate_trip
from .place_refs import CityRefSnapshot
from .place_resolution import (
    COUNTRY_OWNER,
    NO_MATCH_OWNER,
    OWNER,
    _choice_id,
    _date,
    _read_pending,
    _sha,
    _target,
    checked_ref_revision,
)

MAX_REMAINING_REQUESTS = 5
_DEFERRED_OWNER = "place_run.deferred.v1"
_SAFE_QUESTION = "Не удалось завершить выбор места. Уточните направление или повторите позже; остальные данные сохранены."


class _InvalidAction(ValueError):
    pass


def _need(value):
    if not value:
        raise _InvalidAction("invalid_place_action")


def _now(harness):
    clock = getattr(harness.place_resolution, "_now", None)
    value = clock() if callable(clock) else datetime.now(UTC)
    _need(isinstance(value, datetime) and value.utcoffset() is not None)
    return value.astimezone(UTC)


def _context(pending):
    return deepcopy(pending.get("context") or {}) if isinstance(pending, dict) else {}


def _ordinary_context(pending):
    old = _context(pending)
    return {"children_known": old["children_known"]} if type(old.get("children_known")) is bool else {}


def _request(value, state):
    _need(isinstance(value, dict) and set(value) <= {
        "role", "kind", "query", "stay_id", "country_iso2", "country_ref", "source_text"})
    result = deepcopy(value)
    _need(result.get("kind") in {"country", "city"} and isinstance(result.get("query"), str)
        and 2 <= len(result["query"]) <= 160 and result["query"].strip() == result["query"]
        and not any(unicodedata.category(c).startswith("C") for c in result["query"]))
    _need(result.get("country_iso2") is None or isinstance(result["country_iso2"], str)
        and re.fullmatch(r"[A-Z]{2}", result["country_iso2"]))
    _need("source_text" not in result or isinstance(result["source_text"], str)
        and len(result["source_text"]) <= 4000)
    result["stay_id"] = _target(state.model_dump(mode="json"), result.get("role"), result.get("stay_id"))
    return result


def _scope_unchanged(before, after, role, stay_id):
    a, b = before.model_dump(mode="json"), after.model_dump(mode="json")
    _need(_target(a, role, stay_id) == stay_id and _target(b, role, stay_id) == stay_id)
    if role == "origin":
        return (before.origin, before.origin_place_id) == (after.origin, after.origin_place_id)
    if role == "route_end":
        return before.route_policy == after.route_policy
    old = next((s for s in before.stays if s.id == stay_id), None)
    current = next((s for s in after.stays if s.id == stay_id), None)
    if old is None:
        return current is None or current.city is None and current.place_id is None
    return current is not None and (old.city, old.place_id) == (current.city, current.place_id)


def _owned_country(pending):
    packet = _context(pending).get("place_resolution")
    return packet.get("country_ref") if isinstance(packet, dict) and packet.get("owner") == OWNER else None


def _country_rebase(ref, before, after, request, coordinator, authority):
    _need(isinstance(ref, dict) and ref == authority and ref.get("owner") == COUNTRY_OWNER
        and ref.get("trip_id") == before.trip_id and ref.get("state_version") == before.state_version
        and ref.get("role") == request["role"] and ref.get("stay_id") == request["stay_id"]
        and _scope_unchanged(before, after, request["role"], request["stay_id"]))
    coordinator._checked_country_ref(ref, before.model_dump(mode="json"), request)
    result = deepcopy(ref)
    result["state_version"] = after.state_version
    return result


def _prepare_request(value, before, current, pending, coordinator):
    result = _request(value, current)
    authority = _owned_country(pending)
    supplied = result.get("country_ref")
    if (supplied is None and result["kind"] == "city" and isinstance(authority, dict)
            and authority.get("role") == result["role"] and authority.get("stay_id") == result["stay_id"]
            and result.get("country_iso2") in {None, authority.get("country_iso2")}):
        supplied = authority
    if supplied is not None:
        result["country_ref"] = _country_rebase(supplied, before, current, result, coordinator, authority)
    return result


def _prepare_selection(selection, pending, before, current, harness):
    old = _read_pending(pending, before.model_dump(mode="json"), _now(harness))
    _need(_scope_unchanged(before, current, old["role"], old["stay_id"]))
    packet = deepcopy(old)
    packet["state_version"] = current.state_version
    if packet.get("country_ref") is not None:
        request = {"role": old["role"], "stay_id": old["stay_id"]}
        packet["country_ref"] = _country_rebase(old["country_ref"], before, current,
            request, harness.place_resolution, old["country_ref"])
    mapping = {}
    for candidate in packet["candidates"]:
        previous = candidate["candidate_id"]
        candidate["candidate_id"] = _choice_id(packet, candidate)
        mapping[previous] = candidate["candidate_id"]
    translated = deepcopy(selection)
    if isinstance(translated, dict):
        _need((set(translated) == {"resolution_id", "candidate_id"}
                or old.get("stage") == "ref_revision_confirm"
                and set(translated) == {"resolution_id", "candidate_id", "decision"} and translated["decision"] == "reject")
            and translated["resolution_id"] == old["resolution_id"] and translated["candidate_id"] in mapping)
        translated["candidate_id"] = mapping[translated["candidate_id"]]
    elif isinstance(translated, str):
        from .place_resolution import interpret_short_reply
        answer = interpret_short_reply(pending, translated, before.model_dump(mode="json"), now=_now(harness))
        _need(answer["selection"] is not None)
        translated = {"resolution_id": old["resolution_id"],
            "candidate_id": mapping[answer["selection"]["candidate_id"]]}
    else:
        _need(False)
    context = _context(pending)
    context["place_resolution"] = packet
    rebound = deepcopy(pending)
    rebound.update(state_version=current.state_version, context=context)
    return translated, rebound


def _owned(pending):
    ctx = _context(pending)
    place, deferred = ctx.get("place_resolution"), ctx.get("place_deferred_action")
    return (isinstance(place, dict) and place.get("owner") == OWNER
        or isinstance(deferred, dict) and deferred.get("owner") == _DEFERRED_OWNER)


def _control_country(ref, before, current, role, target, now):
    _need(isinstance(ref, dict) and ref.get("owner") == COUNTRY_OWNER
        and ref.get("trip_id") == before.trip_id and type(ref.get("state_version")) is int
        and ref.get("state_version") == before.state_version
        and ref.get("role") == role and ref.get("stay_id") == target
        and _scope_unchanged(before, current, role, target))
    code, name = ref.get("country_iso2"), ref.get("name")
    _need(isinstance(code, str) and re.fullmatch(r"[A-Z]{2}", code)
        and isinstance(name, str) and 0 < len(name) <= 160
        and ref.get("fact_sha256") == _sha({"country_iso2": code, "name": name})
        and isinstance(ref.get("source_sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", ref["source_sha256"])
        and _date(ref["retrieved_at"]) <= now < _date(ref["expires_at"]))
    result = deepcopy(ref)
    result["state_version"] = current.state_version
    return result


def _control_request(value, before, current, now):
    result = _request(value, current)
    _need(_scope_unchanged(before, current, result["role"], result["stay_id"]))
    if result.get("country_ref") is not None:
        result["country_ref"] = _control_country(result["country_ref"], before, current,
            result["role"], result["stay_id"], now)
    return result


def _witness_projection(scope, role, target):
    _need(isinstance(scope, dict))
    if role == "origin":
        return scope.get("origin"), scope.get("origin_place_id")
    if role == "route_end":
        return scope.get("route_policy")
    items = scope.get("stays")
    _need(isinstance(items, list))
    item = next((row for row in items if isinstance(row, dict) and row.get("id") == target), None)
    return (item.get("city"), item.get("place_id")) if item else (None, None)


def _scope_state(state):
    return {"origin": state.origin, "origin_place_id": state.origin_place_id,
        "route_policy": state.route_policy.model_dump(mode="json"),
        "stays": [{"id": stay.id, "city": stay.city, "place_id": stay.place_id} for stay in state.stays]}


def _rebase_owned_pending(pending, before, current, now, depth=0):
    _need(depth < 3 and _owned(pending) and before.trip_id == current.trip_id)
    result = deepcopy(pending)
    context = _context(result)
    packet = context.get("place_resolution")
    deferred = context.get("place_deferred_action")
    _need(not (isinstance(packet, dict) and isinstance(deferred, dict)))
    if isinstance(packet, dict):
        packet = _read_pending(pending, before.model_dump(mode="json"), now)
        _need(_scope_unchanged(before, current, packet["role"], packet["stay_id"]))
        packet["state_version"] = current.state_version
        if packet.get("country_ref") is not None:
            packet["country_ref"] = _control_country(packet["country_ref"], before, current,
                packet["role"], packet["stay_id"], now)
        for candidate in packet["candidates"]:
            candidate["candidate_id"] = _choice_id(packet, candidate)
        context["place_resolution"] = packet
    if isinstance(deferred, dict):
        _need(deferred.get("owner") == _DEFERRED_OWNER and deferred.get("trip_id") == before.trip_id
            and type(deferred.get("state_version")) is int and deferred.get("state_version") == before.state_version
            and deferred.get("action") in {"resolve", "select"})
        if deferred["action"] == "resolve":
            request = _control_request(deferred["request"], before, current, now)
            role, target = request["role"], request["stay_id"]
            deferred["request"] = request
        else:
            prior = deferred.get("pending_question")
            old_packet = _read_pending(prior, before.model_dump(mode="json"), now)
            role, target = old_packet["role"], old_packet["stay_id"]
            updated = _rebase_owned_pending(prior, before, current, now, depth + 1)
            new_packet = updated["context"]["place_resolution"]
            mapping = {old["candidate_id"]: new["candidate_id"] for old, new in zip(old_packet["candidates"], new_packet["candidates"])}
            selected = deferred.get("selection")
            _need(isinstance(selected, dict) and set(selected) == {"resolution_id", "candidate_id"}
                and selected["resolution_id"] == old_packet["resolution_id"] and selected["candidate_id"] in mapping)
            selected["candidate_id"] = mapping[selected["candidate_id"]]
            deferred["pending_question"] = updated
        _need(_witness_projection(deferred.get("scope_state"), role, target)
            == _witness_projection(_scope_state(before), role, target))
        deferred["state_version"] = current.state_version
        deferred["scope_state"] = _scope_state(current)
        jobs = deferred.get("remaining_requests", [])
        _need(isinstance(jobs, list) and len(jobs) <= MAX_REMAINING_REQUESTS)
        deferred["remaining_requests"] = [_control_request(job, before, current, now) for job in jobs]
        context["place_deferred_action"] = deferred
    jobs = context.get("place_remaining_requests", [])
    _need(isinstance(jobs, list) and len(jobs) <= MAX_REMAINING_REQUESTS)
    if "place_remaining_requests" in context:
        context["place_remaining_requests"] = [_control_request(job, before, current, now) for job in jobs]
    result.update(state_version=current.state_version, context=context)
    return result


def rebase_place_control(pending, before_state, new_version, *, now=None):
    """Pure owned control-CAS rebinding; invalid/expired authority returns None.

    Only the epoch and derived candidate IDs change. Source timestamps and TTL
    never refresh. The Store caller must clear/reask when None is returned.
    """
    try:
        before = TripState.model_validate(before_state)
        _need(type(new_version) is int and new_version == before.state_version + 1)
        current = before.model_copy(deep=True, update={"state_version": new_version})
        instant = _date(now) if now is not None else datetime.now(UTC)
        return _rebase_owned_pending(pending, before, current, instant)
    except (ValueError, TypeError, KeyError):
        return None


def preserve_place_choice_for_independent_facts(pending, before_state, operations, *, now):
    """Pure dispatch predicate for a fresh owned unresolved place question.

    Ordinary model unknowns do not consume a displayed identity choice. Only
    edits leaving that choice's geographic scope intact qualify; the existing
    maintenance transition performs the actual CAS/rebase without tool calls.
    This does not grant authority to expired, forged or changed-scope packets.
    """
    try:
        before = TripState.model_validate(before_state)
        _need(isinstance(pending, dict) and str(pending.get("field", "")).startswith("place.")
            and isinstance(pending.get("question_id"), str) and bool(pending["question_id"])
            and type(pending.get("state_version")) is int and pending["state_version"] == before.state_version)
        instant = _date(now)
        packet = _read_pending(pending, before.model_dump(mode="json"), instant)
        _need(packet.get("stage") in {"city_query", "country_choice", "city_choice", "ref_revision_confirm"}
            and pending.get("target_id") == packet.get("stay_id"))
        edits = [Operation.model_validate(operation) for operation in operations]
        current, changes = apply_operations(before, edits)
        if changes:
            current.state_version = before.state_version + 1
        _rebase_owned_pending(pending, before, current, instant)
        return True
    except (ValueError, TypeError, KeyError):
        return False


def _pending(question, state, run, *, context=None, field=None, target=None):
    _need(isinstance(question, str) and 0 < len(question.strip()) <= 8192)
    ctx = deepcopy(context or {})
    if field is None:
        field, target = ctx.get("field", "place.resolution"), ctx.get("target_id")
    return {"question_id": run["run_id"] + "-place-question", "text": question,
        "questions": [question], "state_version": state.state_version, "reason": "clarification",
        "field": field, "target_id": target, "context": ctx}


def _next_missing(state, run, previous):
    issues = validate_trip(state)
    if not issues:
        return None
    issue = issues[0]
    context = _ordinary_context(previous)
    bound = derive_pending_field([issue], state)
    context.update(bound.get("context") or {})
    text = human_question(issue, state, {**context, **bound})
    return _pending(text, state, run, context=context, field=bound.get("field"), target=bound.get("target_id"))


def _selected_operations(packet, role, target):
    values = packet.get("operations")
    _need(isinstance(values, list) and len(values) == 1 and packet.get("resolved_places"))
    operation = Operation.model_validate(values[0])
    if operation.type == "set_origin":
        _need(role == "origin" and operation.target_id is None and set(operation.value) == {"city", "place_id"})
    elif operation.type == "set_route_policy":
        _need(role == "route_end" and operation.target_id is None and set(operation.value) == {"kind", "end_city", "end_place_id"}
            and operation.value["kind"] == "open_jaw")
    else:
        _need(role == "destination" and operation.type == "upsert_stay" and operation.target_id == target
            and set(operation.value) in ({"city", "place_id"}, {"city", "place_id", "hotel_id"})
            and operation.value.get("hotel_id") is None)
    selected_id = operation.value.get("end_place_id") if role == "route_end" else operation.value.get("place_id")
    _need(isinstance(packet["resolved_places"], dict) and set(packet["resolved_places"]) == {selected_id})
    return [operation]


async def finish_place_run(harness, session, run, state, *, request=None, selection=None,
                           operations=(), model_meta=None, control="none", remaining_requests=(),
                           informational=False, precomputed_packet=None, independent_notice=None) -> None:
    """Finish one owned resolution run and at most one persistent revision."""
    if informational is not False:
        raise DomainError("place_information_mode_unavailable", "Справочный режим выбора места пока не включён.")
    before = TripState.model_validate(state)
    version = session["state_version"]
    maintenance = request is None and selection is None and precomputed_packet is None
    _need(independent_notice is None or maintenance and model_meta is None
        and isinstance(independent_notice, str) and 0 < len(independent_notice.strip()) <= 8192)
    _need(before.state_version == version and before.trip_id == session["state"]["trip_id"]
        and run["session_id"] == session["session_id"] and control in {"none", "continue", "wait", "resume"}
        and not (request is not None and selection is not None)
        and (not maintenance or _owned(session.get("pending_question"))))
    edits = [Operation.model_validate(op) for op in operations]
    _need(precomputed_packet is None or isinstance(precomputed_packet, dict))
    _need(precomputed_packet is None or selection is None and control != "wait"
        and (not edits or precomputed_packet.get("action") == "no_match"
            and precomputed_packet.get("clarification_owner") == NO_MATCH_OWNER))
    if precomputed_packet is not None and edits:
        # The marker alone grants nothing. Check server-owned trip/epoch/TTL
        # before applying even independent edits from a precomputed packet.
        proof = _read_pending({"context": precomputed_packet.get("pending_context")},
            before.model_dump(mode="json"), _now(harness))
        _need(proof.get("stage") == "city_query" and not proof["candidates"])
        if proof.get("country_ref") is not None:
            harness.place_resolution._checked_country_ref(proof["country_ref"],
                before.model_dump(mode="json"), {"role": proof["role"], "stay_id": proof["stay_id"]})
    from .harness import operation_conflicts
    conflicts = operation_conflicts(before, edits)
    if conflicts:
        raise DomainError("conflicting_operations", conflicts[0], conflicts)
    current, changes = apply_operations(before, edits)
    paused = control == "wait" or session.get("waiting_reason") == "user_pause"
    if control == "wait" and session.get("waiting_reason") != "user_pause" and not changes:
        changes.append({"operation": "control_pause", "field": "control", "before": None, "after": "user_pause"})
    if changes:
        current.state_version = version + 1
    queue = list(deepcopy(remaining_requests))
    old_pending = deepcopy(session.get("pending_question"))
    if not queue and (selection is not None or precomputed_packet is not None):
        queue = deepcopy(_context(old_pending).get("place_remaining_requests", []))
    result = {"response_source": "canonical_place_resolution", "plans": [], "place_resolution": {"action": "pending"}}
    if model_meta is not None:
        result["llm"] = deepcopy(model_meta)
    pending, status, waiting, reply = None, "TRIP_DRAFT", None, "Выбор места сохранён."

    async def fence():
        await harness._ensure_current_run(session, run)

    async def before_call(event):
        await fence()
        remaining = harness._before_place_call(session, run, deepcopy(event))
        remaining = await remaining if inspect.isawaitable(remaining) else remaining
        _need(type(remaining) in {int, float} and math.isfinite(remaining) and remaining > 0)
        await fence()
        return remaining

    if maintenance:
        try:
            rebound = _rebase_owned_pending(old_pending, before, current, _now(harness))
        except (ValueError, TypeError, KeyError):
            rebound = _pending(_SAFE_QUESTION, current, run, context={}, field="place.city_query")
            result["error"] = {"code": "PLACE_PENDING_STALE", "message": _SAFE_QUESTION, "retryable": False}
        rebound["question_id"] = run["run_id"] + "-place-question"
        if paused:
            reply = "Условия сохранены. Жду явного «продолжай»; выбор места и поиск не выполнялись."
            rebound["reason"] = "user_pause"
        else:
            await fence()
            reply = rebound["text"]
        if independent_notice is not None:
            reply = independent_notice.strip() + " " + reply
            result["independent_notice"] = {"source": "canonical_slot", "message": independent_notice.strip()}
        result["place_resolution"] = {"action": "maintained_pending"}
        await harness.store.finish_run(run["run_id"], version,
            current.model_dump(mode="json") if changes else None, "WAITING_USER", reply,
            pending_question=rebound, waiting_reason="user_pause" if paused else "clarification",
            changes=changes, result=result)
        return

    try:
        _need(len(queue) <= MAX_REMAINING_REQUESTS)
        queue = [_prepare_request(value, before, current, old_pending, harness.place_resolution) for value in queue]
        if precomputed_packet is not None:
            packet = deepcopy(precomputed_packet)
            _need(isinstance(packet, dict) and packet.get("action") in {"ask", "no_match", "unavailable"}
                and not packet.get("operations") and not packet.get("resolved_places"))
            context = packet.get("pending_context") or {}
            if context.get("place_resolution") is not None:
                owned_packet = _read_pending({"context": context}, before.model_dump(mode="json"), _now(harness))
                _need(context.get("target_id") == owned_packet["stay_id"])
                if owned_packet.get("country_ref") is not None:
                    _control_country(owned_packet["country_ref"], before, current,
                        owned_packet["role"], owned_packet["stay_id"], _now(harness))
                if request is not None:
                    checked = _request(request, current)
                    _need((checked["role"], checked["stay_id"]) == (owned_packet["role"], owned_packet["stay_id"]))
                # Independent facts may change the virtual revision, never the
                # role identity or source TTL of this already-owned question.
                rebound = _rebase_owned_pending({"context": context}, before, current, _now(harness))
                packet["pending_context"] = rebound["context"]
            elif packet["action"] == "ask":
                _need(False)
            prepared, chosen, selection_pending = None, None, None
        elif request is not None:
            prepared = _prepare_request(request, before, current, old_pending, harness.place_resolution)
            chosen, selection_pending = None, None
        else:
            chosen, selection_pending = _prepare_selection(selection, old_pending, before, current, harness)
            prepared = None
    except (ValueError, TypeError, KeyError):
        prepared, chosen, selection_pending = None, None, None
        packet = {"action": "unavailable", "question": _SAFE_QUESTION, "pending_context": None}
    else:
        if precomputed_packet is None:
            packet = None

    if paused:
        deferred = None
        if packet is None:
            deferred = {"owner": _DEFERRED_OWNER, "trip_id": current.trip_id,
                "state_version": current.state_version, "action": "resolve" if prepared is not None else "select",
                "request": prepared, "selection": chosen, "pending_question": selection_pending,
                "remaining_requests": queue,
                "scope_state": _scope_state(current)}
        context = _ordinary_context(old_pending)
        if deferred is not None:
            context["place_deferred_action"] = deferred
            context["place_remaining_requests"] = queue
        reply = "Условия сохранены. Жду явного «продолжай»; выбор места и поиск не выполнялись."
        pending = _pending(reply, current, run, context=context, field="place.deferred")
        pending["reason"] = "user_pause"
        status, waiting = "WAITING_USER", "user_pause"
        result.update(place_resolution={"action": "deferred" if deferred is not None else "invalid_deferred"},
            deferred_questions=[_SAFE_QUESTION] if deferred is None else [])
    else:
        # A coordinator may convert source/callback errors into a safe packet.
        # Recheck the original DB lease afterwards so a stale result cannot be
        # persisted even when the source layer swallowed the fence exception.
        await fence()
        for _ in range(MAX_REMAINING_REQUESTS + 2):
            if packet is None:
                try:
                    if prepared is not None:
                        packet = await harness.place_resolution.resolve(prepared, current, before_call=before_call)
                    else:
                        packet = await harness.place_resolution.select(chosen, selection_pending, current, before_call=before_call)
                except Exception:  # noqa: BLE001 - source failures can contain credentials; never echo them.
                    packet = {"action": "unavailable", "question": _SAFE_QUESTION, "pending_context": None}
                await fence()
            action = packet.get("action") if isinstance(packet, dict) else None
            if action == "selected":
                try:
                    scope = prepared if prepared is not None else _context(selection_pending)["place_resolution"]
                    selected = _selected_operations(packet, scope["role"], scope.get("stay_id"))
                    verified = await harness.geo_catalog.verify_places(packet["resolved_places"],
                        now=_now(harness), require_fresh=True, require_current=True)
                    _need({key: ref.model_dump(mode="json") for key, ref in verified.items()}
                        == {key: CityRefSnapshot.model_validate(ref).model_dump(mode="json")
                            for key, ref in packet["resolved_places"].items()})
                    if selection_pending is not None:
                        shown = [choice for choice in scope["candidates"] if choice["candidate_id"] == chosen["candidate_id"]]
                        _need(len(shown) == 1 and shown[0].get("kind") in {"city", "ref_revision"}
                            and all(ref.geographic_source.external_id == shown[0].get("source_id")
                                and ref.country_iso2 == shown[0].get("country_iso2") for ref in verified.values()))
                        if shown[0]["kind"] == "ref_revision":
                            _packet_scope, old_ref, new_ref = checked_ref_revision(selection_pending, chosen, current, now=_now(harness))
                            _need(chosen.get("decision") is None
                                and packet.get("replace_place_versions") == {old_ref.place_id: old_ref.ref_version}
                                and verified == {new_ref.place_id: new_ref})
                        else:
                            _need(packet.get("replace_place_versions") is None)
                    else:
                        _need(packet.get("replace_place_versions") is None)
                    candidate, delta = apply_operations(current, selected, resolved_places=verified,
                        replace_place_versions=packet.get("replace_place_versions"))
                except Exception:  # noqa: BLE001 - only source selection is discarded; independent facts remain.
                    await fence()
                    packet = {"action": "unavailable", "question": _SAFE_QUESTION, "pending_context": None}
                    continue
                await fence()
                changes.extend(delta)
                current = candidate
                current.state_version = version + int(bool(changes))
                result["place_resolution"] = {"action": "selected"}
                if packet.get("replace_place_versions"):
                    result["place_ref_revisions"] = [{"place_id": ref_id, "previous_ref_version": old_version,
                        "ref_version": verified[ref_id].ref_version} for ref_id, old_version in packet["replace_place_versions"].items()]
                result["notice"] = packet.get("notice") or "Выбранное место сохранено."
                if queue:
                    prepared = _request(queue.pop(0), current)
                    chosen, selection_pending, packet = None, None, None
                    continue
                pending = _next_missing(current, run, old_pending)
                if pending is None:
                    await fence()
                    try:
                        result.update(await harness._search_trip(current, session=session, run=run))
                    except Exception:  # noqa: BLE001 - retain the selected geography and other available facts.
                        await fence()
                        result.update(plans=[], error={"code": "PLACE_SEARCH_UNAVAILABLE",
                            "message": "Поиск временно недоступен; выбранные места и условия сохранены.", "retryable": True})
                    await fence()
                    questions = result.get("questions") or []
                    if questions:
                        binding = derive_pending_field(questions, current)
                        text = human_question(questions[0], current, binding)
                        pending = _pending(text, current, run, context=binding.get("context"),
                            field=binding.get("field"), target=binding.get("target_id"))
                    status = "OPTIONS_READY" if result.get("plans") else "WAITING_USER" if pending else "TRIP_DRAFT"
                else:
                    status = "WAITING_USER"
                waiting = "clarification" if pending else None
                reply = pending["text"] if pending else result.get("notice", "Выбранное место сохранено.")
                break
            if action == "retained":
                try:
                    _need(selection_pending is not None and chosen.get("decision") == "reject"
                        and not packet.get("operations") and not packet.get("resolved_places")
                        and packet.get("replace_place_versions") is None)
                    checked_ref_revision(selection_pending, chosen, current, now=_now(harness))
                except (ValueError, TypeError, KeyError):
                    packet = {"action": "unavailable", "question": _SAFE_QUESTION, "pending_context": None}
                    continue
                await fence()
                result["place_resolution"] = {"action": "revision_declined"}
                pending, status, waiting = None, "TRIP_DRAFT", None
                reply = "Прежние сведения о месте сохранены. Новая версия не применена; поиск не выполнялся."
                break
            if action in {"ask", "no_match", "unavailable"}:
                context = deepcopy(packet.get("pending_context") or {})
                owned = context.get("place_resolution")
                # An explicitly supplied next city in the same model batch can
                # consume a freshly resolved country without asking it twice.
                if (action == "ask" and isinstance(owned, dict) and owned.get("owner") == OWNER
                        and owned.get("stage") == "city_query" and queue):
                    following = queue[0]
                    country_ref = owned.get("country_ref")
                    if (following.get("kind") == "city" and following.get("role") == owned.get("role")
                            and following.get("stay_id") == owned.get("stay_id") and isinstance(country_ref, dict)
                            and following.get("country_iso2") in {None, country_ref.get("country_iso2")}):
                        prepared = _request(queue.pop(0), current)
                        prepared["country_ref"] = deepcopy(country_ref)
                        chosen, selection_pending, packet = None, None, None
                        continue
                context["place_remaining_requests"] = queue
                reply = _SAFE_QUESTION if action == "unavailable" else packet.get("question") or _SAFE_QUESTION
                pending = _pending(reply, current, run, context=context)
                status, waiting = "WAITING_USER", "clarification"
                result["place_resolution"] = {"action": action}
                if action == "unavailable":
                    result["error"] = {"code": "PLACE_RESOLUTION_UNAVAILABLE", "message": _SAFE_QUESTION, "retryable": True}
                break
            packet = {"action": "unavailable", "question": _SAFE_QUESTION, "pending_context": None}
        else:
            reply = _SAFE_QUESTION
            pending = _pending(reply, current, run, context={"place_remaining_requests": queue})
            status, waiting = "WAITING_USER", "clarification"
            result["error"] = {"code": "PLACE_RESOLUTION_LIMIT", "message": _SAFE_QUESTION, "retryable": False}
        await fence()
    await harness.store.finish_run(run["run_id"], version,
        current.model_dump(mode="json") if changes else None, status, reply,
        pending_question=pending, waiting_reason=waiting, changes=changes, result=result)
