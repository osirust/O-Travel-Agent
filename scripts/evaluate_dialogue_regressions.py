"""Bounded development-dialogue evaluation; default is zero-HTTP dry run.

Freeze expectations before inference. Only --confirm-inference can contact the
owner's loopback:8083 Qwen backend. This is not the original 106-case evaluation,
not a holdout, and never a full-oracle or raw-model PASS.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import quote
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluate_acceptance_pilot import PilotHTTP, package_fingerprint
from evaluate_owner_dialogues import Evidence, PROVIDER_ERRORS

EXPECTED = ROOT / "fixtures/evaluation/dialogue-regressions-development-v3.expected.json"
SOURCES = {
    "owner11_v1": "fixtures/evaluation/owner-dialogue-extended-v1.json",
    "policy_v2": "fixtures/evaluation/owner-dialogue-extended-policy-v2.json",
    "erratum_v3": "fixtures/evaluation/owner-dialogue-policy-v3-erratum.json",
    "derived_b": "fixtures/evaluation/owner-dialogue-compound-derived-v1.json",
    "historical8_provenance_only": "fixtures/evaluation/owner-dialogue-compound-history-v1.json",
}
HELPERS = ("scripts/evaluate_acceptance_pilot.py", "scripts/evaluate_owner_dialogues.py",
           "scripts/check_acceptance_pilot.py")
REFERENCE_DATE = "2026-10-25"
MSK = timezone(timedelta(hours=3), "Europe/Moscow")
COMPOUND = "белград в сербии из москвы рано утром, детей нет, один взрослый"
B_MESSAGES = [COMPOUND, "Замени направление Белград на Санкт-Петербург", "да", "26.10.2026", "завтра"]
A_MESSAGES = ["сербию знаешь?", "сербия", "стамбул", "нет", "белгород", "минимум стоимости", "москва", "1", "0", "белгород", "белград"]
EMPTY = {"origin": None, "travelers": [], "groups": [], "stays": [], "legs": [], "budget": None,
         "constraints": [], "preferences": []}
RAW_SLOT = re.compile(r"\b(?:destination_city|origin_city|adult_count|children_count|check_in|check_out|missing_[a-z_]+|(?:destination|origin)\.city|party\.(?:adults|children)|traveler\.age|stay\.(?:check_in|check_out|rooms)|leg\.departure_date|budget\.amount_minor)\b")


def require(condition, code):
    if not condition:
        raise ValueError(code)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def metadata(session):
    pending = session.get("pending_question") or {}
    result = dict(pending.get("context") or {})
    for key in ("requested_destination", "requested_origin", "proposed_destination", "confirmation_required", "field", "target_id"):
        if key in pending:
            result[key] = pending[key]
    return result


def requested(session):
    value = metadata(session).get("requested_destination") or {}
    return value if isinstance(value, dict) else {}


def pending_field(session):
    return metadata(session).get("field")


def has_preference(trip, key, value):
    return any(row.get("key") == key and row.get("value") == value for row in trip.get("preferences", []))


def selected_city(session, city):
    stays = session.get("trip", {}).get("stays", [])
    concrete_cities = [stay.get("city") for stay in stays if stay.get("city")]
    durable_city = any(stay.get("city") == city for stay in stays)
    remembered = requested(session)
    # Conflicting sidebar metadata is a failure even if the stay is correct.
    metadata_agrees = remembered.get("kind") != "city" or remembered.get("name") == city
    canonical_agrees = not concrete_cities or all(name == city for name in concrete_cities)
    return (durable_city or remembered.get("kind") == "city" and remembered.get("name") == city) and metadata_agrees and canonical_agrees


def destination_confirmation(session, candidate):
    ctx = metadata(session)
    proposed = ctx.get("proposed_destination") or {}
    country = requested(session).get("country_code")
    country_matches = country == "RS"
    if candidate == "Санкт-Петербург" and country is None:
        # The B1 canonical-city alternative remains valid through B2/B3, too.
        country_matches = (selected_city(session, "Белград") and
                           any(s.get("city") == "Белград" for s in session.get("trip", {}).get("stays", [])))
    return (ctx.get("field") == "destination.city" and ctx.get("confirmation_required") is True
            and isinstance(proposed, dict) and proposed.get("name") == candidate
            and session.get("trip", {}).get("origin") == ("Москва" if candidate == "Санкт-Петербург" else None)
            and candidate in {"Стамбул", "Санкт-Петербург"} and country_matches)


def prerequisite(case_id, index, before):
    """No speculative reinterpretation of a short answer after the wrong slot."""
    if case_id == "A":
        if index == 4 and not destination_confirmation(before, "Стамбул"):
            return "DEPENDENCY_MISSING_TR_CONFIRMATION"
        expected = {7: "origin.city", 8: "party.adults", 9: "party.children"}.get(index)
        if expected and pending_field(before) != expected:
            return "DEPENDENCY_MISSING_" + expected.replace(".", "_").upper()
    if case_id == "B":
        if index == 3:
            if selected_city(before, "Санкт-Петербург") and before.get("trip", {}).get("origin") == "Москва":
                return "NOT_NEEDED_ALREADY_CHANGED"
            if not destination_confirmation(before, "Санкт-Петербург"):
                return "DEPENDENCY_MISSING_DESTINATION_CONFIRMATION"
        expected = {4: "stay.check_in", 5: "stay.check_out"}.get(index)
        if expected and pending_field(before) != expected:
            return "DEPENDENCY_MISSING_" + expected.replace(".", "_").upper()
    return None


def grade_turn(case_id, index, observation):
    """Pure, predeclared bound checks. Missing observation fields cannot PASS."""
    checks = []

    def check(name, condition, status="FAIL"):
        checks.append({"id": name, "status": "PASS" if condition else status})

    before, after = observation.get("state_before") or {}, observation.get("state_after") or {}
    run = observation.get("actual_run") or {}
    trip, prior = after.get("trip") or {}, before.get("trip") or {}
    reply = observation.get("assistant_text") or ""
    events = observation.get("current_run_events")
    orders = observation.get("orders")
    error = run.get("error") or {}
    context = metadata(after)
    adults = sum(p.get("type") == "adult" for p in trip.get("travelers", []))
    children = sum(p.get("type") == "child" for p in trip.get("travelers", []))
    check("completed_without_error", run.get("status") == "completed" and not error)
    check("no_order", isinstance(orders, list) and orders == [])
    check("no_search_tool", isinstance(events, list) and not any(e.get("event_type") == "TOOL_CALL_STARTED" for e in events))
    check("one_persisted_assistant_reply", observation.get("assistant_reply_count") == 1 and bool(reply))
    check("human_reply_without_raw_slots", bool(reply) and RAW_SLOT.search(reply) is None)
    check("no_invented_numeric_budget", "budget" in trip and trip["budget"] is None)
    if not observation.get("runtime_clock_verified") and case_id == "B":
        check("clock_bound_to_25_oct_msk", False, "DEPENDENCY")
    elif case_id == "B":
        check("clock_bound_to_25_oct_msk", observation.get("clock_local_date") == REFERENCE_DATE, "DEPENDENCY")
    dependency = prerequisite(case_id, index, before)
    if dependency:
        check(dependency, False, "DEPENDENCY")
    if case_id == "A":
        if index == 1:
            check("faq_nonmutation", {k: v for k, v in trip.items() if k != "state_version"} == {k: v for k, v in prior.items() if k != "state_version"})
            check("faq_answers_serbia", "серби" in reply.casefold() and bool(re.search(r"стран|балкан|европ|белград", reply, re.I)))
            check("faq_not_destination_consent", not requested(after) and not trip.get("stays") and trip.get("origin") is None)
        if index >= 2:
            check("requested_rs_retained", requested(after).get("country_code") == "RS")
        if index == 2:
            check("country_does_not_invent_city", not trip.get("stays") and requested(after).get("kind") == "country")
        if index == 3:
            check("istanbul_candidate_not_mutation", trip.get("origin") == prior.get("origin") and trip.get("stays") == prior.get("stays"))
            check("country_mismatch_explained", "турц" in reply.casefold() and "серби" in reply.casefold())
            check("tr_confirmation_is_pending", destination_confirmation(after, "Стамбул"))
        if index == 4:
            check("rejected_tr_not_selected", not any(s.get("city") == "Стамбул" for s in trip.get("stays", [])) and not context.get("confirmation_required"))
            check("rejection_is_not_global_pause", after.get("waiting_reason") != "user_pause")
        if index in {5, 10, 11}:
            check("belgrade_intent_retained", selected_city(after, "Белград"))
        if index in {5, 10}:
            check("contextual_typo_ack_visible", "белград" in reply.casefold() and "серби" in reply.casefold() and bool(re.search(r"понял как|проч[её]л как|исправ|интерпрет|имеете в виду", reply, re.I)))
        if index >= 6:
            check("lowest_total_preference_saved", has_preference(trip, "price_priority", "lowest_total"))
        if index >= 7:
            check("origin_moscow_retained", trip.get("origin") == "Москва")
        if index >= 8:
            check("one_adult_retained", adults == 1)
        if index >= 9:
            check("zero_children_retained", children == 0 and context.get("children_known") is True)
    elif case_id == "B":
        check("compound_origin_moscow", trip.get("origin") == "Москва")
        check("compound_one_adult_zero_children", adults == 1 and children == 0 and context.get("children_known") is True)
        check("early_morning_preference_saved", has_preference(trip, "departure_time", "early_morning"))
        check("no_repeated_party_question", pending_field(after) not in {"party.adults", "party.children"} and not re.search(r"сколько взрослых|сколько.*детей|кто поедет", reply, re.I))
        if index == 1:
            canonical_belgrade = any(s.get("city") == "Белград" for s in trip.get("stays", []))
            country = requested(after).get("country_code")
            # Curated canonical Belgrade already establishes Serbia. A duplicate
            # requested-country field is optional; a contradictory country fails.
            check("compound_belgrade_rs", selected_city(after, "Белград") and
                  (country == "RS" or canonical_belgrade and country is None))
            check("morning_has_no_invented_clock", not any(c.get("key") in {"departure_time", "departure_after", "departure_before"} and re.search(r"\d{1,2}:\d{2}", str(c.get("value"))) for c in trip.get("constraints", [])))
        if index == 2:
            check("destination_changed_or_confirmation", selected_city(after, "Санкт-Петербург") or destination_confirmation(after, "Санкт-Петербург"))
        if index >= 3:
            check("spb_is_destination_not_origin", selected_city(after, "Санкт-Петербург") and trip.get("origin") == "Москва")
        if index >= 4:
            check("checkin_26_oct", any(s.get("city") == "Санкт-Петербург" and s.get("check_in") == "2026-10-26" for s in trip.get("stays", [])))
        if index == 5:
            stays = [s for s in trip.get("stays", []) if s.get("city") == "Санкт-Петербург"]
            check("collision_not_silently_repaired", bool(stays) and all(s.get("check_out") is None for s in stays))
            check("ask_for_later_checkout", pending_field(after) == "stay.check_out" and bool(re.search(r"позже|поздн|после.*заезд|выезд.*заезд", reply, re.I)))
    else:
        check("known_scenario", False)
    status = "DEPENDENCY_FAILED" if any(c["status"] == "DEPENDENCY" for c in checks) else "PASS_BOUND_CHECKS" if all(c["status"] == "PASS" for c in checks) else "FAIL_BOUND_CHECKS"
    return {"subset_status": status, "full_scenario_status": "BLOCKED_ORACLE", "checks": checks}


def current_sources():
    return {name: {"path": path, "sha256": sha(ROOT / path)} for name, path in SOURCES.items()}


def make_expectations():
    inputs = {name: json.loads((ROOT / path).read_bytes()) for name, path in SOURCES.items()}
    require(inputs["owner11_v1"]["messages"] == A_MESSAGES, "owner11_inputs_changed")
    require(inputs["derived_b"]["messages"] == B_MESSAGES, "derived_inputs_changed")
    require(inputs["policy_v2"]["new_compound_scenario_plan"]["first_exact_input"] == COMPOUND, "compound_source_changed")
    require(inputs["erratum_v3"].get("revision") == 3, "erratum_v3_required")
    return {"format": "otravel.dialogue_regressions.expected.v3", "revision": 3,
        "frozen_at_utc": now(), "provenance_class": "DEVELOPMENT_REGRESSION_NOT_HOLDOUT",
        "pre_replay_erratum": "B1 and B2/B3 specific SPb destination confirmation permit curated canonical Belgrade/RS without duplicate requested metadata; contradictory country or canonical destination still fails. A explicit-country checks unchanged. Historical expected-v1/v2 remain preserved.",
        "sources": current_sources(), "executor_sha256": sha(Path(__file__)), "grader_sha256": sha(Path(__file__)),
        "grader_embedded_in_executor": True, "helpers": {path: sha(ROOT / path) for path in HELPERS},
        "clock": {"reference_date": REFERENCE_DATE, "timezone": "Europe/Moscow", "runtime_clock_injected": False,
                  "actual_b_requires_matching_client_msk_date": True,
                  "server_clock_binding": "INDEPENDENT_WSL_CLOCK_CHECK_THEN_EXPLICIT_OPERATOR_DATE_FLAG_REQUIRED"},
        "cases": [{"id": "A", "provenance": "EXACT_OWNER11_WITH_V2_V3_POLICY", "messages": A_MESSAGES},
                  {"id": "B", "provenance": "DERIVED_DEVELOPMENT_NOT_ORIGINAL_TRANSCRIPT", "messages": B_MESSAGES,
                   "conditional_turn_3": "only actual destination-change confirmation; already selected SPb => NOT_NEEDED"}],
        "bound_expectations": {
            "every_observed_turn": ["completed run without error", "one durable human assistant reply", "no order", "no search tool call", "budget remains null", "provider and handler evidence consistent"],
            "A": {"1": ["FAQ about Serbia answers knowledge without any TripState or requested-destination mutation"],
                  "2": ["requested country RS; no concrete city invented"],
                  "3": ["TR/RS mismatch explained; explicit destination confirmation; no origin/stay mutation"],
                  "4": ["requires actual preceding TR confirmation; reject TR, retain RS; not global pause"],
                  "5,10": ["strong RS typo resolves as Belgrade/RS with visible acknowledgement"],
                  "6..11": ["price_priority=lowest_total persists; never numeric budget"],
                  "7": ["requires actual origin.city question; origin Moscow; missing prerequisite is DEPENDENCY, not A6 forced-flow failure"],
                  "8": ["requires actual party.adults question; exactly one adult"],
                  "9": ["requires actual party.children question; zero children explicitly remembered"],
                  "7..11": ["Moscow retained"], "8..11": ["one adult retained"], "9..11": ["zero children retained"],
                  "11": ["Belgrade RS intent retained in canonical stay or durable requested-city context"]},
            "B": {"1": ["Belgrade RS in canonical stay or durable city intent; canonical Belgrade needs no duplicate country metadata, but any contradictory country/city fails; Moscow origin; adult1/child0; departure_time=early_morning; no invented exact hour; no repeated party question"],
                  "2": ["SPb replaces DESTINATION or specific destination confirmation; Moscow never replaced"],
                  "3": ["send yes only for specific destination confirmation; already SPb => NOT_NEEDED; otherwise DEPENDENCY"],
                  "4": ["requires stay.check_in question; check_in=2026-10-26"],
                  "5": ["requires stay.check_out question; on verified25OctMSK tomorrow equals check-in; keep checkout unknown and ask later date; no silent +1day or INVALID_LLM failure"],
                  "all": ["all explicit compound facts/preferences retained", "verified runtime reference date25OctMSK required"]}},
        "binding": {"status": "BOUND_DEVELOPMENT_CHECKS_ONLY", "grader_entrypoint": "grade_turn",
                    "unbound": ["complete natural-language behavior", "all106", "live search availability", "global cheapest", "full UI"],
                    "full_scenario_status": "BLOCKED_ORACLE"},
        "policy": {"previous_slot_required": True, "no_reinterpretation_of_bare_numbers": True,
                   "actual_provider": "ollama", "actual_model": "qwen3.5:4b", "max_actual_model_attempts": 12,
                   "original106_used": False, "no_supplier_requests": True},
        "execution": {"status": "NOT_RUN", "http_calls": 0, "model_calls": 0}}


def validate_frozen(expected):
    require(expected.get("format") == "otravel.dialogue_regressions.expected.v3", "unsupported_expectation_format")
    require(expected["sources"] == current_sources(), "frozen_source_hash_changed")
    require(expected["executor_sha256"] == sha(Path(__file__)) and expected["grader_sha256"] == sha(Path(__file__)), "executor_or_grader_changed_after_freeze")
    require(expected["helpers"] == {path: sha(ROOT / path) for path in HELPERS}, "http_helpers_changed_after_freeze")
    require(expected["cases"][0]["messages"] == A_MESSAGES and expected["cases"][1]["messages"] == B_MESSAGES, "frozen_case_inputs_changed")


class CountingHTTP(PilotHTTP):
    def __init__(self, base):
        super().__init__(base)
        self.http_calls = 0

    def request(self, *args, **kwargs):
        self.http_calls += 1
        return super().request(*args, **kwargs)


def execute(http, expected, selected, report, target, folder, evidence, cap):
    sessions = []

    def save():
        report["http_calls"] = http.http_calls
        target.write_text(json.dumps(evidence.clean(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def events(route, token):
        result = http.get(route + "/events", token)["events"]
        require(isinstance(result, list), "durable_events_missing")
        return result

    def count_calls():
        return sum(e.get("event_type") == "MODEL_CALL_STARTED" for route, token in sessions for e in events(route, token))

    require(http.get("/health").get("database") == "ready", "database_not_ready")
    runtime = http.get("/api/v1/providers/status").get("_runtime", {})
    require(runtime.get("llm_provider") == "ollama" and runtime.get("model") == "qwen3.5:4b", "local_qwen_only")
    report["runtime"] = runtime
    stop = None
    try:
        for case in expected["cases"]:
            if case["id"] not in selected:
                continue
            record = {"id": case["id"], "turns": [], "full_scenario_status": "BLOCKED_ORACLE"}
            report["cases"].append(record)
            if stop:
                record.update(execution_status="NOT_EXECUTED", reason=stop)
                continue
            if case["id"] == "B" and (datetime.now(MSK).date().isoformat() != REFERENCE_DATE or report.get("operator_verified_runtime_date") != REFERENCE_DATE):
                record.update(execution_status="BLOCKED_CLOCK", reason="actual_runtime_clock_different_or_unverified")
                save()
                continue
            code, created = http.request("POST", "/api/v1/sessions", body={"initial_trip": {}})
            require(code in {200, 201}, "session_create_failed")
            sid, token = created["session_id"], created["session_token"]
            evidence.tokens.add(token)
            evidence.identity(sid, "session")
            evidence.identity(created["trip"]["trip_id"], "trip")
            route = "/api/v1/sessions/" + quote(sid, safe="")
            sessions.append((route, token))
            before = http.get(route, token)
            require(all(before["trip"].get(k) == v for k, v in EMPTY.items()) and before["state_version"] == 0 and not before.get("pending_question"), "fresh_empty_session_required")
            initial = events(route, token)
            require(len(initial) == 1 and initial[0].get("event_type") == "SESSION_CREATED", "fresh_creation_event_required")
            require(not http.get(route + "/messages", token)["messages"] and not http.get(route + "/orders", token)["orders"], "fresh_session_history_required")
            require(before["trip"].get("search_policy", {}).get("mode", "DEMO") == "DEMO", "no_live_search_policy")
            record.update(session_alias=evidence.identities[sid], execution_status="IN_PROGRESS")
            for index, text in enumerate(case["messages"], 1):
                row = {"index": index, "request_text": text, "status": "NOT_EXECUTED"}
                record["turns"].append(row)
                validate_frozen(expected)
                require(package_fingerprint()["sha256"] == report["package_fingerprint"]["sha256"], "package_changed_during_execution")
                before = http.get(route, token)
                dependency = prerequisite(case["id"], index, before)
                if dependency == "NOT_NEEDED_ALREADY_CHANGED":
                    row.update(status="NOT_NEEDED", reason=dependency, state_before=before, actual_model_attempts=0)
                    save()
                    continue
                if dependency:
                    row.update(status="DEPENDENCY_FAILED", reason=dependency, state_before=before, actual_model_attempts=0)
                    record["execution_status"] = "DEPENDENCY_FAILED"
                    break
                local_date = datetime.now(MSK).date().isoformat()
                if case["id"] == "B" and local_date != REFERENCE_DATE:
                    row.update(status="BLOCKED_CLOCK", reason="msk_date_changed", actual_model_attempts=0)
                    break
                used = count_calls()
                report["actual_model_attempts"] = used
                # Local source limits Ollama to one attempt/run; reserve two as
                # extra headroom and refuse any observed multi-attempt routing.
                if used + 2 > cap:
                    stop = "ATTEMPT_CAP_GUARD"
                    row.update(status="NOT_EXECUTED", reason=stop, actual_model_attempts=0)
                    break
                row.update(status="INTAKE_PENDING", state_before=before)
                save()
                started = time.monotonic()
                code, accepted = http.request("POST", route + "/messages", token, {"client_message_id": str(uuid4()), "expected_version": before["state_version"], "text": text})
                require(code == 202, "intake_failed_no_retry")
                run_id = accepted["run_id"]
                evidence.identity(run_id, "run")
                row.update(status="RUN_PENDING", run_alias=evidence.identities[run_id])
                save()
                run = http.wait(run_id, token)
                after = http.get(route, token)
                history = http.get(route + "/messages", token)["messages"]
                current = [e for e in events(route, token) if e.get("run_id") == run_id]
                orders = http.get(route + "/orders", token)["orders"]
                replies = [m["text"] for m in history if m.get("role") == "assistant" and m.get("run_id") == run_id]
                reply = replies[0] if len(replies) == 1 else ""
                result = (run.get("result") or {}).get("result") or {}
                calls = [e for e in current if e.get("event_type") == "MODEL_CALL_STARTED"]
                model = run.get("llm") or result.get("llm")
                handler = result.get("response_source", "not_reported")
                integrity = (len(calls) == 1 and handler == "llm" and isinstance(model, dict) and model.get("provider") == "ollama" and model.get("model") == "qwen3.5:4b" and all(e.get("payload", {}).get("provider") == "ollama" for e in calls)) if calls else handler == "contextual_slot"
                observation = {"case_id": case["id"], "turn_index": index, "request_text": text,
                    "state_before": before, "state_after": after, "actual_run": run,
                    "assistant_text": reply, "assistant_reply_count": len(replies), "current_run_events": current,
                    "orders": orders, "clock_local_date": local_date, "clock_utc": now(),
                    "runtime_clock_verified": report.get("operator_verified_runtime_date") == REFERENCE_DATE}
                graded = grade_turn(case["id"], index, observation)
                row.update(graded, status=graded["subset_status"], assistant_text=reply, state_after=after,
                    actual_model_attempts=len(calls), actual_model_metadata=model, handler_source=handler,
                    provider_metadata_consistent=integrity, elapsed_seconds=round(time.monotonic() - started, 3))
                path = folder / f"{case['id']}-turn-{index:02d}.json"
                with path.open("x", encoding="utf-8") as stream:
                    json.dump(evidence.clean({"observation": observation, "grading": graded}), stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
                row["evidence_path"] = str(path)
                report["actual_model_attempts"] = count_calls()
                require(report["actual_model_attempts"] <= cap, "attempt_cap_exceeded")
                if (run.get("error") or {}).get("code") in PROVIDER_ERRORS:
                    stop = "PROVIDER_UNAVAILABLE"
                elif not integrity:
                    stop = "PROVIDER_METADATA_UNVERIFIED"
                elif any(e.get("event_type") == "TOOL_CALL_STARTED" for e in current) or orders:
                    stop = "UNEXPECTED_TOOL_OR_ORDER"
                save()
                print(json.dumps({"case": case["id"], "turn": index, "status": row["status"], "handler": handler, "model_attempts": len(calls)}, ensure_ascii=False), flush=True)
                if stop or run.get("status") != "completed":
                    break
            record["execution_status"] = ("PASS_BOUND_CHECKS_ONLY" if len(record["turns"]) == len(case["messages"]) and all(t["status"] in {"PASS_BOUND_CHECKS", "NOT_NEEDED"} for t in record["turns"]) else "NOT_ALL_BOUND_CHECKS_PASS")
            save()
    finally:
        try:
            report["actual_model_attempts"] = count_calls()
        except Exception:
            report["actual_model_attempts_last_observed"] = report.get("actual_model_attempts")
            report["actual_model_attempts"] = None
            report["attempt_accounting_unavailable"] = True
        report["stop_reason"] = stop
        save()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8083")
    parser.add_argument("--label", default="dry")
    parser.add_argument("--cases", default="A,B")
    parser.add_argument("--attempt-cap", type=int, default=12)
    parser.add_argument("--freeze", action="store_true", help="Create immutable pre-inference expectations; zero HTTP")
    parser.add_argument("--expectations", type=Path, default=EXPECTED)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm-inference", action="store_true")
    parser.add_argument("--confirm-runtime-clock-date", choices=[REFERENCE_DATE],
                        help="Only after independently checking the WSL backend clock; otherwise B stays BLOCKED_CLOCK")
    parser.add_argument("--output-dir", type=Path, default=ROOT.parent / "tmp/otravel-dialogue-regressions")
    args = parser.parse_args(argv)
    try:
        require(bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", args.label)), "invalid_label")
        require(2 <= args.attempt_cap <= 12, "attempt_cap_must_be_2_to_12")
        selected = {s.strip() for s in args.cases.split(",") if s.strip()}
        require(bool(selected) and selected <= {"A", "B"}, "unknown_case_id")
        http = CountingHTTP(args.base_url)  # Construction performs no HTTP.
        if args.freeze:
            require(not args.confirm_inference, "freeze_and_inference_are_separate_steps")
            expected = make_expectations()
            with args.expectations.open("x", encoding="utf-8") as stream:
                json.dump(expected, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            print(json.dumps({"status": "FROZEN_NOT_RUN", "expectations": str(args.expectations), "sha256": sha(args.expectations), "http_calls": 0, "model_calls": 0}, ensure_ascii=False))
            return 0
        expected = json.loads(args.expectations.read_bytes())
        validate_frozen(expected)
        report = {"format": "otravel.dialogue_regressions.execution.v1", "status": "DRY_RUN_NO_HTTP",
            "provenance": "DEVELOPMENT_NOT_HOLDOUT_NOT_106", "expectation_sha256": sha(args.expectations),
            "sources": expected["sources"], "executor_sha256": sha(Path(__file__)), "grader_sha256": sha(Path(__file__)),
            "helpers": expected["helpers"], "package_fingerprint": package_fingerprint(),
            "base_url": http.base, "attempt_cap": args.attempt_cap, "http_calls": 0, "actual_model_attempts": 0,
            "clock": expected["clock"], "client_clock_utc": now(), "client_date_msk": datetime.now(MSK).date().isoformat(),
            "operator_verified_runtime_date": args.confirm_runtime_clock_date,
            "full_scenario_status": "BLOCKED_ORACLE", "cases": [],
            "limitations": ["Only bound checks qualify; complete behavior remains unbound",
                "Client package hashes do not prove the loaded backend revision; owner sync/start preflight is separate",
                "Contextual handlers and LLM extraction with server guards are reported separately",
                "Two-call headroom is reserved before every turn; no automatic retries or supplier calls"]}
        if args.dry_run or not args.confirm_inference:
            print(json.dumps({**report, "package_fingerprint": {"sha256": report["package_fingerprint"]["sha256"]},
                              "selected_cases": sorted(selected)}, ensure_ascii=False, indent=2))
            return 0
        require(args.label != "dry", "actual_run_requires_unique_label")
        folder = args.output_dir / args.label
        target = args.output_dir / (args.label + ".json")
        require(not folder.exists() and not target.exists(), "report_label_already_exists")
        args.output_dir.mkdir(parents=True, exist_ok=True)
        folder.mkdir()
        report.update(status="IN_PROGRESS", started_at_utc=now())
        evidence = Evidence()
        try:
            execute(http, expected, selected, report, target, folder, evidence, args.attempt_cap)
        except Exception as error:
            report.update(execution_stopped=True, error_class=type(error).__name__)
            if isinstance(error, ValueError) and re.fullmatch(r"[a-z0-9_]+", str(error)):
                report["error_code"] = str(error)
        report["finished_at_utc"] = now()
        report["http_calls"] = http.http_calls
        report["turn_status_counts"] = dict(Counter(t["status"] for c in report["cases"] for t in c["turns"]))
        report["package_source_unchanged"] = package_fingerprint()["sha256"] == report["package_fingerprint"]["sha256"]
        complete = (not report.get("execution_stopped") and not report.get("stop_reason") and report["package_source_unchanged"]
                    and report.get("actual_model_attempts") is not None
                    and report["actual_model_attempts"] <= args.attempt_cap
                    and len(report["cases"]) == len(selected)
                    and all(c.get("execution_status") == "PASS_BOUND_CHECKS_ONLY" for c in report["cases"]))
        report["status"] = "PASS_BOUND_CHECKS_ONLY" if complete else "NOT_ALL_BOUND_CHECKS_PASS"
        target.write_text(json.dumps(evidence.clean(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"report": str(target), "status": report["status"], "full_scenario_status": "BLOCKED_ORACLE", "http_calls": http.http_calls, "model_attempts": report["actual_model_attempts"]}, ensure_ascii=False))
        return 0 if complete else 1
    except Exception as error:
        print(json.dumps({"status": "NOT_STARTED", "error_class": type(error).__name__, "http_calls": 0, "model_calls": 0}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
