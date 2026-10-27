"""Price units and partial media public contract; controlled HTTP only.

EUR311.75 is an arithmetic control, NOT a captured screenshot offer.
No frontend code or actual new supplier/model request is exercised here.
"""
import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from otravel.api import create_app
from otravel.config import Settings
from otravel.domain import TripState
from otravel.fx import convert_minor, parse_cbr_xml
from otravel.media import MediaResolver
from otravel.planning import assemble_plan
from otravel.providers import TravelGateway

NOW = datetime(2026, 10, 27, 14, 18, 27, tzinfo=UTC)
TOKEN = 'controlled-price-media-owner'


def fx_snapshot():
    # Numeric reference observed in R2; this XML is a declared synthetic shape.
    raw = (b'<ValCurs Date="27.10.2026"><Valute><CharCode>EUR</CharCode><Nominal>1</Nominal>'
        b'<Value>90,0935</Value></Valute><Valute><CharCode>USD</CharCode><Nominal>1</Nominal>'
        b'<Value>80,4909</Value></Valute></ValCurs>')
    return parse_cbr_xml(raw, NOW)


def trip():
    return TripState.model_validate({'trip_id': 'price-contract-trip', 'state_version': 4,
        'origin': 'Казань', 'travelers': [{'id': 'adult-1', 'type': 'adult'}, {'id': 'adult-2', 'type': 'adult'}],
        'stays': [{'id': 'stay-1', 'city': 'Стамбул', 'check_in': '2026-11-10', 'check_out': '2026-11-13',
            'rooms': [{'id': 'room-1', 'traveler_ids': ['adult-1', 'adult-2']}]}],
        'legs': [{'id': 'leg-1', 'mode': 'flight', 'origin': 'Казань', 'destination': 'Стамбул', 'departure_date': '2026-11-10'}],
        'budget': {'amount_minor': 100_000_000, 'currency': 'RUB', 'scope': 'whole_trip'}})


class ReadOnlyPlanStore:
    def __init__(self, state, plan):
        self.state, self.plan = deepcopy(state), deepcopy(plan)
        self.session = {'session_id': 'own', 'trip_id': state['trip_id'], 'state_version': state['state_version'],
            'owner_token_hash': hashlib.sha256(TOKEN.encode()).hexdigest()}
        self.run = {'run_id': 'search', 'session_id': 'own', 'status': 'completed',
            'based_version': state['state_version'], 'result': {'result': {'plans': [], 'partial_plan': self.plan}}}

    async def get_session(self, sid):
        assert sid == 'own'
        return deepcopy(self.session)

    async def list_messages(self, sid):
        assert sid == 'own'
        return [{'run_id': 'search'}]

    async def get_run(self, rid):
        assert rid == 'search'
        return deepcopy(self.run)


@pytest.mark.asyncio
async def test_group_native_price_fx_and_public_run_keep_minor_units_and_do_not_multiply_by_passengers():
    state, wire = trip(), []
    def respond(request):
        wire.append(request)
        assert request.method == 'POST' and request.url.host == 'api.duffel.com'
        assert request.url.path == '/air/offer_requests'
        assert json.loads(request.content)['data']['cabin_class'] == 'business'
        return httpx.Response(200, json={'data': {'offers': [{
            'id': 'synthetic-group-business', 'total_amount': '311.75', 'total_currency': 'EUR',
            'expires_at': (NOW + timedelta(hours=1)).isoformat(),
            'passengers': [{'id': 'vendor-1', 'type': 'adult'}, {'id': 'vendor-2', 'type': 'adult'}],
            'slices': [{'segments': [
                {'origin': {'iata_code': 'KZN'}, 'destination': {'iata_code': 'DXB'},
                    'departing_at': '2026-11-10T07:00:00', 'stops': []},
                {'origin': {'iata_code': 'DXB'}, 'destination': {'iata_code': 'IST'},
                    'departing_at': '2026-11-10T12:00:00', 'stops': []}]}]}]}})
    gateway = TravelGateway({'DUFFEL_ACCESS_TOKEN': 'duffel_test_synthetic_price_contract',
        'clock': lambda: NOW, 'http_transport': httpx.MockTransport(respond)})
    try:
        request = {'slices': [{'origin': 'KZN', 'destination': 'IST', 'departure_date': '2026-11-10'}],
            'passengers': [{'type': 'adult'}, {'type': 'adult'}], 'cabin_class': 'business'}
        offer, = await gateway.search_flights(request)
        assert len(wire) == 1 and offer['amount_minor'] == 31_175 and offer['currency'] == 'EUR'
        assert offer['price_kind'] == 'GROUP_ITINERARY_TOTAL'
        assert offer['details']['group_quote'] is True and offer['details']['passengers_count'] == 2
        # Explicit controlled TripSearch binding; no real transport identity is claimed.
        offer['details']['binding'] = {'trip_id': state.trip_id, 'state_version': state.state_version,
            'leg_ids': ['leg-1'], 'traveler_ids': ['adult-1', 'adult-2'],
            'origin': 'Казань', 'destination': 'Стамбул', 'departure_date': '2026-11-10'}
        frozen = deepcopy(offer)
        plan = assemble_plan(state, [offer], fx_snapshot=fx_snapshot(), now=NOW)
        component, = plan['components']
        assert offer == frozen and component['amount_minor'] == 31_175 and component['currency'] == 'EUR'
        assert component['budget_amount_minor'] == 2_808_665 and component['budget_currency'] == 'RUB'
        assert plan['known_subtotal_minor'] == 2_808_665 and plan['total_minor'] is None
        assert plan['coverage']['complete'] is False and plan['ready_for_simulated_checkout'] is False
        assert plan['ready_for_real_booking'] is False and plan['booking_created'] is False
        store = ReadOnlyPlanStore(state.model_dump(mode='json'), plan)
        app = create_app(Settings('unused', 'groq', 1, 8000, 0, 1), store=store,
            llm=SimpleNamespace(), travel=SimpleNamespace(), media=SimpleNamespace())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://controlled') as client:
            response = await client.get('/api/v1/runs/search', headers={'Authorization': 'Bearer ' + TOKEN})
        assert response.status_code == 200
        public = response.json()['result']['result']['partial_plan']
        assert public['offers'][0]['amount_minor'] == 31_175 and public['offers'][0]['currency'] == 'EUR'
        assert public['components'][0]['budget_amount_minor'] == 2_808_665
        assert public['known_subtotal_minor'] == 2_808_665 and public['total_minor'] is None
        assert Decimal(public['components'][0]['budget_amount_minor']).scaleb(-2) == Decimal('28086.65')
        assert public['components'][0]['budget_amount_minor'] != 2 * 2_808_665
    finally:
        await gateway.close()


