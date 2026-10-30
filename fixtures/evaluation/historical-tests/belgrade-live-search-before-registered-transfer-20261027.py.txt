"""Source-derived location facts, MockTransport and controlled travel only."""
import asyncio
from copy import deepcopy

import httpx
import pytest
from test_orchestrator import FakeTravel, search

import otravel.orchestrator as orchestration
from otravel.domain import CITIES, TripState
from otravel.inventory import DemoInventory, bounded_transport_catalog
from otravel.providers import ProviderError, TravelGateway

ACTUAL_METADATA = [{'id': identifier, 'country': 'RS', 'city': 'Belgrade'}
    for identifier in ('lpcc0bc', 'lp64167', 'lp71935')]


def belgrade(*, demo=False, fallback=True):
    return TripState.model_validate({'origin': 'Москва', 'travelers': [{'id': 'adult-1', 'type': 'adult'}],
        'stays': [{'id': 'stay-belgrade', 'city': 'Белград', 'check_in': '2026-11-10', 'check_out': '2026-11-13',
            'rooms': [{'id': 'room-1', 'traveler_ids': ['adult-1']}]}],
        'legs': [{'id': 'out', 'origin': 'Москва', 'destination': 'Белград', 'departure_date': '2026-11-10'},
            {'id': 'back', 'origin': 'Белград', 'destination': 'Москва', 'departure_date': '2026-11-13'}],
        'search_policy': {'mode': 'DEMO' if demo else 'HYBRID', 'hotel_provider': 'liteapi',
            'flight_provider': 'duffel', 'guest_nationality': 'RU', 'demo_fallback': fallback}})


async def lookup(handler, city='Белград'):
    gateway = TravelGateway({'LITEAPI_API_KEY': 'sand_synthetic-only', 'LITEAPI_MODE': 'sandbox',
        'http_transport': httpx.MockTransport(handler)})
    try:
        return await gateway.resolve_hotels(city)
    finally:
        await gateway.close()


def test_belgrade_lookup_uses_exact_observed_provider_country_city_and_three_id_bound():
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == 'GET' and request.url.path == '/v3.0/data/hotels'
        assert dict(request.url.params) == {'countryCode': 'RS', 'cityName': 'Belgrade', 'limit': '3'}
        assert 'sand_synthetic-only' not in str(request.url)
        return httpx.Response(200, json={'data': deepcopy(ACTUAL_METADATA)})
    rows = asyncio.run(lookup(handler, 'Belgrade'))
    assert len(calls) == 1
    assert [row['id'] for row in rows] == [row['id'] for row in ACTUAL_METADATA]
    assert all(row['city'] == 'Белград' for row in rows)


@pytest.mark.parametrize('wrong', [{'id': 'foreign', 'country': 'RU', 'city': 'Belgorod'},
    {'id': 'unknown-location'}, {'id': 'foreign', 'country': 'RS', 'city': 'Novi Sad'}])
def test_new_city_lookup_does_not_label_wrong_or_unverified_location_as_belgrade(wrong):
    with pytest.raises(ProviderError) as error:
        asyncio.run(lookup(lambda _: httpx.Response(200, json={'data': [wrong]})))
    assert error.value.code == 'INVALID_RESPONSE'


def test_hybrid_eighth_city_reaches_actual_format_lookups_but_does_not_make_demo_transfer():
    state, gateway = belgrade(), FakeTravel()
    before = state.model_dump(mode='json')
    result = search(gateway, state)
    assert len(gateway.calls) == 4  # lookup + hotel rates + two controlled flight searches.
    flights = [request for method, _, request in gateway.calls if method == 'search_flights']
    assert [row['slices'][0]['destination'] for row in flights] == ['BEG', 'MOW']
    assert [row['slices'][0]['origin'] for row in flights] == ['MOW', 'BEG']
    assert result['plans'] == []
    partial = result['partial_plan']
    assert partial and partial['ready_for_simulated_checkout'] is False
    assert partial['ready_for_real_booking'] is False
    assert partial['coverage']['missing'] == ['transfer:stay-belgrade']
    assert all(offer['source_type'] == 'SANDBOX' for offer in partial['offers'])
    assert not any(offer.get('kind', offer.get('details', {}).get('kind')) == 'transfer' for offer in partial['offers'])
    assert state.model_dump(mode='json') == before


def test_missing_iata_is_a_component_failure_not_keyerror_or_guessed_airport(monkeypatch):
    current = orchestration.flight_iata
    monkeypatch.setattr(orchestration, 'flight_iata', lambda city: None if city == 'Белград' else current(city))
    gateway = FakeTravel()
    result = search(gateway, belgrade())
    assert not any(method == 'search_flights' for method, _, _ in gateway.calls)
    assert len([issue for issue in result['component_errors'] if issue['code'] == 'MISSING_PROVIDER_IATA']) == 2
    assert not result['plans'] and result['partial_plan']['ready_for_simulated_checkout'] is False


def test_failed_hotel_source_never_replaces_belgrade_with_another_city_or_demo_hotel():
    gateway = FakeTravel(fail=True)
    state = belgrade()
    result = search(gateway, state)
    assert result['plans'] == [] and result['partial_plan']['offers'] == []
    assert state.stays[0].city == 'Белград'
    assert 'Белград' not in CITIES
    assert bounded_transport_catalog(state)['offers'] == []
    assert DemoInventory().search_trip(state, catalogue_only=True)['plans'] == []


def test_demo_and_unqualified_hybrid_pair_still_stop_without_gateway_calls():
    for state in (belgrade(demo=True), belgrade()):
        if state.search_policy.mode == 'HYBRID':
            state.search_policy.flight_provider = 'serpapi'
        gateway = FakeTravel()
        result = search(gateway, state)
        assert gateway.calls == [] and result['tool_calls_used'] == 0 and not result['plans']
