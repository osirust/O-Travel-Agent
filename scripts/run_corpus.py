"""Evaluate the immutable 106-case corpus with explicit typed/adapter fixtures.

No DB, credentials, model inference or network. A typed assertion pass is never
reported as a natural-language or full-case pass. Unsupported/blocked cases
remain visible, including cases already covered elsewhere by integration tests.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import socket
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def require(condition, code):
    if not condition:
        raise AssertionError(code)


def single(*, city="Казань", origin="Москва", children=(), adults=2,
           start="2026-11-10", end="2026-11-17"):
    from otravel.domain import Budget, Group, RoomAllocation, Stay, Traveler, TripState
    people = [Traveler(id=f"adult-{i}", type="adult", group_id="family") for i in range(adults)]
    people += [Traveler(id=f"child-{i}", type="child", age=age, group_id="family") for i, age in enumerate(children)]
    ids = [person.id for person in people]
    return TripState(trip_id="corpus-typed-fixture", origin=origin, travelers=people,
                     groups=[Group(id="family", traveler_ids=ids, keep_separate=True)],
                     stays=[Stay(id="stay-1", city=city, check_in=start, check_out=end,
                                 rooms=[RoomAllocation(id="room-1", traveler_ids=ids)])],
                     budget=Budget(amount_minor=80_000_000))


def plans(state):
    from otravel.domain import validate_trip
    from otravel.inventory import DemoInventory, validate_plan
    require(validate_trip(state) == [], "positive_fixture_invalid")
    result = DemoInventory().search_trip(state)
    require(bool(result["plans"]), "positive_fixture_has_no_plan")
    require(all(validate_plan(state, item) == [] for item in result["plans"]), "positive_plan_invalid")
    return result["plans"]


def check_missing(params):
    from otravel.domain import Traveler, TripState, validate_trip
    from otravel.inventory import DemoInventory
    state = TripState(origin=params.get("origin"), travelers=[Traveler(id="a", type="adult")] if params.get("one_adult") else [])
    before = state.model_dump(mode="json")
    result = DemoInventory().search_trip(state)
    codes = [item["code"] for item in validate_trip(state)]
    require("missing_stays" in codes and not result["plans"] and result["questions"], "missing_inputs_not_blocked")
    require(state.model_dump(mode="json") == before, "unknown_input_invented")
    return {"issue_codes": codes, "questions": len(result["questions"]), "invented_state": False}


def check_alias(params):
    from otravel.domain import Operation, TripState, apply_operations
    state = TripState()
    result, _ = apply_operations(state, [Operation(type="set_origin", value={"city": "СПб"}),
                                       Operation(type="set_budget", value={"amount_minor": 12_000_000, "currency": "RUB", "scope": "whole_trip"})])
    require(result.origin == "Санкт-Петербург" and result.budget.amount_minor == 12_000_000, "alias_or_total_budget_wrong")
    require(result.stays == [] and result.travelers == [], "unprovided_people_or_dates_invented")
    return {"origin": result.origin, "budget_minor": result.budget.amount_minor, "missing_people_dates_preserved": True}


def check_geography(params):
    from otravel.domain import DomainError, canonical_city
    require(canonical_city("Питер") == "Санкт-Петербург", "supported_alias_failed")
    try:
        canonical_city(params["city"])
    except DomainError as exc:
        require(exc.code == "unsupported_city", "unsupported_city_wrong_error")
        return {"guard": exc.code, "requested_city": params["city"], "global_impossibility_claimed": False}
    raise AssertionError("unsupported_geography_silently_accepted")


def check_unsupported_constraint(params):
    from otravel.domain import Constraint
    from otravel.inventory import DemoInventory
    state = single()
    plans(state)  # Positive control: an always-empty engine must not pass.
    state.constraints = [Constraint(key=params["key"], value=params.get("value", True), hard=True)]
    before = state.model_dump(mode="json")
    result = DemoInventory().search_trip(state)
    require(not result["plans"] and any(item["code"] == "unsupported_constraint" for item in result["violations"]), "unknown_hard_constraint_ignored")
    require(state.model_dump(mode="json") == before, "hard_constraint_removed")
    return {"unsupported_key": params["key"], "offers": 0, "constraint_retained": True}


def check_invalid_date(params):
    from otravel.domain import DomainError, Operation, apply_operations
    state = single(); before = state.model_dump(mode="json")
    try:
        apply_operations(state, [Operation(type="upsert_stay", target_id="stay-1", value={"check_in": "2026-11-31"})])
    except DomainError as exc:
        require(exc.code == "invalid_operation", "calendar_date_wrong_error")
        require(state.model_dump(mode="json") == before, "invalid_date_mutated_input")
        return {"guard": exc.code, "original_unchanged": True}
    raise AssertionError("nonexistent_date_accepted")


def check_chronology(params):
    from otravel.domain import DomainError, Operation, apply_operations
    state = single(); before = state.model_dump(mode="json")
    try:
        apply_operations(state, [Operation(type="upsert_stay", target_id="stay-1",
                                          value={"check_in": "2027-01-02", "check_out": "2027-01-01"})])
    except DomainError as exc:
        require(exc.code == "invalid_dates", "chronology_wrong_error")
        require(state.model_dump(mode="json") == before, "invalid_batch_not_atomic")
        return {"guard": exc.code, "original_unchanged": True}
    raise AssertionError("reverse_chronology_accepted")


def check_group(params):
    state = single(children=(2, 9))
    result = plans(state)
    expected = {person.id for person in state.travelers}
    for plan in result:
        hotel = next(item for item in plan["offers"] if item["kind"] == "hotel")
        assigned = [person for room in hotel["room_assignments"] for person in room["traveler_ids"]]
        require(set(assigned) == expected and len(assigned) == len(expected), "guest_lost_or_duplicated")
        ages = {line["age"] for line in hotel["breakdown"] if line["kind"] == "child_nights"}
        require(ages == {2, 9}, "child_ages_not_used")
    return {"travelers": 4, "child_ages": [2, 9], "allocation_complete": True}


def check_birthday(params):
    state = single(children=(None,), start="2026-11-10", end="2026-11-17")
    state.travelers[-1].birth_date = date(2024, 11, 12)
    result = plans(state)[0]
    legs = [offer for offer in result["offers"] if offer["kind"] == "flight"]
    require(len(legs) == 2, "birthday_fixture_missing_legs")
    child_lines = [next(line for line in leg["breakdown"] if line.get("traveler_id") == "child-0") for leg in legs]
    require([line["age"] for line in child_lines] == [1, 2], "age_not_recomputed_per_leg")
    require(child_lines[1]["unit_minor"] > child_lines[0]["unit_minor"], "synthetic_infant_rate_not_changed")
    return {"ages_by_leg": [1, 2], "scope": "synthetic age calculation, not airline policy"}


def check_pets(params):
    from otravel.domain import Constraint
    state = single()
    state.constraints = [Constraint(key="pets", value=[{"kind": "dog", "weight_kg": 8}])]
    result = plans(state)
    require(all(offer.get("pets_allowed") is True and offer.get("max_pet_kg", 0) >= 8
                for plan in result for offer in plan["offers"]), "pet_rule_not_checked_across_components")
    return {"weight_kg": 8, "components_checked": sorted({item["kind"] for item in result[0]["offers"]}), "source": "DEMO"}


def check_nights(params):
    state = single(start=params["start"], end=params["end"])
    hotel = next(item for item in plans(state)[0]["offers"] if item["kind"] == "hotel")
    require(hotel["nights"] == params["expected_nights"], "calendar_night_count_wrong")
    return {"nights": hotel["nights"], "relative_language_parsed": False}


def check_route(params):
    from otravel.domain import Operation, apply_operations, sample_trip
    state, _ = apply_operations(sample_trip(), [Operation(type="set_origin", value={"city": "Москва"})])
    edges = [(leg.origin, leg.destination, leg.departure_date.isoformat()) for leg in state.legs]
    require(edges == [("Москва", "Стамбул", "2026-11-06"), ("Стамбул", "Анталья", "2026-11-09"), ("Анталья", "Москва", "2026-11-13")], "route_not_synchronized")
    hotel_nights = [item["nights"] for item in plans(state)[0]["offers"] if item["kind"] == "hotel"]
    require(hotel_nights == [3, 4], "route_nights_inconsistent")
    return {"edges": edges, "hotel_nights": hotel_nights, "source": "DEMO"}


def check_train(params):
    from otravel.domain import Leg
    state = single(city="Сочи", origin="Казань")
    state.legs = [Leg(id="out", origin="Казань", destination="Сочи", departure_date="2026-11-10", mode="train"),
                  Leg(id="back", origin="Сочи", destination="Казань", departure_date="2026-11-17", mode="train")]
    offer_kinds = [item["kind"] for item in plans(state)[0]["offers"]]
    require(offer_kinds.count("train") == 2 and "flight" not in offer_kinds, "forbidden_flight_substituted")
    return {"train_legs": 2, "flight_legs": 0, "actual_rail_timetable_verified": False}


def check_rates(params):
    from otravel.domain import Constraint
    state = single()
    state.constraints = [Constraint(key="breakfast", value=True), Constraint(key="refundable", value=True)]
    result = plans(state)
    require(all(item["breakfast"] and item["refundable"] for plan in result for item in plan["offers"] if item["kind"] == "hotel"), "rate_conditions_violated")
    return {"plans": len(result), "breakfast": True, "refundable": True, "deadline_verified": False}


def check_total(params):
    result = plans(single())
    totals = [plan["total_minor"] for plan in result]
    for plan in result:
        require(plan["total_minor"] == sum(item["total_minor"] for item in plan["offers"]), "component_missing_from_total")
        require(any(item["kind"] == "transfer" and item["total_minor"] > 0 for item in plan["offers"]), "transfer_not_costed")
        require(plan["booking_created"] is False and "listed_" in plan["price_scope"], "cost_scope_overclaimed")
    require(totals == sorted(totals), "ranking_not_by_plan_total")
    return {"plan_totals_minor": totals, "transfer_included": True, "scope": "listed demo components, not all real-world costs"}


def check_custom_reprice(params):
    from otravel.inventory import reprice_plan
    if params["case"] == "person_night_fee":
        lines = [{"unit_minor": 550000, "quantity": 7}, {"unit_minor": 100000, "quantity": 14}]
        expected, old = 5_250_000, 0
    else:
        lines = [{"unit_minor": 4_700_000, "quantity": 1}]
        expected, old = 4_700_000, 4_000_000
    plan = {"source_type": "DEMO", "booking_created": False, "total_minor": old,
            "offers": [{"source_type": "DEMO", "breakdown": lines, "total_minor": old}]}
    result = reprice_plan(plan)
    require(result["total_minor"] == expected and result["repriced"], "fee_or_reprice_math_wrong")
    require(plan["total_minor"] == old, "reprice_mutated_original")
    return {"before_minor": old, "after_minor": expected, "delta_minor": expected-old,
            "reprice_kind": result["reprice_kind"], "quote_consent_tested": False}


def check_currency(params):
    from otravel.domain import Budget
    from otravel.inventory import DemoInventory
    state = single(); plans(state)
    state.budget = Budget(amount_minor=1000000, currency="EUR")
    result = DemoInventory().search_trip(state)
    require(not result["plans"] and any(item["code"] == "unsupported_budget" for item in result["violations"]), "unsupported_fx_silently_converted")
    require(state.budget.currency == "EUR", "currency_silently_replaced")
    return {"guard": "unsupported_budget", "fx_conversion_implemented": False}


def check_preserve_budget(params):
    from otravel.domain import Constraint, Operation, apply_operations
    from otravel.inventory import DemoInventory, validate_plan
    state = single()
    if params.get("nonstop"):
        state.constraints = [Constraint(key="nonstop", value=True)]
        initial = state.model_dump(mode="json")
        changed, _ = apply_operations(state, [Operation(type="set_budget", value={"amount_minor": 8_000_000, "currency": "RUB", "scope": "whole_trip"})])
        require(changed.constraints == state.constraints and changed.stays == state.stays and changed.travelers == state.travelers, "unrelated_requirement_lost")
        require(state.model_dump(mode="json") == initial, "patch_mutated_original")
        require(changed.budget.amount_minor == 8_000_000, "budget_not_changed")
        require(not DemoInventory().search_trip(changed)["plans"], "unsupported_nonstop_claimed_verified")
        return {"budget_minor": 8_000_000, "hard_condition_retained": True, "nonstop_support": "unsupported"}
    plan = plans(state)[0]
    original_amount = plan["total_minor"]
    changed, _ = apply_operations(state, [Operation(type="set_budget", value={"amount_minor": max(1, original_amount-3_000_000), "currency": "RUB", "scope": "whole_trip"})])
    require(plan["total_minor"] == original_amount and changed.stays == state.stays, "fixed_plan_or_price_changed")
    require(any(item["code"] == "budget_exceeded" for item in validate_plan(changed, plan)), "fixed_plan_underbudget_not_blocked")
    return {"fixed_price_minor": original_amount, "new_budget_minor": changed.budget.amount_minor, "guard": "budget_exceeded"}


def check_headcount(params):
    from otravel.domain import Operation, apply_operations, validate_trip
    state = single(adults=3)
    changed, _ = apply_operations(state, [Operation(type="add_traveler", value={"id": "fourth", "type": "adult", "group_id": "family"})])
    corrected, _ = apply_operations(changed, [Operation(type="update_traveler", target_id="fourth", value={"type": "child", "age": None})])
    require(len(corrected.travelers) == 4 and sum(p.type == "adult" for p in corrected.travelers) == 3, "headcount_became_five")
    require(any(item["code"] == "missing_child_age" for item in validate_trip(corrected)), "unprovided_child_age_invented")
    return {"total": 4, "adults": 3, "children": 1, "age_still_unknown": True}


def check_pet_reversal(params):
    from otravel.domain import Operation, apply_operations
    original = single()
    before = plans(original)
    with_pet, _ = apply_operations(original, [Operation(type="set_constraint", value={"key": "pets", "value": [{"kind": "dog", "weight_kg": 8}], "hard": True})])
    pet_plans = plans(with_pet)
    restored, _ = apply_operations(with_pet, [Operation(type="clear_constraint", value={"key": "pets"})])
    after = plans(restored)
    require(restored.model_dump() == original.model_dump(), "pet_filter_not_removed_or_other_state_lost")
    require([p["total_minor"] for p in before] == [p["total_minor"] for p in after], "pet_cost_not_reversed")
    require(len(pet_plans) < len(before), "pet_positive_control_did_not_filter")
    return {"baseline_plans": len(before), "pet_plans": len(pet_plans), "restored_plans": len(after)}


def check_stale(params):
    from otravel.domain import Operation, apply_operations
    from otravel.inventory import validate_plan
    state = single(); previous = plans(state)[0]
    updated, _ = apply_operations(state, [Operation(type="shift_return", value={"days": 2})])
    updated.state_version = 1  # Explicit fixture of the repository's committed revision.
    require(any(item["code"] == "stale_plan" for item in validate_plan(updated, previous)), "old_plan_not_stale")
    fresh = plans(updated)[0]
    require(fresh["state_version"] == 1 and fresh["plan_id"] != previous["plan_id"], "rebuild_reused_old_plan")
    return {"old_version": 0, "new_version": 1, "stale_guard": True, "quote_order_storage_tested": False}


def check_empty(params):
    from otravel.domain import Budget
    from otravel.inventory import DemoInventory
    state = single(); plans(state)
    state.budget = Budget(amount_minor=1)
    before = state.model_dump(mode="json")
    result = DemoInventory().search_trip(state)
    require(result["plans"] == [] and result["warnings"], "no_match_not_explained")
    require(result["source_type"] == "DEMO" and state.model_dump(mode="json") == before, "no_match_scope_or_state_changed")
    return {"plans": 0, "warnings": len(result["warnings"]), "budget_minor": 1, "scope": "finite DEMO catalogue"}


def check_single_match(params):
    from otravel.domain import Constraint
    state = single(); state.constraints = [Constraint(key="refundable", value=True)]
    result = plans(state)
    require(len(result) == 1, "one_match_padded_to_three")
    return {"plans": 1, "invented_padding": False}


def check_structured(params):
    result = plans(single())
    required = {"plan_id", "total_minor", "currency", "offers", "source_type", "booking_created"}
    require(len(result) > 1 and all(required.issubset(plan) for plan in result), "comparison_not_structured")
    require(all(type(plan["total_minor"]) is int and plan["source_type"] == "DEMO" for plan in result), "comparison_price_or_provenance_wrong")
    return {"plans": len(result), "common_fields": sorted(required), "ui_render_tested": False}


async def mock_check(params):
    import httpx
    from otravel.providers import LLMGateway, ProviderError, TravelGateway
    calls = []
    kind = params["kind"]
    def handler(request):
        calls.append(request.method)
        if kind == "rate_limit":
            return httpx.Response(429, json={"error": {"metadata": {"limit_source": "unknown"}}})
        if kind == "timeout":
            raise httpx.ReadTimeout("synthetic fixture timeout", request=request)
        if kind == "invalid_model_json":
            return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})
        days = [(date(2026, 10, 25)+timedelta(days=i)).isoformat() for i in range(7)]
        require(request.url.params.get("forecast_days") == "7", "forecast_scope_changed")
        return httpx.Response(200, json={"timezone": "Europe/Istanbul", "current": {"temperature_2m": 20}, "daily": {"time": days}})
    settings = {"http_transport": httpx.MockTransport(handler), "SERPAPI_FREE_TIER_CONFIRMED": True,
                "SERPAPI_API_KEY": "evaluation_dummy_not_a_key", "DUFFEL_ACCESS_TOKEN": "duffel_test_evaluation_dummy",
                "LLM_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "evaluation_dummy_not_a_key",
                "OPENROUTER_MODEL": "google/gemma-4-31b-it:free"}
    gateway = LLMGateway(settings) if kind == "invalid_model_json" else TravelGateway(settings)
    try:
        if kind == "forecast":
            result = await gateway.weather("Стамбул", 41.0082, 28.9784)
            require(result["kind"] == "FORECAST" and "2026-11-20" not in result["daily"]["time"], "future_weather_guaranteed")
            require(result["attribution"] and "7" in result["warning"], "weather_warning_or_attribution_missing")
            require(len(calls) == 1, "unexpected_retry")
            return {"mock_http_attempts": 1, "forecast_days": 7, "future_date_not_forecast": True, "climate_not_implemented": True}
        try:
            if kind == "rate_limit":
                await gateway.search_hotels({"q": "Казань", "checkin": "2026-11-10", "checkout": "2026-11-12", "currency": "RUB", "occupancies": [{"adults": 2, "children": []}]}, provider="serpapi")
            elif kind == "timeout":
                await gateway.reprice({"provider": "duffel", "details": {"native_offer_id": "off_evaluation_dummy"}})
            else:
                await gateway.extract_operations({"state": {}, "user_message": "Синтетический тест структуры ответа"})
        except ProviderError as exc:
            expected = {"rate_limit": "RATE_LIMITED", "timeout": "REQUEST_TIMEOUT", "invalid_model_json": "INVALID_LLM_RESPONSE"}[kind]
            require(exc.code == expected and len(calls) == 1, "adapter_error_or_retry_wrong")
            return {"error_code": exc.code, "mock_http_attempts": 1, "stateful_harness_fallback_tested": False}
        raise AssertionError("injected_fault_not_rejected")
    finally:
        await gateway.close()


CHECKS = {
    "missing": check_missing, "alias": check_alias, "geography": check_geography,
    "unsupported_constraint": check_unsupported_constraint, "invalid_date": check_invalid_date,
    "chronology": check_chronology, "group": check_group, "birthday": check_birthday,
    "pets": check_pets, "nights": check_nights, "route": check_route, "train": check_train,
    "rates": check_rates, "total": check_total, "custom_reprice": check_custom_reprice,
    "currency": check_currency, "preserve_budget": check_preserve_budget,
    "headcount": check_headcount, "pet_reversal": check_pet_reversal, "stale": check_stale,
    "empty": check_empty, "single_match": check_single_match, "structured": check_structured,
    "mock_adapter": lambda params: asyncio.run(mock_check(params)),
}


def no_network(*args, **kwargs):
    raise AssertionError("network_forbidden_in_corpus_evaluation")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapping", type=Path, default=ROOT / "fixtures/evaluation/corpus-map.json")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/verification/corpus-evaluation-20261025.json")
    args = parser.parse_args()
    mapping = json.loads(args.mapping.read_text(encoding="utf-8"))
    source = ROOT / mapping["source_path"]
    require(hashlib.sha256(source.read_bytes()).hexdigest() == mapping["source_sha256"], "immutable_source_sha_mismatch")
    originals = {json.loads(line)["id"]: hashlib.sha256(line).hexdigest()
                 for line in source.read_bytes().splitlines(keepends=True) if line.strip()}
    require(set(originals) == {case["case_id"] for case in mapping["cases"]}, "source_case_mapping_incomplete")
    require(all(originals[case["case_id"]] == case["source_record_sha256"] for case in mapping["cases"]),
            "source_record_sha_mismatch")
    records = []
    with patch.object(socket.socket, "connect", no_network), patch("socket.create_connection", no_network), patch("socket.getaddrinfo", no_network):
        for case in mapping["cases"]:
            record = deepcopy(case)
            checker = case.get("check")
            record.update(real_nlu_evaluated=False, outbound_http_calls=0)
            if checker is None:
                record.update(execution_status="NOT_EXECUTED", case_status=case["disposition"], assertion_results=[])
            else:
                try:
                    facts = CHECKS[checker["name"]](checker.get("params", {}))
                    record.update(execution_status="ASSERTIONS_PASS", case_status=case["disposition"],
                                  assertion_results=[{"check_id": checker["name"], "passed": True, "facts": facts}])
                except Exception as exc:
                    record.update(execution_status="ASSERTIONS_FAIL", case_status="FINDING_OPEN",
                                  finding_id="C106-FAIL-"+case["case_id"],
                                  assertion_results=[{"check_id": checker["name"], "passed": False,
                                                     "error_type": type(exc).__name__,
                                                     "reason": str(exc) if isinstance(exc, AssertionError) else "fixture_or_code_error_requires_triage"}])
            records.append(record)
    counts = Counter(item["execution_status"] for item in records)
    report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "source_path": mapping["source_path"],
              "source_sha256": mapping["source_sha256"], "mapping_sha256": hashlib.sha256(args.mapping.read_bytes()).hexdigest(),
              "cases_considered": len(records), "execution_counts": dict(counts),
              "case_status_counts": dict(Counter(item["case_status"] for item in records)),
              "full_natural_language_case_passes": 0, "real_llm_calls": 0, "outbound_http_calls": 0,
              "database_used": False, "network_guard_active": True, "original_files_changed": False,
              "code_sha256": {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
                              ("otravel/domain.py", "otravel/inventory.py", "otravel/providers.py", "scripts/run_corpus.py")},
              "limitations": ["Typed fixtures bypass language extraction", "MockTransport is in-memory, not provider availability",
                              "No fresh DB/HTTP/UI/checkpoint/restart verification", "Unsupported guards do not implement the requested feature"],
              "cases": records}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"cases": len(records), "execution_counts": dict(counts),
                      "full_nlu_passes": 0, "real_llm_calls": 0, "outbound_http_calls": 0,
                      "evidence": str(args.output)}, ensure_ascii=False))
    return 1 if counts["ASSERTIONS_FAIL"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
