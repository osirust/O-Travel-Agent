"""Offline content-oracle examples; no runtime, model, provider or source vault."""
import hashlib
import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("after80_grader_test", ROOT / "scripts/grade_after80.py")
grader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(grader)


def state(adults=5, ages=(3, 6, 9, 12)):
    members = [{"id": "adult" + str(n), "type": "adult", "age": None, "group_id": None} for n in range(1, adults + 1)]
    members += [{"id": "child" + str(age), "type": "child", "age": age, "group_id": None} for age in ages]
    return {"trip_id": "trip", "state_version": 0, "origin": "Москва", "budget": {"amount_minor": 13_000_000,
        "currency": "RUB", "scope": "whole_trip"}, "travelers": members, "groups": [], "constraints": [], "preferences": [],
        "stays": [{"id": "stay", "city": "Сочи", "check_in": "2026-11-10", "check_out": "2026-11-16",
            "rooms": [{"id": "r1", "traveler_ids": ["adult1", "adult2", "child3", "child6"]},
                {"id": "r2", "traveler_ids": ["adult3", "adult4", "child9"]},
                {"id": "r3", "traveler_ids": ["adult5", "child12"]}]}]}


def wrap(value, pending=None):
    return {"session_id": "session", "trip_id": value["trip_id"], "state_version": value["state_version"],
        "trip": deepcopy(value), "pending_question": pending, "waiting_reason": "clarification" if pending else None}


def observation(before, after, n=2):
    return {"turn": n, "user": "synthetic source", "before": wrap(before), "after": wrap(after),
        "run": {"status": "completed"}, "assistant": ["synthetic answer"], "source_events": []}


def context(initial=None):
    initial = state() if initial is None else initial
    case = {"turns": [observation(initial, initial, 1)]}
    return grader.Context(case, {"original_party": {"adults": sum(p["type"] == "adult" for p in initial["travelers"]),
        "child_ages": sorted(p["age"] for p in initial["travelers"] if p["type"] == "child")}})


def test_same_counts_wrong_family_room_map_fails_and_correct_map_passes():
    before = state()
    rule = {"kind": "room_map", "rooms": [["a1", "a2", "c3", "c6"], ["a3", "a4", "c9"], ["a5", "c12"]]}
    assert grader.evaluate(rule, observation(before, before), context())[0] is True
    wrong = deepcopy(before)
    wrong["stays"][0]["rooms"][1]["traveler_ids"][0] = "adult5"
    wrong["stays"][0]["rooms"][2]["traveler_ids"][0] = "adult3"
    assert len(wrong["travelers"]) == len(before["travelers"])
    assert grader.evaluate(rule, observation(before, wrong), context())[0] is False


def test_targeted_child_edit_requires_same_id_and_preserves_every_other_fact():
    before, after = state(), state()
    grader.people(after)["child6"]["age"] = 7
    rule = {"kind": "only_age", "target": "c6", "value": 7}
    assert grader.evaluate(rule, observation(before, after), context())[0] is True
    grader.people(after)["child9"]["age"] = 7
    assert grader.evaluate(rule, observation(before, after), context())[0] is False


def test_new_nine_year_old_is_distinct_from_existing_child_and_keeps_originals():
    before, after = state(), state()
    after["travelers"].append({"id": "new-child", "type": "child", "age": 9, "group_id": None})
    rule = {"kind": "new_child", "value": 9, "expected_before_total": 9, "keep_room_count": True}
    assert grader.evaluate(rule, observation(before, after), context())[0] is True
    after["travelers"] = [p for p in after["travelers"] if p["id"] != "child9"]
    assert grader.evaluate(rule, observation(before, after), context())[0] is False


def test_old_add_recovery_after_success_needs_adaptive_but_eleventh_person_still_fails():
    before = state()
    before["travelers"].append({"id": "new-child", "type": "child", "age": 9, "group_id": None})
    rule = {"kind": "new_child", "value": 9, "expected_before_total": 9, "skip_if_already_total": 10}
    with pytest.raises(grader.Dependency) as caught:
        grader.evaluate(rule, observation(before, before), context())
    assert caught.value.status == "NEEDS_ADAPTIVE"
    after = deepcopy(before)
    after["travelers"].append({"id": "extra", "type": "child", "age": 9})
    assert grader.evaluate({"kind": "party_ceiling", "value": 10}, observation(before, after), context())[0] is False


def test_uncommitted_child_is_not_reported_as_deleted_by_later_room_request():
    rule = {"kind": "declared_total", "value": 10, "needs_before_total": 10}
    with pytest.raises(grader.Dependency) as caught:
        grader.evaluate(rule, observation(state(), state()), context())
    assert caught.value.status == "NEEDS_ADAPTIVE"
    assert caught.value.code == "OLD_EXPECTATION_REFERRED_TO_UNCOMMITTED_CHILD"


def removal_base():
    value = state(4, (4, 9))
    value["stays"][0]["rooms"] = [{"id": "r1", "traveler_ids": ["adult1", "adult2", "child4"]},
        {"id": "r2", "traveler_ids": ["adult3", "adult4", "child9"]}]
    return value


