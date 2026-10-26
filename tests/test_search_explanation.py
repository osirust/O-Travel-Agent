"""Causal, source-bound explanations; no database, model or network calls."""
import json
from copy import deepcopy

import pytest

from otravel.domain import TripState
from otravel.search_explanation import render_search_explanation


def state():
    return TripState(trip_id="owned-trip", state_version=3, origin="Казань", search_policy={"mode": "HYBRID"},
        stays=[{"id": "private-stay-1", "city": "Стамбул"}, {"id": "private-stay-2", "city": "Анталья"}],
        legs=[{"id": "private-leg-1", "origin": "Казань", "destination": "Стамбул"},
              {"id": "private-leg-2", "origin": "Стамбул", "destination": "Анталья"},
              {"id": "private-leg-3", "origin": "Анталья", "destination": "Казань"}])


def hotel(stay_id="private-stay-1", **facts):
    return {"offer_id": "PRIVATE-NATIVE-ID", "provider": "liteapi", "source_type": "SANDBOX",
        "details": {"kind": "hotel", "binding": {"stay_id": stay_id}, "mandatory_fees_known": True,
                    "allocation_verified": True, "capacity_verified": True, **facts}}


def result(offers=None, errors=None, **plan_fields):
    plan = {"trip_id": "owned-trip", "state_version": 3, "ready_for_simulated_checkout": True,
            "coverage": {"complete": True}, "offers": offers if offers is not None else [hotel()], **plan_fields}
    return {"plans": [plan], "component_errors": errors or []}


def error(code="NO_VERIFIED_COMPONENT", component="private-stay-1", message=""):
    return {"code": code, "details": {"component_id": component}, "message": message}


def test_actual_mixed_shape_explains_three_rejected_legs_without_inventing_EUR_or_global_failure():
    trip = state()
    offers = [hotel(), hotel("private-stay-2")]
    offers += [{"kind": "flight", "source_type": "DEMO"} for _ in range(3)]
    offers += [{"kind": "transfer", "source_type": "DEMO"} for _ in range(2)]
    errors = [error(component=f"private-leg-{i}", message="Нет пригодного тарифа: unpriced_or_fx_required. RAW-SECRET")
              for i in range(1, 4)]
    data = result(offers, errors)
    original_trip, original_data = trip.model_dump(mode="json"), deepcopy(data)
    rendered = render_search_explanation(trip, data)
    summary, text = rendered["summary"], rendered["text"]
    assert summary["status"] == "complete" and summary["source_counts"] == {"DEMO": 5, "SANDBOX": 2}
    assert summary["contains_demo"] and summary["uses_sandbox"] and not summary["uses_indicative"]
    assert summary["reasons"][0]["code"] == "price_not_comparable_or_unpriced"
    assert len(summary["reasons"]) == 1 and len(summary["reasons"][0]["targets"]) == 3
    assert "Казань → Стамбул" in text and "Стамбул → Анталья" in text and "Анталья → Казань" in text
    assert "валюте бюджета" in text and "SANDBOX" in text and "DEMO" in text
    assert all(secret not in json.dumps(rendered, ensure_ascii=False) for secret in
               ("PRIVATE-NATIVE-ID", "private-leg", "private-stay", "RAW-SECRET", "EUR", "30 секунд"))
    assert "получить проверяемый ответ источника не удалось" not in text
    assert original_trip == trip.model_dump(mode="json") and original_data == data


def test_no_source_offers_does_not_assert_sold_out_or_whole_market_coverage():
    rendered = render_search_explanation(state(), {"plans": [], "component_errors": [error("NO_MATCHES")]})
    assert rendered["summary"]["status"] == "empty"
    assert rendered["summary"]["source_counts"] == {}
    assert rendered["summary"]["reasons"][0]["code"] == "no_source_offers"
    assert "Стамбул" in rendered["text"] and "для этого запроса" in rendered["text"]
    assert "отсутствие мест этим не доказано" in rendered["text"]
    assert all(word not in rendered["text"].lower() for word in ("распродан", "нет свободных", "мест нет", "во всех отелях"))


