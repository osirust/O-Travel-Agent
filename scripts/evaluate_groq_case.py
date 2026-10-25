"""One explicit Groq-backed harness case; no API keys read by this client.

Uses unchanged development/original inputs and existing pure graders. A passing
BOUND subset is never full-case acceptance. No retry or supplier order is made.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_acceptance_pilot import EMPTY, evaluate_observation
from evaluate_acceptance_pilot import NoRedirect, package_fingerprint
from evaluate_dialogue_regressions import grade_turn
from evaluate_owner_dialogues import Evidence, LocalHTTP


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=["compound", "NLU-03", "NLU-06"], default="compound")
    parser.add_argument("--label", required=True)
    parser.add_argument("--confirm-free", action="store_true")
    args = parser.parse_args()
    if not args.label.replace("-", "").isalnum():
        raise ValueError("invalid_label")
    fixture = ROOT / ("fixtures/evaluation/owner-dialogue-compound-derived-v1.json" if args.case == "compound"
        else "fixtures/evaluation/oracle_bindings/pilot-v2.json")
    raw = fixture.read_bytes()
    suite = json.loads(raw)
    case = None if args.case == "compound" else next(row for row in suite["cases"] if row["id"] == args.case)
    message = suite["messages"][0] if case is None else case["turns"][0]["text"]
    target = ROOT / ("docs/verification/groq-harness-20261025-" + args.label + ".json")
    report = {"scope": "SINGLE_ACTUAL_HARNESS_BOUND_SUBSET_NOT_FULL_NLU", "case_id": args.case,
        "fixture_sha256": hashlib.sha256(raw).hexdigest(), "request_text": message,
        "executor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "package_fingerprint": package_fingerprint(), "full_case_status": "BLOCKED_ORACLE",
        "actual_model_attempts": 0, "status": "DRY_RUN_NO_HTTP"}
    grader = ROOT / "scripts" / ("evaluate_dialogue_regressions.py" if case is None else "check_acceptance_pilot.py")
    report["grader_sha256"] = hashlib.sha256(grader.read_bytes()).hexdigest()
    if not args.confirm_free:
        print(json.dumps({k: v for k, v in report.items() if k != "package_fingerprint"}, ensure_ascii=False))
        return 0
    with target.open("x", encoding="utf-8") as stream:
        json.dump({**report, "status": "IN_PROGRESS"}, stream)
    evidence = Evidence()
    http = LocalHTTP("http://127.0.0.1:8081")
    http.opener = build_opener(ProxyHandler({}), NoRedirect())
    route, token = None, None
    try:
        assert http.get("/health").get("database") == "ready"
        providers = http.get("/api/v1/providers/status")
        assert providers["_runtime"] == {"llm_provider": "groq", "model": "qwen/qwen3.8-27b"}
        assert providers["groq"]["free_tier_confirmed"] is True
        report["runtime"] = providers["_runtime"]
        code, created = http.request("POST", "/api/v1/sessions", body={"initial_trip": {}})
        assert code == 201
        token = created.pop("session_token")
        evidence.tokens.add(token)
        evidence.identity(created["session_id"], "session")
        route = "/api/v1/sessions/" + created["session_id"]
        before = http.get(route, token)
        assert all(before["trip"].get(k) == v for k, v in EMPTY.items())
        initial_events = http.get(route + "/events", token)["events"]
        assert len(initial_events) == 1 and initial_events[0]["event_type"] == "SESSION_CREATED"
        server_clock = datetime.fromisoformat(initial_events[0]["created_at"].replace("Z", "+00:00"))
        clock_verified = server_clock.utcoffset() is not None and abs((datetime.now(timezone.utc) - server_clock).total_seconds()) < 300
        report["server_creation_clock"] = server_clock.isoformat()
        report["started_at_utc"] = datetime.now(timezone.utc).isoformat()
        code, accepted = http.request("POST", route + "/messages", token, {
            "text": message, "expected_version": before["state_version"], "client_message_id": str(uuid4())})
        assert code == 202
        run = http.wait(accepted["run_id"], token)
        after = http.get(route, token)
        events = [row for row in http.get(route + "/events", token)["events"] if row.get("run_id") == accepted["run_id"]]
        history = http.get(route + "/messages", token)["messages"]
        replies = [row["text"] for row in history if row.get("run_id") == accepted["run_id"] and row["role"] == "assistant"]
        orders = http.get(route + "/orders", token)["orders"]
        actual = (run.get("result") or {}).get("result") or {}
        observation = {"case_id": args.case, "request_text": message, "state_before": before,
            "state_after": after, "actual_run": run, "current_run_events": events, "orders": orders,
            "assistant_text": replies[0] if len(replies) == 1 else "", "assistant_reply_count": len(replies),
            "clock_local_date": server_clock.astimezone(timezone(timedelta(hours=3))).date().isoformat(),
            "runtime_clock_verified": clock_verified}
        graded = grade_turn("B", 1, observation) if case is None else evaluate_observation(case, observation)
        calls = [row for row in events if row["event_type"] == "MODEL_CALL_STARTED"]
        meta = run.get("llm") or actual.get("llm") or {}
        integrity = (len(calls) == 1 and all(row["payload"].get("provider") == "groq" for row in calls)
            and meta.get("provider") == "groq" and meta.get("model") == "qwen/qwen3.8-27b"
            and actual.get("response_source") == "llm")
        report.update(observation=observation, grading=graded, provider_integrity=integrity,
            actual_model_attempts=len(calls), actual_model_metadata=meta)
        report["status"] = "PASS_BOUND_SUBSET_ONLY" if graded["subset_status"] == "PASS_BOUND_CHECKS" and integrity else "NOT_ALL_BOUND_CHECKS_PASS"
    except Exception as error:
        report.update(status="NOT_COMPLETED", error_class=type(error).__name__)
    finally:
        if route:
            try:
                report["actual_model_attempts"] = sum(row["event_type"] == "MODEL_CALL_STARTED"
                    for row in http.get(route + "/events", token)["events"])
            except Exception:
                report["actual_model_attempts"] = None
        report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        report["source_unchanged"] = package_fingerprint()["sha256"] == report["package_fingerprint"]["sha256"]
        if not report["source_unchanged"]:
            report["status"] = "BLOCKED_SOURCE_CHANGED"
        target.write_text(json.dumps(evidence.clean(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(target), "status": report["status"], "attempts": report["actual_model_attempts"]}))
    return 0 if report["status"] == "PASS_BOUND_SUBSET_ONLY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
