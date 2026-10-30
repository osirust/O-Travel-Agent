"""Trip search with fake gateways only. No HTTP, credentials or persistence."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from otravel.domain import SearchPolicy, sample_trip
from otravel.inventory import DemoInventory
from otravel.orchestrator import TripSearch
from otravel.providers import ProviderError


NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)


def hybrid(**policy):
    state = sample_trip()
    state.search_policy = SearchPolicy(mode="HYBRID", guest_nationality="RU", **policy)
    return state


class FakeTravel:
    def __init__(self, *, proven=True, fail=False, alternatives=False):
        self.proven, self.fail, self.alternatives = proven, fail, alternatives
        self.calls = []

    async def resolve_hotels(self, city, limit=3):
        self.calls.append(("resolve_hotels", None, {"city": city, "limit": limit}))
        if self.fail:
            raise ProviderError("CONFIG_MISSING", "Нет ключа фикстуры.")
        return [{"id": "fixture-hotel-" + city}]

    def offer(self, request, provider, kind):
        details = {"kind": kind, "fees_included": self.proven, "allocation_verified": self.proven,
                   "group_quote": self.proven, "constraint_evidence": {"breakfast": self.proven},
                   "search_request": deepcopy(request)}
        if kind == "hotel":
            details["hotel_id"] = request.get("hotelIds", ["fixture-hotel-" + request.get("city", "")])[0]
            identity = request["checkin"]
        else:
            details["passengers_count"] = len(request["passengers"])
            identity = request["slices"][0]["departure_date"]
        return {"offer_id": f"{provider}-{kind}-{identity}", "provider": provider,
                "source_type": "SANDBOX" if provider in {"liteapi", "duffel"} else "LIVE_INDICATIVE",
                "price_kind": "COMPONENT_TOTAL", "amount_minor": 1_000_000, "currency": request["currency"],
                "retrieved_at": NOW.isoformat(), "expires_at": (NOW + timedelta(hours=1)).isoformat(),
                "details": details}

    async def search_hotels(self, request, provider="liteapi"):
        self.calls.append(("search_hotels", provider, deepcopy(request)))
        if self.fail:
            raise ProviderError("CONFIG_MISSING", "Нет ключа фикстуры.")
        offer = self.offer(request, provider, "hotel")
        if not self.alternatives:
            return [offer]
        cheaper_unknown = deepcopy(offer)
        cheaper_unknown.update(offer_id="unknown-fees-" + offer["offer_id"], amount_minor=1)
        cheaper_unknown["details"]["fees_included"] = False
        wrong_currency = deepcopy(offer)
        wrong_currency.update(offer_id="fx-" + offer["offer_id"], currency="EUR", amount_minor=1)
        return [cheaper_unknown, wrong_currency, offer]

    async def search_flights(self, request, provider="serpapi"):
        self.calls.append(("search_flights", provider, deepcopy(request)))
        if self.fail:
            raise ProviderError("CONFIG_MISSING", "Нет ключа фикстуры.")
        return [self.offer(request, provider, "flight")]


def search(gateway, state, **kwargs):
    return asyncio.run(TripSearch(gateway, clock=lambda: NOW, **kwargs).search(state))


def test_demo_mode_is_exact_legacy_result_and_uses_no_gateway():
    state = sample_trip()
    gateway = FakeTravel()
    expected = DemoInventory().search_trip(state)
    result = search(gateway, state)
    assert {key: result[key] for key in expected} == expected
    assert gateway.calls == []
    assert result["tool_calls_used"] == 0


def test_legacy_demo_before_after_compares_saved_values_without_replaying_them():
    state = sample_trip()
    previous = DemoInventory().search_trip(state)["plans"][0]
    old = deepcopy(previous)
    result = asyncio.run(TripSearch(FakeTravel()).search(state, previous_plan=previous))
    comparison = result["plans"][0]["comparison"]
    assert previous == old
    assert comparison["changes"] == []
    assert comparison["delta_minor"] == 0
    assert comparison["requires_new_confirmation"] is False


def test_hybrid_covers_every_stay_leg_and_group_with_explicit_demo_transfers():
    state = hybrid()
    snapshot = state.model_dump(mode="json")
    gateway = FakeTravel()
    result = search(gateway, state)
    assert state.model_dump(mode="json") == snapshot
    assert result["violations"] == []
    assert result["source_type"] == "MIXED"
    assert result["tool_calls_used"] == 7  # 2 resolver + 2 hotel + 3 leg calls.
    assert len(gateway.calls) == 7
    plan = result["plans"][0]
    assert plan["total_minor"] == 5_720_000  # 5*1m + two 360k synthetic transfers.
    assert plan["ready_for_simulated_checkout"]
    assert plan["ready_for_real_booking"] is False
    assert plan["coverage"]["complete"]
    assert sum(c["source_type"] == "DEMO" for c in plan["components"]) == 2
    assert all(len(c["binding"]["room_ids"]) == 3 for c in plan["components"] if c["binding"].get("room_ids"))
    hotel_calls = [request for method, _, request in gateway.calls if method == "search_hotels"]
    assert all(request["guestNationality"] == "RU" for request in hotel_calls)
    assert sorted(age for room in hotel_calls[0]["occupancies"] for age in room["children"]) == [3, 7, 15]
    for method, _, request in gateway.calls:
        if method == "search_flights":
            assert len(request["slices"]) == 1 and len(request["passengers"]) == 9


def test_unknown_fees_allocation_are_not_promoted_and_explicitly_fall_back():
    gateway = FakeTravel(proven=False)
    result = search(gateway, hybrid())
    assert result["plans"][0]["source_type"] == "DEMO"
    assert result["plans"][0]["total_minor"] == 33_340_000
    assert result["component_errors"]
    assert any("mandatory_fees_unknown" in warning for warning in result["warnings"])
    assert all(offer["source_type"] == "DEMO" for offer in result["plans"][0]["offers"])


def test_unverified_components_without_fallback_remain_blocked_not_fake_ready():
    result = search(FakeTravel(proven=False), hybrid(demo_fallback=False))
    assert result["plans"] == []
    assert result["partial_plan"]["total_minor"] is None
    assert result["partial_plan"]["ready_for_simulated_checkout"] is False
    assert result["violations"]
    assert not any(c["source_type"] == "DEMO" for c in result["partial_plan"]["components"])


def test_missing_liteapi_nationality_never_resolves_or_searches_hotels():
    state = hybrid()
    state.search_policy.guest_nationality = None
    gateway = FakeTravel()
    result = search(gateway, state)
    assert gateway.calls == [] and result["tool_calls_used"] == 0
    assert any(issue["code"] == "missing_nationality" for issue in result["violations"])
    assert result["questions"] == ["Гражданство какой страны использовать для поиска отеля?"]
    assert result["plans"] == [] and result["source_type"] == "INCOMPLETE"
    assert state.search_policy.guest_nationality is None


def test_missing_nationality_without_fallback_is_a_blocking_question():
    state = hybrid(demo_fallback=False)
    state.search_policy.guest_nationality = None
    gateway = FakeTravel()
    result = search(gateway, state)
    assert result["questions"] == ["Гражданство какой страны использовать для поиска отеля?"]
    assert result["plans"] == []
    assert gateway.calls == [] and result["tool_calls_used"] == 0
    assert state.search_policy.guest_nationality is None


def test_serpapi_multiroom_does_not_invent_a_room_count_parameter():
    gateway = FakeTravel()
    result = search(gateway, hybrid(hotel_provider="serpapi"))
    assert all(method == "search_flights" for method, _, _ in gateway.calls)
    assert len([e for e in result["component_errors"] if e["code"] == "UNSUPPORTED_MULTIROOM"]) == 2
    assert result["plans"][0]["coverage"]["complete"]


@pytest.mark.parametrize("cap", [0, 1, 3])
def test_search_budget_caps_gateway_attempts_without_cartesian_explosion(cap):
    gateway = FakeTravel()
    result = search(gateway, hybrid(), max_tool_calls=cap)
    assert len(gateway.calls) == result["tool_calls_used"] == cap
    assert result["plans"]
    assert any(e["code"] == "TOOL_BUDGET_EXHAUSTED" for e in result["component_errors"])


def test_cheapest_unknown_fee_or_wrong_currency_offer_is_not_selected():
    result = search(FakeTravel(alternatives=True), hybrid())
    plan = result["plans"][0]
    assert plan["total_minor"] == 5_720_000
    assert not any(o["offer_id"].startswith(("unknown-fees-", "fx-")) for o in plan["offers"])
    assert result["component_errors"] == []


def test_gateway_failures_do_not_claim_api_success_or_lose_state():
    state = hybrid()
    snapshot = state.model_dump(mode="json")
    result = search(FakeTravel(fail=True), state)
    assert state.model_dump(mode="json") == snapshot
    assert result["source_type"] == "DEMO"
    assert all(error["code"] == "CONFIG_MISSING" for error in result["component_errors"])
    assert result["ready_for_real_booking"] is False


def test_group_aware_request_allocation_does_not_mutate_empty_user_rooms():
    state = hybrid()
    for room in state.stays[0].rooms:
        room.traveler_ids = []
    result = search(FakeTravel(), state)
    assert all(room.traveler_ids == [] for room in state.stays[0].rooms)
    assert result["plans"]
    component = next(c for c in result["plans"][0]["components"] if c["binding"].get("stay_id") == state.stays[0].id and c["binding"].get("room_ids"))
    people = {p.id: p for p in state.travelers}
    assigned = component["binding"]["room_assignments"]
    assert sorted(p for r in assigned for p in r["traveler_ids"]) == sorted(people)
    assert all(len({people[p].group_id for p in r["traveler_ids"]}) == 1 for r in assigned)
    assert all("capacity" not in r for r in assigned)  # No invented real room capacity.


def test_event_sink_is_async_and_receives_only_bounded_metadata():
    async def run():
        events = []
        async def sink(kind, data):
            events.append((kind, data))
        result = await TripSearch(FakeTravel(), clock=lambda: NOW).search(hybrid(), event_sink=sink)
        assert result["plans"]
        assert sum(kind == "TOOL_CALL_STARTED" for kind, _ in events) == 7
        assert events[-1][0] == "TRIP_SEARCH_COMPLETED"
        assert all("request" not in data and "passengers" not in data for _, data in events)
    asyncio.run(run())


def test_event_persistence_failure_happens_before_any_gateway_call():
    gateway = FakeTravel()
    async def run():
        def failed_sink(kind, data):
            raise RuntimeError("audit storage unavailable")
        await TripSearch(gateway, clock=lambda: NOW).search(hybrid(), event_sink=failed_sink)
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        asyncio.run(run())
    assert gateway.calls == []


def test_incomplete_trip_only_asks_blocking_questions_without_calls():
    state = hybrid()
    state.origin = None
    gateway = FakeTravel()
    result = search(gateway, state)
    assert gateway.calls == []
    assert result["questions"]
    assert result["plans"] == []


def test_gateway_returns_unrequested_hotel_is_not_bound_to_a_stay_by_name():
    class WrongHotel(FakeTravel):
        async def search_hotels(self, request, provider="liteapi"):
            rows = await super().search_hotels(request, provider)
            rows[0]["details"]["hotel_id"] = "unrequested-hotel"
            return rows
    result = search(WrongHotel(), hybrid())
    assert result["plans"]
    assert all(c["source_type"] == "DEMO" for c in result["plans"][0]["components"] if c["binding"].get("room_ids"))
    assert any("unrequested_hotel_id" in warning for warning in result["warnings"])


def test_unknown_source_type_is_not_promoted_to_live():
    class UnknownSource(FakeTravel):
        async def search_flights(self, request, provider="serpapi"):
            rows = await super().search_flights(request, provider)
            rows[0]["source_type"] = "REAL_GUARANTEED"
            return rows
    result = search(UnknownSource(), hybrid())
    assert result["plans"]
    assert all(c["source_type"] == "DEMO" for c in result["plans"][0]["components"] if any(k.startswith("leg:") for k in c["covers"]))


def test_advancing_clock_validates_sources_after_async_calls_not_search_start():
    class Clock:
        moment = NOW
        def __call__(self):
            self.moment += timedelta(milliseconds=1)
            return self.moment
    clock = Clock()
    class FreshTravel(FakeTravel):
        def offer(self, request, provider, kind):
            result = super().offer(request, provider, kind)
            retrieved = clock()
            result.update(retrieved_at=retrieved.isoformat(), expires_at=(retrieved + timedelta(hours=1)).isoformat())
            return result
    result = asyncio.run(TripSearch(FreshTravel(), clock=clock).search(hybrid()))
    assert result["source_type"] == "MIXED"
    assert result["component_errors"] == []
    assert result["plans"][0]["ready_for_simulated_checkout"]


def test_realistic_async_gateway_creates_timestamps_after_run_started():
    class AsyncTravel(FakeTravel):
        async def search_hotels(self, request, provider="liteapi"):
            await asyncio.sleep(0)
            return await super().search_hotels(request, provider)
        async def search_flights(self, request, provider="serpapi"):
            await asyncio.sleep(0)
            return await super().search_flights(request, provider)
        def offer(self, request, provider, kind):
            result = super().offer(request, provider, kind)
            retrieved = datetime.now(timezone.utc)
            result.update(retrieved_at=retrieved.isoformat(), expires_at=(retrieved + timedelta(minutes=5)).isoformat())
            return result
    result = asyncio.run(TripSearch(AsyncTravel()).search(hybrid()))
    assert result["source_type"] == "MIXED"
    assert result["component_errors"] == []


def test_genuinely_future_source_timestamp_remains_rejected():
    class FutureTravel(FakeTravel):
        def offer(self, request, provider, kind):
            result = super().offer(request, provider, kind)
            result["retrieved_at"] = (NOW + timedelta(minutes=1)).isoformat()
            return result
    result = search(FutureTravel(), hybrid())
    assert result["source_type"] == "DEMO"
    assert all("stale_source_snapshot" in e["message"] for e in result["component_errors"])
