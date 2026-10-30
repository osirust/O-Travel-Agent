"""Selected sandbox offer prebook; no external requests, booking or payment."""
import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from otravel.providers import ProviderError, TravelGateway

NOW = datetime(2026, 10, 27, 8, 0, tzinfo=UTC)


def rate(number=1, *, amount="100.50", currency="RUB", adults=1, children=None):
    children = [] if children is None else children
    return {"rateId": f"prebook-new-rate-{number}", "occupancyNumber": number,
        "name": "Fixture suite", "adultCount": adults, "childCount": len(children),
        "childrenAges": children, "maxOccupancy": adults + len(children),
        "boardType": "RO", "boardName": "Room Only",
        "retailRate": {"total": [{"amount": amount, "currency": currency}], "taxesAndFees": None},
        "cancellationPolicies": {"refundableTag": "NRFN", "cancelPolicyInfos": []}}


def selected(*, rooms=None, currency="RUB"):
    rooms = [{"adults": 1, "children": []}] if rooms is None else rooms
    return {"offer_id": "liteapi:owned-selected", "provider": "liteapi", "source_type": "SANDBOX",
        "amount_minor": 10_050, "currency": currency,
        "expires_at": (NOW + timedelta(seconds=90)).isoformat(),
        "details": {"kind": "hotel", "native_offer_id": "opaque-selected-native-rate", "hotel_id": "lp-fixture",
            "checkin": "2026-11-10", "checkout": "2026-11-13",
            "rates": [{"rateId": "old-search-rate"}],
            "fees_included": True, "allocation_verified": True, "constraint_evidence": {"breakfast": True},
            "binding": {"trip_id": "trip", "state_version": 3},
            "search_request": {"hotelIds": ["lp-fixture"], "checkin": "2026-11-10", "checkout": "2026-11-13",
                "occupancies": rooms, "currency": currency, "guestNationality": "RU"}}}


def response(*, rates=None, currency="RUB", amount="100.50"):
    return {"data": {"offerId": "opaque-selected-native-rate", "prebookId": "prebook-fixture-1",
        "hotelId": "lp-fixture", "checkin": "2026-11-10", "checkout": "2026-11-13", "currency": currency,
        "price": amount, "sellingPriceToUser": amount, "suggestedSellingPrice": "9999.00",
        "roomTypes": [{"rates": rates or [rate(currency=currency, amount=amount)]}],
        "boardChanged": False, "cancellationChanged": False, "supplier": "Fixture supplier", "supplierId": 2}}


def refresh(body=None, offer=None, status=200, clock=None):
    calls = []
    def handler(request):
        import json
        calls.append(request)
        assert request.method == "POST"
        assert str(request.url) == "https://book.liteapi.travel/v3.0/rates/prebook"
        assert request.headers["X-API-Key"] == "sand_synthetic-key"
        assert json.loads(request.content) == {"offerId": "opaque-selected-native-rate", "usePaymentSdk": False}
        return httpx.Response(status, json=deepcopy(response() if body is None else body))
    async def run():
        gateway = TravelGateway({"LITEAPI_API_KEY": "sand_synthetic-key", "LITEAPI_MODE": "sandbox",
            "http_transport": httpx.MockTransport(handler), "clock": clock or (lambda: NOW)})
        try:
            return await gateway.reprice(deepcopy(selected() if offer is None else offer))
        finally:
            await gateway.close()
    return asyncio.run(run()), calls


def test_prebook_rechecks_exact_selected_identity_without_repeating_search_or_booking():
    original = selected()
    updated, calls = refresh(offer=original)
    assert len(calls) == 1
    assert updated["offer_id"] == original["offer_id"]
    assert updated["details"]["native_offer_id"] == original["details"]["native_offer_id"]
    assert updated["details"]["hotel_id"] == "lp-fixture"
    assert updated["details"]["search_request"] == original["details"]["search_request"]
    assert "binding" not in updated["details"], "API must reattach its owned verified association"
    assert updated["amount_minor"] == 10_050 and updated["currency"] == "RUB"
    assert updated["source_type"] == "SANDBOX"
    assert updated["details"]["sandbox_prebook_verified"] is True
    assert updated["details"]["booking_created_at_supplier"] is False
    assert updated["details"]["availability_verified"] is False
    assert updated["details"]["ready_for_real_booking"] is False
    assert updated["details"]["prebook_validity_verified"] is False
    assert updated["details"]["prebook_id"] == "prebook-fixture-1"
    assert updated["expires_at"] == original["expires_at"]
    assert updated["details"]["rates"][0]["rateId"] == "prebook-new-rate-1"


def test_changed_price_is_returned_as_new_economics_of_same_selected_offer():
    updated, _ = refresh(response(amount="130.25"))
    assert updated["amount_minor"] == 13_025 and updated["offer_id"] == "liteapi:owned-selected"
    assert updated["details"]["constraint_evidence"]["breakfast"] is False


