"""Pure pilot oracle: grade saved observations; no HTTP, model, DB or env reads."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures/evaluation/oracle_bindings/pilot-v2.json"
MISSING = object()
EMPTY = {"origin": None, "travelers": [], "groups": [], "legs": [], "stays": [],
         "budget": None, "constraints": [], "preferences": []}


def pointer(value, path):
    if path and not path.startswith("/"):
        raise ValueError("invalid_pointer")
    try:
        for part in path[1:].split("/") if path else []:
            part = part.replace("~1", "/").replace("~0", "~")
            value = value[int(part)] if isinstance(value, list) else value[part]
        return value
    except (KeyError, IndexError, TypeError, ValueError):
        return MISSING


def matches(item, where):
    if "$any_nonempty" in where:
        if (set(where) != {"$any_nonempty"} or not isinstance(where["$any_nonempty"], list)
                or not where["$any_nonempty"] or not all(isinstance(key, str) and key for key in where["$any_nonempty"])):
            raise ValueError("invalid_filter")
        return isinstance(item, dict) and any(item.get(key) not in (None, "", []) for key in where["$any_nonempty"])
    if any(key.startswith("$") for key in where):
        raise ValueError("unsupported_filter")
    return not where or isinstance(item, dict) and all(item.get(key, MISSING) == value for key, value in where.items())


def evaluate_expression(expression, surfaces, baseline):
    op = expression["operator"]
    if op in {"all_of", "any_of"}:
        children = expression.get("children")
        if not isinstance(children, list) or not children:
            raise ValueError("empty_composite")
        results = [evaluate_expression(child, surfaces, baseline) for child in children]
        return all(results) if op == "all_of" else any(results)
    actual = pointer(surfaces[expression["surface"]], expression["path"])
    if actual is MISSING:
        return False
    if op == "eq":
        return type(actual) is type(expression["expected"]) and actual == expression["expected"]
    if op == "unchanged":
        if expression.get("baseline_ref") != "before_turn":
            raise ValueError("unsupported_baseline")
        wanted = pointer(baseline, expression["path"])
        return wanted is not MISSING and actual == wanted
    if op == "count_where":
        where = expression.get("where", {})
        matches({}, where)  # Validate the filter even when the observed array is empty.
        return isinstance(actual, list) and sum(matches(item, where) for item in actual) == expression["expected"]
    if op == "within":
        if (expression.get("where") != {"measure": "nonempty_strings"}
                or not set(expression["expected"]).intersection({"min", "max"})):
            raise ValueError("unsupported_measure")
        if not isinstance(actual, list):
            return False
        count = sum(isinstance(item, str) and bool(item.strip()) for item in actual)
        return expression["expected"].get("min", 0) <= count <= expression["expected"].get("max", float("inf"))
    raise ValueError("unsupported_operator")


def evaluate_observation(case, observation):
    binding = case["binding"]
    unbound = [check["check_id"] for check in binding["expectedChecks"] if check["binding_status"] != "BOUND"]
    result = {"case_id": case["id"], "binding_revision": binding["binding_revision"],
              "subset_status": "NOT_RUN", "full_case_status": "BLOCKED_ORACLE", "unbound_check_ids": unbound, "checks": []}
    try:
        if observation["case_id"] != case["id"] or observation["request_text"] != case["turns"][0]["text"]:
            raise ValueError("observation_case_or_text_mismatch")
        baseline = observation["state_before"]["trip"]
        if any(baseline.get(key, MISSING) != value for key, value in EMPTY.items()):
            raise ValueError("nonempty_initial_trip")
        run = observation["actual_run"]
        if run["status"] != "completed":
            result["subset_status"] = "BLOCKED_RUN"
            return result
        raw = run["result"]["result"]
        if not isinstance(raw, dict):
            raise ValueError("missing_run_result")
        surfaces = {"trip": observation["state_after"]["trip"], "session": observation["state_after"],
                    "events": observation["current_run_events"], "orders": observation["orders"],
                    "source": {**raw, "run_status": run["status"], **{key: raw.get(key, []) for key in ("plans", "offers", "live_offers")}}}
        for check in binding["expectedChecks"]:
            if check["binding_status"] == "BOUND":
                passed = evaluate_expression(check["expression"], surfaces, baseline)
                result["checks"].append({"check_id": check["check_id"], "passed": passed})
        if not result["checks"]:
            raise ValueError("no_bound_checks")
        result["subset_status"] = "PASS_BOUND_CHECKS" if all(check["passed"] for check in result["checks"]) else "FAIL_BOUND_CHECKS"
    except (KeyError, IndexError, TypeError, ValueError) as error:
        result.update(subset_status="BLOCKED_OBSERVATION_OR_BINDING", error_class=type(error).__name__)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--observations", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    raw = args.fixture.read_bytes()
    suite = json.loads(raw)
    report = {"scope": "BOUND_TYPED_SUBSET_ONLY_NOT_FULL_NLU", "fixture_sha256": hashlib.sha256(raw).hexdigest(),
              "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "network_calls": 0,
              "status": "DRY_RUN_NO_HTTP", "cases": []}
    if args.observations:
        saved = json.loads(args.observations.read_text(encoding="utf-8"))
        if saved["fixture_sha256"] != report["fixture_sha256"] or set(saved["observations"]) - {case["id"] for case in suite["cases"]}:
            raise ValueError("observation_fixture_mismatch")
        report["cases"] = [evaluate_observation(case, saved["observations"][case["id"]]) if case["id"] in saved["observations"]
                           else {"case_id": case["id"], "subset_status": "NOT_RUN", "full_case_status": "BLOCKED_ORACLE"} for case in suite["cases"]]
        report["status"] = "SUBSET_REPORT_FULL_CASES_UNQUALIFIED"
    else:
        report["cases"] = [{"case_id": case["id"], "bound_checks": sum(check["binding_status"] == "BOUND" for check in case["binding"]["expectedChecks"]),
                            "full_case_status": "BLOCKED_ORACLE"} for case in suite["cases"]]
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(text)
    print(text, end="")


if __name__ == "__main__":
    main()
