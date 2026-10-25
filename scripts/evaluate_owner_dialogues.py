"""Frozen owner-dialogue checks against an actual loopback backend.

No keys/environment files are read. Without --confirm-inference (or with
--dry-run), no HTTP request is made. A real run must be authorized only after
the corrected backend has been synchronized/restarted. This evaluates the
combined harness: contextual slots and server guards are not raw-model NLU.
"""

from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures/evaluation/frozen-owner-dialogues.json"
ORACLE = ROOT / "tests/test_owner_dialogues.py"
ORACLE_FUNCTIONS = {"_pointer", "_assert_trip", "_requested_country", "_assert_question"}
PROVIDER_ERRORS = {"RATE_LIMITED", "HTTP_ERROR", "CONFIG_MISSING", "MODEL_BUDGET_EXHAUSTED",
                   "MODEL_COOLDOWN", "REQUEST_TIMEOUT", "NETWORK_ERROR", "CONFIG_BLOCKED"}
TERMINAL_RUNS = {"completed", "failed", "conflict", "interrupted", "cancelled"}
PRIVATE = {"session_token", "owner_token_hash", "authorization", "headers", "input_text", "request_hash",
           "api_key", "apikey", "secret", "password", "access_token", "refresh_token", "database_url"}
UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)


def now():
    return datetime.now(timezone.utc).isoformat()


def load_oracle():
    """Reuse only the exact pure assertions; never import/execute a test adapter.

    Selecting these AST definitions avoids importing pytest, Store, app fixtures
    or WrongSlotLLM into this stdlib-only HTTP client. The entire source hash is
    recorded so subsequent oracle edits cannot be mistaken for the same run.
    """
    source = ORACLE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(ORACLE))
    selected = [node for node in tree.body if
                isinstance(node, ast.FunctionDef) and node.name in ORACLE_FUNCTIONS or
                isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_RAW_FIELD" for t in node.targets)]
    if {node.name for node in selected if isinstance(node, ast.FunctionDef)} != ORACLE_FUNCTIONS:
        raise ValueError("Frozen oracle functions are missing")
    namespace = {"re": re}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(ORACLE), "exec"), namespace)
    return namespace


class Evidence:
    def __init__(self):
        self.tokens: set[str] = set()
        self.identities: dict[str, str] = {}

    def identity(self, value, prefix="id"):
        if isinstance(value, str) and value:
            self.identities.setdefault(value, f"{prefix}-{len(self.identities) + 1}")

    def clean(self, value):
        if isinstance(value, dict):
            return {key: self.clean(item) for key, item in value.items()
                    if key.casefold().replace("-", "_") not in PRIVATE
                    and not key.casefold().endswith(("_api_key", "_api_secret", "_access_token"))}
        if isinstance(value, list):
            return [self.clean(item) for item in value]
        if isinstance(value, str):
            for token in self.tokens:
                value = value.replace(token, "[REDACTED]")
            for identifier, alias in sorted(self.identities.items(), key=lambda pair: -len(pair[0])):
                value = value.replace(identifier, alias)
            value = re.sub(r"/api/v1/sessions/[^\s/?#\"']+", "/api/v1/sessions/{session_id}", value)
            value = re.sub(r"/api/v1/runs/[^\s/?#\"']+", "/api/v1/runs/{run_id}", value)
            def replace(match):
                self.identity(match.group())
                return self.identities[match.group()]
            return UUID.sub(replace, value)
        return value


