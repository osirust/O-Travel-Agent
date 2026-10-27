"""Guarded connected public-API dialogues; default dry run performs zero HTTP.

No provider key, dotenv, SQL, Store seeding, model judge or runtime mutation.
Actual runs require reviewed recipe/driver/package hashes and one fixed profile.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import time
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request
from uuid import uuid4

from replay_after80 import BLOCKS, HTTP, LLM_PROFILES, Cleaner, check_profile, private_json, require

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / "fixtures/evaluation/prefrontend-dialogues-20261027.json"
RECIPE_SHA256 = "cbaf7b168e0574aca26a9f30b043a7dd86d91dbca5d273f26b786ca413ffdad6"
NATIVE_SOURCE = Path("/home/aegml/projects/otravel-after80-20261026")
TERMINAL = {"completed", "failed", "conflict", "interrupted", "cancelled"}
ACTION_KINDS = {"message", "queued_message", "resume", "preview", "set_leg_mode", "confirm_stale",
                "confirm_false", "confirm", "repeat_confirm", "live_validation", "control"}
CHECK_KINDS = {"eq", "unchanged", "party", "stay", "reply_contains", "absent", "run_status", "http_status",
               "last_leg_date", "preference", "group_count", "allocation_complete", "constraint", "hotel_capture",
               "plans_min", "ready_plan", "plans_unchanged", "order_count", "only_simulated_orders", "same_order",
               "leg_mode", "run_error", "reply_money", "reply_nights", "reply_age", "reply_dates", "reply_change_marker"}
MISSING = object()


class StopRun(RuntimeError):
    """A resource/fence/unknown write halts the run; nothing is resent."""


class NotReached(RuntimeError):
    """An ordinary public conversation did not reach the required setup."""


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def stable(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def pointer(value, path):
    require(isinstance(path, str) and path.startswith("/"), "invalid_check_pointer")
    for part in path[1:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict):
            if key not in value:
                return MISSING
            value = value[key]
        elif isinstance(value, list) and key.isdecimal() and int(key) < len(value):
            value = value[int(key)]
        else:
            return MISSING
    return value


def load_recipe():
    raw = RECIPE.read_bytes()
    require(sha(raw) == RECIPE_SHA256, "reviewed_recipe_sha_mismatch")
    recipe = json.loads(raw)
    require(recipe.get("format") == "otravel.prefrontend.dialogues.v1", "unknown_recipe_format")
    ids = []
    for case in recipe["cases"]:
        ids.append(case["id"])
        require(isinstance(case["steps"], list) and 8 <= len(case["steps"]) <= (24 if case.get("long_history_exception") else 15),
                "whole_connected_dialogue_length_required")
        require(not case["initial_trip"] or case["bootstrap_scope"].startswith("EXPLICIT_TYPED_BOOTSTRAP"),
                "unlabelled_typed_bootstrap_forbidden")
        require(len({step["id"] for step in case["steps"]}) == len(case["steps"]), "duplicate_step_id")
        for step in case["steps"]:
            require(step["kind"] in ACTION_KINDS and step["model_policy"] in {"zero", "possible"}, "unknown_step_contract")
            require(step.get("checks") and all(check.get("op") in CHECK_KINDS for check in step["checks"]),
                    "case_specific_checks_required")
            require(all(check.get("op") in CHECK_KINDS for check in step.get("requires", [])), "unknown_prerequisite")
            if step["kind"] in {"message", "queued_message", "resume"}:
                require(isinstance(step.get("text"), str) and step["text"].strip(), "user_message_required")
            if step["kind"] == "queued_message":
                require(step["model_policy"] == "zero", "queued_message_cannot_generate")
    require(5 <= len(ids) <= 8 and len(ids) == len(set(ids)), "reviewed_case_count_required")
    return recipe


def select(recipe, ids=None):
    wanted = ids or [case["id"] for case in recipe["cases"]]
    require(len(wanted) == len(set(wanted)), "duplicate_selected_case")
    indexed = {case["id"]: case for case in recipe["cases"]}
    require(wanted and all(key in indexed for key in wanted), "unknown_selected_case")
    return [deepcopy(indexed[key]) for key in wanted]


def check_one(check, obs):
    """Explicit predicates; unknown/missing evidence can never become PASS."""
    kind = check["op"]
    before, after = obs["before"], obs["after"]
    trip = after.get("trip") or {}
    if kind == "eq":
        actual = pointer(after, check["path"])
        return actual is not MISSING and stable(actual) == stable(check["value"])
    if kind == "unchanged":
        left, right = pointer(before, check["path"]), pointer(after, check["path"])
        return left is not MISSING and right is not MISSING and stable(left) == stable(right)
    if kind == "absent":
        return pointer(after, check["path"]) is MISSING
    if kind == "party":
        people = trip.get("travelers", [])
        return (sum(p.get("type") == "adult" for p in people) == check["adults"]
            and sorted(p.get("age") for p in people if p.get("type") == "child") == sorted(check["ages"]))
    if kind == "stay":
        rows = [stay for stay in trip.get("stays", []) if stay.get("city") == check["city"]]
        if len(rows) != 1:
            return False
        return all((len(rows[0].get("rooms", [])) == value if key == "rooms" else rows[0].get(key, MISSING) == value)
                   for key, value in check.items() if key not in {"op", "city"})
    if kind == "reply_contains":
        text = " ".join(obs.get("reply", "").casefold().replace("ё", "е").split())
        return all(part.casefold().replace("ё", "е") in text for part in check["parts"])
    if kind in {"reply_money", "reply_nights", "reply_age", "reply_dates", "reply_change_marker"}:
        text = " ".join(obs.get("reply", "").casefold().replace("ё", "е").split())
        if kind == "reply_change_marker":
            return re.search(r"\b(?:было|стало|был|стал|добавлен\w*|измен\w*)\b|→", text) is not None
        if kind == "reply_money":
            minor = check["amount_minor"]
            require(type(minor) is int and minor > 0 and check["currency"] == "RUB", "unsupported_reply_money_oracle")
            major, cents = divmod(minor, 100)
            digits = f"{major:,}".split(",")
            number = r"\s?".join(digits)
            decimal = rf"[.,]{cents:02d}" if cents else r"(?:[.,]00)?"
            amount = rf"(?<![\d.,]){number}{decimal}\s*(?:rub|₽|руб(?:\.|лей|ля)?)(?![а-яa-z])"
            return (check.get("topic") != "budget" or "бюджет" in text) and re.search(amount, text) is not None
        if kind == "reply_nights":
            return re.search(rf"(?<!\d){check['value']}(?!\d)\s*(?:ночь|ночи|ночей)\b", text) is not None
        if kind == "reply_age":
            value = check["value"]
            return re.search(rf"(?:ребен\w*[^.!?]{{0,100}}(?<!\d){value}(?!\d)\s*(?:лет|год|года)\b|возраст[^.!?]{{0,40}}(?<!\d){value}(?!\d))", text) is not None
        for value in check["values"]:
            year, month, day = value.split("-")
            if re.search(rf"(?<!\d)(?:{re.escape(value)}|{day}\.{month}\.{year})(?!\d)", text) is None:
                return False
        return True
    if kind == "run_status":
        return obs.get("run", {}).get("status") == check["value"]
    if kind == "http_status":
        return obs.get("http_status") == check["value"]
    if kind == "last_leg_date":
        return bool(trip.get("legs")) and trip["legs"][-1].get("departure_date") == check["value"]
    if kind == "preference":
        return any(p.get("key") == check["key"] and p.get("value") == check["value"] for p in trip.get("preferences", []))
    if kind == "group_count":
        return len(trip.get("groups", [])) == check["value"]
    if kind == "allocation_complete":
        people = sorted(p["id"] for p in trip.get("travelers", []))
        groups = {g["id"]: set(g["traveler_ids"]) for g in trip.get("groups", []) if g.get("keep_separate")}
        return bool(people) and bool(trip.get("stays")) and all(
            len(stay.get("rooms", [])) == check["rooms"]
            and sorted(p for room in stay["rooms"] for p in room.get("traveler_ids", [])) == people
            and all(sum(bool(set(room.get("traveler_ids", [])) & members) for members in groups.values()) <= 1 for room in stay["rooms"])
            for stay in trip["stays"])
    if kind == "constraint":
        ids = {s["id"] for s in trip.get("stays", []) if s.get("city") == check.get("city")}
        return any(c.get("key") == check["key"] and c.get("value") is check["value"] and c.get("hard") is check["hard"]
                   and c.get("stay_id") in ids for c in trip.get("constraints", []))
    if kind == "hotel_capture":
        expected = obs.get("captures", {}).get(check["capture"], MISSING)
        rows = [s for s in trip.get("stays", []) if s.get("city") == check["city"]]
        return expected is not MISSING and len(rows) == 1 and rows[0].get("hotel_id") == expected
    if kind == "plans_min":
        return len(obs.get("plans", [])) >= check["value"]
    if kind == "ready_plan":
        return any(p.get("ready_for_simulated_checkout") is True
                   or "ready_for_simulated_checkout" not in p and p.get("source_type") == "DEMO" and type(p.get("total_minor")) is int
                   for p in obs.get("plans", []))
    if kind == "plans_unchanged":
        return stable(obs.get("plans", [])) == stable(obs.get("before_plans", []))
    if kind == "order_count":
        return isinstance(obs.get("orders"), list) and len(obs["orders"]) == check["value"]
    if kind == "only_simulated_orders":
        return bool(obs.get("orders")) and all(o.get("status") == "BOOKED_DEMO" and o.get("is_demo") is True
            and o.get("booking_created_at_supplier") is False for o in obs["orders"])
    if kind == "same_order":
        return obs.get("same_order") is True
    if kind == "leg_mode":
        legs = trip.get("legs", [])
        return len(legs) > check["index"] and legs[check["index"]].get("mode") == check["value"]
    if kind == "run_error":
        return obs.get("run", {}).get("error", {}).get("code") in check["codes"]
    raise ValueError("unknown_case_specific_predicate")


def grade(checks, obs):
    result = []
    for check in checks:
        try:
            passed = check_one(check, obs)
            result.append({"check": check, "status": "PASS" if passed else "FAIL"})
        except (KeyError, TypeError, ValueError, IndexError):
            result.append({"check": check, "status": "MISSING_EVIDENCE"})
    return result


class ModelPacer:
    """Conservative monotonic wait after an observed MODEL_CALL_STARTED."""
    def __init__(self, seconds=60, *, clock=time.monotonic, sleep=time.sleep):
        require(type(seconds) in {int, float} and math.isfinite(seconds) and 60 <= seconds <= 120, "model_interval_60_to_120_required")
        self.seconds, self.clock, self.sleep, self.last_model_seen = seconds, clock, sleep, None

    def before(self, possible):
        if not possible or self.last_model_seen is None:
            return
        while (remaining := self.last_model_seen + self.seconds - self.clock()) > 0:
            self.sleep(min(10, remaining))

    def observe(self, events):
        if any(event.get("event_type") == "MODEL_CALL_STARTED" for event in events):
            self.last_model_seen = self.clock()


class Budget:
    def __init__(self, models=50, tools=160):
        require(type(models) is int and 0 <= models <= 50 and type(tools) is int and 0 <= tools <= 160, "bounded_budget_required")
        self.maximum_models, self.maximum_tools = models, tools
        self.models = self.tools = self.preview_reservations = 0

    def require_capacity(self, models, tools):
        if self.models + models > self.maximum_models or self.tools + self.preview_reservations + tools > self.maximum_tools:
            raise StopRun("ENGINEERING_BUDGET_NO_WRITE")

    def observe(self, events):
        self.models += sum(event.get("event_type") == "MODEL_CALL_STARTED" for event in events)
        self.tools += sum(event.get("event_type") == "TOOL_CALL_STARTED" for event in events)
        if self.models > self.maximum_models or self.tools + self.preview_reservations > self.maximum_tools:
            raise StopRun("VERIFIED_RUN_BOUND_EXCEEDED")


class LocalHTTP(HTTP):
    def request(self, method, path, token=None, body=None):
        require(path.startswith(("/api/v1/sessions", "/api/v1/runs/", "/api/v1/providers/status", "/health"))
            and not any(c in path for c in ("?", "#", "\\", "..")), "unexpected_local_route")
        require(method in {"GET", "POST"}, "unexpected_http_method")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = Request(self.base + path, data=None if body is None else stable(body).encode(), headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=120 if path.endswith("/checkout/preview") else 20) as response:
                status, raw = response.status, response.read(20_000_001)
        except HTTPError as error:
            status, raw = error.code, error.read(20_000_001)
        except (URLError, TimeoutError, OSError):
            raise StopRun("UNKNOWN_WRITE_NO_RESEND" if method == "POST" else "READ_UNAVAILABLE_NO_WRITE_RETRY") from None
        require(len(raw) <= 20_000_000, "response_size_exceeded")
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError):
            body = {"non_json_body_sha256": sha(raw)}
        return {"status": status, "body": body}


class NativeFence:
    def __init__(self, profile, expected_package):
        self.profile, self.expected = profile, expected_package

    def check(self):
        try:
            import local_backend
        except (ImportError, OSError):
            raise StopRun("NATIVE_FENCE_REFUSED_NO_WRITE") from None
        try:
            profile = LLM_PROFILES[self.profile]
            receipt = json.loads((ROOT / profile["evidence"]).read_text())
            require(receipt.get("status") == "RUNNING" and receipt.get("manager") == profile["manager"], "runtime_manager_not_owned")
            require(receipt.get("source") == str(NATIVE_SOURCE), "runtime_source_not_fixed")
            require(receipt.get("current_source_manifest", {}).get("sha256") == self.expected
                and receipt.get("loaded_manifest", {}).get("matches_current") is True, "runtime_package_mismatch")
            require(local_backend.source_manifest(NATIVE_SOURCE)["sha256"] == self.expected, "native_source_changed_during_evaluation")
            process = receipt.get("process") or {}
            pid, ticks = process.get("pid"), process.get("start_ticks")
            require(type(pid) is int and type(ticks) is int, "runtime_process_identity_missing")
            stat = Path(f"/proc/{pid}/stat").read_text()
            require(int(stat[stat.rfind(")") + 2:].split()[19]) == ticks, "runtime_process_changed")
            return receipt
        except (local_backend.Refusal, OSError, ValueError, KeyError, TypeError, IndexError):
            # The source-manifest helper has its own refusal class. Preserve a
            # terminal code without logging a path, raw exception or credentials.
            raise StopRun("NATIVE_FENCE_REFUSED_NO_WRITE") from None


class Driver:
    def __init__(self, cases, folder, *, http, fence, budget=None, pacer=None, writer=private_json, clock=time.monotonic, sleep=time.sleep):
        self.cases, self.folder, self.http, self.fence = cases, folder, http, fence
        self.budget, self.pacer = budget or Budget(), pacer or ModelPacer()
        self.writer, self.clock, self.sleep = writer, clock, sleep
        self.cleaner, self.seen = Cleaner(), set()
        self.vault = {"format": "otravel.prefrontend.vault.v1", "sessions": {}}
        self.report = {"format": "otravel.prefrontend.results.v1", "status": "IN_PROGRESS", "cases": [],
            "recipe_sha256": RECIPE_SHA256, "model_calls_started": 0, "tool_calls_started": 0,
            "preview_tool_reservations": 0, "source_http_count": "UNKNOWN_TOOL_EVENTS_ARE_NOT_WIRE_REQUESTS",
            "local_model_calls": 0, "provider_remaining": "UNKNOWN", "full80_or106": False}

    def save(self):
        self.report.update(model_calls_started=self.budget.models, tool_calls_started=self.budget.tools,
                           preview_tool_reservations=self.budget.preview_reservations)
        self.writer(self.folder / "vault.json", self.vault)
        self.writer(self.folder / "report.json", self.cleaner.clean(self.report))

    def get(self, path, token=None):
        result = self.http.request("GET", path, token)
        if result["status"] != 200 or not isinstance(result["body"], dict):
            raise StopRun("LOCAL_READ_NOT_200")
        return result["body"]

    def post(self, path, token, body):
        self.fence.check()
        # Journal the exact request privately BEFORE writing. An unknown result
        # is never resent automatically, with either a new or old identifier.
        self.vault["last_intent"] = {"path": path, "body": deepcopy(body), "status": "ABOUT_TO_SEND"}
        self.save()
        result = self.http.request("POST", path, token, body)
        self.vault["last_intent"]["status"] = "KNOWN_HTTP_" + str(result["status"])
        self.save()
        if result["status"] in {429, 502, 504}:
            raise StopRun("HTTP_RESOURCE_FAILURE_NO_RETRY")
        return result

    def wait(self, run_id, token):
        until = self.clock() + 240
        while self.clock() < until:
            run = self.get("/api/v1/runs/" + quote(run_id, safe=""), token)
            if run.get("status") in TERMINAL:
                return run
            self.sleep(0.5)
        raise StopRun("UNKNOWN_RUNNING_RUN_NO_RESEND")

    def hotel(self, plans, spec):
        index = spec["plan_index"]
        if len(plans) <= index:
            raise NotReached("NO_REQUIRED_SHOWN_PLAN")
        offers = plans[index].get("offers", [])
        matches = [offer for offer in offers if offer.get("kind", offer.get("details", {}).get("kind")) == "hotel"
                   and offer.get("city", offer.get("details", {}).get("binding", {}).get("city")) == spec["city"]]
        if len(matches) != 1:
            raise NotReached("NO_UNIQUE_OWNED_HOTEL")
        offer = matches[0]
        value = offer.get(spec["field"])
        if value is None and spec["field"] == "hotel_id":
            value = offer.get("details", {}).get("hotel_id")
        if value is None and spec["field"] == "name":
            value = offer.get("description")
        if not isinstance(value, str) or not value or len(value) > 300:
            raise NotReached("NO_REQUIRED_OWNED_HOTEL_FIELD")
        return value

    def observe(self, ctx, before, response, run=None):
        after = self.get(ctx["route"], ctx["token"])
        events = self.get(ctx["route"] + "/events", ctx["token"])["events"]
        fresh = [event for event in events if event["event_id"] not in self.seen]
        self.seen.update(event["event_id"] for event in fresh)
        self.budget.observe(fresh)
        self.pacer.observe(fresh)
        messages = self.get(ctx["route"] + "/messages", ctx["token"])["messages"]
        run_ids = {row["run_id"] for row in ctx.pop("observed_runs", [])}
        if run:
            run_ids.add(run["run_id"])
        reply = "\n".join(m["text"] for m in messages if m.get("role") == "assistant" and m.get("run_id") in run_ids)
        previous = deepcopy(ctx["plans"])
        drained = ctx.get("drained", [])
        runs = ([run] if run else []) + [row for row in drained if row is not run]
        for row in runs:
            result = (row.get("result") or {}).get("result") or {}
            if isinstance(result.get("plans"), list):
                ctx["plans"] = deepcopy(result["plans"])
        ctx.pop("drained", None)
        ctx["plans"] = [p for p in ctx["plans"] if p.get("state_version") == after["state_version"]]
        obs = {"before": before, "after": after, "run": run or {}, "http_status": response["status"],
            "reply": reply, "events": fresh, "orders": self.get(ctx["route"] + "/orders", ctx["token"])["orders"],
            "before_plans": previous, "plans": deepcopy(ctx["plans"]), "captures": deepcopy(ctx["captures"]),
            "drained_runs": deepcopy(drained)}
        error_codes = {(row.get("error") or {}).get("code") for row in runs}
        response_error = response.get("body", {}).get("error")
        error_codes.add(response_error.get("code") if isinstance(response_error, dict) else None)
        error_codes.update((event.get("payload") or {}).get("code") for event in fresh if event.get("event_type") in {"MODEL_CALL_FAILED", "TOOL_CALL_FAILED", "MODEL_SKIPPED_COOLDOWN"})
        if error_codes & BLOCKS:
            obs["halt_code"] = min(error_codes & BLOCKS)
        return obs

    def execute(self, ctx, step):
        before = self.get(ctx["route"], ctx["token"])
        prerequisite = {"before": before, "after": before, "plans": ctx["plans"], "captures": ctx["captures"]}
        if any(row["status"] != "PASS" for row in grade(step.get("requires", []), prerequisite)):
            raise NotReached("PUBLIC_CONVERSATION_PREREQUISITE_NOT_REACHED")
        for name, spec in step.get("prepare", {}).items():
            ctx["captures"][name] = self.hotel(ctx["plans"], spec)
        kind, possible = step["kind"], step["model_policy"] == "possible"
        # Zero-model messages skip pacing, not capacity admission: leave room
        # for one unexpected generation/search so a broken contract cannot
        # overrun the client's hard evaluation cap before it is detected.
        model_reserve = len(ctx["queued"]) + 1 if kind == "resume" else int(kind in {"message", "queued_message"})
        tool_reserve = 16 * (len(ctx["queued"]) + 1) if kind == "resume" else 16 if kind in {"message", "queued_message", "preview", "live_validation"} else 0
        self.budget.require_capacity(model_reserve, tool_reserve)
        self.pacer.before(possible)
        run, submitted_text = None, None
        if kind in {"message", "queued_message", "resume"}:
            if kind == "queued_message":
                require(before.get("waiting_reason") == "user_pause", "queue_without_explicit_pause_forbidden")
            text = step["text"].format_map(ctx["captures"])
            submitted_text = text
            response = self.post(ctx["route"] + "/messages", ctx["token"], {"client_message_id": str(uuid4()),
                "expected_version": before["state_version"], "text": text})
            require(response["status"] == 202, "message_not_accepted")
            run_id = response["body"]["run_id"]
            if kind == "queued_message":
                ctx["queued"].append(run_id)
                run = self.get("/api/v1/runs/" + run_id, ctx["token"])
                require(run["status"] == "pending", "paused_message_was_not_queued")
            else:
                run = self.wait(run_id, ctx["token"])
                if kind == "resume":
                    drained = [self.wait(queued, ctx["token"]) for queued in ctx["queued"]]
                    ctx["queued"] = []
                    ctx["drained"] = drained
                    ctx["observed_runs"] = drained
        elif kind == "set_leg_mode":
            legs = before["trip"].get("legs", [])
            if len(legs) <= step["leg_index"]:
                raise NotReached("OWNED_LEG_NOT_REACHED")
            response = self.post(ctx["route"] + "/commands", ctx["token"], {"client_message_id": str(uuid4()),
                "expected_version": before["state_version"], "search": False,
                "operations": [{"type": "set_leg_mode", "target_id": legs[step["leg_index"]]["id"], "value": {"mode": step["mode"]}}]})
            require(response["status"] == 202, "typed_leg_mode_not_accepted")
            run = self.wait(response["body"]["run_id"], ctx["token"])
        elif kind == "control":
            require(step.get("action") == "pause", "only_reviewed_UI_pause_control_allowed")
            response = self.post(ctx["route"] + "/control", ctx["token"], {"action": "pause",
                "client_message_id": str(uuid4()), "expected_version": before["state_version"]})
        elif kind == "preview":
            if not ctx["plans"]:
                raise NotReached("NO_CURRENT_PLAN_FOR_PREVIEW")
            self.budget.preview_reservations += 16
            response = self.post(ctx["route"] + "/checkout/preview", ctx["token"], {"plan_id": ctx["plans"][0]["plan_id"], "state_version": before["state_version"]})
            if response["status"] == 200:
                ctx["quote"] = deepcopy(response["body"])
        elif kind.startswith("confirm") or kind == "repeat_confirm":
            if not ctx.get("quote"):
                raise NotReached("NO_ACTUAL_QUOTE_FOR_CONFIRMATION")
            if kind == "repeat_confirm":
                if not ctx.get("confirmation_succeeded"):
                    raise NotReached("NO_KNOWN_SUCCESS_FOR_IDEMPOTENCY_TEST")
                body = deepcopy(ctx["confirmation"])
            else:
                quote_row = ctx["quote"]
                body = {"quote_id": quote_row["quote_id"], "quote_version": quote_row["quote_version"],
                    "state_version": quote_row["state_version"], "idempotency_key": str(uuid4()), "confirmed": kind != "confirm_false"}
            response = self.post(ctx["route"] + "/checkout/confirm", ctx["token"], body)
            if kind == "confirm" and response["status"] == 200:
                ctx.update(confirmation=body, confirmation_succeeded=True, order_id=response["body"].get("order_id"))
            if kind == "repeat_confirm":
                ctx["same_order"] = (isinstance(ctx.get("order_id"), str) and bool(ctx["order_id"])
                    and response["status"] == 200 and response["body"].get("order_id") == ctx["order_id"])
        elif kind == "live_validation":
            response = self.post(ctx["route"] + "/live/search", ctx["token"], {"client_message_id": str(uuid4()),
                "expected_version": before["state_version"], "kind": "hotels", "provider": step["provider"],
                "request": deepcopy(step["request"]), "demo_fallback": False})
            require(response["status"] == 202, "live_validation_not_accepted")
            run = self.wait(response["body"]["run_id"], ctx["token"])
        else:
            raise ValueError("unsupported_step")
        obs = self.observe(ctx, before, response, run)
        obs["same_order"] = ctx.get("same_order", False)
        if step.get("capture"):
            spec = step["capture"]
            try:
                ctx["captures"][spec["name"]] = deepcopy(ctx["plans"]) if spec["kind"] == "current_plans" else self.hotel(ctx["plans"], spec)
            except NotReached as exc:
                obs["capture_error"] = str(exc)
        obs["captures"] = deepcopy(ctx["captures"])
        rows = grade(step["checks"], obs)
        if kind in {"message", "resume", "set_leg_mode"}:
            rows += grade([{"op": "run_status", "value": "completed"}], obs)
        for drained in obs["drained_runs"]:
            rows.append({"check": {"op": "drained_run_completed", "run_id": drained.get("run_id")},
                         "status": "PASS" if drained.get("status") == "completed" else "FAIL"})
        if any(event.get("event_type") == "MODEL_CALL_STARTED" for event in obs["events"]) and not possible:
            obs["halt_code"] = "ZERO_MODEL_CONTRACT_BROKEN"
        return {"id": step["id"], "kind": kind, "text": submitted_text, "recipe_text": step.get("text"), "observation": obs, "checks": rows,
                "status": "PASS" if all(row["status"] == "PASS" for row in rows) else "FAIL"}

    def run(self):
        for case in self.cases:
            record = {"id": case["id"], "title": case["title"], "scope": case["bootstrap_scope"], "status": "IN_PROGRESS", "steps": []}
            self.report["cases"].append(record)
            try:
                response = self.post("/api/v1/sessions", None, {"initial_trip": deepcopy(case["initial_trip"])})
                require(response["status"] == 201, "fresh_session_not_created")
                created = response["body"]
                token, sid = created["session_token"], created["session_id"]
                self.cleaner.add(token)
                self.vault["sessions"][case["id"]] = {"session_id": sid, "token": token}
                ctx = {"token": token, "route": "/api/v1/sessions/" + sid, "plans": [], "captures": {}, "queued": []}
                self.save()
                for step in case["steps"]:
                    try:
                        result = self.execute(ctx, step)
                    except NotReached as exc:
                        record["steps"].append({"id": step["id"], "status": "NOT_REACHED", "reason": str(exc)})
                        break
                    except StopRun as exc:
                        record["steps"].append({"id": step["id"], "kind": step["kind"], "status": "STOPPED",
                            "reason": str(exc), "last_intent": self.cleaner.clean(self.vault.get("last_intent", {}))})
                        raise
                    record["steps"].append(result)
                    self.save()
                    if result["observation"].get("halt_code"):
                        raise StopRun(result["observation"]["halt_code"])
                statuses = [step["status"] for step in record["steps"]]
                record["status"] = "FAIL" if "FAIL" in statuses else "NOT_REACHED" if "NOT_REACHED" in statuses else "PASS"
                if len(record["steps"]) != len(case["steps"]) and record["status"] == "PASS":
                    record["status"] = "NOT_COMPLETED"
            except (StopRun, ValueError, KeyError, TypeError, IndexError) as exc:
                record.update(status="STOPPED", reason=str(exc))
                self.report.update(status="STOPPED_NO_RETRY", reason=str(exc))
                self.save()
                return self.report
            self.save()
        self.report["status"] = "COMPLETED_NOT_ALL_PASS" if any(case["status"] != "PASS" for case in self.report["cases"]) else "CONNECTED_CASES_PASS_IN_DECLARED_SCOPES"
        self.save()
        return self.report


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--llm-profile", choices=tuple(LLM_PROFILES), default="groq-qwen")
    parser.add_argument("--case-id", action="append")
    parser.add_argument("--expected-package-sha256")
    parser.add_argument("--expected-driver-sha256")
    parser.add_argument("--max-model-calls", type=int, default=50)
    parser.add_argument("--max-tool-calls", type=int, default=160)
    parser.add_argument("--model-interval-seconds", type=float, default=60)
    parser.add_argument("--output-root", type=Path, default=Path("/home/aegml/evaluation-runs-prefrontend"))
    args = parser.parse_args(argv)
    recipe = load_recipe()
    cases = select(recipe, args.case_id)
    budget, pacer = Budget(args.max_model_calls, args.max_tool_calls), ModelPacer(args.model_interval_seconds)
    driver_sha = sha(Path(__file__).read_bytes())
    frozen = {"format": recipe["format"], "recipe_sha256": RECIPE_SHA256, "driver_sha256": driver_sha,
        "selected_cases": [case["id"] for case in cases], "steps": sum(len(case["steps"]) for case in cases),
        "language_messages": sum(step["kind"] in {"message", "queued_message", "resume"} for case in cases for step in case["steps"]),
        "profile": args.llm_profile, "expected_package_sha256": args.expected_package_sha256,
        "max_model_calls": args.max_model_calls, "max_tool_calls": args.max_tool_calls,
        "model_interval_seconds": args.model_interval_seconds, "parallel": 1,
        "bootstrap_cases": [case["id"] for case in cases if case["initial_trip"]], "default_dry_run_http_calls": 0}
    if not args.run:
        print(json.dumps(frozen, ensure_ascii=False, indent=2))
        return 0
    require(os.name == "posix", "actual_run_requires_native_private_output")
    require(args.expected_driver_sha256 == driver_sha, "reviewed_driver_sha_required")
    require(isinstance(args.expected_package_sha256, str) and len(args.expected_package_sha256) == 64
        and all(c in "0123456789abcdef" for c in args.expected_package_sha256), "reviewed_package_sha_required")
    require(args.output_root.is_absolute() and args.output_root.resolve().is_relative_to(Path("/home/aegml"))
        and not args.output_root.is_symlink(), "private_C_backed_output_required")
    http, fence = LocalHTTP(), NativeFence(args.llm_profile, args.expected_package_sha256)
    receipt = fence.check()
    check_profile(http.get("/health"), http.get("/api/v1/providers/status"), receipt, args.llm_profile)
    folder = args.output_root / ("prefrontend-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:8])
    folder.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.chmod(folder, 0o700)
    private_json(folder / "plan.json", frozen)
    driver = Driver(cases, folder, http=http, fence=fence, budget=budget, pacer=pacer)
    result = driver.run()
    print(json.dumps({"status": result["status"], "output": str(folder), "cases": len(result["cases"]),
        "model_calls_started": result["model_calls_started"], "tool_calls_started": result["tool_calls_started"]}))
    return 0 if result["status"] == "CONNECTED_CASES_PASS_IN_DECLARED_SCOPES" else 2


if __name__ == "__main__":
    raise SystemExit(main())