@pytest.mark.parametrize("code,reason", [
    ("mandatory_fees_unknown", "fees_unverified"), ("allocation_unverified", "allocation_unverified"),
    ("capacity_unverified", "capacity_unverified"), ("group_quote_unverified", "group_fare_unverified"),
    ("hard_constraint_unverified", "hard_condition_unverified"), ("unpriced_or_fx_required", "price_not_comparable_or_unpriced"),
    ("not_component_total", "component_total_unverified"), ("stale_source_snapshot", "snapshot_requires_refresh"),
    ("invalid_source_lifetime", "snapshot_requires_refresh"), ("source_binding_mismatch", "identity_unverified"),
    ("unrequested_hotel_id", "identity_unverified"), ("provider_hotel_id_unknown", "identity_unverified"),
    ("source_mismatch", "identity_unverified"), ("invalid_shape", "source_data_unverified"),
    ("unknown_source_type", "source_data_unverified"), ("no_matches", "no_source_offers"),
])
def test_distinct_rejection_causes_remain_distinct_and_messages_never_echo(code, reason):
    rendered = render_search_explanation(state(), result(errors=[error(message=f"{code}, https://secret.invalid?api_key=SECRET")]))
    assert rendered["summary"]["reasons"] == [{"code": reason, "targets": [{"kind": "stay", "index": 1, "label": "Проживание: Стамбул"}]}]
    assert "SECRET" not in json.dumps(rendered) and "secret.invalid" not in rendered["text"]


@pytest.mark.parametrize("code,reason", [
    ("RATE_LIMITED", "source_rate_limited"), ("SOURCE_COOLDOWN", "source_rate_limited"),
    ("REQUEST_TIMEOUT", "source_failure"), ("NETWORK_ERROR", "source_failure"), ("HTTP_ERROR", "source_failure"),
    ("PROVIDER_FAILURE", "source_failure"), ("PROVIDER_RESPONSE_ERROR", "source_failure"), ("INVALID_RESPONSE", "source_failure"),
    ("CONFIG_MISSING", "source_not_configured"), ("CONFIG_BLOCKED", "source_not_configured"),
    ("UNSUPPORTED_CITY", "source_request_unsupported"), ("UNSUPPORTED_REQUEST", "source_request_unsupported"),
    ("UNSUPPORTED_PROVIDER", "source_request_unsupported"), ("TOOL_BUDGET_EXHAUSTED", "search_limit_reached"),
    ("fx_required", "price_not_comparable_or_unpriced"), ("UNTRUSTED_ERROR_api_key_SECRET", "source_data_unverified"),
])
def test_error_classification_does_not_promote_unknown_failure_to_missing_inventory(code, reason):
    rendered = render_search_explanation(state(), result(errors=[error(code, message="RAW-SECRET")]))
    assert rendered["summary"]["reasons"][0]["code"] == reason
    assert "RAW-SECRET" not in rendered["text"] and "api_key" not in json.dumps(rendered)
    if code in {"RATE_LIMITED", "SOURCE_COOLDOWN"}:
        assert "время восстановления не подтверждено" in rendered["text"]


@pytest.mark.parametrize("capacity", [None, False, "true", 1])
def test_absent_or_unconfirmed_capacity_is_not_sold_out(capacity):
    rendered = render_search_explanation(state(), result([hotel(capacity_verified=capacity)]))
    assert rendered["summary"]["reasons"][0]["code"] == "capacity_unverified"
    assert "не означает отсутствия мест" in rendered["text"]
    assert "распродан" not in rendered["text"].lower()


def test_selected_offer_fee_and_allocation_gaps_do_not_disappear_behind_SANDBOX_tag():
    rendered = render_search_explanation(state(), result([hotel(mandatory_fees_known=False, allocation_verified=None, capacity_verified=None)]))
    assert {x["code"] for x in rendered["summary"]["reasons"]} == {"fees_unverified", "allocation_unverified", "capacity_unverified"}
    assert "SANDBOX" in rendered["text"] and rendered["summary"]["real_booking_confirmed"] is False


def test_indicative_and_DEMO_are_not_rendered_as_bookable_or_actual_orders():
    data = result([{"kind": "flight", "source_type": "LIVE_INDICATIVE"}, {"kind": "transfer", "source_type": "DEMO"}])
    rendered = render_search_explanation(state(), data)
    assert "справочные цены поиска" in rendered["text"] and "демонстрационный расчёт" in rendered["text"]
    assert rendered["summary"]["uses_indicative"] and rendered["summary"]["contains_demo"]
    assert "не подтверждает наличие мест или реальное бронирование" in rendered["text"]