def test_multiroom_prebook_sums_only_the_unique_returned_occupancies_of_selected_offer():
    rooms = [{"adults": 1, "children": [7]}, {"adults": 1, "children": []}]
    rows = [rate(2, amount="90.25"), rate(1, amount="110.50", children=[7])]
    updated, _ = refresh(response(rates=rows, amount="200.75"), selected(rooms=rooms))
    assert updated["amount_minor"] == 20_075
    assert updated["details"]["allocation_verified"] is True and updated["details"]["capacity_verified"] is True
    assert updated["details"]["occupancies"] == rooms


@pytest.mark.parametrize("currency,amount,minor", [("JPY", "1234", 1234), ("BHD", "1.234", 1234), ("EUR", "1.23", 123)])
def test_currency_minor_units_are_preserved(currency, amount, minor):
    updated, _ = refresh(response(currency=currency, amount=amount), selected(currency=currency))
    assert updated["amount_minor"] == minor


@pytest.mark.parametrize("field,value", [("offerId", "different-selected-rate"), ("offerId", None),
    ("hotelId", "foreign-hotel"), ("checkin", "2026-11-11"), ("checkout", "2026-11-14"), ("currency", "EUR")])
def test_changed_or_missing_prebook_identity_never_becomes_refreshed_quote(field, value):
    body = response()
    body["data"][field] = value
    with pytest.raises(ProviderError) as caught:
        refresh(body)
    assert caught.value.code == "REPRICE_IDENTITY_MISMATCH"


@pytest.mark.parametrize("change", [lambda d: d.pop("prebookId"), lambda d: d.update(prebookId="https://unsafe.test"),
    lambda d: d.update(roomTypes=[]), lambda d: d.update(roomTypes=[{"rates": []}]),
    lambda d: d["roomTypes"][0]["rates"].append(rate(2)),
    lambda d: d["roomTypes"][0]["rates"][0]["retailRate"]["total"].append({"amount": 1, "currency": "RUB"}),
    lambda d: d["roomTypes"][0]["rates"][0]["retailRate"]["total"][0].update(currency="EUR")])
def test_incomplete_or_ambiguous_prebook_response_is_not_filled_from_old_search(change):
    body = response()
    change(body["data"])
    with pytest.raises(ProviderError) as caught:
        refresh(body)
    assert caught.value.code == "INVALID_RESPONSE"


@pytest.mark.parametrize("change", [lambda d: d["roomTypes"][0]["rates"][0].update(adultCount=2),
    lambda d: d["roomTypes"][0]["rates"][0].update(occupancyNumber=2),
    lambda d: d["roomTypes"][0]["rates"][0].update(childCount=1, childrenAges=[7])])
def test_guest_distribution_mismatch_is_rejected_even_when_old_search_verified(change):
    body = response()
    change(body["data"])
    with pytest.raises(ProviderError) as caught:
        refresh(body)
    assert caught.value.code == "REPRICE_ALLOCATION_MISMATCH"


@pytest.mark.parametrize("field,value", [("price", "100.51"), ("sellingPriceToUser", "100.51"),
    ("price", None), ("sellingPriceToUser", True)])
def test_inconsistent_customer_or_room_totals_are_not_reinterpreted_as_retail(field, value):
    body = response()
    body["data"][field] = value
    with pytest.raises(ProviderError) as caught:
        refresh(body)
    assert caught.value.code in {"INVALID_RESPONSE", "PREBOOK_PRICE_AMBIGUOUS"}


def test_fresh_missing_fee_fact_and_changed_board_do_not_inherit_old_good_facts():
    body = response()
    body["data"]["boardChanged"] = True
    body["data"]["roomTypes"][0]["rates"][0]["retailRate"].pop("taxesAndFees")
    updated, _ = refresh(body)
    assert updated["details"]["mandatory_fees_known"] is False
    assert updated["details"]["constraint_evidence"]["breakfast"] is False
    assert updated["details"]["conditions_changed"]["boardChanged"] is True


@pytest.mark.parametrize("status", [400, 404, 408, 409, 429, 500])
def test_provider_error_does_not_trigger_new_search_booking_or_automatic_retry(status):
    with pytest.raises(ProviderError):
        refresh({"error": {"message": "Synthetic unavailable"}}, status=status)


@pytest.mark.parametrize("change", [lambda o: o.update(expires_at=NOW.isoformat()),
    lambda o: o.update(expires_at="unknown"), lambda o: o["details"].update(native_offer_id="https://arbitrary.test"),
    lambda o: o["details"].update(hotel_id="foreign"), lambda o: o.update(source_type="LIVE_BOOKABLE")])
def test_invalid_or_expired_saved_selection_is_blocked_before_prebook(change):
    offer = selected()
    change(offer)
    with pytest.raises(ProviderError) as caught:
        refresh(offer=offer)
    assert caught.value.code in {"INVALID_REQUEST", "OFFER_UNAVAILABLE"}
