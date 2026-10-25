"""Pure oracle counterexamples; these are not actual model acceptance results."""
import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("pilot_oracle", ROOT / "scripts/check_acceptance_pilot.py")
ORACLE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ORACLE)
CASES = {case["id"]: case for case in json.loads((ROOT / "fixtures/evaluation/oracle_bindings/pilot-v2.json").read_text(encoding="utf-8"))["cases"]}


def observed(case_id):
    before = {"origin": None, "travelers": [], "groups": [], "legs": [], "stays": [],
              "budget": None, "constraints": [], "preferences": []}
    trip = deepcopy(before)
    if case_id in {"NLU-03", "NLU-06"}:
        trip["stays"] = [{"id": "stay-unit", "city": "Сочи" if case_id == "NLU-03" else "Стамбул"}]
        trip["budget"] = {"amount_minor": 10000000 if case_id == "NLU-03" else 12000000, "currency": "RUB", "scope": "whole_trip"}
    if case_id == "NLU-06":
        trip["origin"] = "Санкт-Петербург"
    if case_id == "NLU-09":
        trip.update(origin="Москва", travelers=[{"id": "adult-unit", "type": "adult"}])
    return {"case_id": case_id, "request_text": CASES[case_id]["turns"][0]["text"], "state_before": {"trip": before},
            "state_after": {"trip": trip, "status": "WAITING_USER", "waiting_reason": "clarification",
                            "pending_question": {"questions": ["Уточните недостающие параметры поездки?"]}},
            "actual_run": {"status": "completed", "result": {"result": {"plans": []}}}, "current_run_events": [], "orders": []}


@pytest.mark.parametrize("case_id", CASES)
def test_valid_typed_subset_never_claims_full_case_pass(case_id):
    result = ORACLE.evaluate_observation(CASES[case_id], observed(case_id))
    assert result["subset_status"] == "PASS_BOUND_CHECKS"
    assert result["full_case_status"] == "BLOCKED_ORACLE"
    assert "original_behavior" in result["unbound_check_ids"]


@pytest.mark.parametrize("case_id,mutation,failed_check", [
    ("NLU-01", lambda o: o["actual_run"]["result"]["result"].update(plans=[{"total_minor": 500000}]), "no_structured_offers_before_missing_parameters"),
    ("NLU-01", lambda o: o["state_after"]["trip"].update(stays=[{"city": "Сочи"}]), "destination_not_committed_without_choice"),
    ("NLU-03", lambda o: o["state_after"]["trip"].update(travelers=[{"type": "adult"}, {"type": "child", "age": 7}]), "family_occupancy_not_assumed"),
    ("NLU-03", lambda o: o["state_after"]["trip"]["budget"].update(scope="per_person"), "budget_scope_not_silently_changed"),
    ("NLU-06", lambda o: o["state_after"]["trip"].update(origin="Стамбул"), "origin_st_petersburg"),
    ("NLU-06", lambda o: o["state_after"]["trip"]["budget"].update(amount_minor=120000), "total_budget_120000"),
    ("NLU-06", lambda o: o["state_after"]["trip"]["stays"][0].update(city="Москва"), "destination_istanbul"),
    ("NLU-09", lambda o: o["state_after"]["trip"].update(travelers=[]), "adult_count_1"),
    ("NLU-09", lambda o: o["state_after"]["trip"].update(stays=[{"city": "Казань"}]), "destination_not_assumed"),
    ("NLU-09", lambda o: o["state_after"]["trip"].update(budget={"amount_minor": 10000000}), "budget_not_invented"),
    ("NLU-01", lambda o: o["state_after"]["pending_question"].update(questions=[""]), "missing_fields_recorded"),
    ("NLU-03", lambda o: o["current_run_events"].append({"event_type": "TOOL_CALL_STARTED"}), "no_supplier_tool_calls"),
    ("NLU-09", lambda o: o["orders"].append({"order_id": "unexpected"}), "no_order_created"),
])
def test_wrong_typed_output_is_detected(case_id, mutation, failed_check):
    observation = observed(case_id)
    mutation(observation)
    result = ORACLE.evaluate_observation(CASES[case_id], observation)
    assert result["subset_status"] == "FAIL_BOUND_CHECKS"
    assert {check["check_id"]: check["passed"] for check in result["checks"]}[failed_check] is False
    assert result["full_case_status"] == "BLOCKED_ORACLE"


def test_wrong_initial_state_is_not_graded_as_fresh_input():
    observation = observed("NLU-03")
    observation["state_before"]["trip"]["origin"] = "Казань"
    assert ORACLE.evaluate_observation(CASES["NLU-03"], observation)["subset_status"] == "BLOCKED_OBSERVATION_OR_BINDING"


def test_failed_model_run_does_not_qualify_typed_output():
    observation = observed("NLU-09")
    observation["actual_run"]["status"] = "failed"
    assert ORACLE.evaluate_observation(CASES["NLU-09"], observation)["subset_status"] == "BLOCKED_RUN"


@pytest.mark.parametrize("expression", [
    {"surface": "events", "operator": "count_where", "path": "", "where": {"$unknown_filter": True}, "expected": 0},
    {"surface": "trip", "operator": "all_of", "path": "", "children": []},
    {"surface": "trip", "operator": "execute_sql", "path": ""},
])
def test_unknown_or_empty_oracle_never_passes(expression):
    case = deepcopy(CASES["NLU-01"])
    next(check for check in case["binding"]["expectedChecks"] if check["binding_status"] == "BOUND")["expression"] = expression
    result = ORACLE.evaluate_observation(case, observed("NLU-01"))
    assert result["subset_status"] == "BLOCKED_OBSERVATION_OR_BINDING"