@pytest.mark.parametrize('native,expected', [(30000, 2702805), (31175, 2808665),
    (15020, 1353204), (15555, 1401404)])
def test_price_inverse_control_and_actual_r2_pairs_are_exact_not_rounded_major_units(native, expected):
    assert convert_minor(native, 'EUR', 'RUB', fx_snapshot(), NOW) == expected


def hotel(hotel_id, offer_id):
    return {'offer_id': offer_id, 'provider': 'liteapi', 'source_type': 'SANDBOX',
        'amount_minor': 1234500, 'currency': 'RUB', 'price_kind': 'PROVIDER_ROOMS_STAY_TOTAL',
        'details': {'kind': 'hotel', 'hotel_id': hotel_id, 'binding': {'hotel_id': hotel_id, 'stay_id': 'stay-1'}}}


@pytest.mark.asyncio
@pytest.mark.parametrize('supplier_response_matches', [True, False])
async def test_partial_public_media_joins_exact_supplier_property_and_never_promotes_cart_or_ratings(supplier_response_matches):
    state = trip()
    offers = [hotel('lp-exact-hotel', 'hotel-offer')]
    plan = {'plan_id': 'partial-photo', 'trip_id': state.trip_id, 'state_version': state.state_version,
        'total_minor': None, 'known_subtotal_minor': 1234500, 'currency': 'RUB', 'offers': offers,
        'coverage': {'complete': False, 'missing': ['transfer:stay-1']},
        'ready_for_simulated_checkout': False, 'ready_for_real_booking': False, 'booking_created': False}
    store, calls = ReadOnlyPlanStore(state.model_dump(mode='json'), plan), []
    def respond(request):
        calls.append(request)
        assert request.method == 'GET' and request.url.host == 'api.liteapi.travel'
        assert request.url.path == '/v3.0/data/hotel'
        assert request.url.params['hotelId'] == 'lp-exact-hotel'
        return httpx.Response(200, json={'data': {
            'id': 'lp-exact-hotel' if supplier_response_matches else 'lp-different-hotel',
            'name': 'Same hotel name does not establish identity', 'stars': 4, 'rating': 9.2,
            'hotelImages': [{'url': 'https://snaphotelapi.com/hotels/exact-property.jpg', 'defaultImage': True}]}})
    gateway = TravelGateway({'LITEAPI_API_KEY': 'sand_synthetic_price_media', 'LITEAPI_MODE': 'sandbox',
        'clock': lambda: NOW, 'http_transport': httpx.MockTransport(respond)})
    try:
        pictures = MediaResolver(gateway)
        app = create_app(Settings('unused', 'groq', 1, 8000, 0, 1), store=store,
            llm=SimpleNamespace(), travel=gateway, media=pictures)
        frozen = deepcopy(store.plan)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://controlled') as client:
            headers = {'Authorization': 'Bearer ' + TOKEN}
            response = await client.get('/api/v1/sessions/own/media?plan_id=partial-photo&state_version=4', headers=headers)
            assert response.status_code == 200
            media = response.json()['items'][0]['media']
            if supplier_response_matches:
                assert media['hotelId'] == media['sourceHotelId'] == 'lp-exact-hotel'
                assert media['url'] == 'https://snaphotelapi.com/hotels/exact-property.jpg'
                assert media['isActualPropertyPhoto'] is True
            else:
                assert media['kind'] == 'PLACEHOLDER' and media['sourceHotelId'] is None
                assert media['url'] is None and media['isActualPropertyPhoto'] is False
            # Photo DTO currently has no rating authority. In particular an
            # untyped9.2 must not silently become nine hotel stars.
            assert media.get('stars') is None and media.get('review_score') is None
            assert media.get('rating') is None
            preview = await client.post('/api/v1/sessions/own/checkout/preview', headers=headers,
                json={'plan_id': 'partial-photo', 'state_version': 4})
            assert preview.status_code == 404
        assert len(calls) == 1 and store.plan == frozen
    finally:
        await gateway.close()
