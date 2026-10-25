"""Executor/grader guards only; synthetic observations are not model evidence."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "scripts/evaluate_dialogue_regressions.py"
SPEC = importlib.util.spec_from_file_location("dialogue_regressions", PATH)
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


def trip(**updates):
    return {"trip_id": "test-trip", "state_version": 0, **deepcopy(evaluation.EMPTY),
            "search_policy": {"mode": "DEMO"}, **updates}


def session(data, field=None, **context):
    ctx = {**context}
    pending = {"context": ctx, "field": field} if field or context else None
    return {"state_version": data["state_version"], "trip": deepcopy(data), "pending_question": pending,
            "status": "WAITING_USER", "waiting_reason": "clarification"}


def observation(before, after, reply="Какую дату поездки вы выбираете?"):
    return {"state_before": before, "state_after": after, "actual_run": {"status": "completed", "error": None},
            "assistant_text": reply, "assistant_reply_count": 1, "orders": [], "current_run_events": [],
            "clock_local_date": "2026-10-25", "runtime_clock_verified": True}


def country():
    return {"kind": "country", "name": "Сербия", "country_code": "RS"}


def city(name="Белград", code="RS"):
    return {"kind": "city", "name": name, "country_code": code}


def compound(city_name="Белград", **updates):
    return trip(origin="Москва", travelers=[{"id": "adult", "type": "adult"}],
        stays=[{"id": "stay", "city": city_name, "check_in": None, "check_out": None}],
        preferences=[{"key": "departure_time", "value": "early_morning"}], **updates)


def test_faq_bound_subset_pass_does_not_qualify_full_scenario():
    data = trip()
    graded = evaluation.grade_turn("A", 1, observation(session(data), session(data), "Да, Сербия — страна в Европе."))
    assert graded["subset_status"] == "PASS_BOUND_CHECKS"
    assert graded["full_scenario_status"] == "BLOCKED_ORACLE"


@pytest.mark.parametrize("mutation", ["origin", "country", "budget", "order", "tool", "error", "reply_count"])
def test_faq_mutation_or_unverified_common_guards_cannot_pass(mutation):
    obs = observation(session(trip()), session(trip()), "Да, Сербия — страна в Европе.")
    if mutation == "origin":
        obs["state_after"]["trip"]["origin"] = "Москва"
    elif mutation == "country":
        obs["state_after"] = session(trip(), "destination.city", requested_destination=country())
    elif mutation == "budget":
        obs["state_after"]["trip"]["budget"] = {"amount_minor": 1}
    elif mutation == "order":
        obs["orders"] = [{"id": "unexpected"}]
    elif mutation == "tool":
        obs["current_run_events"] = [{"event_type": "TOOL_CALL_STARTED"}]
    elif mutation == "error":
        obs["actual_run"] = {"status": "failed", "error": {"code": "INVALID_LLM_OPERATION"}}
    else:
        obs["assistant_reply_count"] = 0
    assert evaluation.grade_turn("A", 1, obs)["subset_status"] != "PASS_BOUND_CHECKS"


def test_contextual_typo_requires_visible_ack_not_only_saved_city():
    before = session(trip(), "destination.city", requested_destination=country())
    after = session(trip(), "destination.city", requested_destination=city())
    assert evaluation.grade_turn("A", 5, observation(before, after, "Понял как «Белград», Сербия. Какую дату выбираете?"))["subset_status"] == "PASS_BOUND_CHECKS"
    assert evaluation.grade_turn("A", 5, observation(before, after, "Какую дату выбираете?"))["subset_status"] == "FAIL_BOUND_CHECKS"


def test_missing_party_slot_is_dependency_not_permission_to_reinterpret_number():
    data = trip(origin="Москва", preferences=[{"key": "price_priority", "value": "lowest_total"}])
    after = session({**data, "travelers": [{"id": "adult", "type": "adult"}]}, "party.children", requested_destination=city())
    before = session(data, "budget.amount_minor", requested_destination=city())
    graded = evaluation.grade_turn("A", 8, observation(before, after))
    assert graded["subset_status"] == "DEPENDENCY_FAILED"
    assert evaluation.prerequisite("A", 8, before) == "DEPENDENCY_MISSING_PARTY_ADULTS"


def test_low_cost_does_not_force_origin_as_next_question():
    before = session(trip(), "destination.city", requested_destination=city())
    after = session(trip(preferences=[{"key": "price_priority", "value": "lowest_total"}]), "destination.city", requested_destination=city())
    assert evaluation.grade_turn("A", 6, observation(before, after))["subset_status"] == "PASS_BOUND_CHECKS"
    assert evaluation.prerequisite("A", 7, after) == "DEPENDENCY_MISSING_ORIGIN_CITY"


def test_compound_facts_bound_and_no_unnecessary_party_question():
    before = session(trip())
    after = session(compound(), "stay.check_in", requested_destination=city(), children_known=True)
    assert evaluation.grade_turn("B", 1, observation(before, after))["subset_status"] == "PASS_BOUND_CHECKS"
    after["pending_question"]["field"] = "party.adults"
    assert evaluation.grade_turn("B", 1, observation(before, after))["subset_status"] == "FAIL_BOUND_CHECKS"


@pytest.mark.parametrize("remembered,canonical_city,expected", [
    (None, "Белград", "PASS_BOUND_CHECKS"),
    ({"kind": "country", "name": "Сербия", "country_code": "RS"}, "Белград", "PASS_BOUND_CHECKS"),
    ({"kind": "country", "name": "Россия", "country_code": "RU"}, "Белград", "FAIL_BOUND_CHECKS"),
    ({"kind": "city", "name": "Белград", "country_code": "RU"}, "Белград", "FAIL_BOUND_CHECKS"),
    ({"kind": "city", "name": "Белград", "country_code": "RS"}, "Санкт-Петербург", "FAIL_BOUND_CHECKS"),
])
def test_compound_canonical_belgrade_needs_no_duplicate_metadata_but_rejects_contradiction(remembered, canonical_city, expected):
    context = {"children_known": True}
    if remembered is not None:
        context["requested_destination"] = remembered
    after = session(compound(canonical_city), "stay.check_in", **context)
    assert evaluation.grade_turn("B", 1, observation(session(trip()), after))["subset_status"] == expected


def test_conditional_yes_only_for_specific_destination_confirmation():
    already = session(compound("Санкт-Петербург"), "stay.check_in", requested_destination=city("Санкт-Петербург", "RU"), children_known=True)
    assert evaluation.prerequisite("B", 3, already) == "NOT_NEEDED_ALREADY_CHANGED"
    correct = session(compound(), "destination.city", requested_destination=city(), proposed_destination=city("Санкт-Петербург", "RU"), confirmation_required=True)
    assert evaluation.prerequisite("B", 3, correct) is None
    correct["pending_question"]["field"] = "origin.city"
    assert evaluation.prerequisite("B", 3, correct) == "DEPENDENCY_MISSING_DESTINATION_CONFIRMATION"


def test_canonical_belgrade_without_duplicate_country_metadata_allows_specific_spb_confirmation():
    before = session(compound(), "stay.check_in", children_known=True)
    after = session(compound(), "destination.city", children_known=True,
                    proposed_destination=city("Санкт-Петербург", "RU"), confirmation_required=True)
    assert evaluation.grade_turn("B", 1, observation(session(trip()), before))["subset_status"] == "PASS_BOUND_CHECKS"
    assert evaluation.grade_turn("B", 2, observation(before, after))["subset_status"] == "PASS_BOUND_CHECKS"
    assert evaluation.prerequisite("B", 3, after) is None
    after["pending_question"]["context"]["requested_destination"] = {"kind": "country", "name": "Россия", "country_code": "RU"}
    assert evaluation.prerequisite("B", 3, after) == "DEPENDENCY_MISSING_DESTINATION_CONFIRMATION"


def test_stale_sidebar_metadata_cannot_qualify_spb_destination_change():
    after = session(compound("Санкт-Петербург"), "stay.check_in", requested_destination=city(), children_known=True)
    assert not evaluation.selected_city(after, "Санкт-Петербург")
    assert evaluation.prerequisite("B", 3, after) == "DEPENDENCY_MISSING_DESTINATION_CONFIRMATION"


@pytest.mark.parametrize("verified,date", [(False, "2026-10-25"), (True, "2026-10-26"), (True, None)])
def test_unverified_or_different_clock_is_dependency(verified, date):
    obs = observation(session(trip()), session(compound(), "stay.check_in", requested_destination=city(), children_known=True))
    obs.update(runtime_clock_verified=verified, clock_local_date=date)
    assert evaluation.grade_turn("B", 1, obs)["subset_status"] == "DEPENDENCY_FAILED"


def test_tomorrow_collision_completed_human_question_not_silent_day_increment():
    data = compound("Санкт-Петербург")
    data["stays"][0]["check_in"] = "2026-10-26"
    before = session(data, "stay.check_out", requested_destination=city("Санкт-Петербург", "RU"), children_known=True)
    after = deepcopy(before)
    obs = observation(before, after, "Дата выезда должна быть позже заезда. Какую более позднюю дату вы выбираете?")
    assert evaluation.grade_turn("B", 5, obs)["subset_status"] == "PASS_BOUND_CHECKS"
    obs["state_after"]["trip"]["stays"][0]["check_out"] = "2026-10-27"
    assert evaluation.grade_turn("B", 5, obs)["subset_status"] == "FAIL_BOUND_CHECKS"


def test_empty_observation_is_never_pass():
    assert evaluation.grade_turn("A", 1, {})["subset_status"] == "FAIL_BOUND_CHECKS"


@pytest.mark.parametrize("url", ["https://localhost:8083", "http://example.com:8083", "http://localhost:8081", "http://token@localhost:8083", "http://localhost:8083/elsewhere"])
def test_http_client_rejects_non_owned_endpoint_without_http(url):
    with pytest.raises(ValueError):
        evaluation.CountingHTTP(url)


def test_freeze_and_default_dry_run_perform_zero_http(tmp_path, monkeypatch, capsys):
    expected = tmp_path / "before-model.expected.json"
    def forbidden(*args, **kwargs):
        raise AssertionError("No HTTP is permitted in freeze/dry-run")
    monkeypatch.setattr(evaluation.CountingHTTP, "request", forbidden)
    assert evaluation.main(["--freeze", "--expectations", str(expected)]) == 0
    assert expected.is_file()
    frozen = json.loads(expected.read_bytes())
    assert frozen["execution"] == {"status": "NOT_RUN", "http_calls": 0, "model_calls": 0}
    assert frozen["policy"]["original106_used"] is False
    assert evaluation.main(["--expectations", str(expected)]) == 0
    assert "DRY_RUN_NO_HTTP" in capsys.readouterr().out
    assert evaluation.main(["--freeze", "--expectations", str(expected)]) == 2


@pytest.mark.parametrize("cap", ["0", "1", "13", "100"])
def test_invalid_attempt_cap_never_contacts_http(cap, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No HTTP")
    monkeypatch.setattr(evaluation.CountingHTTP, "request", forbidden)
    assert evaluation.main(["--attempt-cap", cap, "--confirm-inference"]) == 2


def test_frozen_hashes_detect_changed_executor(tmp_path, monkeypatch):
    frozen = evaluation.make_expectations()
    frozen["executor_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="executor_or_grader_changed_after_freeze"):
        evaluation.validate_frozen(frozen)


class FakeHTTP:
    """In-memory protocol double; never opens a socket or imports the app."""
    def __init__(self):
        self.http_calls = 0
        self.sent = []
        self.started = False

    def get(self, route, token=None):
        self.http_calls += 1
        if route == "/health":
            return {"database": "ready"}
        if route == "/api/v1/providers/status":
            return {"_runtime": {"llm_provider": "ollama", "model": "qwen3.5:4b"}}
        if route.endswith("/events"):
            events = [{"event_type": "SESSION_CREATED", "run_id": None}]
            if self.started:
                events.append({"event_type": "MODEL_CALL_STARTED", "run_id": "run-test", "payload": {"provider": "ollama"}})
            return {"events": events}
        if route.endswith("/messages"):
            return {"messages": [{"role": "assistant", "run_id": "run-test", "text": "Да, Сербия — страна в Европе."}] if self.started else []}
        if route.endswith("/orders"):
            return {"orders": []}
        return session(trip())

    def request(self, method, route, token=None, body=None):
        self.http_calls += 1
        if route == "/api/v1/sessions":
            return 201, {"session_id": "test-session", "session_token": "private-test-token", "trip": trip()}
        self.sent.append(body["text"])
        self.started = True
        return 202, {"run_id": "run-test"}

    def wait(self, run_id, token):
        return {"status": "completed", "error": None, "llm": {"provider": "ollama", "model": "qwen3.5:4b"},
                "result": {"result": {"response_source": "llm"}}}


def execute_fake(tmp_path, selected, cap=12):
    http = FakeHTTP()
    report = {"cases": [], "actual_model_attempts": 0, "package_fingerprint": evaluation.package_fingerprint()}
    folder = tmp_path / "observations"
    folder.mkdir()
    target = tmp_path / "report.json"
    evaluation.execute(http, evaluation.make_expectations(), selected, report, target, folder, evaluation.Evidence(), cap)
    return http, report, target


def test_executor_counts_durable_owned_events_and_stops_before_cap(tmp_path):
    http, report, target = execute_fake(tmp_path, {"A"}, cap=2)
    assert http.sent == [evaluation.A_MESSAGES[0]]
    assert report["actual_model_attempts"] == 1
    assert report["stop_reason"] == "ATTEMPT_CAP_GUARD"
    assert report["cases"][0]["turns"][0]["provider_metadata_consistent"] is True
    saved = target.read_text(encoding="utf-8")
    assert "private-test-token" not in saved
    assert report["cases"][0]["full_scenario_status"] == "BLOCKED_ORACLE"


def test_executor_blocks_b_without_runtime_clock_confirmation_before_session_creation(tmp_path):
    http, report, target = execute_fake(tmp_path, {"B"})
    assert http.sent == [] and not http.started
    assert report["actual_model_attempts"] == 0
    assert report["cases"][0]["execution_status"] == "BLOCKED_CLOCK"
