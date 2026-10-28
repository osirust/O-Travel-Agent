"""Offline partial after80 oracles. No HTTP, runtime, key, vault or DB access.

Full semantic acceptance is always INCOMPLETE while any behavior is unbound.
Old failure-recovery followups are not a fresh adaptive dialogue. A comparability
warning never hides independent corruption such as quote-price→budget mutation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from copy import deepcopy
from datetime import date
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "fixtures/evaluation/after80-oracles-v1.json"
PRIVATE = {"session_token", "token", "owner_token_hash", "authorization", "api_key", "api_secret", "password",
    "access_token", "refresh_token", "database_url", "headers", "idempotency_key"}


class Dependency(Exception):
    def __init__(self, code, status="BLOCKED_DEPENDENCY"):
        self.code, self.status = code, status


def sha(value):
    return hashlib.sha256(value).hexdigest()


def clean(value):
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items() if key.casefold() not in PRIVATE
            and not key.casefold().endswith(("_api_key", "_api_secret", "_access_token"))}
    if isinstance(value, list):
        return [clean(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)Bearer\s+[^\s\"']+", "Bearer [REDACTED]", value)
        return re.sub(r"(https?://[^\s?\"']+)\?[^\s\"']+", r"\1?[QUERY_REDACTED]", value)
    return value


def trip(snapshot):
    value = snapshot.get("trip", snapshot.get("state")) if isinstance(snapshot, dict) else None
    if not isinstance(value, dict) or not isinstance(value.get("travelers"), list) or not isinstance(value.get("stays"), list):
        raise Dependency("ACTUAL_STATE_MISSING", "MISSING_EVIDENCE")
    return value


def domain(value):
    return {key: item for key, item in value.items() if key != "state_version"}


def people(value):
    return {person["id"]: person for person in value["travelers"]}


def single_stay(value):
    if len(value["stays"]) != 1:
        raise Dependency("ONE_EXISTING_STAY_REQUIRED")
    return value["stays"][0]


def room_rows(value):
    return single_stay(value).get("rooms", [])


def maps(value):
    return {room["id"]: set(room.get("traveler_ids", [])) for room in room_rows(value)}


def occupants(value, number):
    rows = room_rows(value)
    if len(rows) < number:
        raise Dependency("ADDRESSABLE_ROOM_MISSING")
    return set(rows[number - 1].get("traveler_ids", []))


def demographic(value):
    return {"adults": sum(person.get("type") == "adult" for person in value["travelers"]),
        "child_ages": sorted((person.get("age") for person in value["travelers"] if person.get("type") == "child"), key=lambda x: -1 if x is None else x),
        "total": len(value["travelers"])}


def actual_by_turn(case):
    result = {}
    for index, observation in enumerate(case.get("turns", [])):
        if type(observation.get("turn")) is int:
            result[observation["turn"]] = (index, observation)
    return result


class Context:
    def __init__(self, case, spec):
        self.case, self.spec, self.turns = case, spec, actual_by_turn(case)
        self.anchors = {}
        baseline = spec.get("original_party")
        if baseline:
            for _, observation in sorted(self.turns.values(), key=lambda row: row[1]["turn"]):
                try:
                    value = trip(observation.get("after"))
                except Dependency:
                    continue
                actual = demographic(value)
                if actual["adults"] != baseline["adults"] or actual["child_ages"] != baseline["child_ages"]:
                    continue
                adults = [p for p in value["travelers"] if p.get("type") == "adult"]
                self.anchors.update({"a" + str(i): p["id"] for i, p in enumerate(adults, 1)})
                for age in baseline["child_ages"]:
                    matches = [p["id"] for p in value["travelers"] if p.get("type") == "child" and p.get("age") == age]
                    if len(matches) == 1:
                        self.anchors["c" + str(age)] = matches[0]
                self.original_ids = set(people(value))
                break
            if self.anchors:
                for _, observation in sorted(self.turns.values(), key=lambda row: row[1]["turn"]):
                    try:
                        value = trip(observation.get("after"))
                    except Dependency:
                        continue
                    added = set(people(value)) - self.original_ids
                    if len(added) == 1 and people(value)[next(iter(added))].get("type") == "child":
                        self.anchors["new_child"] = next(iter(added))
                        break

    def ref(self, name, before):
        if name == "room4_child":
            candidates = [identifier for identifier in occupants(before, 4) if people(before)[identifier].get("type") == "child"]
            if len(candidates) == 1:
                return candidates[0]
        elif name in self.anchors:
            return self.anchors[name]
        raise Dependency("STABLE_PERSON_REFERENCE_UNRESOLVED")


def preserve_except(before, after, keys):
    return {k: v for k, v in domain(before).items() if k not in keys} == {k: v for k, v in domain(after).items() if k not in keys}


def generic_checks(observation):
    output = []

    def add(identifier, ok, actual=None):
        output.append({"check_id": "O80-GEN-" + identifier, "status": "PASS" if ok else "FAIL", "actual": actual})

    try:
        before, after = observation["before"], observation["after"]
        _before_trip, a = trip(before), trip(after)
    except (KeyError, Dependency):
        return [{"check_id": "O80-GEN-EVIDENCE", "status": "MISSING_EVIDENCE", "reason": "BEFORE_AFTER_REQUIRED"}]
    run = observation.get("run") or {}
    add("IDENTITY", bool(before.get("session_id")) and before.get("session_id") == after.get("session_id")
        and before.get("trip_id") == after.get("trip_id") == a.get("trip_id"))
    add("VERSION", type(after.get("state_version")) is int and after["state_version"] == a.get("state_version")
        and after["state_version"] >= before.get("state_version", 0))
    identifiers = [p.get("id") for p in a["travelers"]]
    refs = bool(all(isinstance(x, str) and x for x in identifiers)) and len(identifiers) == len(set(identifiers))
    gids = {g.get("id") for g in a.get("groups", [])}
    refs = refs and len(gids) == len(a.get("groups", [])) and len({s.get("id") for s in a["stays"]}) == len(a["stays"])
    refs = refs and all(p.get("group_id") is None or p["group_id"] in gids for p in a["travelers"])
    refs = refs and all(set(g.get("traveler_ids", [])) <= set(identifiers) for g in a.get("groups", []))
    for group in a.get("groups", []):
        refs = refs and all(people(a).get(pid, {}).get("group_id") == group.get("id") for pid in group.get("traveler_ids", []))
    for person in a["travelers"]:
        if person.get("group_id") is not None:
            refs = refs and any(group.get("id") == person["group_id"] and person["id"] in group.get("traveler_ids", []) for group in a.get("groups", []))
    valid_dates = True
    for stay in a["stays"]:
        assigned = [identifier for room in stay.get("rooms", []) for identifier in room.get("traveler_ids", [])]
        refs = refs and set(assigned) <= set(identifiers) and len(assigned) == len(set(assigned))
        try:
            start = date.fromisoformat(stay["check_in"]) if stay.get("check_in") else None
            end = date.fromisoformat(stay["check_out"]) if stay.get("check_out") else None
            valid_dates = valid_dates and (start is None or end is None or start < end)
        except (ValueError, TypeError):
            valid_dates = False
    add("REFERENCES", refs)
    add("DATES", valid_dates)
    if run:
        error = run.get("error") or {}
        code = error.get("code")
        layer = "provider" if code in {"RATE_LIMITED", "MODEL_COOLDOWN", "REQUEST_TIMEOUT", "NETWORK_ERROR"} else "model_contract" if code == "INVALID_LLM_OPERATION" else "run"
        output.append({"check_id": "O80-GEN-EXECUTION", "status": "PASS" if run.get("status") == "completed" and not error else "FAIL",
            "layer": layer, "actual": {"status": run.get("status"), "error_code": code}})
    return output


def evaluate(rule, observation, ctx):
    before, after = trip(observation.get("before")), trip(observation.get("after"))
    kind, actual = rule["kind"], {}
    if kind == "initial":
        actual = {**demographic(after), "origin": after.get("origin"), "destinations": [s.get("city") for s in after["stays"]],
            "rooms": sum(len(s.get("rooms", [])) for s in after["stays"]), "budget": after.get("budget")}
        pending = (observation.get("after") or {}).get("pending_question") or {}
        remembered = pending.get("requested_destination", (pending.get("context") or {}).get("requested_destination")) or {}
        retained = rule["destination"] in actual["destinations"] or not actual["destinations"] and remembered.get("kind") == "city" and remembered.get("name") == rule["destination"]
        expected = ctx.spec["original_party"]
        ok = actual["adults"] == expected["adults"] and actual["child_ages"] == expected["child_ages"] and actual["origin"] == rule["origin"] and retained
        ok = ok and actual["rooms"] == rule["rooms"] and (after.get("budget") is None if rule["budget_minor"] is None else (after.get("budget") or {}).get("amount_minor") == rule["budget_minor"])
        ok = ok and all(s.get("check_in") == rule["check_in"] and s.get("check_out") == rule["check_out"] for s in after["stays"])
        if rule.get("forbid_guessed_membership"):
            ok = ok and not any(g.get("traveler_ids") for g in after.get("groups", [])) and not any(p.get("group_id") for p in after["travelers"])
        return ok, actual
    if kind in {"no_domain_change", "rooms_unchanged", "budget_unchanged"}:
        key = "budget" if kind == "budget_unchanged" else "stays" if kind == "rooms_unchanged" else None
        if key == "stays":
            actual = {"before": [s.get("rooms") for s in before["stays"]], "after": [s.get("rooms") for s in after["stays"]]}
        else:
            actual = {"before": before.get(key) if key else before, "after": after.get(key) if key else after}
        return actual["before"] == actual["after"], actual
    if kind in {"only_budget", "budget_delta", "budget_preference"}:
        prior, current = before.get("budget") or {}, after.get("budget") or {}
        if kind == "budget_delta" and (type(prior.get("amount_minor")) is not int or prior.get("currency") != "RUB"):
            raise Dependency("CANONICAL_RUB_BASE_REQUIRED")
        expected = prior["amount_minor"] + rule["delta"] if kind == "budget_delta" else rule["value"]
        keys = {"budget", "preferences"} if kind == "budget_preference" else {"budget"}
        ok = current.get("amount_minor") == expected and current.get("currency") == "RUB" and current.get("scope") == "whole_trip" and preserve_except(before, after, keys)
        if kind == "budget_preference":
            ok = ok and any(p.get("key") == "price_priority" and p.get("value") == "lowest_total" for p in after.get("preferences", []))
        return ok, {"expected_minor": expected, "before": prior, "after": current}
    if kind == "only_age":
        identifier = ctx.ref(rule["target"], before)
        if identifier not in people(before) or identifier not in people(after):
            raise Dependency("PREVIOUS_PERSON_NOT_COMMITTED_OR_REMOVED")
        if rule.get("required_room") and identifier not in occupants(before, rule["required_room"]):
            raise Dependency("ROOM_REFERENCE_DIFFERS_FROM_OLD_TRANSCRIPT", "NEEDS_ADAPTIVE")
        if rule.get("requires_previous_age_edit"):
            previous = ctx.turns.get(rule["turn"] - 1)
            if previous is None:
                raise Dependency("PREVIOUS_PRONOUN_REFERENCE_MISSING")
            pbefore, pafter = trip(previous[1].get("before")), trip(previous[1].get("after"))
            changed = [pid for pid in people(pbefore) if pid in people(pafter) and people(pbefore)[pid].get("age") != people(pafter)[pid].get("age")]
            if changed != [identifier]:
                raise Dependency("OLD_PRONOUN_REFERENCE_NOT_COMMITTED", "NEEDS_ADAPTIVE")
        expected = deepcopy(before)
        people(expected)[identifier]["age"] = rule["value"]
        return domain(after) == domain(expected), {"target": identifier, "before_age": people(before)[identifier].get("age"), "expected_age": rule["value"], "after_age": people(after)[identifier].get("age")}
    if kind in {"short_slot", "date_slot"}:
        pending = (observation.get("before") or {}).get("pending_question") or {}
        if rule.get("expected_pending") and pending.get("field") != rule["expected_pending"]:
            raise Dependency("ORIGINAL_SHORT_ANSWER_SLOT_CHANGED", "NEEDS_ADAPTIVE")
        if kind == "short_slot":
            return any(s.get("city") == rule["destination"] for s in after["stays"]), {"destination": rule["destination"]}
        stay = single_stay(after)
        return stay.get(rule["field"]) == rule["value"], {"field": rule["field"], "expected": rule["value"], "actual": stay.get(rule["field"])}
    if kind == "room_count":
        actual = len(room_rows(after))
        return actual == rule["count"] and set(people(before)) == set(people(after)), {"expected": rule["count"], "actual": actual}
    if kind == "room_map":
        prior_rooms = room_rows(before)
        if len(prior_rooms) != len(rule["rooms"]):
            raise Dependency("ROOM_COUNT_NOT_COMMITTED_BEFORE_ADDRESSING")
        expected = {room["id"]: {ctx.ref(ref, before) for ref in refs} for room, refs in zip(prior_rooms, rule["rooms"])}
        return maps(after) == expected and set(people(before)) == set(people(after)), {"expected": {k: sorted(v) for k, v in expected.items()}, "actual": {k: sorted(v) for k, v in maps(after).items()}}
    if kind == "separation":
        expected = [{ctx.ref(ref, before) for ref in group} for group in rule["families"]]
        groups = after.get("groups", [])
        if not groups and (observation.get("after") or {}).get("waiting_reason") == "clarification":
            raise Dependency("FAMILY_REQUIREMENT_HONESTLY_BLOCKED_NOT_IMPLEMENTED", "UNSUPPORTED")
        ok = len(groups) == len(expected)
        for members in expected:
            matches = [g for g in groups if set(g.get("traveler_ids", [])) == members]
            ok = ok and len(matches) == 1 and matches[0].get("keep_separate") is True
            if len(matches) == 1:
                ok = ok and all(people(after).get(pid, {}).get("group_id") == matches[0]["id"] for pid in members)
        return ok, {"expected_families": [sorted(x) for x in expected], "actual_groups": groups}
    if kind in {"room_tariffs_not_broadened", "room_tariffs_supported"}:
        keys = {"breakfast", "refundable"}
        constraints = [c for c in after.get("constraints", []) if c.get("key") in keys and c.get("hard") is True and c.get("value") is True]
        broad = [c for c in constraints if not c.get("room_id")]
        if kind == "room_tariffs_not_broadened":
            return not broad, {"unscoped_hard_requirements": broad}
        rooms = room_rows(before)
        if len(rooms) != 2:
            raise Dependency("TWO_ROOM_SCOPE_REQUIRED")
        wanted = {(rooms[0]["id"], "refundable"), (rooms[1]["id"], "breakfast")}
        actual = {(c.get("room_id"), c["key"]) for c in constraints}
        if actual != wanted:
            raise Dependency("PER_ROOM_TARIFF_CAPABILITY_NOT_FULFILLED", "UNSUPPORTED")
        return True, {"room_constraints": constraints}
    if kind in {"declared_total", "party_ceiling"}:
        if rule.get("needs_before_total") and len(before["travelers"]) != rule["needs_before_total"]:
            raise Dependency("OLD_EXPECTATION_REFERRED_TO_UNCOMMITTED_CHILD", "NEEDS_ADAPTIVE")
        value = len(after["travelers"])
        return (value <= rule["value"] if kind == "party_ceiling" else value == rule["value"]), {"expected": rule["value"], "actual": value}
    if kind == "new_child":
        if rule.get("skip_if_already_total") and len(before["travelers"]) == rule["skip_if_already_total"]:
            raise Dependency("OLD_FAILURE_RECOVERY_CHILD_ALREADY_COMMITTED", "NEEDS_ADAPTIVE")
        if len(before["travelers"]) != rule["expected_before_total"]:
            raise Dependency("DECLARED_BASE_PARTY_NOT_AVAILABLE")
        prior, current = people(before), people(after)
        added = set(current) - set(prior)
        ok = set(prior) <= set(current) and len(added) == 1 and len(current) == len(prior) + 1
        ok = ok and all(current[pid] == person for pid, person in prior.items())
        if len(added) == 1:
            child = current[next(iter(added))]
            ok = ok and child.get("type") == "child" and child.get("age") == rule["value"]
        if rule.get("keep_room_count"):
            ok = ok and len(room_rows(before)) == len(room_rows(after))
        return ok, {"added": [current[pid] for pid in added], "before_count": len(prior), "after_count": len(current)}
    if kind == "split_first_family":
        a = {ctx.ref("a1", before), ctx.ref("a2", before)}
        child3, child6, new = (ctx.ref(ref, before) for ref in ("c3", "c6", "new_child"))
        first, fourth = occupants(after, 1), occupants(after, 4)
        ok = {child3, child6} <= first and new in fourth and len(first & a) == len(fourth & a) == 1
        ok = ok and first | fourth == a | {child3, child6, new} and not first & fourth
        ok = ok and occupants(before, 2) == occupants(after, 2) and occupants(before, 3) == occupants(after, 3)
        return ok, {"room1": sorted(first), "room4": sorted(fourth)}
    if kind == "remove_second_room_adult":
        prior, current = people(before), people(after)
        if rule.get("allow_already_removed") and len(prior) == 5:
            return set(prior) == set(current) and domain(before) == domain(after), {"idempotent_recovery": True}
        if len(prior) != 6:
            raise Dependency("ORIGINAL_REMOVAL_BASE_DIFFERS", "NEEDS_ADAPTIVE")
        second = occupants(before, 2)
        candidates = {pid for pid in second if prior[pid].get("type") == "adult"}
        if rule.get("allow_already_removed"):
            assigned = set().union(*(maps(before).values()))
            candidates = {pid for pid, person in prior.items() if person.get("type") == "adult" and pid not in assigned}
        removed = set(prior) - set(current)
        ok = len(removed) == 1 and removed <= candidates and len(current) == 5 and demographic(after)["adults"] == 3
        ok = ok and not set(current) - set(prior) and occupants(before, 1) == occupants(after, 1)
        ok = ok and all(current[pid].get("age") == prior[pid].get("age") for pid in current if pid in prior)
        return ok, {"removed": sorted(removed), "allowed_adults": sorted(candidates), "actual_party": demographic(after)}
    raise Dependency("ORACLE_KIND_UNBOUND", "UNBOUND")


def evidence(observation, index, file):
    contracts = []
    for i, event in enumerate(observation.get("source_events", observation.get("events", []))):
        if event.get("event_type") in {"MODEL_EXTRACTION_VALIDATED", "MODEL_OPERATIONS_CHECKED"}:
            contracts.append({"pointer": f"/turns/{index}/source_events/{i}", "type": event["event_type"], "payload": event.get("payload")})
    return clean({"file": str(file), "observation_pointer": f"/turns/{index}", "user": observation.get("user"),
        "before": observation.get("before"), "after": observation.get("after"),
        "pending_before": (observation.get("before") or {}).get("pending_question"),
        "pending_after": (observation.get("after") or {}).get("pending_question"),
        "response": observation.get("assistant", observation.get("agent_messages")),
        "run_error": (observation.get("run") or {}).get("error"), "actual_raw_checked_contracts": contracts})


def grade_case(case, binding, spec, file):
    ctx, turns = Context(case, spec), []
    by_number = ctx.turns
    for source_turn in binding["turns"]:
        number = source_turn["turn"]
        output = {"turn_id": source_turn["turn_id"], "comparability": [], "checks": [],
            "response_semantics": "UNBOUND", "audit_refs": source_turn["audit_refs"]}
        if number not in by_number:
            output["execution_status"] = "NOT_RUN"
            turns.append(output)
            continue
        index, observation = by_number[number]
        output["execution_status"] = observation.get("execution_status", (observation.get("run") or {}).get("status", "UNKNOWN"))
        output["checks"] = generic_checks(observation)
        same_text = isinstance(observation.get("user"), str) and sha(observation["user"].encode()) == source_turn["user_sha256"]
        if not same_text:
            output["comparability"].append({"status": "NOT_COMPARABLE", "reason": "SOURCE_USER_TEXT_CHANGED"})
        if binding["case_id"] == "ZAI100-002" and number in {9, 10, 11}:
            pending = (observation.get("before") or {}).get("pending_question") or {}
            if pending.get("field") == "stay.rooms":
                output["comparability"].append({"status": "NEEDS_ADAPTIVE", "reason": "ORIGINAL_USER_NEVER_PROVIDED_ROOM_COUNT_SEARCH_PREMISE_INVALID"})
            if number == 11 and case.get("source_actions") and not any(action.get("kind") == "checkout/confirm" and action.get("status") == "REPLAYED_BEFORE_TURN" for action in case["source_actions"]):
                output["comparability"].append({"status": "NOT_COMPARABLE", "reason": "HISTORICAL_TYPED_CONFIRMATION_NOT_REPLAYED_BEFORE_ORDER_RECALL"})
        if same_text:
            for rule in spec.get("checks", []):
                if rule["turn"] != number:
                    continue
                result = {"check_id": rule["check_id"], "kind": rule["kind"]}
                if rule.get("finding_id"):
                    result["finding_id"] = rule["finding_id"]
                try:
                    ok, values = evaluate(rule, observation, ctx)
                    result.update(status="PASS" if ok else "FAIL", actual=clean(values))
                except Dependency as error:
                    result.update(status=error.status, reason=error.code)
                except (KeyError, TypeError, ValueError, IndexError):
                    result.update(status="MISSING_EVIDENCE", reason="REQUIRED_TYPED_VALUES_MISSING")
                output["checks"].append(result)
        output["unbound_case_specific"] = not source_turn["case_specific_checks"]
        if output["comparability"] or any(check["status"] != "PASS" for check in output["checks"]):
            output["evidence"] = evidence(observation, index, file)
        turns.append(output)
    return {"case_id": binding["case_id"], "full_behavioral_status": "INCOMPLETE_ORACLES_NOT_80PASS",
        "turns": turns, "check_status_counts": dict(Counter(check["status"] for turn in turns for check in turn["checks"]))}


def grade_folder(folder, manifest):
    output = {"format": "otravel.after80.offline-grade.v1", "source_sha256": manifest["source"]["sha256"],
        "manifest_revision": manifest["revision"], "full_behavioral_status": "INCOMPLETE_ORACLES_NOT_80PASS", "cases": []}
    progress = folder / "progress.json"
    if progress.is_file():
        data = json.loads(progress.read_text(encoding="utf-8"))
        actual_sha = (data.get("plan") or {}).get("source_sha256")
        if actual_sha != manifest["source"]["sha256"]:
            raise ValueError("result_source_sha_mismatch")
        output["reported_runtime_binding"] = clean(data.get("run_binding", {}))
    for binding in manifest["cases"]:
        file = folder / (binding["case_id"] + ".json")
        if not file.is_file():
            output["cases"].append({"case_id": binding["case_id"], "execution_status": "NOT_RUN", "full_behavioral_status": "UNASSESSED"})
            continue
        case = json.loads(file.read_text(encoding="utf-8"))
        if case.get("id") != binding["case_id"]:
            raise ValueError("result_case_identity_mismatch")
        output["cases"].append(grade_case(case, binding, manifest["case_specs"].get(binding["case_id"], {}), file))
    output["summary"] = {"case_files_found": sum("turns" in case for case in output["cases"]),
        "source_cases": 80, "source_turn_ids": 697, "fully_bound_case_oracles": 0,
        "check_status_counts": dict(Counter(check["status"] for case in output["cases"] for turn in case.get("turns", []) for check in turn["checks"])),
        "comparability_counts": dict(Counter(item["status"] for case in output["cases"] for turn in case.get("turns", []) for item in turn.get("comparability", [])))}
    return output


def validate_manifest(manifest):
    if manifest.get("format") != "otravel.after80.oracles.v1":
        raise ValueError("unsupported_manifest")
    cases = manifest.get("cases", [])
    if (len(cases) != 80 or {case.get("case_id") for case in cases} != {f"ZAI100-{n:03d}" for n in range(1, 81)}
            or sum(len(case.get("turns", [])) for case in cases) != 697):
        raise ValueError("incomplete_source_turn_coverage")
    seen = set()
    for case in cases:
        if [turn.get("turn") for turn in case["turns"]] != list(range(1, case["turn_count"] + 1)):
            raise ValueError("manifest_turn_order_invalid")
        for turn in case["turns"]:
            identifier = f"{case['case_id']}:T{turn['turn']}"
            if (turn.get("turn_id") != identifier or identifier in seen
                    or not re.fullmatch(r"[a-f0-9]{64}", turn.get("user_sha256", ""))):
                raise ValueError("manifest_turn_binding_invalid")
            seen.add(identifier)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    raw = args.manifest.read_bytes()
    manifest = json.loads(raw)
    validate_manifest(manifest)
    report = grade_folder(args.results, manifest)
    report["manifest_sha256"] = sha(raw)
    output = args.output or ROOT / ".local/evaluation/after80-20261026/offline-grades" / (args.results.name + "-" + uuid4().hex[:8] + ".json")
    if output.name in {"progress.json", "resume-vault.json"} or re.fullmatch(r"ZAI100-[0-9]{3}\.json", output.name):
        raise ValueError("cannot_overwrite_original_case_or_vault")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(clean(report), stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"output": str(output), "summary": report["summary"], "full_behavioral_status": report["full_behavioral_status"]}, ensure_ascii=False))
    return 1 if report["summary"]["check_status_counts"].get("FAIL") else 0


if __name__ == "__main__":
    raise SystemExit(main())
