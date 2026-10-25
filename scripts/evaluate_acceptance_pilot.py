"""Bounded actual localhost:8083 executor for the frozen four-case pilot.

Default is dry-run: no HTTP, model, DB or environment reads. A confirmed run
sends each original turn once to a fresh, genuinely empty session. The pure
grader evaluates only its BOUND subset; full cases remain BLOCKED_ORACLE.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import quote, urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_acceptance_pilot import EMPTY, evaluate_observation
from evaluate_owner_dialogues import Evidence, LocalHTTP, PROVIDER_ERRORS


FIXTURE = ROOT / "fixtures/evaluation/oracle_bindings/pilot-v2.json"
GRADER = ROOT / "scripts/check_acceptance_pilot.py"
ORIGINAL = ROOT / "docs/research/sources/ostrovok_stress_tests.jsonl"
REUSE = ROOT / "scripts/evaluate_owner_dialogues.py"
EXPECTED_IDS = {"NLU-01", "NLU-03", "NLU-06", "NLU-09"}


def now():
    return datetime.now(timezone.utc).isoformat()


def require(condition, code):
    if not condition:
        raise ValueError(code)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        return None  # Bearer credentials may never follow a foreign redirect.


class PilotHTTP(LocalHTTP):
    def __init__(self, base):
        require(urlparse(base).port == 8083, "pilot_requires_local_port_8083")
        super().__init__(base)
        self.opener = build_opener(ProxyHandler({}), NoRedirect())


def package_fingerprint():
    """Hash complete checked-out Python package source plus packaged assets.

    This records client-side source on disk. It cannot prove which revision a
    previously started backend loaded; process/start synchronization is a
    separate operator preflight, never inferred from these hashes.
    """
    files = [path for path in (ROOT / "otravel").rglob("*") if path.is_file()
             and path.suffix in {".py", ".json", ".html", ".css", ".js"}
             and "__pycache__" not in path.parts]
    files.append(ROOT / "pyproject.toml")
    entries = [{"path": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size, "sha256": sha(path)}
               for path in sorted(files)]
    digest = hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"sha256": digest, "files": entries, "scope": "CLIENT_PACKAGE_SOURCE_ON_DISK_NOT_LOADED_PROCESS_PROOF"}


def load_suite():
    suite = json.loads(FIXTURE.read_bytes())
    require(suite.get("format") == "otravel.acceptance_pilot.v2", "unsupported_frozen_format")
    cases = suite["cases"]
    require(len(cases) == 4 and {case["id"] for case in cases} == EXPECTED_IDS, "frozen_four_cases_required")
    original_lines = ORIGINAL.read_bytes().splitlines(keepends=True)
    originals = {json.loads(line)["id"]: (line, json.loads(line)) for line in original_lines if line.strip()}
    for case in cases:
        require(case.get("initial_trip") == {}, "initial_trip_must_be_genuinely_empty")
        require(len(case["turns"]) == 1, "exactly_one_original_turn_required")
        physical, source = originals[case["id"]]
        text = case["turns"][0]["text"]
        require(source["messages"] == [text] and case["binding"]["messages"] == [text], "original_turn_mismatch")
        source_ref = case["binding"]["source"]["original"]
        require(source_ref["sha256"] == sha(ORIGINAL) and source_ref["record_sha256"] == hashlib.sha256(physical).hexdigest(), "original_source_hash_mismatch")
        require(case["binding"]["case_id"] == case["id"], "binding_case_mismatch")
        checks = case["binding"]["expectedChecks"]
        require(any(check["binding_status"] == "BOUND" for check in checks), "no_bound_subset")
        require(any(check["check_id"] == "original_behavior" and check["binding_status"] != "BOUND" for check in checks), "full_behavior_must_stay_unbound")
    return suite


def evaluate(http, suite, report, target, folder, evidence, cap, sessions):
    stop = None

    def save():
        target.write_text(json.dumps(evidence.clean(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def events(route, token):
        rows = http.get(route + "/events", token)["events"]
        require(isinstance(rows, list), "durable_events_unavailable")
        return rows

    def attempts():
        return sum(event["event_type"] == "MODEL_CALL_STARTED"
                   for route, token in sessions for event in events(route, token))

    health = http.get("/health")
    require(health.get("database") == "ready", "database_not_ready")
    providers = http.get("/api/v1/providers/status")
    report["runtime_provider_metadata"] = providers.get("_runtime", {})
    # The local Store enforces one attempt/run. Refuse remote routing rather
    # than assuming four single-turn submissions imply at most four attempts.
    require(providers.get("_runtime", {}).get("llm_provider") == "ollama", "pilot_requires_local_ollama")
    require(providers.get("_runtime", {}).get("model") == "qwen3.5:4b", "unexpected_local_model")
    for case in suite["cases"]:
        record = {"case_id": case["id"], "subset_status": "NOT_RUN", "full_case_status": "BLOCKED_ORACLE"}
        report["cases"].append(record)
        if stop:
            record.update(execution_status="NOT_EXECUTED", reason=stop)
            save()
            continue
        require(sha(FIXTURE) == report["fixture_sha256"] and sha(GRADER) == report["grader_sha256"], "fixture_or_grader_changed")
        require(package_fingerprint()["sha256"] == report["package_fingerprint"]["sha256"], "package_changed_during_execution")
        used = attempts()
        report["actual_model_attempts"] = used
        if used >= cap:
            stop = "ATTEMPT_CAP_GUARD"
            record.update(execution_status="NOT_EXECUTED", reason=stop)
            save()
            continue
        status, created = http.request("POST", "/api/v1/sessions", body={"initial_trip": {}})
        require(status in {200, 201}, "session_creation_failed")
        sid, token = created["session_id"], created["session_token"]
        evidence.tokens.add(token)
        evidence.identity(sid, "session")
        evidence.identity(created["trip"]["trip_id"], "trip")
        route = "/api/v1/sessions/" + quote(sid, safe="")
        sessions.append((route, token))
        before = http.get(route, token)
        require(all(before["trip"].get(key) == value for key, value in EMPTY.items()), "backend_initial_trip_not_empty")
        require(before["state_version"] == 0 and before.get("pending_question") is None, "backend_session_not_fresh")
        initial_events = events(route, token)
        require(not http.get(route + "/messages", token)["messages"], "backend_session_has_messages")
        require(len(initial_events) == 1 and initial_events[0]["event_type"] == "SESSION_CREATED"
                and initial_events[0].get("run_id") is None, "backend_session_has_noncreation_history")
        require(not http.get(route + "/orders", token)["orders"], "backend_session_has_orders")
        require(before["trip"].get("search_policy", {}).get("mode", "DEMO") == "DEMO", "unexpected_live_search_policy")
        text = case["turns"][0]["text"]
        record.update(execution_status="INTAKE_PENDING", state_before=before, request_text=text,
                      backend_session_created_at=initial_events[0].get("created_at"))
        save()
        start = time.monotonic()
        status, accepted = http.request("POST", route + "/messages", token, {
            "client_message_id": str(uuid4()), "text": text, "expected_version": before["state_version"]})
        require(status == 202, "message_not_accepted")
        run_id = accepted["run_id"]
        evidence.identity(run_id, "run")
        record.update(execution_status="RUN_PENDING", run_alias=evidence.identities[run_id])
        save()
        run = http.wait(run_id, token)
        after = http.get(route, token)
        current = [event for event in events(route, token) if event.get("run_id") == run_id]
        orders = http.get(route + "/orders", token)["orders"]
        observation = {"case_id": case["id"], "request_text": text, "state_before": before, "state_after": after,
                       "actual_run": run, "current_run_events": current, "orders": orders,
                       "session_created_events": initial_events}
        graded = evaluate_observation(case, observation)
        require(graded["full_case_status"] == "BLOCKED_ORACLE", "grader_must_not_promote_full_case")
        calls = [event for event in current if event["event_type"] == "MODEL_CALL_STARTED"]
        attempt_policy_valid = len(calls) <= 1 and all(event.get("payload", {}).get("provider") == "ollama" for event in calls)
        actual = (run.get("result") or {}).get("result") or {}
        model = run.get("llm") or actual.get("llm")
        handler = actual.get("response_source", "not_reported")
        provider_integrity = (isinstance(model, dict) and model.get("provider") == "ollama" and model.get("model") == "qwen3.5:4b") if calls else handler == "contextual_slot"
        record.update(graded, execution_status="OBSERVED", actual_model_attempts=len(calls),
            actual_model_metadata=model, handler_source=handler,
            path_classification="contextual_slot" if handler == "contextual_slot" else "llm_extraction_with_server_guards" if calls else "unverified",
            provider_metadata_consistent=provider_integrity, local_attempt_policy_valid=attempt_policy_valid,
            elapsed_seconds=round(time.monotonic() - start, 3))
        report["observations"][case["id"]] = observation
        evidence_path = folder / (case["id"] + ".json")
        with evidence_path.open("x", encoding="utf-8") as stream:
            json.dump(evidence.clean({"observation": observation, "grading": graded}), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        record["evidence_paths"] = [evidence_path.relative_to(ROOT).as_posix()]
        error_code = (run.get("error") or {}).get("code")
        if error_code in PROVIDER_ERRORS:
            record["execution_status"] = "BLOCKED_PROVIDER"
            stop = "PROVIDER_UNAVAILABLE"
        elif not provider_integrity or not attempt_policy_valid:
            record["execution_status"] = "BLOCKED_PROVIDER_METADATA"
            stop = "PROVIDER_METADATA_UNVERIFIED"
        report["actual_model_attempts"] = attempts()
        require(report["actual_model_attempts"] <= cap, "durable_attempt_cap_exceeded")
        save()
        print(json.dumps({"case": case["id"], "execution": record["execution_status"],
                          "subset": record["subset_status"], "full_case": "BLOCKED_ORACLE",
                          "handler": handler, "attempts": len(calls)}, ensure_ascii=False), flush=True)
    return attempts()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8083")
    parser.add_argument("--label", required=True, help="Unique immutable report label")
    parser.add_argument("--attempt-cap", type=int, default=4)
    parser.add_argument("--confirm-inference", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        require(bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", args.label)), "invalid_label")
        require(1 <= args.attempt_cap <= 4, "attempt_cap_must_be_1_to_4")
        http = PilotHTTP(args.base_url)
        suite = load_suite()
        date = datetime.now(timezone.utc).strftime("%Y%m%d")
        target = ROOT / f"docs/verification/acceptance-pilot-v2-{date}-{args.label}.json"
        folder = target.with_suffix("")
        require(not target.exists() and not folder.exists(), "immutable_report_label_already_exists")
        report = {"format": "otravel.acceptance_pilot_execution.v1", "mode": "DRY_RUN_NO_HTTP",
            "suite_id": suite["suite_id"], "binding_revision": suite["binding_revision"],
            "fixture_path": FIXTURE.relative_to(ROOT).as_posix(), "fixture_sha256": sha(FIXTURE),
            "grader_path": GRADER.relative_to(ROOT).as_posix(), "grader_sha256": sha(GRADER),
            "executor_sha256": sha(Path(__file__)), "http_evidence_helper_sha256": sha(REUSE),
            "package_fingerprint": package_fingerprint(), "base_url": http.base,
            "clock_policy": suite["clock_policy"], "runtime_clock_injected": False,
            "attempt_cap": args.attempt_cap, "case_ids": [case["id"] for case in suite["cases"]],
            "report": target.relative_to(ROOT).as_posix(), "actual_model_attempts": 0,
            "full_case_status": "BLOCKED_ORACLE", "cases": [], "observations": {},
            "scope": "Bound typed subset on actual backend observations; never full106 or raw-model NLU acceptance",
            "limitations": ["Package fingerprint records client source, not proof of loaded backend revision",
                "Backend sync/restart process identity must be verified separately before confirmation",
                "LLM-backed turns include deterministic server guards; handler paths are reported separately",
                "No automatic DEMO scenario/search or supplier request is issued by this executor"]}
        if args.dry_run or not args.confirm_inference:
            print(json.dumps({key: value for key, value in report.items() if key not in {"package_fingerprint", "cases", "observations"}}, ensure_ascii=False, indent=2))
            return 0
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as stream:
            json.dump({**report, "status": "IN_PROGRESS"}, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        folder.mkdir()
        evidence = Evidence()
        sessions = []  # Credentials stay in memory, outside the serializable report.
        report.update(mode="ACTUAL_LOCAL_HTTP_OBSERVATIONS", status="IN_PROGRESS", started_at_utc=now())
        try:
            report["actual_model_attempts"] = evaluate(http, suite, report, target, folder, evidence, args.attempt_cap, sessions)
        except Exception as error:
            report.update(execution_stopped=True, error_class=type(error).__name__)
            if isinstance(error, ValueError):
                code = str(error)
                report["stop_reason"] = code if re.fullmatch(r"[a-z0-9_]+", code) else "unclassified_validation_error"
        finally:
            try:
                report["actual_model_attempts"] = sum(event["event_type"] == "MODEL_CALL_STARTED"
                    for route, token in sessions for event in http.get(route + "/events", token)["events"])
                report["attempt_cap_respected"] = report["actual_model_attempts"] <= args.attempt_cap
            except Exception:
                report["actual_model_attempts_last_observed"] = report["actual_model_attempts"]
                report["actual_model_attempts"] = None
                report["attempt_accounting_unavailable"] = True
                report["attempt_cap_respected"] = None
            report["source_unchanged_during_execution"] = (sha(FIXTURE) == report["fixture_sha256"] and
                sha(GRADER) == report["grader_sha256"] and package_fingerprint()["sha256"] == report["package_fingerprint"]["sha256"])
            report["finished_at_utc"] = now()
            report["subset_status_counts"] = dict(Counter(case["subset_status"] for case in report["cases"]))
            complete = (not report.get("execution_stopped") and report["attempt_cap_respected"] is True
                and report["source_unchanged_during_execution"] and
                len(report["cases"]) == 4 and all(case.get("execution_status") == "OBSERVED" and
                    case["subset_status"] == "PASS_BOUND_CHECKS" for case in report["cases"]))
            report["status"] = "PASS_BOUND_SUBSET_ONLY" if complete else "BOUND_SUBSET_NOT_ALL_PASS"
            target.write_text(json.dumps(evidence.clean(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"report": str(target), "status": report["status"], "full_case_status": "BLOCKED_ORACLE",
                          "actual_attempts": report["actual_model_attempts"]}, ensure_ascii=False))
        return 0 if report["status"] == "PASS_BOUND_SUBSET_ONLY" else 1
    except Exception as error:
        print(json.dumps({"status": "NOT_STARTED", "error_class": type(error).__name__}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
