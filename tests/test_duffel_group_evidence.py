"""Duffel group coverage is response-bound, with only intercepted test HTTP."""
import asyncio
import json
from copy import deepcopy

import httpx
import pytest
from test_providers import FLIGHT_REQUEST, settings

from otravel.providers import TravelGateway

WANTED = [{"type": "adult"}, {"type": "adult"}, {"age": 6}, {"age": 14}]
ACTUAL = [{"id": "pas-a1", "type": "adult", "age": None},
          {"id": "pas-a2", "type": "adult", "age": 40},
          {"id": "pas-c6", "type": "child", "age": 6},
          {"id": "pas-c14", "type": "adult", "age": 14}]


def wire_offer(passengers=ACTUAL):
    result = {"id": "off_group", "total_amount": "123.45", "total_currency": "EUR",
              "slices": [], "expires_at": "2026-11-01T00:00:00Z", "live_mode": False}
    if passengers is not ...:
        result["passengers"] = deepcopy(passengers)
    return result


def parse(passengers=ACTUAL, wanted=WANTED):
    gateway = TravelGateway({})
    try:
        return gateway._duffel_offer(wire_offer(passengers), {**deepcopy(FLIGHT_REQUEST), "passengers": deepcopy(wanted)})
    finally:
        asyncio.run(gateway.close())


def test_response_passengers_match_type_age_multiset_without_own_child_threshold():
    original = deepcopy(ACTUAL)
    offer = parse()
    details = offer["details"]
    assert details["passengers_count"] == details["received_passengers_count"] == 4
    assert details["requested_passengers_count"] == 4
    assert details["group_quote"] is True
    assert details["passenger_evidence"] == {"source": "response.passengers", "status": "verified",
        "reason": "type_age_multiset_match", "passengers": ACTUAL}
    assert ACTUAL == original
    assert offer["source_type"] == "SANDBOX" and details["availability_verified"] is False
    assert offer["amount_minor"] == 12345 and offer["currency"] == "EUR"


@pytest.mark.parametrize("response", [..., None, {}, "4"])
def test_missing_response_list_never_promotes_request_echo_to_group_proof(response):
    details = parse(response)["details"]
    assert details["passengers_count"] is None and details["received_passengers_count"] is None
    assert details["requested_passengers_count"] == 4 and details["group_quote"] is False
    assert details["passenger_evidence"]["status"] == "unknown"
    assert details["passenger_evidence"]["passengers"] == []


@pytest.mark.parametrize("response", [[], ACTUAL[:3], ACTUAL + [{"id": "pas-extra", "type": "adult"}]])
def test_response_count_zero_three_or_five_is_not_requested_four(response):
    details = parse(response)["details"]
    assert details["passengers_count"] == len(response)
    assert details["requested_passengers_count"] == 4
    assert details["group_quote"] is False and details["passenger_evidence"]["status"] == "mismatch"
    assert details["passenger_evidence"]["reason"] == "passengers_count_mismatch"


@pytest.mark.parametrize("change", ["duplicate_id", "missing_id", "empty_id", "spaced_id", "bad_age", "bool_age", "float_age", "bad_type", "nonobject"])
def test_ambiguous_or_malformed_response_has_no_group_proof_or_invented_ID_age(change):
    actual = deepcopy(ACTUAL)
    if change == "duplicate_id": actual[1]["id"] = actual[0]["id"]
    elif change == "missing_id": actual[1].pop("id")
    elif change == "empty_id": actual[1]["id"] = ""
    elif change == "spaced_id": actual[1]["id"] = " pas-a2 "
    elif change == "bad_age": actual[2]["age"] = "6"
    elif change == "bool_age": actual[2]["age"] = True
    elif change == "float_age": actual[2]["age"] = 6.0
    elif change == "bad_type": actual[2]["type"] = 2
    elif change == "nonobject": actual[2] = "passenger"
    details = parse(actual)["details"]
    assert details["passengers_count"] == 4 and details["group_quote"] is False
    assert details["passenger_evidence"]["status"] == "unknown"
    for row in details["passenger_evidence"]["passengers"]:
        assert set(row) <= {"id", "type", "age"}
    if change == "missing_id": assert "id" not in details["passenger_evidence"]["passengers"][1]


def test_response_child_age_mismatch_is_not_filled_from_request():
    actual = deepcopy(ACTUAL)
    actual[2]["age"] = 7
    details = parse(actual)["details"]
    assert details["group_quote"] is False
    assert details["passenger_evidence"]["status"] == "mismatch"
    assert details["passenger_evidence"]["passengers"][2]["age"] == 7


