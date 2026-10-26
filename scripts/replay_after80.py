"""Guarded fixed-transcript replay, never an automatic runtime controller.

Without --run this only reads/fingerprints the immutable 80-dialogue source.
No model keys, dotenv files, runtime managers, Docker or DB clients are used.
Case-specific behavioral oracles are UNBOUND; typed checks are not 80 NLU PASS.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".local/evaluation/zai-full100-20261026/dialogues.jsonl"
SOURCE_SHA = "81a7e59c65d41da8dfc6101fc0429c455c259561e44f6a9a79f0dc99323058ca"
RUNTIME_EVIDENCE = ROOT / "docs/verification/after80-backend-20261026.json"
BASE = "http://127.0.0.1:8084"
LLM_PROFILES = {
    "zai": {"provider": "zai", "model": "glm-4.5-flash", "manager": "after80_backend.py",
        "evidence": "docs/verification/after80-backend-20261026.json"},
    "cloudflare-nemotron": {"provider": "cloudflare", "model": "@cf/nvidia/nemotron-3-120b-a12b",
        "manager": "after80_cf_backend.py", "evidence": "docs/verification/after80-cf-backend-20261026.json"},
    "openrouter-dots": {"provider": "openrouter", "model": "dots-studio/dots-3-note-preview:free",
        "manager": "after80_router_backend.py", "evidence": "docs/verification/after80-router-backend-20261026.json"},
    "groq-qwen": {"provider": "groq", "model": "qwen/qwen3.8-27b",
        "manager": "after80_groq_backend.py", "evidence": "docs/verification/after80-groq-backend-20261026.json"},
}
TOOL_RESERVATION = 16  # The verified TripSearch per-run bound, not vendor HTTP requests.
TERMINAL = {"completed", "failed", "conflict", "interrupted", "cancelled"}
BLOCKS = {"RATE_LIMITED", "SOURCE_COOLDOWN", "MODEL_COOLDOWN", "MODEL_BUDGET_EXHAUSTED", "REQUEST_TIMEOUT", "NETWORK_ERROR", "HTTP_ERROR"}
PRIVATE = {"session_token", "token", "secret", "owner_token_hash", "authorization", "headers", "request_headers", "api_key",
    "api_secret", "password", "access_token", "refresh_token", "database_url", "client_message_id", "idempotency_key"}
INITIAL_TRIP = {"search_policy": {"mode": "HYBRID", "hotel_provider": "liteapi", "flight_provider": "duffel",
    "demo_fallback": True, "guest_nationality": "RU"}}


def require(condition, code):
    if not condition:
        raise ValueError(code)


def utcnow():
    return datetime.now(UTC).isoformat()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def load_source():
    raw = SOURCE.read_bytes()
    require(digest(raw) == SOURCE_SHA, "immutable_source_sha_mismatch")
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    require(len(rows) == 80 and {row["id"] for row in rows} == {f"ZAI100-{n:03d}" for n in range(1, 81)}, "only_original_001_to_080_allowed")
    require(sum(len(row["turns"]) for row in rows) == 697, "original_697_user_turns_required")
    for row in rows:
        require(row.get("initial_trip_seeded") is False, "source_contains_seeded_trip")
        require([turn["turn"] for turn in row["turns"]] == list(range(1, len(row["turns"]) + 1)), "source_turn_order_invalid")
        require(all(isinstance(turn.get("user"), str) and turn["user"].strip() for turn in row["turns"]), "source_user_text_invalid")
    return sorted(rows, key=lambda row: row["id"])


def validate_limits(dialogues, parallel, models, tools, start):
    for value, low, high in ((dialogues, 1, 80), (parallel, 1, 2), (models, 0, 900), (tools, 0, 600), (start, 1, 80)):
        require(type(value) is int and low <= value <= high, "engineering_limit_out_of_range")
    require(start + dialogues - 1 <= 80, "selection_exceeds_original_080")


def validate_message_interval(value):
    require(type(value) in {int, float} and math.isfinite(value) and 0 <= value <= 120,
        "message_interval_out_of_range")


def plan(rows, *, dialogues=2, parallel=1, models=40, tools=100, start=1, actions=False, llm_profile="zai",
         message_interval_seconds=0):
    require(llm_profile in LLM_PROFILES, "unsupported_fixed_llm_profile")
    profile = LLM_PROFILES[llm_profile]
    validate_limits(dialogues, parallel, models, tools, start)
    validate_message_interval(message_interval_seconds)
    selected = rows[start - 1:start - 1 + dialogues]
    require(len(selected) == dialogues, "selection_missing_source_cases")
    require(all(row["id"] == f"ZAI100-{start + index:03d}" for index, row in enumerate(selected)), "source_selection_id_mismatch")
    return {"format": "otravel.after80.replay.v1", "source_sha256": SOURCE_SHA,
        "selected_ids": [row["id"] for row in selected], "selected_user_turns": sum(len(row["turns"]) for row in selected),
        "source_dialogues": 80, "source_user_turns": 697, "base_url": BASE, "parallel": parallel,
        "llm_profile": llm_profile, "expected_runtime": {"llm_provider": profile["provider"], "model": profile["model"]},
        "runtime_evidence": profile["evidence"],
        "message_interval_seconds": message_interval_seconds,
        "max_model_calls": models, "max_tool_calls": tools, "tool_reservation_per_message": TOOL_RESERVATION,
        "include_simulated_checkout": actions, "synthetic_initial_trip": deepcopy(INITIAL_TRIP),
        "nationality_provenance": "EXPLICIT_SYNTHETIC_TEST_FIXTURE_NOT_INFERRED_FROM_LANGUAGE_OR_ORIGIN",
        "behavioral_status": "UNBOUND_FIXED_REPLAY_NOT_FRESH_ADAPTIVE", "provider_remaining": "UNKNOWN",
        "native_budget_shared": True, "native_budget_remaining": "UNKNOWN"}


class Cleaner:
    def __init__(self):
        self.tokens = []
        self.lock = threading.Lock()

    def add(self, token):
        with self.lock:
            self.tokens.append(token)

    def clean(self, value):
        if isinstance(value, dict):
            return {key: self.clean(item) for key, item in value.items()
                if key.casefold().replace("-", "_") not in PRIVATE
                and not key.casefold().endswith(("_api_key", "_api_secret", "_access_token"))}
        if isinstance(value, list):
            return [self.clean(item) for item in value]
        if isinstance(value, str):
            with self.lock:
                tokens = tuple(self.tokens)
            for token in tokens:
                value = value.replace(token, "[REDACTED]").replace(quote(token, safe=""), "[REDACTED]")
            value = re.sub(r"(?i)Bearer\s+[^\s\"']+", "Bearer [REDACTED]", value)
            value = re.sub(r"(https?://[^\s?\"']+)\?[^\s\"']+", r"\1?[QUERY_REDACTED]", value)
            return value
        return value


def private_json(path, value):
    require(os.name == "posix", "actual_replay_requires_posix_private_output")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(not path.parent.is_symlink() and stat.S_IMODE(path.parent.stat().st_mode) == 0o700,
        "output_directory_must_be_private_0700")
    temporary = path.with_name(path.name + ".tmp-" + uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        require(stat.S_IMODE(temporary.stat().st_mode) == 0o600, "private_file_mode_unavailable")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class Budget:
    def __init__(self, model_limit, tool_limit, saved=None):
        self.model_limit, self.tool_limit = model_limit, tool_limit
        self.models, self.tools, self.held = 0, 0, {}
        self.lock = threading.Lock()
        if saved:
            self.models, self.tools, self.held = saved["models"], saved["tools"], saved["held"]
            require(type(self.models) is int and type(self.tools) is int and self.models >= 0 and self.tools >= 0
                and isinstance(self.held, dict) and all(isinstance(row, list) and len(row) == 2
                    and all(type(value) is int and value >= 0 for value in row) for row in self.held.values()),
                "resume_budget_snapshot_invalid")
            require(self.models + sum(row[0] for row in self.held.values()) <= model_limit
                and self.tools + sum(row[1] for row in self.held.values()) <= tool_limit, "resume_budget_exceeds_cap")

    def reserve(self, key, models, tools):
        with self.lock:
            require(key not in self.held, "duplicate_budget_reservation")
            if (self.models + sum(row[0] for row in self.held.values()) + models > self.model_limit
                    or self.tools + sum(row[1] for row in self.held.values()) + tools > self.tool_limit):
                raise ValueError("engineering_budget_admission_blocked")
            self.held[key] = [models, tools]

    def settle(self, key, models, tools):
        with self.lock:
            reserved = self.held[key]
            require(0 <= models <= reserved[0] and 0 <= tools <= reserved[1], "runtime_exceeded_verified_per_run_bound")
            self.models += models
            self.tools += tools
            del self.held[key]

    def snapshot(self):
        with self.lock:
            return {"models": self.models, "tools": self.tools, "held": deepcopy(self.held)}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        return None


class PostPacer:
    """One process-wide POST schedule, not a claim about vendor token quota.

    GET/poll has no delay. All POSTs, including local zero-model slot answers,
    use the same spacing; stopped/unknown runs are never automatically resent.
    """
    def __init__(self, interval=0, *, clock=None, sleep=None, stop=None, resume=False):
        validate_message_interval(interval)
        self.interval, self.clock, self.sleep = interval, clock or time.monotonic, sleep or time.sleep
        self.stop, self.lock = stop, threading.Lock()
        self.last_post = self.clock() if resume and interval else None

    def before_post(self):
        with self.lock:
            while True:
                if self.stop is not None and self.stop.is_set():
                    raise RuntimeError("REPLAY_STOPPED_BEFORE_WRITE")
                remaining = 0 if self.last_post is None else self.last_post + self.interval - self.clock()
                if remaining <= 0:
                    self.last_post = self.clock()
                    return self.last_post
                self.sleep(min(10, remaining))


_POST_PACER = PostPacer()


class HTTP:
    def __init__(self, base=BASE):
        parsed = urlsplit(base)
        require(parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            and parsed.port == 8084 and parsed.path in {"", "/"} and not parsed.username and not parsed.password
            and not parsed.query and not parsed.fragment, "only_loopback_after80_8084_allowed")
        self.base = base.rstrip("/")
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, method, path, token=None, body=None):
        require(path.startswith(("/api/v1/sessions", "/api/v1/runs/", "/api/v1/providers/status", "/health"))
            and not any(symbol in path for symbol in ("?", "#", "\\", "..")), "unexpected_local_route")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        encoded = None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
        request = Request(self.base + path, data=encoded, headers=headers, method=method)
        if method == "POST":
            _POST_PACER.before_post()
        started = time.monotonic()
        try:
            with self.opener.open(request, timeout=15) as response:
                code, raw, retry = response.status, response.read(20_000_001), response.headers.get("Retry-After")
        except HTTPError as error:
            code, raw, retry = error.code, error.read(20_000_001), error.headers.get("Retry-After")
        except (URLError, TimeoutError, OSError):
            raise RuntimeError("UNKNOWN_WRITE_NO_RESEND" if method == "POST" else "READ_UNAVAILABLE_NO_WRITE_RETRY") from None
        require(len(raw) <= 20_000_000, "response_size_exceeded")
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError):
            data = {"non_json_body_sha256": digest(raw)}
        return {"status": code, "body": data, "retry_after": retry, "elapsed_seconds": round(time.monotonic() - started, 3)}

    def get(self, path, token=None):
        response = self.request("GET", path, token)
        require(response["status"] == 200 and isinstance(response["body"], dict), "local_read_failed_" + str(response["status"]))
        return response["body"]

    def wait(self, run_id, token, deadline=300):
        until = time.monotonic() + deadline
        while time.monotonic() < until:
            run = self.get("/api/v1/runs/" + quote(run_id, safe=""), token)
            if run.get("status") in TERMINAL:
                return run
            time.sleep(0.5)
        raise RuntimeError("UNKNOWN_RUNNING_RUN_NO_RESEND")


def typed_invariants(before, after, run, orders):
    checks = []

    def check(name, condition):
        checks.append({"id": name, "status": "PASS" if condition else "FAIL"})

    check("own_session_identity", after.get("session_id") == before.get("session_id") == run.get("session_id"))
    check("trip_identity_stable", after.get("trip_id") == before.get("trip_id"))
    trip = after.get("trip") or {}
    check("wrapper_state_version_consistent", type(after.get("state_version")) is int
        and trip.get("state_version") == after.get("state_version") and after["state_version"] >= before["state_version"])
    people, stays, groups = trip.get("travelers", []), trip.get("stays", []), trip.get("groups", [])
    people_ids = {person.get("id") for person in people}
    group_ids = {group.get("id") for group in groups}
    check("unique_people_and_stays", None not in people_ids and len(people_ids) == len(people)
        and len({stay.get("id") for stay in stays}) == len(stays))
    refs = all(person.get("group_id") is None or person["group_id"] in group_ids for person in people)
    refs = refs and all(set(group.get("traveler_ids", [])) <= people_ids for group in groups)
    for stay in stays:
        assigned = [value for room in stay.get("rooms", []) for value in room.get("traveler_ids", [])]
        refs = refs and set(assigned) <= people_ids and len(assigned) == len(set(assigned))
    check("saved_references_exist_without_double_allocation", refs)
    dates = True
    for stay in stays:
        try:
            start = date.fromisoformat(stay["check_in"]) if stay.get("check_in") else None
            end = date.fromisoformat(stay["check_out"]) if stay.get("check_out") else None
            dates = dates and (start is None or end is None or start < end)
        except (ValueError, TypeError):
            dates = False
    check("stored_stay_dates_valid", dates)
    check("no_supplier_orders", all(order.get("is_demo") is True and order.get("booking_created_at_supplier") is False for order in orders))
    check("chat_did_not_create_order_without_typed_confirmation", orders == [])
    if run.get("status") == "failed":
        check("failed_run_keeps_domain_state", trip == before.get("trip"))
    return checks


def provider_block(run, events):
    error = run.get("error") or {}
    if error.get("code") in BLOCKS:
        return {"code": error["code"], "details": error.get("details", {}), "llm": error.get("llm", {})}
    for row in events:
        payload = row.get("payload") or {}
        if row.get("event_type") in {"TOOL_CALL_FAILED", "MODEL_CALL_FAILED", "MODEL_SKIPPED_COOLDOWN"} and payload.get("code") in BLOCKS:
            return {"code": payload["code"], "details": payload}
    return None


def check_profile(health, status, evidence, llm_profile="zai"):
    require(llm_profile in LLM_PROFILES, "unsupported_fixed_llm_profile")
    profile = LLM_PROFILES[llm_profile]
    require(all(isinstance(value, dict) for value in (health, status, evidence)), "runtime_metadata_malformed")
    require(all(isinstance(value, dict) for value in status.values()), "runtime_provider_metadata_malformed")
    require(isinstance(evidence.get("limits"), dict) and isinstance(evidence.get("loaded_manifest"), dict),
        "runtime_evidence_metadata_malformed")
    require(health.get("database") == "ready" and health.get("supplier_booking") is False, "backend_health_or_booking_guard_failed")
    require(status.get("_runtime") == {"llm_provider": profile["provider"], "model": profile["model"]}, "fixed_llm_runtime_profile_mismatch")
    selected = status.get(profile["provider"], {})
    require(selected.get("configured") is True and selected.get("paid_fallback") is False,
        "selected_free_llm_profile_not_confirmed")
    if llm_profile == "openrouter-dots":
        require(selected.get("model") == profile["model"] and profile["model"].endswith(":free")
            and selected.get("backup_model") == "", "fixed_router_free_no_backup_required")
    else:
        require(selected.get("free_tier_confirmed") is True, "selected_free_llm_profile_not_confirmed")
    require(not any(value.get("paid_fallback") is True for value in status.values()), "paid_fallback_forbidden")
    require(status.get("liteapi", {}).get("configured") is True and status["liteapi"].get("mode") == "sandbox"
        and status.get("duffel", {}).get("configured") is True and status["duffel"].get("mode") == "sandbox"
        and status.get("serpapi", {}).get("configured") is True and status["serpapi"].get("free_tier_confirmed") is True
        and status["serpapi"].get("source_type") == "LIVE_INDICATIVE",
        "hybrid_test_provider_profile_required")
    allowed = {"_runtime", profile["provider"], "liteapi", "duffel", "serpapi", "open_meteo"}
    others = {"gemini", "openrouter", "groq", "zai", "cloudflare", "ollama", "hotelbeds", "ratehawk", "travelpayouts"} - {profile["provider"]}
    require(all(isinstance(status.get(name), dict) and status[name].get("configured") is False
        for name in others), "other_provider_not_explicitly_disabled")
    require(all(not value.get("configured") and not value.get("free_tier_confirmed")
        for name, value in status.items() if name not in allowed), "unexpected_other_provider")
    require(evidence.get("manager") == profile["manager"] and evidence.get("status") == "RUNNING"
        and evidence.get("source") == "/home/aegml/projects/otravel-after80-20261026"
        and evidence.get("database") == "otravel_zai_eval" and evidence.get("url") == BASE
        and evidence.get("loaded_manifest", {}).get("matches_current") is True, "owned_after80_runtime_evidence_required")
    require(type(evidence.get("limits", {}).get("calls_per_run")) is int and evidence["limits"]["calls_per_run"] == 1
        and evidence.get("limits", {}).get("fallback") is False
        and type(evidence.get("limits", {}).get("local_calls")) is int and evidence["limits"]["local_calls"] == 0,
        "single_attempt_runtime_required")
    if llm_profile == "cloudflare-nemotron":
        require(evidence.get("limits", {}).get("cloudflare_daily_engineering_bound") == 2000,
            "explicit_cf_evaluation_limit_required")
    elif llm_profile == "openrouter-dots":
        require(evidence.get("limits", {}).get("daily_engineering_bound") == 40,
            "router_shared40_application_limit_required")
    elif llm_profile == "groq-qwen":
        require(type(evidence.get("limits", {}).get("groq_daily_engineering_bound")) is int
            and evidence["limits"]["groq_daily_engineering_bound"] == 1000,
            "explicit_groq_evaluation_limit_required")


def code_manifest():
    entries = {str(path.relative_to(ROOT)): digest(path.read_bytes()) for path in sorted((ROOT / "otravel").rglob("*"))
        if path.is_file() and path.suffix in {".py", ".html", ".css", ".js", ".json"} and "__pycache__" not in path.parts}
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
    return {"git_head": revision.stdout.strip() if revision.returncode == 0 else "UNKNOWN", "files": entries,
        "scope": "CLIENT_SOURCE_ON_DISK_RUNTIME_PROOF_RECORDED_SEPARATELY"}


class Replay:
    def __init__(self, rows, frozen, folder, *, http_factory=HTTP, saved=None):
        self.rows, self.frozen, self.folder, self.http_factory = rows, frozen, folder, http_factory
        self.cleaner, self.lock, self.stop, self.local = Cleaner(), threading.RLock(), threading.Event(), threading.local()
        self.vault = saved or {"format": frozen["format"], "source_sha256": SOURCE_SHA,
            "run_id": folder.name, "plan": frozen, "sessions": {}}
        for row in self.vault["sessions"].values():
            if row.get("token"):
                self.cleaner.add(row["token"])
        self.budget = Budget(frozen["max_model_calls"], frozen["max_tool_calls"], self.vault.get("budget"))
        self.report = {"format": frozen["format"], "plan": frozen, "started_at_utc": utcnow(), "status": "RUNNING",
            "cases": {}, "behavioral_status": "UNASSESSED_CASE_SPECIFIC_ORACLES_UNBOUND",
            "tool_budget_basis": "DURABLE_TOOL_CALL_STARTED_PLUS_CONSERVATIVE_16_PER_OPTIONAL_PREVIEW_NOT_VENDOR_HTTP_COUNT",
            "limitations": ["Fixed old followups can depend on an old bug; this is not fresh adaptive testing.",
                "Tool events are not vendor HTTP-request counts or free-account quota remaining.",
                "Native DB budget is shared with previous runs; server/provider remaining are UNKNOWN.",
                "Typed invariant PASS does not mean a dialogue or all80 behaviors passed."]}
        if saved and (folder / "progress.json").is_file():
            self.report = json.loads((folder / "progress.json").read_text(encoding="utf-8"))
            self.report.setdefault("resume_segments", []).append({"started_at_utc": utcnow(), "explicit_run_resume": True})
            self.report["status"] = "RUNNING"

    def save(self, case=None):
        with self.lock:
            if case and getattr(self.local, "secret", None) is not None:
                self.vault["sessions"][case["id"]] = deepcopy(self.local.secret)
                self.report["cases"][case["id"]] = {"id": case["id"], "file": case["id"] + ".json",
                    "execution_status": case["execution_status"], "observations": len(case["turns"]),
                    "behavioral_status": "UNASSESSED_FIXED_REPLAY"}
            self.vault["budget"] = self.budget.snapshot()
            private_json(self.folder / "resume-vault.json", self.vault)
            self.report["budget"] = self.budget.snapshot()
            private_json(self.folder / "progress.json", self.cleaner.clean(self.report))
            if case:
                private_json(self.folder / (case["id"] + ".json"), self.cleaner.clean(case))

    def halt(self, reason):
        with self.lock:
            self.report.setdefault("stop_reasons", []).append(self.cleaner.clean(reason))
            self.stop.set()

    def case(self, source):
        cid, http = source["id"], self.http_factory()
        with self.lock:
            secret = deepcopy(self.vault["sessions"].get(cid, {"next_turn": 0, "queued_runs": []}))
        self.local.secret = secret
        old_case = self.folder / (cid + ".json")
        record = json.loads(old_case.read_text(encoding="utf-8")) if old_case.is_file() else {"id": cid,
            "execution_status": "NOT_RUN", "behavioral_status": "UNASSESSED_FIXED_REPLAY", "turns": [],
            "source_actions": [{"id": action.get("action"), "kind": action.get("kind"), "status": "NOT_EXECUTED"}
                for action in source.get("actions", [])], "quote_idempotency": "NOT_RUN"}
        if self.stop.is_set():
            self.save(record)
            return
        observation = None
        try:
            if not secret.get("session_id"):
                require(not secret.get("unknown_session_creation"), "UNKNOWN_SESSION_CREATION_NO_RESEND")
                secret["unknown_session_creation"] = True
                self.save(record)
                created = http.request("POST", "/api/v1/sessions", body={"initial_trip": deepcopy(INITIAL_TRIP)})
                require(created["status"] == 201, "session_creation_failed")
                data = created["body"]
                require(isinstance(data.get("session_token"), str) and bool(data.get("session_id")), "session_response_missing_identity")
                secret.update(session_id=data["session_id"], token=data["session_token"], unknown_session_creation=False)
                self.cleaner.add(secret["token"])
                record["initial_snapshot"] = data
                record["session_id"] = secret["session_id"]
                self.save(record)
            sid, token = secret["session_id"], secret["token"]
            route = "/api/v1/sessions/" + quote(sid, safe="")
            for index in range(secret["next_turn"], len(source["turns"])):
                if self.stop.is_set():
                    break
                original = source["turns"][index]
                turn_started = time.monotonic()
                pending = secret.get("inflight")
                observation = {"turn": original["turn"], "user": original["user"], "original_utc": original.get("utc"),
                    "source_expected_unbound": original.get("expected"), "started_at_utc": utcnow()}
                ticket = f"{cid}:T{original['turn']}"
                if pending:
                    require(pending["turn"] == index and pending.get("run_id"), "UNKNOWN_SUBMISSION_NO_RESEND")
                    before, baseline = pending["before"], set(pending["baseline_events"])
                    observation.update(before=before, execution_kind="RESUME_POLL_SAME_RUN_NO_POST")
                    run_id = pending["run_id"]
                else:
                    before = http.get(route, token)
                    baseline = {event["event_id"] for event in http.get(route + "/events", token)["events"]}
                    observation["before"] = before
                    paused = before.get("waiting_reason") == "user_pause"
                    resume = original["user"].strip().casefold().rstrip(".! ") in {"продолжай", "продолжить"}
                    resumed_queue = list(secret["queued_runs"]) if paused and resume else []
                    # The original transcript itself explicitly resumes these
                    # own queued messages. Reserve their full work before POST;
                    # never invent a resume merely to advance a test.
                    allowance = len(resumed_queue) + 1 if resume else 0 if paused else 1
                    self.budget.reserve(ticket, allowance, allowance * TOOL_RESERVATION)
                    secret["inflight"] = {"turn": index, "before": before, "baseline_events": sorted(baseline),
                        "client_message_id": self.vault["run_id"] + ":" + ticket, "run_id": None}
                    self.save(record)
                    accepted = http.request("POST", route + "/messages", token, {"client_message_id": secret["inflight"]["client_message_id"],
                        "expected_version": before["state_version"], "text": original["user"]})
                    observation["intake"] = accepted
                    require(accepted["status"] in {200, 202} and accepted["body"].get("run_id"), "UNKNOWN_INTAKE_RESPONSE_NO_RESEND")
                    run_id = secret["inflight"]["run_id"] = accepted["body"]["run_id"]
                    self.save(record)
                    if paused and not resume:
                        secret["queued_runs"].append(run_id)
                        self.budget.settle(ticket, 0, 0)
                        secret.update(next_turn=index + 1, inflight=None)
                        observation.update(after=http.get(route, token), run={"run_id": run_id, "status": "pending"},
                            execution_status="QUEUED_WHILE_USER_PAUSED_NO_AUTO_RESUME")
                        record["turns"].append(observation)
                        self.save(record)
                        continue
                run = http.wait(run_id, token)
                resumed_queue = (list(secret["queued_runs"])
                    if before.get("waiting_reason") == "user_pause"
                    and original["user"].strip().casefold().rstrip(".! ") in {"продолжай", "продолжить"} else [])
                if resumed_queue:
                    observation["explicitly_resumed_queued_runs"] = [http.wait(queued_id, token) for queued_id in resumed_queue]
                    secret["queued_runs"] = []
                after = http.get(route, token)
                messages = http.get(route + "/messages", token)["messages"]
                events = [event for event in http.get(route + "/events", token)["events"] if event["event_id"] not in baseline]
                orders = http.get(route + "/orders", token)["orders"]
                models = sum(event["event_type"] == "MODEL_CALL_STARTED" for event in events)
                tools = sum(event["event_type"] == "TOOL_CALL_STARTED" for event in events)
                self.budget.settle(ticket, models, tools)
                observation.update(run=run, after=after, assistant=[row["text"] for row in messages if row.get("run_id") == run_id and row["role"] == "assistant"],
                    source_events=events, model_usage=run.get("llm"), elapsed_seconds=round(time.monotonic() - turn_started, 3),
                    completed_at_utc=utcnow(), execution_status=run["status"].upper(), typed_checks=typed_invariants(before, after, run, orders))
                record["turns"].append(observation)
                secret.update(next_turn=index + 1, inflight=None)
                block = provider_block(run, events)
                if block is None:
                    block = next((value for queued in observation.get("explicitly_resumed_queued_runs", [])
                        if (value := provider_block(queued, []))), None)
                if any(check["status"] == "FAIL" and check["id"] in {"own_session_identity", "trip_identity_stable"}
                        for check in observation["typed_checks"]):
                    self.halt({"case": cid, "turn": original["turn"], "code": "IDENTITY_INVARIANT_FAILED"})
                if block:
                    observation["provider_block"] = block
                    self.halt({"case": cid, "turn": original["turn"], "provider_block": block,
                        "continuation": "ONLY_NEW_RUN_OR_EXPLICIT_RUN_RESUME_NO_AUTOMATIC_COOLDOWN_RETRY"})
                self.save(record)
                print(json.dumps({"case": cid, "turn": original["turn"], "status": observation["execution_status"],
                    "model_calls": models, "tool_calls": tools}), flush=True)
            record["execution_status"] = ("QUEUED_UNRESOLVED" if secret["queued_runs"] else
                "COMPLETED_FIXED_TRANSCRIPT" if secret["next_turn"] == len(source["turns"]) else "STOPPED_PARTIAL")
            if (self.frozen["include_simulated_checkout"] and record["execution_status"] == "COMPLETED_FIXED_TRANSCRIPT"
                    and not secret.get("checkout_done")):
                self.checkout(source, record, secret, http)
        except (RuntimeError, ValueError, OSError, KeyError, TypeError) as error:
            record["execution_status"] = "UNKNOWN_OR_BLOCKED"
            if observation is not None and observation not in record["turns"]:
                observation.update(execution_status="UNKNOWN_OR_BLOCKED", actual_after="UNKNOWN_NOT_GUESSED")
                record["turns"].append(observation)
            record.setdefault("errors", []).append({"type": type(error).__name__,
                "code": str(error) if isinstance(error, (ValueError, RuntimeError)) else "details_suppressed", "at_utc": utcnow()})
            self.halt({"case": cid, "code": record["errors"][-1]["code"]})
        finally:
            self.save(record)

    def checkout(self, source, record, secret, http):
        """Opt-in end-of-dialogue probes; never silently reuse old plan/quote IDs.

        Historical mid-dialogue API actions remain NOT_REPLAYED_TIMING_DIFFERENT.
        Only a fresh own-current first plan may be previewed, and confirmation is
        allowed only for a source case with explicit simulated confirmed=True.
        """
        actions = source.get("actions", [])
        if not any(action.get("kind") == "checkout/preview" for action in actions):
            secret["checkout_done"] = True
            return
        route, token = "/api/v1/sessions/" + quote(secret["session_id"], safe=""), secret["token"]
        snapshot = http.get(route, token)
        record["checkout_before"] = snapshot
        plans = []
        for turn in reversed(record["turns"]):
            data = (turn.get("run", {}).get("result") or {}).get("result") or {}
            if "plans" in data:
                plans = data["plans"]
                break
        if not plans or any(plan.get("trip_id") != snapshot["trip_id"] or plan.get("state_version") != snapshot["state_version"] for plan in plans):
            record["quote_idempotency"] = "BLOCKED_NO_OWN_CURRENT_PLAN"
            secret["checkout_done"] = True
            return
        ticket = source["id"] + ":checkout"
        self.budget.reserve(ticket, 0, TOOL_RESERVATION)
        secret["unknown_checkout_submission"] = True
        self.save(record)
        preview = http.request("POST", route + "/checkout/preview", token,
            {"plan_id": plans[0]["plan_id"], "state_version": snapshot["state_version"]})
        record["checkout_preview"] = preview
        record["checkout_conservative_tool_charge"] = TOOL_RESERVATION
        if preview["status"] != 200:
            self.budget.settle(ticket, 0, TOOL_RESERVATION)  # Unknown reprice work is charged conservatively.
            record["quote_idempotency"] = "BLOCKED_PREVIEW_FAILED"
            secret["unknown_checkout_submission"] = False
            secret["checkout_done"] = True
            block = provider_block({"error": preview["body"].get("error")}, [])
            if preview["status"] == 429 or block:
                self.halt({"case": source["id"], "provider_block": block, "retry_after": preview["retry_after"]})
            return
        quote_data = preview["body"]
        require(quote_data.get("session_id") == secret["session_id"] and quote_data.get("state_version") == snapshot["state_version"], "foreign_or_stale_quote")
        self.budget.settle(ticket, 0, TOOL_RESERVATION)
        source_confirmed = any(action.get("kind") == "checkout/confirm" and action.get("request_body", {}).get("confirmed") is True for action in actions)
        if source_confirmed:
            body = {"quote_id": quote_data["quote_id"], "quote_version": quote_data["quote_version"],
                "state_version": snapshot["state_version"], "confirmed": True,
                "idempotency_key": self.vault["run_id"] + ":" + source["id"] + ":confirm"}
            secret["checkout_confirmation"] = body
            self.save(record)
            first = http.request("POST", route + "/checkout/confirm", token, body)
            record["checkout_confirm"] = first
            if first["status"] == 200:
                require(first["body"].get("is_demo") is True and first["body"].get("booking_created_at_supplier") is False,
                    "real_supplier_order_forbidden")
                second = http.request("POST", route + "/checkout/confirm", token, body)
                record["checkout_exact_retry"] = second
                require(second["status"] == 200 and second["body"].get("order_id") == first["body"].get("order_id"), "quote_idempotency_failed")
                orders = http.get(route + "/orders", token)["orders"]
                require(len(orders) == 1 and orders[0].get("order_id") == first["body"].get("order_id"), "duplicate_simulated_order")
                record["checkout_orders"] = orders
                record["quote_idempotency"] = "PASS_TYPED_SIMULATED_ORDER_ONLY"
            else:
                record["quote_idempotency"] = "BLOCKED_CONFIRMATION_FAILED"
        else:
            record["quote_idempotency"] = "NOT_RUN_SOURCE_DID_NOT_CONFIRM"
        secret["unknown_checkout_submission"] = False
        secret["checkout_done"] = True
        record["checkout_after"] = http.get(route, token)
        record["source_action_timing"] = "END_OF_DIALOGUE_PROBE_NOT_EXACT_HISTORICAL_API_TIMING"
        self.save(record)


def main(argv=None):
    global _POST_PACER
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--llm-profile", choices=tuple(LLM_PROFILES), default="zai",
        help="Fixed8084 runtime profile; --llm-profile cloudflare-nemotron alone remains a zero-HTTP dry run.")
    parser.add_argument("--max-dialogues", type=int, default=2)
    parser.add_argument("--start-case", type=int, default=1)
    parser.add_argument("--parallel", type=int, default=1)
    parser.add_argument("--max-model-calls", type=int, default=40)
    parser.add_argument("--max-tool-calls", type=int, default=100)
    parser.add_argument("--include-simulated-checkout", action="store_true")
    parser.add_argument("--message-interval-seconds", type=float, default=0,
        help="Global gap between all POST starts (0..120s); no vendor quota guarantee or automatic429 retry.")
    parser.add_argument("--output-root", type=Path, default=Path("/home/aegml/.local/evaluation"))
    parser.add_argument("--resume", type=Path)
    args = parser.parse_args(argv)
    rows = load_source()
    frozen = plan(rows, dialogues=args.max_dialogues, parallel=args.parallel, models=args.max_model_calls,
        tools=args.max_tool_calls, start=args.start_case, actions=args.include_simulated_checkout, llm_profile=args.llm_profile,
        message_interval_seconds=args.message_interval_seconds)
    if not args.run:
        print(json.dumps({"status": "DRY_RUN_ZERO_REQUESTS", "plan": frozen}, ensure_ascii=False))
        return 0
    require(os.name == "posix", "actual_run_requires_wsl_private_posix_output")
    saved = None
    folder = args.output_root / ("after80-replay-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8])
    if args.resume:
        require(not args.resume.is_symlink() and stat.S_IMODE(args.resume.stat().st_mode) == 0o600
            and args.resume.stat().st_uid == os.geteuid(), "resume_vault_not_private")
        saved = json.loads(args.resume.read_text(encoding="utf-8"))
        require(saved.get("source_sha256") == SOURCE_SHA and saved.get("plan") == frozen, "resume_source_or_plan_mismatch")
        require(not any(row.get("unknown_checkout_submission") for row in saved["sessions"].values()), "UNKNOWN_CHECKOUT_NO_AUTOMATIC_REPLAY")
        folder = args.resume.parent
    folder.mkdir(mode=0o700, parents=True, exist_ok=bool(saved))
    replay = Replay(rows, frozen, folder, saved=saved)
    replay.save()  # Private output is proven before the first HTTP request.
    evidence_path = ROOT / frozen["runtime_evidence"]
    require(evidence_path.is_file() and not evidence_path.is_symlink(), "fixed_runtime_evidence_missing_or_symlinked")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    http = HTTP()
    health, providers = http.get("/health"), http.get("/api/v1/providers/status")
    check_profile(health, providers, evidence, args.llm_profile)
    client = code_manifest()
    runtime_files = evidence["current_source_manifest"]["files"]
    orchestrator = next((row for row in runtime_files if row["path"].endswith("otravel/orchestrator.py")), None)
    require(orchestrator is not None and orchestrator["sha256"] == client["files"]["otravel/orchestrator.py"], "verified_tool_bound_source_mismatch")
    binding = {"runner_sha256": digest(Path(__file__).read_bytes()), "runtime_model": providers["_runtime"],
        "llm_profile": frozen["llm_profile"], "runtime_evidence": frozen["runtime_evidence"],
        "runtime_source_sha256": evidence["current_source_manifest"]["sha256"],
        "client_source_sha256": digest(json.dumps(client["files"], sort_keys=True).encode())}
    require(not replay.vault.get("run_binding") or replay.vault["run_binding"] == binding, "resume_code_or_runtime_changed_new_run_required")
    replay.vault["run_binding"] = binding
    replay.report.update(runtime_profile=providers, runtime_evidence_sha256=digest(evidence_path.read_bytes()),
        runtime_source_manifest=evidence["current_source_manifest"], client_code=client, run_binding=binding)
    replay.save()
    selected = [row for row in rows if row["id"] in frozen["selected_ids"]]
    _POST_PACER = PostPacer(frozen["message_interval_seconds"], stop=replay.stop, resume=bool(saved))
    with ThreadPoolExecutor(max_workers=args.parallel) as executor:
        list(executor.map(replay.case, selected))
    replay.report.update(status="STOPPED_PARTIAL" if replay.stop.is_set() else "EXECUTION_FINISHED_BEHAVIOR_UNASSESSED", finished_at_utc=utcnow())
    replay.save()
    print(json.dumps({"status": replay.report["status"], "output": str(folder), "budget": replay.budget.snapshot(),
        "provider_remaining": "UNKNOWN", "behavioral_status": "UNASSESSED"}, ensure_ascii=False))
    return 1 if replay.stop.is_set() else 0


if __name__ == "__main__":
    raise SystemExit(main())
