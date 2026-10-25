"""Repeatable local HTTP DEMO smoke; never posts chat or live-search requests.

Session tokens stay in memory. Evidence deliberately omits tokens, private
responses, session/run identifiers and any credentials. No Docker/DB lifecycle
operations are performed. Synthetic sessions/orders remain for inspection.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4


class SmokeFailure(Exception):
    pass


def ensure(condition, label):
    if not condition:
        raise SmokeFailure(label)


def public_payload(value):
    private = {"owner_token_hash", "input_text", "request_hash", "database_url",
               "authorization", "api_key", "session_token", "client_secret"}
    if isinstance(value, dict):
        ensure(not private.intersection(key.casefold() for key in value), "private_field_exposed")
        for item in value.values():
            public_payload(item)
    elif isinstance(value, list):
        for item in value:
            public_payload(item)


def find_plans(value):
    if isinstance(value, dict):
        if isinstance(value.get("plans"), list):
            return value["plans"]
        for key in ("result", "run"):
            found = find_plans(value.get(key))
            if found:
                return found
    return []


class Smoke:
    def __init__(self, base_url):
        parsed = urlparse(base_url)
        ensure(parsed.scheme in {"http", "https"} and parsed.hostname in {"localhost", "127.0.0.1", "::1"},
               "only_localhost_base_url_allowed")
        ensure(not parsed.username and not parsed.password and not parsed.query and not parsed.fragment,
               "invalid_base_url")
        self.base_url = base_url.rstrip("/")
        self.opener = build_opener(ProxyHandler({}))
        self.checks = []
        self.sessions = []

    def request(self, method, path, token=None, body=None):
        # This client has no path to model extraction or external provider tools.
        ensure(not path.endswith("/messages") and "/live/" not in path, "external_action_forbidden")
        if method == "POST" and path.endswith(("/demo", "/search")):
            ensure(body and body.get("mode") == "DEMO", "only_demo_search_allowed")
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        encoded = None
        if body is not None:
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=encoded, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=15) as response:
                status, raw = response.status, response.read()
        except HTTPError as response:
            status, raw = response.code, response.read()
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError):
            raise SmokeFailure("server_response_is_not_json") from None
        return status, data

    def record(self, name, **facts):
        self.checks.append({"name": name, "passed": True, **facts})

    def create_session(self):
        code, data = self.request("POST", "/api/v1/sessions", body={})
        ensure(code == 201, "session_creation_failed")
        token = data.pop("session_token", None)
        ensure(isinstance(token, str) and len(token) >= 32, "session_token_missing")
        public_payload(data)
        ensure(data.get("state_version") == 0, "new_session_not_empty")
        session = {"id": data["session_id"], "token": token}
        self.sessions.append(session)
        return session

    def snapshot(self, session):
        code, data = self.request("GET", "/api/v1/sessions/" + session["id"], session["token"])
        ensure(code == 200, "owned_session_unavailable")
        public_payload(data)
        return data

    def poll(self, run_id, token):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            code, run = self.request("GET", "/api/v1/runs/" + run_id, token)
            ensure(code == 200, "run_poll_failed")
            public_payload(run)
            if run.get("status") == "completed":
                ensure(not run.get("error") and not run.get("llm"), "demo_run_used_model_or_failed")
                return run
            ensure(run.get("status") in {"pending", "running"}, "demo_run_not_completed")
            time.sleep(0.1)
        raise SmokeFailure("demo_run_poll_timeout")

    def action(self, session, action):
        snapshot = self.snapshot(session)
        code, result = self.request("POST", f"/api/v1/sessions/{session['id']}/{action}", session["token"], {
            "client_message_id": "runtime-smoke-" + str(uuid4()),
            "expected_version": snapshot["state_version"], "mode": "DEMO",
        })
        ensure(code == 202 and result.get("run_id"), "demo_action_not_accepted")
        return self.poll(result["run_id"], session["token"])

    def preview(self, session, plan):
        version = self.snapshot(session)["state_version"]
        code, quote = self.request("POST", f"/api/v1/sessions/{session['id']}/checkout/preview", session["token"], {
            "plan_id": plan["plan_id"], "state_version": version,
        })
        ensure(code == 200, "preview_failed")
        public_payload(quote)
        ensure(quote["state_version"] == version and quote["plan"].get("repriced") is True,
               "preview_not_repriced_or_wrong_version")
        ensure(quote["plan"].get("reprice_kind") == "deterministic_demo_not_live", "preview_not_marked_demo")
        return quote

    def run(self):
        code, health = self.request("GET", "/health")
        ensure(code == 200 and health.get("application") == "backend" and
               health.get("database") == "ready" and health.get("supplier_booking") is False,
               "target_is_not_ready_otravel_backend")
        self.record("health", http_status=code, database="ready", supplier_booking=False)
        first, second = self.create_session(), self.create_session()
        path = "/api/v1/sessions/" + first["id"]
        missing_code, _ = self.request("GET", path)
        wrong_code, _ = self.request("GET", path, "invalid-smoke-token")
        cross_code, _ = self.request("GET", path, second["token"])
        ensure(missing_code == 401 and wrong_code in {401, 403} and cross_code in {401, 403}, "session_auth_not_isolated")
        self.record("session_authorization", missing_token_status=missing_code,
                    invalid_token_status=wrong_code, other_session_token_status=cross_code)

        self.action(first, "demo")
        trip = self.snapshot(first)["trip"]
        ensure(trip.get("origin") == "Казань" and [stay["city"] for stay in trip["stays"]] == ["Стамбул", "Анталья"],
               "complex_route_not_loaded")
        ensure(len(trip["legs"]) >= 3 and len(trip["groups"]) >= 2 and
               all(len(stay["rooms"]) >= 2 for stay in trip["stays"]), "complex_group_not_loaded")
        self.record("complex_demo_loaded", origin=trip["origin"], stay_cities=[stay["city"] for stay in trip["stays"]],
                    legs=len(trip["legs"]), groups=len(trip["groups"]), travelers=len(trip["travelers"]),
                    rooms_per_stay=[len(stay["rooms"]) for stay in trip["stays"]])
        plans = find_plans(self.action(first, "search"))
        ensure(plans and all(plan.get("source_type") == "DEMO" and plan.get("booking_created") is False for plan in plans),
               "demo_search_not_marked")
        plan = plans[0]
        ensure(all(offer.get("is_demo") is True and offer.get("price_kind") == "synthetic" for offer in plan["offers"]),
               "offer_not_marked_synthetic")
        self.record("demo_search", plans=len(plans), offers_in_selected_plan=len(plan["offers"]),
                    selected_total_minor=plan["total_minor"], currency=plan["currency"], source_type="DEMO")
        quote = self.preview(first, plan)
        self.record("quote_preview", quote_version=quote["quote_version"], state_version=quote["state_version"],
                    repriced=True, reprice_kind=quote["plan"]["reprice_kind"])
        confirmation = {"quote_id": quote["quote_id"], "quote_version": quote["quote_version"],
                        "state_version": quote["state_version"], "idempotency_key": "runtime-smoke-" + str(uuid4()),
                        "confirmed": True}
        checkout = path + "/checkout/confirm"
        code, order = self.request("POST", checkout, first["token"], confirmation)
        ensure(code == 200 and order.get("is_demo") is True and order.get("booking_created_at_supplier") is False,
               "order_not_simulated")
        public_payload(order)
        repeated_code, repeated = self.request("POST", checkout, first["token"], confirmation)
        ensure(repeated_code == 200 and repeated["order_id"] == order["order_id"], "confirmation_not_idempotent")
        changed_code, _ = self.request("POST", checkout, first["token"], {
            **confirmation, "quote_version": confirmation["quote_version"] + 1,
        })
        different_key_code, _ = self.request("POST", checkout, first["token"], {
            **confirmation, "idempotency_key": "runtime-smoke-new-" + str(uuid4()),
        })
        ensure(changed_code == different_key_code == 409, "duplicate_or_changed_confirmation_accepted")
        code, orders = self.request("GET", path + "/orders", first["token"])
        ensure(code == 200 and len(orders.get("orders", [])) == 1, "duplicate_demo_orders_created")
        public_payload(orders)
        self.record("simulated_order_and_idempotency", status=order["status"], created_at_supplier=False,
                    order_count=1, exact_retry_status=repeated_code,
                    changed_payload_status=changed_code, second_operation_status=different_key_code)

        other_plans = find_plans(self.action(second, "demo"))
        ensure(other_plans, "second_demo_search_failed")
        old_quote = self.preview(second, other_plans[0])
        second_path = "/api/v1/sessions/" + second["id"]
        before_trip = self.snapshot(second)["trip"]
        for action in ("pause", "resume"):
            code, control = self.request("POST", second_path + "/control", second["token"], {"action": action})
            ensure(code == 200, "control_failed")
            public_payload(control)
        current = self.snapshot(second)
        ensure(current["state_version"] > old_quote["state_version"] and
               current["trip"]["travelers"] == before_trip["travelers"], "control_lost_state_or_version")
        stale_code, _ = self.request("POST", second_path + "/checkout/confirm", second["token"], {
            "quote_id": old_quote["quote_id"], "quote_version": old_quote["quote_version"],
            "state_version": old_quote["state_version"], "idempotency_key": "runtime-smoke-stale-" + str(uuid4()),
            "confirmed": True,
        })
        code, orders = self.request("GET", second_path + "/orders", second["token"])
        ensure(stale_code == 409 and code == 200 and orders.get("orders") == [], "stale_quote_created_order")
        self.record("stale_quote_after_control", confirmation_status=stale_code, order_count=0, state_preserved=True)

        event_count = external_events = 0
        for session in self.sessions:
            code, payload = self.request("GET", f"/api/v1/sessions/{session['id']}/events", session["token"])
            ensure(code == 200, "event_audit_failed")
            public_payload(payload)
            rows = payload.get("events", [])
            event_count += len(rows)
            external_events += sum(event["event_type"] in {
                "MODEL_CALL_STARTED", "LLM_REQUEST_STARTED", "TOOL_CALL_STARTED", "LIVE_SEARCH_STARTED"
            } for event in rows)
        ensure(external_events == 0, "unexpected_external_call_event")
        self.record("no_model_or_live_tools", audited_events=event_count, external_call_events=0,
                    free_text_messages_sent=0, live_search_requests_sent=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8081")
    parser.add_argument("--output", type=Path, default=Path("docs/verification/runtime-smoke-20261025.json"))
    args = parser.parse_args()
    report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "base_url": args.base_url,
              "scope": "local_http_demo_only", "real_supplier_booking": False,
              "credentials_recorded": False, "database_records_deleted": False,
              "docker_restarted": False,
              "limitations": ["Нет проверки LLM/NLU или live API", "Reprice детерминированный DEMO",
                              "Нет проверки перезапуска контейнера или браузерного frontend",
                              "Созданные synthetic sessions/orders сохранены"]}
    smoke = None
    try:
        smoke = Smoke(args.base_url)
        smoke.run()
        report["status"] = "PASS"
        exit_code = 0
    except Exception as exc:
        report["status"] = "FAIL"
        report["failure"] = str(exc) if isinstance(exc, SmokeFailure) else type(exc).__name__
        exit_code = 1
    report["checks"] = smoke.checks if smoke else []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"runtime_smoke={report['status']}; checks={len(report['checks'])}; evidence={args.output}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