class LocalHTTP:
    def __init__(self, base):
        parsed = urlparse(base)
        if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
                or parsed.port not in {8081, 8083} or parsed.path not in {"", "/"}
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("Only loopback HTTP ports 8081/8083 are supported")
        self.base = base.rstrip("/")
        self.opener = build_opener(ProxyHandler({}))

    def request(self, method, route, token=None, body=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = Request(self.base + route, method=method, headers=headers,
            data=None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        try:
            with self.opener.open(request, timeout=10) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            # Error bodies/headers and credential-bearing URLs are not evidence.
            return error.code, {}
        except (URLError, TimeoutError, OSError):
            raise RuntimeError("loopback_backend_unavailable") from None

    def get(self, route, token=None):
        status, body = self.request("GET", route, token)
        if status != 200 or not isinstance(body, dict):
            raise RuntimeError("read_http_" + str(status))
        return body

    def wait(self, run_id, token):
        deadline = time.monotonic() + 130
        while time.monotonic() < deadline:
            run = self.get("/api/v1/runs/" + run_id, token)
            if run.get("status") in TERMINAL_RUNS:
                return run
            time.sleep(0.25)
        raise TimeoutError("actual_run_timeout_stop_no_further_inference")


class Evaluation:
    def __init__(self, http, oracle, evidence, folder, report, cap):
        self.http, self.oracle, self.evidence = http, oracle, evidence
        self.folder, self.report, self.cap = folder, report, cap
        self.sessions: list[tuple[str, str]] = []
        self.stop = None
        self.reserve_per_turn = 2  # Fail closed until provider metadata confirms local single attempt.

    def checkpoint(self):
        target = ROOT / self.report["report"]
        target.write_text(json.dumps(self.evidence.clean(self.report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def events(self, route, token):
        return self.http.get(route + "/events", token)["events"]

    def attempts(self):
        # Only sessions created by this invocation; no global quota is guessed.
        return sum(row["event_type"] == "MODEL_CALL_STARTED"
                   for route, token in self.sessions for row in self.events(route, token))

    def check(self, checks, name, function):
        try:
            function()
            checks.append({"name": name, "passed": True})
        except (AssertionError, KeyError, IndexError, TypeError, ValueError) as error:
            # Assert messages contain only frozen field paths/questions; other
            # exceptions log a class, never arbitrary backend response content.
            checks.append({"name": name, "passed": False,
                           "reason": str(error) if isinstance(error, AssertionError) else type(error).__name__})

    def run_case(self, case):
        record = {"id": case["id"], "title": case["title"], "status": "IN_PROGRESS", "turns": []}
        self.report["cases"].append(record)
        status, created = self.http.request("POST", "/api/v1/sessions", body={"initial_trip": case["initial_trip"]})
        if status not in {200, 201}:
            raise RuntimeError("session_create_http_" + str(status))
        token, sid = created["session_token"], created["session_id"]
        self.evidence.tokens.add(token)
        self.evidence.identity(sid, "session")
        self.evidence.identity(created["trip"]["trip_id"], "trip")
        route = "/api/v1/sessions/" + sid
        self.sessions.append((route, token))
        record["session_alias"] = self.evidence.identities[sid]
        baseline = created["trip"]
        for index, turn in enumerate(case["turns"], 1):
            if self.stop:
                record["turns"].append({"index": index, "status": "NOT_EXECUTED", "reason": self.stop})
                continue
            remaining = self.cap - self.attempts()
            if remaining < self.reserve_per_turn:
                self.stop = "ATTEMPT_CAP_GUARD"
                record["turns"].append({"index": index, "status": "NOT_EXECUTED", "reason": self.stop})
                continue
            before = self.http.get(route, token)
            record["turns"].append({"index": index, "text": turn["text"], "status": "IN_PROGRESS", "state_before": before})
            self.checkpoint()
            start = time.monotonic()
            status, accepted = self.http.request("POST", route + "/messages", token, {
                "client_message_id": str(uuid4()), "expected_version": before["state_version"], "text": turn["text"]})
            if status != 202:
                raise RuntimeError("intake_http_" + str(status))
            run_id = accepted["run_id"]
            self.evidence.identity(run_id, "run")
            record["turns"][-1]["run_alias"] = self.evidence.identities[run_id]
            self.checkpoint()
            run = self.http.wait(run_id, token)
            after = self.http.get(route, token)
            history = self.http.get(route + "/messages", token)["messages"]
            events = self.events(route, token)
            current_events = [event for event in events if event.get("run_id") == run_id]
            orders = self.http.get(route + "/orders", token)["orders"]
            replies = [row["text"] for row in history if row["role"] == "assistant" and row.get("run_id") == run_id]
            reply = replies[0] if len(replies) == 1 else ""
            result = (run.get("result") or {}).get("result") or {}
            source = result.get("response_source", "not_reported")
            calls = [event for event in current_events if event["event_type"] == "MODEL_CALL_STARTED"]
            model_meta = run.get("llm") or result.get("llm")
            checks = []
            def require(condition, message):
                assert condition, message
            self.check(checks, "completed_actual_run", lambda: require(run["status"] == "completed", "Turn did not complete"))
            self.check(checks, "forbidden_error_codes", lambda: require((run.get("error") or {}).get("code") not in turn["expect"].get("run_error_forbidden", []), "Forbidden extraction error"))
            self.check(checks, "frozen_trip_assertions", lambda: self.oracle["_assert_trip"](after["trip"], baseline, turn["expect"].get("assertions", [])))
            expected = turn["expect"]
            for key in ("session_status", "waiting_reason"):
                if key in expected:
                    self.check(checks, key, lambda key=key: require(after["status" if key == "session_status" else key] == expected[key], "Unexpected " + key))
            if expected.get("pending_country_code"):
                self.check(checks, "pending_country", lambda: require(expected["pending_country_code"] == self.oracle["_requested_country"](after.get("pending_question")), "Requested country lost"))
            if "adult_count" in expected:
                self.check(checks, "adult_count", lambda: require(sum(person["type"] == "adult" for person in after["trip"]["travelers"]) == expected["adult_count"], "Adult count was lost or duplicated"))
            self.check(checks, "one_durable_assistant_reply", lambda: require(len(replies) == 1, "Expected one persisted assistant reply"))
            self.check(checks, "frozen_human_question", lambda: self.oracle["_assert_question"](after, reply, expected["question"]))
            ids = {sid, run_id, after["trip"]["trip_id"]}
            for collection in ("travelers", "groups", "stays", "legs"):
                for item in after["trip"].get(collection, []):
                    ids.add(item["id"])
                    ids.update(room["id"] for room in item.get("rooms", []))
            visible = [reply, *((after.get("pending_question") or {}).get("questions") or [])]
            self.check(checks, "no_internal_ids_in_chat", lambda: require(not any(
                re.search(r"(?<!\w)" + re.escape(identifier) + r"(?!\w)", text)
                for identifier in ids if len(identifier) >= 4 for text in visible), "Internal stable ID leaked into chat"))
            if "known_geography" in expected:
                geo = expected["known_geography"]
                self.check(checks, "frozen_known_geography", lambda: require(
                    "стамбул" in reply.casefold() and "турц" in reply.casefold() if geo["country_code"] == "TR"
                    else "белград" in reply.casefold(), "Known geography absent from actual reply"))
            self.check(checks, "no_order_created", lambda: require(orders == [], "Unexpected order"))
            self.check(checks, "no_supplier_tool_calls", lambda: require(not any(event["event_type"] == "TOOL_CALL_STARTED" for event in current_events), "Clarification called a search tool"))
            self.check(checks, "no_stub_model_provider", lambda: require(all(event.get("payload", {}).get("provider") in {"ollama", "openrouter", "gemini"} for event in calls), "A test adapter cannot qualify actual model evaluation"))
            if calls:
                self.check(checks, "actual_model_metadata", lambda: require(isinstance(model_meta, dict) and bool(model_meta.get("model")) and bool(model_meta.get("provider")), "Actual model metadata missing"))
            if source == "contextual_slot":
                self.check(checks, "slot_has_no_model_attempt", lambda: require(not calls, "Typed contextual slot unexpectedly consumed a model call"))
            elif source == "llm":
                self.check(checks, "llm_has_actual_attempt", lambda: require(bool(calls), "LLM source has no actual started event"))
            else:
                self.check(checks, "response_source_reported", lambda: require(False, "Backend must distinguish contextual slot from LLM"))
            classification = "contextual_slot" if source == "contextual_slot" else "llm_extraction_with_server_guards" if calls else "unknown"
            evidence_name = f"{case['id']}-turn-{index:02d}.json"
            evidence_path = self.folder / evidence_name
            detail = {"case_id": case["id"], "turn_index": index, "request_text": turn["text"],
                "state_before": before, "state_after": after, "actual_run": run,
                "messages": history, "current_run_events": current_events, "orders": orders,
                "http_routes": ["POST /api/v1/sessions/{session_id}/messages", "GET /api/v1/runs/{run_id}",
                                "GET /api/v1/sessions/{session_id}", "GET /api/v1/sessions/{session_id}/messages",
                                "GET /api/v1/sessions/{session_id}/events", "GET /api/v1/sessions/{session_id}/orders"]}
            with evidence_path.open("x", encoding="utf-8") as stream:
                json.dump(self.evidence.clean(detail), stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            turn_record = {"index": index, "text": turn["text"], "status": "PASS" if all(c["passed"] for c in checks) else "FAIL",
                "checks": checks, "response_source": source, "evaluation_path": classification,
                "actual_model_attempts": len(calls), "actual_model_metadata": model_meta,
                "elapsed_seconds": round(time.monotonic() - start, 3), "assistant_text": reply,
                "state_before": before, "state_after": after,
                "evidence_paths": [str(evidence_path.relative_to(ROOT)).replace("\\", "/")]}
            record["turns"][-1] = turn_record
            error = (run.get("error") or {}).get("code")
            if error in PROVIDER_ERRORS:
                self.stop = "PROVIDER_UNAVAILABLE"
                turn_record["status"] = "BLOCKED_PROVIDER"
            elif run["status"] != "completed":
                # Subsequent dependent turns cannot qualify a failed intake.
                for rest in range(index + 1, len(case["turns"]) + 1):
                    record["turns"].append({"index": rest, "status": "NOT_EXECUTED", "reason": "DEPENDENCY_FAILED"})
                break
            self.checkpoint()
            print(json.dumps({"case": case["id"], "turn": index, "status": turn_record["status"],
                              "source": classification, "attempts": len(calls)}, ensure_ascii=False), flush=True)
        record["status"] = "PASS" if len(record["turns"]) == len(case["turns"]) and all(turn["status"] == "PASS" for turn in record["turns"]) else "NOT_ALL_PASS"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8083")
    parser.add_argument("--label", required=True, help="Unique simple label; existing reports are never overwritten")
    parser.add_argument("--cases", "--case-ids", default="", help="Comma-separated frozen case IDs")
    parser.add_argument("--attempt-cap", type=int, default=12)
    parser.add_argument("--confirm-inference", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Validate fixture/oracle and print plan; no HTTP or inference")
    args = parser.parse_args(argv)
    try:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", args.label) or not 1 <= args.attempt_cap <= 12:
            raise ValueError("Use a unique simple label and attempt cap 1..12")
        http = LocalHTTP(args.base_url)
        raw = FIXTURE.read_bytes()
        suite, oracle = json.loads(raw), load_oracle()
        selected = {value.strip() for value in args.cases.split(",") if value.strip()}
        if selected - {case["id"] for case in suite["cases"]}:
            raise ValueError("Unknown frozen case IDs")
        cases = [case for case in suite["cases"] if not selected or case["id"] in selected]
        date = datetime.now(timezone.utc).strftime("%Y%m%d")
        target = ROOT / f"docs/verification/owner-dialogues-{date}-{args.label}.json"
        folder = target.with_suffix("")
        if target.exists() or folder.exists():
            raise ValueError("Report label already exists; original evidence is immutable")
        plan = {"mode": "DRY_RUN_NO_HTTP", "fixture": str(FIXTURE.relative_to(ROOT)).replace("\\", "/"),
                "fixture_sha256": hashlib.sha256(raw).hexdigest(), "oracle_sha256": hashlib.sha256(ORACLE.read_bytes()).hexdigest(),
                "selected_case_ids": [case["id"] for case in cases], "turns": sum(len(case["turns"]) for case in cases),
                "attempt_cap": args.attempt_cap, "base_url": http.base, "report": str(target.relative_to(ROOT)).replace("\\", "/"),
                "oracle_functions": sorted(ORACLE_FUNCTIONS), "stub_execution": False}
        if args.dry_run or not args.confirm_inference:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 0 if args.dry_run else 2
        target.parent.mkdir(parents=True, exist_ok=True)
        # Reserve both unique outputs before any network call; no retry/resume
        # can accidentally repeat an inference under an already-used label.
        with target.open("x", encoding="utf-8") as stream:
            stream.write('{"status":"IN_PROGRESS"}\n')
        folder.mkdir()
        report = {**plan, "mode": "ACTUAL_HTTP_HARNESS_EVALUATION", "status": "IN_PROGRESS", "started_at": now(),
                  "cases": [], "scope": "Three owner dialogues, not the original106 corpus or raw-model NLU. Server guards may override LLM output; contextual slots are counted separately.",
                  "model_calls_authorization": "Explicit --confirm-inference, backend owns credentials and durable budgets"}
        evidence = Evidence()
        evaluation = Evaluation(http, oracle, evidence, folder, report, args.attempt_cap)
        try:
            health = http.get("/health")
            if health.get("database") != "ready":
                raise RuntimeError("backend_database_not_ready")
            providers = http.get("/api/v1/providers/status")
            report["runtime_provider_metadata"] = providers.get("_runtime", {})
            if providers.get("_runtime", {}).get("llm_provider") == "ollama":
                evaluation.reserve_per_turn = 1
            report["conservative_attempt_reservation_per_turn"] = evaluation.reserve_per_turn
            for case in cases:
                if evaluation.stop:
                    report["cases"].append({"id": case["id"], "status": "NOT_EXECUTED", "reason": evaluation.stop, "turns": []})
                else:
                    evaluation.run_case(case)
                report["actual_model_attempts"] = evaluation.attempts()
                target.write_text(json.dumps(evidence.clean(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception as error:
            report["execution_error_class"] = type(error).__name__
            report["execution_stopped"] = True
        finally:
            try:
                report["actual_model_attempts"] = evaluation.attempts()
            except Exception:
                report["attempt_accounting_unavailable"] = True
            report["finished_at"] = now()
            turns = [turn for case in report["cases"] for turn in case.get("turns", [])]
            report["response_path_counts"] = {path: sum(turn.get("evaluation_path") == path for turn in turns)
                for path in ("contextual_slot", "llm_extraction_with_server_guards", "unknown")}
            report["status"] = "PASS_HARNESS_DIALOGUES" if (not report.get("execution_stopped") and
                len(report["cases"]) == len(cases) and all(case["status"] == "PASS" for case in report["cases"])) else "NOT_ALL_PASS"
            target.write_text(json.dumps(evidence.clean(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"report": str(target), "status": report["status"],
                          "attempts": report.get("actual_model_attempts"), "paths": report["response_path_counts"]}, ensure_ascii=False))
        return 0 if report["status"] == "PASS_HARNESS_DIALOGUES" else 1
    except Exception as error:
        print(json.dumps({"status": "NOT_STARTED", "error_class": type(error).__name__}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