def test_first_current_plan_only_no_alternative_plan_double_counting():
    data = result([hotel()])
    data["plans"].append({**deepcopy(data["plans"][0]), "offers": [{"kind": "hotel", "source_type": "DEMO"}] * 3})
    rendered = render_search_explanation(state(), data)
    assert rendered["summary"]["source_counts"] == {"SANDBOX": 1}


@pytest.mark.parametrize("fields", [{"trip_id": "different-private-trip"}, {"state_version": 2}, {"state_version": True}])
def test_foreign_or_stale_plan_does_not_expose_its_offer_or_error_data(fields):
    rendered = render_search_explanation(state(), result(errors=[error("RATE_LIMITED")], **fields))
    assert rendered["summary"]["status"] == "stale_or_foreign"
    assert rendered["summary"]["source_counts"] == {} and rendered["summary"]["reasons"] == []
    assert "SANDBOX" not in rendered["text"] and "different-private-trip" not in rendered["text"]


def test_partial_current_plan_can_explain_source_failure_without_claiming_complete_trip():
    data = result(errors=[error("REQUEST_TIMEOUT")], ready_for_simulated_checkout=False)
    data["partial_plan"] = data["plans"].pop()
    rendered = render_search_explanation(state(), data)
    assert rendered["summary"]["status"] == "partial"
    assert rendered["summary"]["source_counts"] == {"SANDBOX": 1}
    assert rendered["summary"]["reasons"][0]["code"] == "source_failure"


def test_reason_limit_is_honest_and_transient_errors_do_not_get_drowned_by_generic_details():
    causes = ["mandatory_fees_unknown", "allocation_unverified", "capacity_unverified", "unpriced_or_fx_required", "hard_constraint_unverified"]
    data = result(errors=[error(message=", ".join(causes)), error("RATE_LIMITED")])
    rendered = render_search_explanation(state(), data, max_reasons=3)
    assert len(rendered["summary"]["reasons"]) == 3 and rendered["summary"]["omitted_reason_count"] == 3
    assert rendered["summary"]["reasons"][0]["code"] == "source_rate_limited"
    assert "ещё 3 причин" in rendered["text"]


@pytest.mark.parametrize("value", [None, {}, [], "RAW-SECRET", {"source_type": "SANDBOX"}, {"notice": "not a search"}])
def test_non_search_or_malformed_input_does_not_invent_search_results(value):
    assert render_search_explanation(state(), value)["summary"]["applicable"] is False
    assert render_search_explanation(state(), value)["text"] == ""


@pytest.mark.parametrize("source", [None, [], {}, "LIVE_BOOKABLE", "SECRET-SOURCE"])
def test_unknown_or_unsupported_source_tag_is_not_a_bookability_promise(source):
    rendered = render_search_explanation(state(), result([{"source_type": source, "kind": "hotel"}]))
    assert rendered["summary"]["source_counts"] == {"UNVERIFIED": 1}
    assert "LIVE_BOOKABLE" not in rendered["text"] and "SECRET-SOURCE" not in rendered["text"]
    assert rendered["summary"]["real_booking_confirmed"] is False


def test_no_fuzzy_labels_or_first_guess_for_duplicate_unknown_component_ids():
    trip = state().model_dump(mode="json")
    trip["stays"][0]["id"] = trip["legs"][0]["id"]
    trip["stays"][1]["city"] = "UNTRUSTED-CITY-SECRET"
    rendered = render_search_explanation(trip, result(errors=[error("NO_MATCHES", "private-leg-1"), error("NO_MATCHES", "private-stay-2")]))
    assert "UNTRUSTED" not in json.dumps(rendered)
    assert rendered["summary"]["reasons"][0]["targets"] == [
        {"kind": "trip", "index": None, "label": "Поездка"}, {"kind": "stay", "index": 2, "label": "Проживание №2"}]


def test_reason_token_must_be_complete_not_substring_of_provider_text():
    rendered = render_search_explanation(state(), result(errors=[error(message="not_mandatory_fees_unknown_extra")]))
    assert rendered["summary"]["reasons"][0]["code"] == "source_data_unverified"


@pytest.mark.parametrize("value", [False, True, 0, 2, 6, None, "4", 4.0])
def test_invalid_reason_bound_is_explicitly_rejected(value):
    with pytest.raises(ValueError, match="between 3 and 5"):
        render_search_explanation(state(), result(), max_reasons=value)


def test_pure_DEMO_policy_is_not_presented_as_HYBRID_source_search():
    assert not render_search_explanation(TripState(), result())["summary"]["applicable"]
