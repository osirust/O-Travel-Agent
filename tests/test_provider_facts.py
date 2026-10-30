"""Pure LiteAPI evidence fixtures; no HTTP, keys, database or vendor calls."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from otravel.provider_facts import lite_rate_facts


NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
DEADLINE = "2026-11-05T12:00:00+00:00"


def example():
    requests = [{"adults": 2, "children": [3, 7]}, {"adults": 1, "children": []}, {"adults": 3, "children": [15]}]
    rates = [{"occupancyNumber": index, "adultCount": room["adults"], "childCount": len(room["children"]),
              "childrenAges": list(room["children"]), "maxOccupancy": max(2, room["adults"] + len(room["children"])),
              "boardType": "BI", "retailRate": {"taxesAndFees": None, "total": [{"amount": "1000.00", "currency": "RUB"}]},
              "cancellationPolicies": {"refundableTag": "RFN", "cancelPolicyInfos": [{"cancelTime": DEADLINE, "amount": "100.00", "currency": "RUB", "type": "amount"}], "hotelRemarks": None}}
             for index, room in enumerate(requests, 1)]
    return rates, requests


def test_three_exact_rooms_are_evidence_not_live_availability():
    rates, requests = example()
    facts = lite_rate_facts(rates, requests, NOW)
    assert facts["allocation_verified"] is True and facts["capacity_verified"] is True
    assert facts["mandatory_fees_known"] is True and facts["fees_included"] is True
    assert facts["constraint_evidence"] == {"breakfast": True, "refundable": True}
    assert facts["cancellation_deadline"] == DEADLINE
    assert facts["source_type"] == "SANDBOX"
    assert facts["availability_verified"] is False
    assert facts["supplier_verified"] is False and facts["ready_for_real_booking"] is False
    assert [row["occupancy_number"] for row in facts["room_facts"]] == [1, 2, 3]


def test_room_number_not_list_position_and_age_multiset_determine_allocation():
    rates, requests = example()
    rates[0]["childrenAges"] = [7, 3]
    assert lite_rate_facts([rates[2], rates[0], rates[1]], requests, NOW)["allocation_verified"] is True


@pytest.mark.parametrize("value", [None, [], {}, [None], ["text"]])
def test_malformed_rates_never_prove_positive_facts(value):
    _, requests = example()
    facts = lite_rate_facts(value, requests, NOW)
    assert facts["allocation_verified"] is False and facts["mandatory_fees_known"] is False
    assert facts["constraint_evidence"] == {} and facts["cancellation_deadline"] is None


@pytest.mark.parametrize("number", [None, 0, True, "1", 4, -1])
def test_exact_one_based_room_numbers_are_required(number):
    rates, requests = example()
    rates[0]["occupancyNumber"] = number
    assert lite_rate_facts(rates, requests, NOW)["allocation_verified"] is False


def test_duplicate_or_missing_room_cannot_be_repaired_from_request():
    rates, requests = example()
    assert lite_rate_facts([rates[0], rates[0], rates[2]], requests, NOW)["allocation_verified"] is False
    assert lite_rate_facts(rates[:2], requests, NOW)["allocation_verified"] is False


@pytest.mark.parametrize("field,value", [("adultCount", None), ("adultCount", True), ("adultCount", -1), ("adultCount", 3),
                                         ("childCount", None), ("childCount", True), ("childCount", "2"), ("childCount", 1),
                                         ("childrenAges", None), ("childrenAges", [3]), ("childrenAges", [3, 8]),
                                         ("childrenAges", [3, "7"]), ("childrenAges", [True, 7]), ("childrenAges", [-1, 7])])
def test_guest_parameters_are_not_filled_or_coerced(field, value):
    rates, requests = example()
    rates[0][field] = value
    assert lite_rate_facts(rates, requests, NOW)["allocation_verified"] is False


def test_missing_age_is_allowed_only_for_explicit_zero_children():
    rates, requests = example()
    rates[1].pop("childrenAges")
    assert lite_rate_facts(rates, requests, NOW)["allocation_verified"] is True
    rates[1]["childrenAges"] = None
    assert lite_rate_facts(rates, requests, NOW)["allocation_verified"] is True
    rates[1].pop("childCount")
    assert lite_rate_facts(rates, requests, NOW)["allocation_verified"] is False
    rates, requests = example()
    rates[0].pop("childrenAges")
    assert lite_rate_facts(rates, requests, NOW)["allocation_verified"] is False


@pytest.mark.parametrize("capacity", [None, 0, -1, True, "4", 3])
def test_present_capacity_must_be_valid_and_sufficient(capacity):
    rates, requests = example()
    rates[0]["maxOccupancy"] = capacity
    facts = lite_rate_facts(rates, requests, NOW)
    assert facts["allocation_verified"] is False and facts["capacity_verified"] is False


def test_missing_capacity_is_not_invented_or_declared_verified():
    rates, requests = example()
    rates[0].pop("maxOccupancy")
    facts = lite_rate_facts(rates, requests, NOW)
    assert facts["capacity_verified"] is None
    assert facts["room_facts"][0]["reported_capacity"] is None
    assert facts["allocation_verified"] is True  # Counts match; capacity-if-present contract.


@pytest.mark.parametrize("requested", [None, [], {}, [{"adults": True, "children": []}], [{"adults": 2}], [{"adults": 2, "children": None}]])
def test_missing_request_guest_information_is_not_assumed(requested):
    rates, _ = example()
    assert lite_rate_facts(rates, requested, NOW)["allocation_verified"] is False


def test_explicit_null_taxes_differs_from_absent_key():
    rates, requests = example()
    assert lite_rate_facts(rates, requests, NOW)["mandatory_fees_known"] is True
    rates[0]["retailRate"].pop("taxesAndFees")
    unknown = lite_rate_facts(rates, requests, NOW)
    assert unknown["mandatory_fees_known"] is False and unknown["fees_included"] is None
    rates[0]["retailRate"]["taxesAndFees"] = []
    assert lite_rate_facts(rates, requests, NOW)["fees_included"] is True


def test_only_all_reported_included_taxes_close_fee_evidence():
    rates, requests = example()
    rates[0]["retailRate"]["taxesAndFees"] = [{"included": True, "amount": "25.50", "currency": "RUB"}]
    assert lite_rate_facts(rates, requests, NOW)["mandatory_fees_known"] is True
    rates[2]["retailRate"]["taxesAndFees"] = [{"included": False, "amount": "10", "currency": "RUB"}]
    facts = lite_rate_facts(rates, requests, NOW)
    assert facts["fees_included"] is False and facts["mandatory_fees_known"] is False
    assert "excluded_fees" in {item["code"] for item in facts["issues"]}


@pytest.mark.parametrize("tax", [None, {}, {"included": "true", "amount": 1, "currency": "RUB"},
                                 {"included": True, "amount": -1, "currency": "RUB"},
                                 {"included": True, "amount": "NaN", "currency": "RUB"},
                                 {"included": True, "amount": True, "currency": "RUB"},
                                 {"included": True, "amount": 1}, {"included": True, "amount": 1, "currency": "RUBLE"}])
def test_malformed_or_negative_tax_is_not_included_evidence(tax):
    rates, requests = example()
    rates[0]["retailRate"]["taxesAndFees"] = [tax]
    assert lite_rate_facts(rates, requests, NOW)["mandatory_fees_known"] is False


@pytest.mark.parametrize("boards,expected", [(["BI", "BI", "BI"], True), (["RO", "RO", "RO"], False),
                                           (["BI", "RO", "BI"], None), (["BI", None, "BI"], None),
                                           (["AI", "AI", "AI"], None)])
def test_breakfast_requires_uniform_explicit_bi_or_ro(boards, expected):
    rates, requests = example()
    for rate, board in zip(rates, boards):
        rate["boardType"] = board
    evidence = lite_rate_facts(rates, requests, NOW)["constraint_evidence"]
    if expected is None:
        assert "breakfast" not in evidence
    else:
        assert evidence["breakfast"] is expected


def test_nrfn_is_negative_evidence_without_assuming_a_deadline():
    rates, requests = example()
    for rate in rates:
        rate["cancellationPolicies"] = {"refundableTag": "NRFN", "cancelPolicyInfos": []}
    facts = lite_rate_facts(rates, requests, NOW)
    assert facts["refundable_tag_verified"] is True and facts["refundable_from_tags"] is False
    assert facts["constraint_evidence"]["refundable"] is False
    assert facts["free_cancellation_now"] is False and facts["cancellation_deadline"] is None


@pytest.mark.parametrize("cancel_time,zone,expected", [
    ("2026-11-05T15:00:00+03:00", None, DEADLINE),
    ("2026-11-05T12:00:00Z", None, DEADLINE),
    ("2026-11-05 12:00:00", "GMT", DEADLINE),
    ("2026-11-05 12:00:00 UTC", None, DEADLINE),
    ("2026-11-05 15:00:00 GMT+03:00", None, DEADLINE),
    ("2026-11-05 12:00:00", None, None),
    ("2026-11-05 12:00:00", "CST", None),
    ("2026-11-05", "UTC", None),
    ("2026-11-05T15:00:00+03:00", "UTC", None),
    ("2026-11-05 12:00:00 UTC", "GMT+03:00", None),
    ("2026-11-05T12:00:00.1234567Z", None, None),
])
def test_exact_deadline_requires_unambiguous_explicit_timezone(cancel_time, zone, expected):
    rates, requests = example()
    for rate in rates:
        info = rate["cancellationPolicies"]["cancelPolicyInfos"][0]
        info["cancelTime"] = cancel_time
        if zone is not None:
            info["timezone"] = zone
    facts = lite_rate_facts(rates, requests, NOW)
    assert facts["cancellation_deadline"] == expected
    if expected is None:
        assert "refundable" not in facts["constraint_evidence"]


def test_policy_level_gmt_is_explicit_timezone_not_a_geographic_guess():
    rates, requests = example()
    for rate in rates:
        rate["cancellationPolicies"]["timezone"] = "GMT"
        rate["cancellationPolicies"]["cancelPolicyInfos"][0]["cancelTime"] = "2026-11-05 12:00:00"
    assert lite_rate_facts(rates, requests, NOW)["cancellation_deadline"] == DEADLINE


def test_current_positive_penalty_is_not_free_even_with_rfn_tag():
    rates, requests = example()
    at_deadline = datetime.fromisoformat(DEADLINE)
    facts = lite_rate_facts(rates, requests, at_deadline)
    assert facts["refundable_from_tags"] is True
    assert facts["free_cancellation_now"] is False
    assert facts["constraint_evidence"]["refundable"] is False
    assert facts["cancellation_deadline"] == DEADLINE
    assert all(row["cancellation_charge_applies_now"] is True for row in facts["cancellation_by_room"])


def test_explicit_zero_current_charge_is_not_free_forever_without_deadline():
    rates, requests = example()
    for rate in rates:
        rate["cancellationPolicies"]["cancelPolicyInfos"] = [{"cancelTime": "2026-10-01T00:00:00Z", "amount": 0}]
    facts = lite_rate_facts(rates, requests, NOW)
    assert facts["free_cancellation_now"] is True and facts["cancellation_deadline"] is None
    assert "refundable" not in facts["constraint_evidence"]


def test_rfn_without_policy_or_timezone_does_not_create_refund_guarantee():
    rates, requests = example()
    rates[0]["cancellationPolicies"]["cancelPolicyInfos"] = None
    facts = lite_rate_facts(rates, requests, NOW)
    assert facts["refundable_from_tags"] is True
    assert "refundable" not in facts["constraint_evidence"]
    assert facts["cancellation_deadline"] is None
    assert facts["cancellation_by_room"][1]["cancellation_deadline"] == DEADLINE


@pytest.mark.parametrize("amount", [-1, "NaN", "Infinity", True, None])
def test_bad_cancellation_amounts_remain_unknown(amount):
    rates, requests = example()
    rates[0]["cancellationPolicies"]["cancelPolicyInfos"][0]["amount"] = amount
    assert "refundable" not in lite_rate_facts(rates, requests, NOW)["constraint_evidence"]


def test_contradictory_same_instant_fees_are_unknown():
    rates, requests = example()
    for rate in rates:
        rate["cancellationPolicies"]["cancelPolicyInfos"].append({"cancelTime": DEADLINE, "amount": 0})
    facts = lite_rate_facts(rates, requests, NOW)
    assert facts["cancellation_deadline"] is None and "refundable" not in facts["constraint_evidence"]


def test_bundle_deadline_is_earliest_exact_room_deadline():
    rates, requests = example()
    rates[1]["cancellationPolicies"]["cancelPolicyInfos"][0]["cancelTime"] = "2026-11-01T06:00:00Z"
    assert lite_rate_facts(rates, requests, NOW)["cancellation_deadline"] == "2026-11-01T06:00:00+00:00"


def test_unknown_tag_and_remarks_do_not_become_free_cancellation_or_html():
    rates, requests = example()
    rates[0]["cancellationPolicies"]["refundableTag"] = "UNKNOWN"
    rates[0]["cancellationPolicies"]["hotelRemarks"] = "<script>untrusted hotel text</script>"
    facts = lite_rate_facts(rates, requests, NOW)
    assert "refundable" not in facts["constraint_evidence"]
    assert "<script>" not in repr(facts)
    assert "hotel_remarks_present" in {issue["code"] for issue in facts["issues"]}


def test_input_is_unchanged_and_source_cannot_be_promoted():
    rates, requests = example()
    rates[0]["source_type"] = "LIVE_BOOKABLE"
    before = deepcopy((rates, requests))
    facts = lite_rate_facts(rates, requests, NOW)
    assert (rates, requests) == before
    assert facts["source_type"] == "SANDBOX" and facts["availability_verified"] is False


def test_naive_clock_is_rejected_instead_of_assuming_utc():
    rates, requests = example()
    with pytest.raises(ValueError):
        lite_rate_facts(rates, requests, NOW.replace(tzinfo=None))
