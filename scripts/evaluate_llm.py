"""Bounded actual-model HTTP acceptance. Frozen fixtures, no live travel calls."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4


def pointer(value, path):
    for part in path.strip("/").split("/") if path else []:
        part = part.replace("~1", "/").replace("~0", "~")
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


class Evaluation:
    def __init__(self, base, suite, report):
        if urlparse(base).hostname not in {"localhost", "127.0.0.1"}:
            raise ValueError("Only an explicitly local backend is supported")
        self.base, self.suite, self.report = base.rstrip("/"), suite, report
        self.client = build_opener(ProxyHandler({}))
        self.attempts = 0
        self.limit = min(12, int(suite["max_actual_model_http_attempts"]))
        self.sessions = []

    def request(self, method, path, token=None, body=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        req = Request(self.base + path, headers=headers, method=method,
                      data=None if body is None else json.dumps(body, ensure_ascii=False).encode())
        try:
            with self.client.open(req, timeout=10) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            # No arbitrary body/headers/credential URL is rendered in a report.
            return exc.code, {}

    def get(self, path, token):
        code, value = self.request("GET", path, token)
        if code != 200:
            raise RuntimeError("local_http_" + str(code))
        return value

    def wait(self, run_id, token):
        deadline = time.monotonic() + 105
        while time.monotonic() < deadline:
            row = self.get("/api/v1/runs/" + run_id, token)
            if row["status"] not in {"pending", "running"}:
                return row
            if row["status"] == "pending":
                session = self.get("/api/v1/sessions/" + row["session_id"], token)
                if session.get("waiting_reason") == "user_pause":
                    return row
            time.sleep(.3)
        raise TimeoutError("run_did_not_finish")

    def events(self, path, token):
        return self.get(path + "/events", token)["events"]

    def count_attempts(self):
        return sum(row["event_type"] == "MODEL_CALL_STARTED"
                   for path, token in self.sessions for row in self.events(path, token))

    def run_case(self, case):
        from otravel.domain import sample_trip
        initial = case.get("initial_trip", "sample")
        trip = sample_trip().model_dump(mode="json") if initial == "sample" else initial
        if trip.get("search_policy", {}).get("mode", "DEMO") != "DEMO":
            raise ValueError("Live travel searches are prohibited in this suite")
        code, created = self.request("POST", "/api/v1/sessions", body={"initial_trip": trip})
        if code != 201:
            raise RuntimeError("session_creation_" + str(code))
        token = created.pop("session_token")
        path = "/api/v1/sessions/" + created["session_id"]
        self.sessions.append((path, token))
        baseline = created["trip"]
        record = {"id": case["id"], "turns": [], "status": "PASS"}
        self.current_record = record
        old_quote = None
        deferred = []
        for setup in case.get("setup_actions", []):
            if setup == "demo_search":
                code, accepted = self.request("POST", path + "/search", token, {"client_message_id": str(uuid4())})
                if code != 202:
                    raise RuntimeError("setup_search_failed")
                run = self.wait(accepted["run_id"], token)
                plans = ((run.get("result") or {}).get("result") or {}).get("plans", [])
            elif setup == "preview_first_plan":
                code, old_quote = self.request("POST", path + "/checkout/preview", token,
                    {"plan_id": plans[0]["plan_id"], "state_version": plans[0]["state_version"]})
                if code != 200:
                    raise RuntimeError("setup_preview_failed")
            else:
                raise ValueError("unsupported_setup")
        for index, turn in enumerate(case["turns"]):
            self.attempts = self.count_attempts()
            consumes = "text" in turn and not turn.get("deferred_while_paused") or turn.get("await_buffered_runs")
            if consumes and self.limit - self.attempts < 2:
                record["turns"].append({"index": index, "status": "NOT_EXECUTED_BUDGET"})
                record["status"] = "PARTIAL_BUDGET"
                break
            before = self.events(path, token)
            before_snapshot = self.get(path, token)
            run = None
            if "action" in turn:
                code, control = self.request("POST", path + "/control", token, {"action": turn["action"]})
                if code != 200:
                    raise RuntimeError("control_" + str(code))
                if turn.get("await_buffered_runs"):
                    for rid in deferred:
                        run = self.wait(rid, token)
                    deferred.clear()
            else:
                snapshot = self.get(path, token)
                code, accepted = self.request("POST", path + "/messages", token, {
                    "client_message_id": str(uuid4()), "expected_version": snapshot["state_version"], "text": turn["text"]})
                if code != 202:
                    raise RuntimeError("message_" + str(code))
                if turn.get("deferred_while_paused"):
                    deferred.append(accepted["run_id"])
                    time.sleep(.3)
                    run = self.get("/api/v1/runs/" + accepted["run_id"], token)
                else:
                    run = self.wait(accepted["run_id"], token)
            snapshot = self.get(path, token)
            after = self.events(path, token)
            new_events = [row for row in after if row["sequence"] > max((v["sequence"] for v in before), default=0)]
            calls = sum(row["event_type"] == "MODEL_CALL_STARTED" for row in new_events)
            self.attempts = self.count_attempts()
            failures = []
            expect = turn["expect"]
            for assertion in expect.get("assertions", []):
                try:
                    actual = pointer(snapshot["trip"], assertion["path"])
                    wanted = pointer(baseline, assertion["path"]) if assertion["op"] == "unchanged" else assertion.get("value")
                    if assertion["op"] not in {"eq", "unchanged"} or actual != wanted:
                        failures.append("state:" + assertion["path"])
                except (KeyError, IndexError, TypeError, ValueError):
                    failures.append("missing:" + assertion["path"])
            for key in ("session_status", "waiting_reason"):
                if key in expect and snapshot.get("status" if key == "session_status" else key) != expect[key]:
                    failures.append(key)
            nested = ((run or {}).get("result") or {}).get("result") or {}
            if expect.get("no_order_created") and self.get(path + "/orders", token)["orders"]:
                failures.append("unexpected_order")
            if expect.get("no_tool_calls") and (any(e["event_type"] == "TOOL_CALL_STARTED" for e in new_events) or nested.get("plans")):
                failures.append("unexpected_search")
            if expect.get("no_llm_calls") and calls:
                failures.append("unexpected_llm")
            if "run_status" in expect and (run or {}).get("status") != expect["run_status"]:
                failures.append("run_status")
            if "max_questions" in expect and len((snapshot.get("pending_question") or {}).get("questions", [])) > expect["max_questions"]:
                failures.append("too_many_questions")
            if expect.get("no_secret_output"):
                text = json.dumps(run or {}, ensure_ascii=False)
                if any(marker in text for marker in ("sk-or-v1-", "duffel_test_", "Bearer ")):
                    failures.append("credential_pattern")
            allowed_run_status = {"pending"} if turn.get("deferred_while_paused") else {"completed"}
            if run and run["status"] not in allowed_run_status:
                failures.append("run_" + run["status"])
            turn_record = {"index": index, "status": "PASS" if not failures else "FAIL", "failed_checks": failures,
                           "model_attempts": calls, "session_status": snapshot["status"],
                           "llm": (run or {}).get("llm"), "error_code": ((run or {}).get("error") or {}).get("code")}
            # Preserve facts needed to diagnose a semantic failure. Never dump
            # internal session rows, bearer tokens, DB credentials or raw HTTP.
            from otravel.providers import _safe
            turn_record["observation"] = _safe({
                "request_text": turn.get("text"),
                "trip_before": before_snapshot["trip"], "trip_after": snapshot["trip"],
                "pending_before": before_snapshot.get("pending_question"),
                "pending_after": snapshot.get("pending_question"),
                "changes": ((run or {}).get("result") or {}).get("changes", []),
                "assistant_message": ((run or {}).get("result") or {}).get("reply"),
                "response_source": nested.get("response_source"),
            })
            record["turns"].append(turn_record)
            if turn_record["error_code"] in {"RATE_LIMITED", "HTTP_ERROR", "CONFIG_MISSING", "MODEL_BUDGET_EXHAUSTED", "MODEL_COOLDOWN", "REQUEST_TIMEOUT", "NETWORK_ERROR"}:
                # Provider availability is not language accuracy. Stop on the
                # first terminal outage; never burn a whole free quota retrying
                # the same unavailable upstream for unrelated cases.
                turn_record["status"] = "BLOCKED_PROVIDER"
                record["status"] = "BLOCKED_PROVIDER"
                self.provider_blocked = True
                print(json.dumps({"case": case["id"], **{k: v for k, v in turn_record.items() if k != "observation"}}, ensure_ascii=False), flush=True)
                break
            if failures:
                record["status"] = "FAIL"
            print(json.dumps({"case": case["id"], **{k: v for k, v in turn_record.items() if k != "observation"}}, ensure_ascii=False), flush=True)
        if record["status"] != "PASS":
            if case.get("after_actions"):
                record["after_actions"] = [{"status": "NOT_EXECUTED_DEPENDENCY_FAILED"}]
            return record
        for action in case.get("after_actions", []):
            if action["action"] != "confirm_original_quote" or not old_quote:
                record["status"] = "NOT_EXECUTED_SETUP"
                continue
            snapshot = self.get(path, token)
            code, _ = self.request("POST", path + "/checkout/confirm", token, {
                "quote_id": old_quote["quote_id"], "quote_version": old_quote["quote_version"],
                "state_version": snapshot["state_version"], "idempotency_key": str(uuid4()), "confirmed": True})
            orders = self.get(path + "/orders", token)["orders"]
            okay = code == action["expected_http_status"] and len(orders) == action["expected_orders"]
            record["after_actions"] = [{"action": action["action"], "passed": okay, "http_status": code}]
            if not okay:
                record["status"] = "FAIL"
        return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-free", action="store_true")
    parser.add_argument("--label", default="initial")
    parser.add_argument("--base-url", default="http://localhost:8081")
    parser.add_argument("--cases", default="", help="Explicit comma-separated case IDs for a justified targeted rerun")
    parser.add_argument("--attempt-cap", type=int, default=12)
    args = parser.parse_args()
    if not args.confirm_free:
        print("No model calls. Explicit --confirm-free is required.")
        return 2
    if sys.platform == "win32":
        path = str(Path(__file__).resolve())
        script = "/mnt/" + path[0].lower() + path[2:].replace("\\", "/")
        command = ["wsl.exe", "-d", "AegML-Hackathon", "-u", "aegml", "--",
            "/home/aegml/venvs/otravel-harness-20261025/bin/python", script, "--confirm-free",
            "--label", args.label, "--base-url", args.base_url, "--attempt-cap", str(args.attempt_cap)]
        if args.cases:
            command.extend(["--cases", args.cases])
        return subprocess.run(command, check=False).returncode
    if not args.label.replace("-", "").isalnum():
        raise ValueError("Use a simple label")
    root = Path("/mnt/e/work/aegml")
    sys.path.insert(0, str(root))
    fixture = root / "fixtures/evaluation/llm-dialogues.json"
    raw = fixture.read_bytes()
    suite = json.loads(raw)
    source_case_count = len(suite["cases"])
    selected = [part.strip() for part in args.cases.split(",") if part.strip()]
    if selected:
        if set(selected) - {case["id"] for case in suite["cases"]}:
            raise ValueError("Unknown requested case IDs")
        suite["cases"] = [case for case in suite["cases"] if case["id"] in selected]
    if not 1 <= args.attempt_cap <= 12:
        raise ValueError("Attempt cap must be 1..12")
    suite["max_actual_model_http_attempts"] = min(suite["max_actual_model_http_attempts"], args.attempt_cap)
    target = root / ("docs/verification/llm-acceptance-20261025-" + args.label + ".json")
    if target.exists():
        print("This report already exists; original results are immutable. Choose an explicit new label for a justified rerun.")
        return 2
    # Exclusive reservation also prevents two identical CLI invocations from
    # starting the same suite concurrently.
    try:
        with target.open("x", encoding="utf-8") as stream:
            stream.write("{}\n")
    except FileExistsError:
        return 2
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "fixture_sha256": hashlib.sha256(raw).hexdigest(),
              "suite_id": suite["suite_id"], "selected_case_ids": [case["id"] for case in suite["cases"]],
              "source_fixture_cases": source_case_count, "attempt_cap": suite["max_actual_model_http_attempts"],
              "cases": [], "scope": "selected actual model HTTP turns; not the entire106 corpus"}
    evaluation = Evaluation(args.base_url, suite, report)
    try:
        for case in suite["cases"]:
            if getattr(evaluation, "provider_blocked", False):
                report["cases"].append({"id": case["id"], "status": "NOT_EXECUTED_PROVIDER_UNAVAILABLE"})
                continue
            evaluation.current_record = {"id": case["id"], "turns": []}
            try:
                result = evaluation.run_case(case)
            except Exception as exc:
                result = getattr(evaluation, "current_record", {"id": case["id"], "turns": []})
                result.update(status="ERROR", error_class=type(exc).__name__)
            report["cases"].append(result)
            # A failed assertion/connection must not erase already reserved
            # provider attempts. If accounting is unavailable, stop, never guess.
            evaluation.attempts = evaluation.count_attempts()
            report["actual_model_attempts"] = evaluation.attempts
            target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["status"] = "PASS" if all(c["status"] == "PASS" for c in report["cases"]) and len(report["cases"]) == len(suite["cases"]) else "NOT_ALL_PASS"
        report["actual_model_attempts"] = evaluation.attempts
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(target), "status": report["status"], "attempts": evaluation.attempts}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
