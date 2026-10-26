"""Pure planning/typing and synthetic transport guards; zero real HTTP or LLM."""
import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import URLError

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("after80_replay_test", ROOT / "scripts/replay_after80.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def rows(count=2, texts=None):
    return [{"id": f"ZAI100-{index:03d}", "turns": [
        {"turn": n, "user": text, "expected": "source text is not a machine oracle", "utc": "2026-10-26T10:00:00+00:00"}
        for n, text in enumerate(texts or ["synthetic original user"], 1)], "actions": []}
        for index in range(1, count + 1)]


def test_default_plan_is_two_cases_with_only_explicit_synthetic_policy_not_seeded_facts():
    value = runner.plan(rows())
    assert value["selected_ids"] == ["ZAI100-001", "ZAI100-002"]
    assert value["parallel"] == 1 and value["provider_remaining"] == "UNKNOWN"
    assert set(value["synthetic_initial_trip"]) == {"search_policy"}
    assert value["synthetic_initial_trip"]["search_policy"] == {
        "mode": "HYBRID", "hotel_provider": "liteapi", "flight_provider": "duffel",
        "demo_fallback": True, "guest_nationality": "RU"}
    assert "NOT_INFERRED" in value["nationality_provenance"]


@pytest.mark.parametrize("field,value", [("dialogues", 0), ("dialogues", 81), ("parallel", 3),
    ("models", 901), ("tools", 601), ("models", -1), ("start", 81), ("parallel", True)])
def test_engineering_caps_and_case_bounds_cannot_be_overridden(field, value):
    values = {"dialogues": 2, "parallel": 1, "models": 40, "tools": 100, "start": 1, field: value}
    with pytest.raises(ValueError):
        runner.validate_limits(**values)


def test_no_81_or_82_and_no_reordered_source_selection():
    source = rows()
    source[1]["id"] = "ZAI100-081"
    with pytest.raises(ValueError, match="source_selection"):
        runner.plan(source)


def test_without_run_main_has_no_http_or_private_output_actions(monkeypatch, capsys):
    monkeypatch.setattr(runner, "load_source", lambda: rows())
    forbidden = Mock(side_effect=AssertionError("Dry run cannot request or write"))
    monkeypatch.setattr(runner, "HTTP", forbidden)
    monkeypatch.setattr(runner, "private_json", forbidden)
    assert runner.main([]) == 0
    assert "DRY_RUN_ZERO_REQUESTS" in capsys.readouterr().out
    forbidden.assert_not_called()


def test_unknown_reservation_survives_resume_and_prevents_budget_reset():
    budget = runner.Budget(1, 16)
    budget.reserve("unknown", 1, 16)
    restored = runner.Budget(1, 16, budget.snapshot())
    with pytest.raises(ValueError, match="admission"):
        restored.reserve("another", 1, 16)
    restored.settle("unknown", 1, 3)
    assert restored.snapshot() == {"models": 1, "tools": 3, "held": {}}


def test_runtime_bound_violation_does_not_mint_new_budget():
    budget = runner.Budget(2, 32)
    budget.reserve("one", 1, 16)
    with pytest.raises(ValueError, match="verified"):
        budget.settle("one", 2, 17)
    assert budget.snapshot()["held"] == {"one": [1, 16]}


def test_corrupt_resume_counters_cannot_expand_caps():
    with pytest.raises(ValueError, match="snapshot"):
        runner.Budget(1, 16, {"models": -1, "tools": 0, "held": {}})
    with pytest.raises(ValueError, match="exceeds"):
        runner.Budget(1, 16, {"models": 1, "tools": 0, "held": {"one": [1, 16]}})


def test_parallel_admission_never_reserves_more_than_shared_caps():
    budget = runner.Budget(2, 32)

    def request(index):
        try:
            budget.reserve(str(index), 1, 16)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        admitted = list(executor.map(request, range(20)))
    assert sum(admitted) == 2 and len(budget.snapshot()["held"]) == 2


def snapshot():
    return {"session_id": "session-own", "trip_id": "trip-own", "state_version": 0, "status": "NEW",
        "waiting_reason": None, "pending_question": None, "trip": {"trip_id": "trip-own", "state_version": 0,
            "origin": None, "travelers": [{"id": "adult", "type": "adult", "group_id": None}],
            "groups": [], "legs": [], "stays": [{"id": "stay", "check_in": "2026-11-10", "check_out": "2026-11-12",
                "rooms": [{"id": "room", "traveler_ids": ["adult"]}]}]}}


@pytest.mark.parametrize("fault,failed_check", [("identity", "own_session_identity"),
    ("dates", "stored_stay_dates_valid"), ("ref", "saved_references_exist_without_double_allocation"),
    ("double", "saved_references_exist_without_double_allocation"), ("version", "wrapper_state_version_consistent")])
def test_typed_invariants_check_state_content_not_only_case_counts(fault, failed_check):
    before, after = snapshot(), snapshot()
    if fault == "identity":
        after["session_id"] = "foreign"
    elif fault == "dates":
        after["trip"]["stays"][0]["check_out"] = "2026-11-09"
    elif fault == "ref":
        after["trip"]["stays"][0]["rooms"][0]["traveler_ids"] = ["missing-person"]
    elif fault == "double":
        after["trip"]["stays"][0]["rooms"].append({"id": "second", "traveler_ids": ["adult"]})
    else:
        after["trip"]["state_version"] = 1
    checks = runner.typed_invariants(before, after, {"session_id": "session-own", "status": "completed"}, [])
    assert next(row for row in checks if row["id"] == failed_check)["status"] == "FAIL"


def test_cleaner_keeps_user_facts_but_removes_tokens_keys_auth_and_query_values():
    cleaner = runner.Cleaner()
    cleaner.add("synthetic-session-secret")
    result = cleaner.clean({"user": "Бюджет 75000 рублей", "session_token": "synthetic-session-secret",
        "nested": {"api_key": "not-public", "token": "not-public"},
        "reply": "synthetic-session-secret Bearer private-value https://example.test/api?api_key=private"})
    assert result["user"] == "Бюджет 75000 рублей"
    assert "session_token" not in result and result["nested"] == {}
    assert "synthetic-session-secret" not in result["reply"] and "private-value" not in result["reply"]
    assert "api_key=private" not in result["reply"]


@pytest.mark.parametrize("base", ["http://127.0.0.1:8083", "https://localhost:8084", "http://external.test:8084",
    "http://localhost:8084/?key=secret", "http://user:pass@localhost:8084"])
def test_client_refuses_other_runtime_or_remote_credentials(base):
    with pytest.raises(ValueError, match="loopback"):
        runner.HTTP(base)


def test_no_redirect_and_unknown_write_has_no_automatic_retry():
    assert runner.NoRedirect().redirect_request(None, None, 302, "", {}, "https://remote.test") is None
    client = runner.HTTP()
    request = Mock(side_effect=URLError("synthetic network failure"))
    client.opener = SimpleNamespace(open=request)
    with pytest.raises(RuntimeError, match="UNKNOWN_WRITE_NO_RESEND"):
        client.request("POST", "/api/v1/sessions", body={})
    assert request.call_count == 1


class FakeHTTP:
    """Protocol-only server. No socket opener or real source facts are used."""
    def __init__(self, *, unknown_write=False, unknown_poll=False, provider_block=False):
        self.snap, self.events, self.messages, self.posts = snapshot(), [], [], []
        self.unknown_write, self.unknown_poll, self.provider_block = unknown_write, unknown_poll, provider_block
        self.run = None

    def request(self, method, path, token=None, body=None):
        assert method == "POST"
        self.posts.append((path, deepcopy(body)))
        if path == "/api/v1/sessions":
            assert body == {"initial_trip": runner.INITIAL_TRIP}
            return {"status": 201, "body": {**deepcopy(self.snap), "session_token": "synthetic-session-secret"}, "retry_after": None}
        assert path.endswith("/messages")
        run_id = "run-" + str(len(self.posts))
        error = {"code": "RATE_LIMITED", "details": {"http_status": 429, "retry_after_seconds": 120}} if self.provider_block else None
        self.run = {"run_id": run_id, "session_id": self.snap["session_id"],
            "status": "failed" if error else "completed", "error": error,
            "llm": {"provider": "zai", "model": "glm-4.5-flash", "usage": {"total_tokens": 7}}, "result": {"result": {}}}
        self.events.append({"event_id": run_id, "run_id": run_id, "event_type": "MODEL_CALL_STARTED", "payload": {"provider": "zai"}})
        self.messages.append({"run_id": run_id, "role": "assistant", "text": "synthetic actual assistant reply"})
        if self.unknown_write:
            raise RuntimeError("UNKNOWN_WRITE_NO_RESEND")  # Server may already have committed.
        return {"status": 202, "body": {"run_id": run_id}, "retry_after": None}

    def get(self, path, token=None):
        if path.endswith("/events"):
            return {"events": deepcopy(self.events)}
        if path.endswith("/messages"):
            return {"messages": deepcopy(self.messages)}
        if path.endswith("/orders"):
            return {"orders": []}
        return deepcopy(self.snap)

    def wait(self, run_id, token):
        assert self.run["run_id"] == run_id
        if self.unknown_poll:
            raise RuntimeError("UNKNOWN_RUNNING_RUN_NO_RESEND")
        return deepcopy(self.run)


def setup_replay(tmp_path, http, texts=None):
    source = rows(1, texts)
    frozen = runner.plan(source, dialogues=1)
    folder = tmp_path / "after80-replay-synthetic"
    folder.mkdir(mode=0o700)
    return runner.Replay(source, frozen, folder, http_factory=lambda: http), source


def test_synthetic_turn_records_actual_reply_state_events_usage_and_private_vault(tmp_path):
    http = FakeHTTP()
    replay, source = setup_replay(tmp_path, http)
    replay.case(source[0])
    report = json.loads((replay.folder / "ZAI100-001.json").read_text())
    assert report["execution_status"] == "COMPLETED_FIXED_TRANSCRIPT"
    turn = report["turns"][0]
    assert turn["user"] == source[0]["turns"][0]["user"]
    assert turn["assistant"] == ["synthetic actual assistant reply"]
    assert turn["before"] == turn["after"] and turn["model_usage"]["usage"]["total_tokens"] == 7
    assert turn["source_events"][0]["event_type"] == "MODEL_CALL_STARTED"
    assert all(row["status"] == "PASS" for row in turn["typed_checks"])
    assert report["behavioral_status"] == "UNASSESSED_FIXED_REPLAY" and report["quote_idempotency"] == "NOT_RUN"
    assert "synthetic-session-secret" not in (replay.folder / "progress.json").read_text()
    assert "synthetic-session-secret" not in (replay.folder / "ZAI100-001.json").read_text()
    assert "synthetic-session-secret" in (replay.folder / "resume-vault.json").read_text()
    assert (replay.folder / "resume-vault.json").stat().st_mode & 0o777 == 0o600


def test_unknown_submission_is_preserved_and_even_explicit_resume_does_not_post_again(tmp_path):
    http = FakeHTTP(unknown_write=True)
    replay, source = setup_replay(tmp_path, http)
    replay.case(source[0])
    saved = json.loads((replay.folder / "resume-vault.json").read_text())
    assert saved["sessions"]["ZAI100-001"]["inflight"]["run_id"] is None
    resumed = runner.Replay(source, replay.frozen, replay.folder, http_factory=lambda: http, saved=saved)
    resumed.case(source[0])
    assert len(http.posts) == 2  # One session + one message; never resend the message.
    assert resumed.budget.snapshot()["held"] == {"ZAI100-001:T1": [1, 16]}
    report = json.loads((replay.folder / "ZAI100-001.json").read_text())
    assert report["turns"][0]["actual_after"] == "UNKNOWN_NOT_GUESSED"


def test_known_run_resume_only_polls_and_reconciles_existing_reservation(tmp_path):
    http = FakeHTTP(unknown_poll=True)
    replay, source = setup_replay(tmp_path, http)
    replay.case(source[0])
    saved = json.loads((replay.folder / "resume-vault.json").read_text())
    http.unknown_poll = False
    resumed = runner.Replay(source, replay.frozen, replay.folder, http_factory=lambda: http, saved=saved)
    resumed.case(source[0])
    assert len(http.posts) == 2 and resumed.budget.snapshot() == {"models": 1, "tools": 0, "held": {}}
    report = json.loads((replay.folder / "ZAI100-001.json").read_text())
    assert len(report["turns"]) == 2 and report["turns"][1]["execution_kind"] == "RESUME_POLL_SAME_RUN_NO_POST"


def test_provider_429_keeps_usage_retry_after_and_stops_before_next_frozen_message(tmp_path):
    http = FakeHTTP(provider_block=True)
    replay, source = setup_replay(tmp_path, http, texts=["first original", "second must not be sent"])
    replay.case(source[0])
    assert replay.stop.is_set() and len(http.posts) == 2
    report = json.loads((replay.folder / "ZAI100-001.json").read_text())
    assert len(report["turns"]) == 1
    assert report["turns"][0]["provider_block"]["details"]["retry_after_seconds"] == 120
    assert report["turns"][0]["model_usage"]["usage"]["total_tokens"] == 7


def test_completed_run_source_cooldown_component_stops_before_next_model_turn(tmp_path):
    http = FakeHTTP()
    original_request = http.request

    def component_cooldown(method, path, token=None, body=None):
        response = original_request(method, path, token, body)
        if path.endswith("/messages"):
            run_id = response["body"]["run_id"]
            http.events.extend([
                {"event_id": run_id + "-tool-start", "run_id": run_id, "event_type": "TOOL_CALL_STARTED",
                    "payload": {"provider": "liteapi"}},
                {"event_id": run_id + "-tool-failed", "run_id": run_id, "event_type": "TOOL_CALL_FAILED",
                    "payload": {"provider": "liteapi", "code": "SOURCE_COOLDOWN", "retry_after_seconds": 30}},
            ])
        return response

    http.request = component_cooldown
    replay, source = setup_replay(tmp_path, http, texts=["first original", "must not trigger another model"])
    replay.case(source[0])
    assert replay.stop.is_set() and len(http.posts) == 2
    report = json.loads((replay.folder / "ZAI100-001.json").read_text())
    assert len(report["turns"]) == 1 and report["turns"][0]["run"]["status"] == "completed"
    assert report["turns"][0]["provider_block"]["code"] == "SOURCE_COOLDOWN"
    assert report["turns"][0]["provider_block"]["details"]["retry_after_seconds"] == 30
    assert replay.budget.snapshot() == {"models": 1, "tools": 1, "held": {}}


def test_paused_input_is_queued_without_poll_or_synthetic_resume(tmp_path):
    http = FakeHTTP()
    http.snap["waiting_reason"], http.snap["status"] = "user_pause", "WAITING_USER"
    original_request = http.request

    def paused_request(method, path, token=None, body=None):
        if path.endswith("/messages"):
            http.posts.append((path, deepcopy(body)))
            return {"status": 202, "body": {"run_id": "queued-owned-run"}, "retry_after": None}
        return original_request(method, path, token, body)

    http.request = paused_request
    http.wait = Mock(side_effect=AssertionError("Paused work is not polled or resumed automatically"))
    replay, source = setup_replay(tmp_path, http, texts=["additional source fact", "продолжай"])
    replay.case(source[0])
    assert len(http.posts) == 2  # Source resume cannot drain an unreserved queue automatically.
    assert replay.stop.is_set() and replay.budget.snapshot() == {"models": 0, "tools": 0, "held": {}}
    http.wait.assert_not_called()
    report = json.loads((replay.folder / "ZAI100-001.json").read_text())
    assert report["turns"][0]["execution_status"] == "QUEUED_WHILE_USER_PAUSED_NO_AUTO_RESUME"


def test_simulated_checkout_is_opt_in_and_exact_retry_returns_one_owned_order(tmp_path):
    http = FakeHTTP()
    original_request, original_get = http.request, http.get
    order = {"order_id": "owned-order", "is_demo": True, "booking_created_at_supplier": False}
    confirmations = []

    def checkout_request(method, path, token=None, body=None):
        if path.endswith("/checkout/preview"):
            http.posts.append((path, deepcopy(body)))
            return {"status": 200, "body": {"session_id": "session-own", "state_version": 0,
                "quote_id": "owned-quote", "quote_version": 1}, "retry_after": None}
        if path.endswith("/checkout/confirm"):
            http.posts.append((path, deepcopy(body)))
            confirmations.append(deepcopy(body))
            return {"status": 200, "body": order, "retry_after": None}
        response = original_request(method, path, token, body)
        if path.endswith("/messages"):
            http.run["result"]["result"]["plans"] = [{"plan_id": "own-plan", "trip_id": "trip-own", "state_version": 0}]
        return response

    def checkout_get(path, token=None):
        if path.endswith("/orders") and confirmations:
            return {"orders": [order]}
        return original_get(path, token)

    http.request, http.get = checkout_request, checkout_get
    replay, source = setup_replay(tmp_path, http)
    source[0]["actions"] = [{"action": "A1", "kind": "checkout/preview"},
        {"action": "A2", "kind": "checkout/confirm", "request_body": {"confirmed": True}}]
    replay.frozen["include_simulated_checkout"] = True
    replay.case(source[0])
    assert len(confirmations) == 2 and confirmations[0] == confirmations[1]
    report = json.loads((replay.folder / "ZAI100-001.json").read_text())
    assert report["quote_idempotency"] == "PASS_TYPED_SIMULATED_ORDER_ONLY"
    assert len(report["checkout_orders"]) == 1
    assert "END_OF_DIALOGUE_PROBE" in report["source_action_timing"]
    assert "idempotency_key" not in json.dumps(report)