def test_room_only_adult_removal_fails_but_actual_roster_removal_passes():
    before = removal_base()
    after = deepcopy(before)
    after["stays"][0]["rooms"][1]["traveler_ids"].remove("adult4")
    rule = {"kind": "remove_second_room_adult", "allow_already_removed": False}
    assert grader.evaluate(rule, observation(before, after), context(before))[0] is False
    after["travelers"] = [p for p in after["travelers"] if p["id"] != "adult4"]
    assert grader.evaluate(rule, observation(before, after), context(before))[0] is True


def test_old_remove_recovery_cannot_delete_a_second_adult():
    before = removal_base()
    before["travelers"] = [p for p in before["travelers"] if p["id"] != "adult4"]
    before["stays"][0]["rooms"][1]["traveler_ids"].remove("adult4")
    rule = {"kind": "remove_second_room_adult", "allow_already_removed": True}
    assert grader.evaluate(rule, observation(before, before), context(before))[0] is True
    after = deepcopy(before)
    after["travelers"] = [p for p in after["travelers"] if p["id"] != "adult3"]
    assert grader.evaluate(rule, observation(before, after), context(before))[0] is False


def test_staywide_room_tariffs_fail_no_broadening_while_honest_missing_support_is_not_pass():
    before, after = removal_base(), removal_base()
    after["constraints"] = [{"key": key, "value": True, "hard": True, "stay_id": "stay"} for key in ("breakfast", "refundable")]
    assert grader.evaluate({"kind": "room_tariffs_not_broadened"}, observation(before, after), context(before))[0] is False
    assert grader.evaluate({"kind": "room_tariffs_not_broadened"}, observation(before, before), context(before))[0] is True
    with pytest.raises(grader.Dependency) as caught:
        grader.evaluate({"kind": "room_tariffs_supported"}, observation(before, before), context(before))
    assert caught.value.status == "UNSUPPORTED"


def test_correct_membership_without_persisted_separation_is_not_fulfillment():
    before, after = removal_base(), removal_base()
    groups = [["adult1", "adult2", "child4"], ["adult3", "adult4", "child9"]]
    after["groups"] = [{"id": f"g{index}", "traveler_ids": members, "keep_separate": False} for index, members in enumerate(groups)]
    for p in after["travelers"]:
        p["group_id"] = "g0" if p["id"] in groups[0] else "g1"
    rule = {"kind": "separation", "families": [["a1", "a2", "c4"], ["a3", "a4", "c9"]]}
    assert grader.evaluate(rule, observation(before, after), context(before))[0] is False
    for group in after["groups"]:
        group["keep_separate"] = True
    assert grader.evaluate(rule, observation(before, after), context(before))[0] is True


def test_missing_rooms_comparability_never_hides_quote_budget_regression_or_raw_evidence():
    before, after = state(), state()
    after["budget"]["amount_minor"] = 10_075_000
    obs = observation(before, after, 10)
    obs["before"]["pending_question"] = {"field": "stay.rooms", "text": "Сколько номеров?"}
    obs["source_events"] = [{"event_type": "MODEL_EXTRACTION_VALIDATED", "payload": {
        "structured_contract": {"value": {"operations": [{"type": "set_budget", "value": {"amount_minor": 10_075_000}}]}}}}]
    case = {"id": "ZAI100-002", "turns": [obs]}
    rule = {"check_id": "Q", "kind": "budget_unchanged", "turn": 10, "finding_id": "AFTER80-MONEY-QUOTE-001"}
    binding = {"case_id": "ZAI100-002", "turns": [{"turn": 10, "turn_id": "ZAI100-002:T10",
        "user_sha256": hashlib.sha256(obs["user"].encode()).hexdigest(), "case_specific_checks": ["Q"], "audit_refs": []}]}
    result = grader.grade_case(case, binding, {"checks": [rule]}, Path("synthetic.json"))
    turn = result["turns"][0]
    assert turn["comparability"][0]["status"] == "NEEDS_ADAPTIVE"
    assert next(check for check in turn["checks"] if check["check_id"] == "Q")["status"] == "FAIL"
    assert turn["evidence"]["actual_raw_checked_contracts"][0]["type"] == "MODEL_EXTRACTION_VALIDATED"
    assert turn["evidence"]["before"]["trip"]["budget"]["amount_minor"] == 13_000_000
    assert result["full_behavioral_status"] == "INCOMPLETE_ORACLES_NOT_80PASS"


def test_short_answer_to_a_new_question_is_needs_adaptive_not_automatic_date_failure():
    value = state()
    obs = observation(value, value, 3)
    obs["before"]["pending_question"] = {"field": "group.membership"}
    rule = {"kind": "date_slot", "field": "check_in", "value": "2026-11-10", "expected_pending": "stay.check_in"}
    with pytest.raises(grader.Dependency) as caught:
        grader.evaluate(rule, obs, context())
    assert caught.value.status == "NEEDS_ADAPTIVE"


def test_readonly_state_version_bump_is_not_a_noop_pass():
    before, after = state(), state()
    after["state_version"] += 1
    assert grader.evaluate({"kind": "no_domain_change"}, observation(before, after), context())[0] is False


def test_manifest_coverage_is_bound_to_unique_turns_and_rejects_substitution_081():
    manifest = json.loads(grader.MANIFEST.read_text(encoding="utf-8"))
    grader.validate_manifest(manifest)
    manifest["cases"][-1]["case_id"] = "ZAI100-081"
    with pytest.raises(ValueError, match="coverage"):
        grader.validate_manifest(manifest)
