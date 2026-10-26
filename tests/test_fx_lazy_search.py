"""Lazy, bounded reference FX in HYBRID; all tools are pure fakes."""
import asyncio
from copy import deepcopy
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from test_orchestrator import NOW, FakeTravel, hybrid

from otravel.fx import FxUnavailable, parse_cbr_xml
from otravel.orchestrator import TripSearch


class Rates:
    def __init__(self, unavailable=False):
        self.calls = 0
        self.unavailable = unavailable

    async def fetch_snapshot(self, now):
        self.calls += 1
        if self.unavailable:
            raise FxUnavailable("fx_source_unavailable", "Controlled CBR outage")
        day = now.astimezone(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y")
        return parse_cbr_xml((f'<ValCurs Date="{day}"><Valute><CharCode>USD</CharCode><Nominal>1</Nominal><Value>80</Value></Valute>'
            '<Valute><CharCode>EUR</CharCode><Nominal>1</Nominal><Value>100</Value></Valute></ValCurs>').encode(), now)


class ForeignFlights(FakeTravel):
    async def search_flights(self, request, provider="duffel"):
        offers = await super().search_flights(request, provider)
        offers[0].update(currency="EUR", amount_minor=1000)
        return offers


class HotelAlternatives(FakeTravel):
    async def search_hotels(self, request, provider="liteapi"):
        rows = await super().search_hotels(request, provider)
        foreign = deepcopy(rows[0])
        foreign.update(offer_id="eur-" + foreign["offer_id"], currency="EUR", amount_minor=100_000)
        return [foreign, rows[0]]  # EUR1000 costs RUB100000; native RUB10000 is cheaper.


def run(travel, rates, max_calls=16):
    journal = []
    result = asyncio.run(TripSearch(travel, fx=rates, max_tool_calls=max_calls, clock=lambda: NOW).search(
        hybrid(flight_provider="duffel"), event_sink=lambda kind, payload: journal.append((kind, payload))))
    return result, journal


def test_all_native_RUB_uses_no_CBR_tool_even_when_reference_client_is_down():
    rates = Rates(unavailable=True)
    result, journal = run(FakeTravel(), rates)
    assert rates.calls == 0 and result["tool_calls_used"] == 7
    assert result["plans"] and result["plans"][0]["currency"] == "RUB"
    assert not any(payload.get("provider") == "cbr" for _, payload in journal)


def test_hotels_are_ranked_by_comparable_reference_price_not_raw_foreign_minor_amount():
    rates = Rates()
    result, _ = run(HotelAlternatives(), rates)
    assert rates.calls == 1
    assert result["plans"]
    hotels = [o for o in result["plans"][0]["offers"] if o.get("details", {}).get("kind") == "hotel"]
    assert len(hotels) == 2 and all(o["currency"] == "RUB" and o["amount_minor"] == 1_000_000 for o in hotels)


def test_CBR_failure_does_not_hide_valid_native_currency_hotel_alternatives():
    rates = Rates(unavailable=True)
    result, _ = run(HotelAlternatives(), rates)
    assert rates.calls == 1 and result["plans"]
    assert not result["plans"][0]["blockers"] and result["fx_error"]["code"] == "fx_source_unavailable"


def test_foreign_flight_candidates_fetch_FX_once_at_the_correct_logical_tool_position():
    rates = Rates()
    result, journal = run(ForeignFlights(), rates)
    started = [payload for kind, payload in journal if kind == "TOOL_CALL_STARTED"]
    assert rates.calls == 1 and result["tool_calls_used"] == len(started) == 8
    assert [event["attempt"] for event in started] == list(range(1, 9))
    assert started[5]["provider"] == "cbr"  # 2 lookup + 2 rates + 1 first flight, then FX.
    plan = result["plans"][0]
    foreign = [o for o in plan["offers"] if o.get("details", {}).get("kind") == "flight"]
    assert len(foreign) == 3 and all(o["currency"] == "EUR" and o["amount_minor"] == 1000 for o in foreign)
    assert plan["fx_reference_only"] and not plan["ready_for_real_booking"]


@pytest.mark.parametrize("bad", ["fees", "allocation", "constraint", "future", "expired"])
def test_rejected_foreign_rows_do_not_request_FX_before_evidence_guards(bad):
    class RejectedForeign(FakeTravel):
        async def search_hotels(self, request, provider="liteapi"):
            rows = await super().search_hotels(request, provider)
            foreign = deepcopy(rows[0])
            foreign.update(offer_id="foreign-" + foreign["offer_id"], currency="EUR", amount_minor=1)
            if bad == "fees": foreign["details"]["fees_included"] = False
            elif bad == "allocation": foreign["details"]["allocation_verified"] = False
            elif bad == "constraint": foreign["details"]["constraint_evidence"]["breakfast"] = False
            elif bad == "future": foreign["retrieved_at"] = (NOW + timedelta(seconds=1)).isoformat()
            elif bad == "expired": foreign["expires_at"] = NOW.isoformat()
            return [foreign, *rows]
    rates = Rates(unavailable=True)
    result, journal = run(RejectedForeign(), rates)
    assert rates.calls == 0 and result["tool_calls_used"] == 7 and result["plans"]
    assert not any(payload.get("provider") == "cbr" for _, payload in journal)


def test_failed_FX_is_attempted_once_and_native_foreign_partial_offers_are_preserved():
    rates = Rates(unavailable=True)
    result, journal = run(ForeignFlights(), rates)
    assert rates.calls == 1 and result["tool_calls_used"] == 8
    assert result["plans"] == [] and result["fx_error"]["code"] == "fx_source_unavailable"
    partial = result["partial_plan"]
    foreign = [o for o in partial["offers"] if o.get("details", {}).get("kind") == "flight"]
    assert len(foreign) == 3 and all(o["currency"] == "EUR" and o["amount_minor"] == 1000 for o in foreign)
    assert partial["total_minor"] is None and not partial["ready_for_simulated_checkout"]
    assert sum(kind == "TOOL_CALL_FAILED" and payload.get("provider") == "cbr" for kind, payload in journal) == 1


@pytest.mark.parametrize("cap,fx_calls", [(0, 0), (5, 0), (6, 1)])
def test_lazy_FX_cannot_exceed_the_shared_tool_budget(cap, fx_calls):
    rates, travel = Rates(), ForeignFlights()
    result, journal = run(travel, rates, max_calls=cap)
    started = [payload for kind, payload in journal if kind == "TOOL_CALL_STARTED"]
    assert rates.calls == fx_calls and result["tool_calls_used"] == len(started) == cap
    assert len(travel.calls) + rates.calls == cap
    assert [event["attempt"] for event in started] == list(range(1, cap + 1))
    if cap == 5:
        assert result["fx_error"]["code"] == "fx_tool_limit" and result["plans"] == []


def test_unpersisted_lazy_FX_start_fails_before_the_FX_gateway():
    rates, travel = Rates(), ForeignFlights()
    def failed_audit(kind, payload):
        if kind == "TOOL_CALL_STARTED" and payload.get("provider") == "cbr":
            raise RuntimeError("Controlled failed audit persistence")
    with pytest.raises(RuntimeError, match="failed audit persistence"):
        asyncio.run(TripSearch(travel, fx=rates, clock=lambda: NOW).search(
            hybrid(flight_provider="duffel"), event_sink=failed_audit))
    assert rates.calls == 0 and len(travel.calls) == 5


def test_existing_foreign_budget_DEMO_branch_still_uses_one_FX_and_no_travel_gateway():
    state, rates, travel = hybrid(), Rates(), FakeTravel()
    state.search_policy.mode = "DEMO"
    state.budget.currency = "USD"
    original = state.model_dump(mode="json")
    result = asyncio.run(TripSearch(travel, fx=rates, clock=lambda: NOW).search(state))
    assert rates.calls == result["tool_calls_used"] == 1 and travel.calls == []
    assert result["plans"] and result["plans"][0]["currency"] == "USD"
    assert all(o["currency"] == "RUB" for o in result["plans"][0]["offers"])
    assert state.model_dump(mode="json") == original