def test_response_missing_child_age_is_unknown_not_request_echo():
    actual = deepcopy(ACTUAL)
    actual[2].pop("age")
    details = parse(actual)["details"]
    assert details["group_quote"] is False and details["passenger_evidence"]["status"] == "unknown"
    assert "age" not in details["passenger_evidence"]["passengers"][2]


@pytest.mark.parametrize("age", [None, 14, 40])
def test_requested_adult_matches_explicit_airline_adult_without_inventing_age_or_threshold(age):
    row = {"id": "pas-adult", "type": "adult", "age": age}
    details = parse([row], [{"type": "adult"}])["details"]
    assert details["group_quote"] is True
    assert details["passenger_evidence"]["passengers"] == [row]


def test_age_payload_can_match_response_age_even_when_airline_calls_14yearold_adult():
    details = parse([{"id": "pas-14", "type": "adult", "age": 14}], [{"age": 14}])["details"]
    assert details["group_quote"] is True
    assert details["passenger_evidence"]["passengers"][0]["type"] == "adult"


def test_type_match_is_literal_not_inferred_from_age_over18():
    details = parse([{"id": "pas-unknown", "type": "child", "age": 40}], [{"type": "adult"}])["details"]
    assert details["group_quote"] is False and details["passenger_evidence"]["status"] == "mismatch"


@pytest.mark.parametrize("wanted", [None, [], {}, [{"type": "child"}], [{"age": None}], [{"age": True}]])
def test_unknown_request_signature_cannot_become_group_proof(wanted):
    details = parse(ACTUAL, wanted)["details"]
    assert details["passengers_count"] == 4 and details["group_quote"] is False
    assert details["passenger_evidence"]["status"] in {"unknown", "mismatch"}


def test_missing_type_for_requested_adult_is_unknown_but_no_age_is_invented():
    details = parse([{"id": "pas-a"}], [{"type": "adult"}])["details"]
    assert details["group_quote"] is False and details["passenger_evidence"]["status"] == "unknown"
    assert details["passenger_evidence"]["passengers"] == [{"id": "pas-a"}]


def test_response_order_is_not_canonical_traveler_mapping_and_age_match_leaves_typed_adult():
    response = [{"id": "pas-a", "type": "adult", "age": 14}, {"id": "pas-c", "type": "child", "age": 14}]
    for rows in (response, list(reversed(response))):
        details = parse(rows, [{"age": 14}, {"type": "adult"}])["details"]
        assert details["group_quote"] is True
        assert details["passenger_evidence"]["passengers"] == rows


def test_only_id_type_age_are_exported_not_names_documents_or_payment_data():
    response = [{"id": "pas-a", "type": "adult", "age": None, "given_name": "PRIVATE",
                 "identity_documents": [{"unique_identifier": "PRIVATE"}], "metadata": {"secret": "PRIVATE"}}]
    details = parse(response, [{"type": "adult"}])["details"]
    assert details["group_quote"] is True
    assert details["passenger_evidence"]["passengers"] == [{"id": "pas-a", "type": "adult", "age": None}]
    assert "PRIVATE" not in json.dumps(details)


@pytest.mark.parametrize("new_response,verified", [(ACTUAL, True), (ACTUAL[:3], False), (..., False)])
def test_search_and_test_offer_refresh_recompute_response_group_evidence(new_response, verified):
    calls = []
    def handler(request):
        calls.append((request.method, request.url.path))
        assert request.headers["Authorization"] == "Bearer duffel_test_synthetic_group"
        if request.method == "POST":
            assert request.url.path == "/air/offer_requests"
            assert json.loads(request.content)["data"]["passengers"] == WANTED
            return httpx.Response(200, json={"data": {"offers": [wire_offer()]}})
        assert request.method == "GET" and request.url.path == "/air/offers/off_group"
        return httpx.Response(200, json={"data": wire_offer(new_response)})
    async def run():
        gateway = TravelGateway(settings(handler, DUFFEL_ACCESS_TOKEN="duffel_test_synthetic_group"))
        try:
            request = {**deepcopy(FLIGHT_REQUEST), "passengers": deepcopy(WANTED)}
            first = (await gateway.search_flights(request, "duffel"))[0]
            assert first["details"]["group_quote"] is True
            refreshed = await gateway.reprice(first)
            assert refreshed["details"]["group_quote"] is verified
            assert refreshed["details"]["requested_passengers_count"] == 4
            assert refreshed["details"]["passengers_count"] == (len(new_response) if isinstance(new_response, list) else None)
            assert refreshed["details"]["reprice_kind"] == "test_offer_refresh"
            assert refreshed["source_type"] == "SANDBOX" and refreshed["details"]["availability_verified"] is False
            assert first["details"]["group_quote"] is True
        finally:
            await gateway.close()
    asyncio.run(run())
    assert calls == [("POST", "/air/offer_requests"), ("GET", "/air/offers/off_group")]
