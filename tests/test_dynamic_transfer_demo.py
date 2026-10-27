"""Opt-in finite transfer fallback; synthetic/controlled sources, no HTTP/SQL."""
import asyncio
import json
from copy import deepcopy
from datetime import timedelta

import httpx
import pytest
from test_dynamic_place_suppliers import (
    NOW,
    DynamicFakeTravel,
    city_row,
    dynamic_trip,
    gateway,
    hotel_row,
    rates_body,
)
from test_place_refs import place

from otravel.domain import Operation, apply_operations, sample_trip
from otravel.fx import convert_minor, parse_cbr_xml
from otravel.inventory import (
    PLACE_TRANSFER_CATALOGUE,
    PLACE_TRANSFER_NOTICE,
    PLACE_TRANSFER_PROVIDER,
    DemoInventory,
    bounded_place_transfer_catalog,
    reprice_plan,
)
from otravel.orchestrator import TripSearch
from otravel.planning import assemble_plan, validate_quote


def search(state=None, adapter=None):
    return asyncio.run(TripSearch(adapter or DynamicFakeTravel(), clock=lambda: NOW).search(state or dynamic_trip()))


def codes(plan):
    return {issue['code'] for issue in plan['blockers']}


def external_offers(plan):
    return [deepcopy(o) for o in plan['offers'] if o['source_type'] != 'DEMO']


def test_policy_true_only_fills_transfer_after_controlled_hotel_and_all_flight_facts_are_complete():
    state, adapter = dynamic_trip(), DynamicFakeTravel()
    before = state.model_dump(mode='json')
    result = search(state, adapter)
    assert state.model_dump(mode='json') == before
    assert result['tool_calls_used'] == len(adapter.calls) == 5
    assert len([c for c in adapter.calls if c[0] == 'resolve_air_place']) == 1
    assert len([c for c in adapter.calls if c[0] == 'search_flights']) == 2
    assert result['effective_demo_fallback'] is False
    assert result['effective_transfer_demo_fallback'] is True
    plan, = result['plans']
    demo, = [o for o in plan['offers'] if o['source_type'] == 'DEMO']
    assert len(plan['offers']) == 4 and len(external_offers(plan)) == 3
    assert all(o['source_type'] == 'SANDBOX' for o in external_offers(plan))
    assert demo['kind'] == 'transfer' and demo['provider'] == PLACE_TRANSFER_PROVIDER
    assert demo['catalogue'] == PLACE_TRANSFER_CATALOGUE and demo['name'] == PLACE_TRANSFER_NOTICE
    assert demo['place_id'] == state.stays[0].place_id and demo['country_iso2'] == 'AR'
    assert demo['date'] == '2026-11-10' and demo['check_out'] == '2026-11-13'
    assert demo['traveler_ids'] == ['adult-1'] and demo['total_minor'] == 40000
    assert plan['total_minor'] == 3_040_000 and plan['coverage']['complete']
    assert plan['source_type'] == 'MIXED' and plan['ready_for_simulated_checkout']
    assert not plan['ready_for_real_booking'] and not plan['booking_created']
    component, = [c for c in plan['components'] if c['provider'] == PLACE_TRANSFER_PROVIDER]
    assert component['binding']['place_ref_sha256'] == state.location_refs[demo['place_id']].snapshot_sha256()
    assert component['binding']['country_iso2'] == 'AR' and component['binding']['state_version'] == state.state_version
    assert PLACE_TRANSFER_NOTICE in plan['warnings']
    assert validate_quote(plan, state, now=NOW) == []


def test_real_gateway_parsers_with_five_mock_requests_and_policy_true_create_exact_mixed_checkout_plan():
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path == '/v3.0/data/hotels':
            return httpx.Response(200, json={'data': [hotel_row()]})
        if request.url.path == '/v3.0/hotels/rates':
            return httpx.Response(200, json=rates_body())
        if request.url.path == '/places/suggestions':
            return httpx.Response(200, json={'data': [city_row()]})
        assert request.url.path == '/air/offer_requests'
        data = json.loads(request.content)['data']
        leg = data['slices'][0]
        segment = {'origin': {'iata_code': leg['origin']}, 'destination': {'iata_code': leg['destination']},
            'departing_at': leg['departure_date'] + 'T09:00:00', 'stops': []}
        return httpx.Response(200, json={'data': {'offers': [{
            'id': 'controlled-flight-' + leg['departure_date'], 'total_amount': '100.00', 'total_currency': 'RUB',
            'expires_at': (NOW + timedelta(hours=1)).isoformat(), 'passengers': [{'id': 'p1', 'type': 'adult'}],
            'slices': [{'origin': segment['origin'], 'destination': segment['destination'], 'segments': [segment]}]}]}})
    async def run():
        adapter = gateway(handler)
        try:
            return await TripSearch(adapter, clock=lambda: NOW).search(dynamic_trip())
        finally:
            await adapter.close()
    result = asyncio.run(run())
    plan, = result['plans']
    assert len(calls) == result['tool_calls_used'] == 5
    assert len(external_offers(plan)) == 3 and plan['source_type'] == 'MIXED'
    demo, = [o for o in plan['offers'] if o['source_type'] == 'DEMO']
    assert demo['kind'] == 'transfer' and demo['country_iso2'] == 'AR'
    assert plan['total_minor'] == 70000 and plan['ready_for_simulated_checkout']
    assert result['effective_transfer_demo_fallback'] and not plan['ready_for_real_booking']


def test_catalogue_is_one_exact_ref_snapshot_per_stay_with_bounded_group_vehicle_capacity():
    state = sample_trip()
    state.search_policy.mode = 'HYBRID'
    state.search_policy.demo_fallback = True
    argentina, spain = place(1001, 'Córdoba', 'AR'), place(1002, 'Córdoba', 'ES', timezone='Europe/Madrid')
    state, _ = apply_operations(state, [
        Operation(type='upsert_stay', target_id=state.stays[0].id,
            value={'city': 'Córdoba', 'place_id': argentina.place_id}),
        Operation(type='upsert_stay', target_id=state.stays[1].id,
            value={'city': 'Córdoba', 'place_id': spain.place_id})],
        resolved_places={argentina.place_id: argentina, spain.place_id: spain})
    frozen = state.model_dump(mode='json')
    result = bounded_place_transfer_catalog(state)
    assert not result['questions'] and not result['violations']
    assert len(result['offers']) == 2 and result == bounded_place_transfer_catalog(state)
    assert {o['country_iso2'] for o in result['offers']} == {'AR', 'ES'}
    assert len({o['offer_id'] for o in result['offers']}) == 2
    for stay, offer in zip(state.stays, result['offers']):
        assert offer['place_id'] == stay.place_id and offer['stay_id'] == stay.id
        assert offer['date'] == stay.check_in.isoformat() and offer['check_out'] == stay.check_out.isoformat()
        assert offer['traveler_ids'] == [p.id for p in state.travelers]
        assert offer['vehicle_count'] == 3 and offer['seats_per_vehicle'] == 4 and offer['capacity'] == 12
        assert offer['total_minor'] == 360000 and offer['currency'] == 'RUB'
        assert offer['availability'] == 'synthetic_not_live' and offer['fees_included'] is True
        assert offer['booking_created_at_supplier'] is False
    assert state.model_dump(mode='json') == frozen


@pytest.mark.parametrize('kind', ['policy_false', 'demo_mode', 'legacy_no_ref'])
def test_dynamic_transfer_catalogue_requires_existing_opt_in_policy_and_an_exact_ref(kind):
    state = dynamic_trip()
    if kind == 'policy_false':
        state.search_policy.demo_fallback = False
        result = search(state)
        assert result['plans'] == [] and not result['effective_transfer_demo_fallback']
        assert result['partial_plan']['coverage']['missing'] == ['transfer:stay-world']
    elif kind == 'demo_mode':
        state.search_policy.mode = 'DEMO'
    else:
        state = sample_trip()
        state.search_policy.mode = 'HYBRID'
    assert bounded_place_transfer_catalog(state)['offers'] == []


@pytest.mark.parametrize('unproven', ['all_sources', 'air_identity', 'fees_allocation'])
def test_transfer_fallback_does_not_fill_failed_hotels_flights_or_unknown_supplier_facts(unproven):
    adapter = DynamicFakeTravel(fail=unproven == 'all_sources', air_bad=unproven == 'air_identity')
    if unproven == 'fees_allocation':
        adapter.proven = False
    result = search(adapter=adapter)
    assert result['plans'] == [] and not result['effective_transfer_demo_fallback']
    assert not result['partial_plan']['ready_for_simulated_checkout']
    assert not any(o['source_type'] == 'DEMO' for o in result['partial_plan']['offers'])
    assert any(key.startswith(('hotel:', 'leg:')) for key in result['partial_plan']['coverage']['missing'])


def test_same_name_US_city_never_accepts_russian_demo_hotel_or_flight_snapshots():
    location = place(55, 'Москва', 'US', latitude=46.7, longitude=-117.0, timezone='America/Los_Angeles')
    state = dynamic_trip(location)
    # The old name-based finite inventory can produce fixtures for this label;
    # the world assembler must refuse those, rather than treating US as RU.
    legacy = DemoInventory().search_trip(state, catalogue_only=True)
    assert legacy['plans'], 'Negative control must contain actual legacy snapshots'
    result = assemble_plan(state, legacy['plans'][0]['offers'], demo_fallback=True, now=NOW)
    assert result['offers'] == [] and 'unknown_demo_snapshot' in codes(result)
    assert not result['ready_for_simulated_checkout']
    offered = bounded_place_transfer_catalog(state)['offers']
    assert len(offered) == 1 and offered[0]['country_iso2'] == 'US'
    assert offered[0]['place_id'] == location.place_id


@pytest.mark.parametrize('field,value', [
    ('country_iso2', 'US'), ('place_id', 'geo:openmeteo:55'), ('place_ref_version', 99),
    ('place_ref_sha256', '0' * 64), ('trip_id', 'foreign-trip'), ('state_version', 99),
    ('stay_id', 'foreign-stay'), ('date', '2026-11-11'), ('check_out', '2026-11-14'),
    ('traveler_ids', ['foreign-person']), ('capacity', 1), ('vehicle_count', 0), ('total_minor', 1),
])
def test_forged_demo_transfer_snapshot_is_rejected_before_refresh_or_ready_flag(field, value):
    state = dynamic_trip()
    original = search(state)['plans'][0]
    offers = deepcopy(original['offers'])
    transfer = next(o for o in offers if o['source_type'] == 'DEMO')
    transfer[field] = value
    rejected = assemble_plan(state, offers, now=NOW)
    assert 'unknown_demo_snapshot' in codes(rejected)
    assert not rejected['ready_for_simulated_checkout']
    assert rejected['coverage']['missing'] == ['transfer:stay-world']
    assert original['ready_for_simulated_checkout']


def test_supplier_fees_and_unknown_hard_constraints_stay_blocking_even_with_transfer_policy_enabled():
    state = dynamic_trip()
    original = search(state)['plans'][0]
    source = external_offers(original)
    hotel = next(o for o in source if o['details']['kind'] == 'hotel')
    hotel['details']['fees_included'] = False
    failed = assemble_plan(state, source, demo_fallback=True, now=NOW)
    assert 'mandatory_fees_unknown' in codes(failed) and not failed['ready_for_simulated_checkout']
    assert not any(o['source_type'] == 'DEMO' for o in failed['offers'])
    state, _ = apply_operations(state, [Operation(type='set_constraint', value={
        'key': 'mandatory_sea_transfer', 'value': True, 'hard': True})])
    unknown = assemble_plan(state, source, demo_fallback=True, now=NOW)
    assert 'unsupported_hard_constraint' in codes(unknown) and not unknown['ready_for_simulated_checkout']
    assert bounded_place_transfer_catalog(state)['offers'] == []


@pytest.mark.parametrize('source_type', ['LIVE_INDICATIVE', 'LIVE_BOOKABLE', 'SANDBOX'])
def test_synthetic_transfer_namespace_cannot_be_promoted_even_with_forged_supplier_flags(source_type):
    state = dynamic_trip()
    plan = search(state)['plans'][0]
    transfer, = [o for o in plan['offers'] if o['provider'] == PLACE_TRANSFER_PROVIDER]
    component, = [c for c in plan['components'] if c['provider'] == PLACE_TRANSFER_PROVIDER]
    forged = deepcopy(transfer)
    forged.update(source_type=source_type, price_kind='COMPONENT_TOTAL', amount_minor=1,
        retrieved_at=NOW.isoformat(), expires_at=(NOW + timedelta(hours=1)).isoformat())
    forged['details'] = {'kind': 'transfer', 'binding': deepcopy(component['binding']),
        'mandatory_fees_known': True, 'availability_verified': True, 'supplier_reprice_verified': True}
    invalid = assemble_plan(state, external_offers(plan) + [forged], now=NOW)
    assert 'unknown_demo_snapshot' in codes(invalid)
    assert not invalid['ready_for_simulated_checkout'] and invalid['total_minor'] is None


def test_full_trip_budget_includes_the_explicit_synthetic_transfer_price():
    state = dynamic_trip()
    state, _ = apply_operations(state, [Operation(type='set_budget', value={'amount_minor': 3_000_000, 'currency': 'RUB'})])
    result = search(state)
    assert not result['plans']
    plan = result['partial_plan']
    assert 'budget_exceeded' in codes(plan)
    assert plan['total_minor'] == 3_040_000 and not plan['ready_for_simulated_checkout']
    assert any(o['provider'] == PLACE_TRANSFER_PROVIDER for o in plan['offers'])


def test_demo_transfer_refresh_is_deterministic_and_rechecked_with_all_real_components():
    state = dynamic_trip()
    first = search(state)['plans'][0]
    transfer, = [o for o in first['offers'] if o['source_type'] == 'DEMO']
    refreshed_demo = reprice_plan({'source_type': 'DEMO', 'offers': [transfer]})['offers']
    assert refreshed_demo == [transfer]
    refreshed = external_offers(first) + refreshed_demo
    for offer in refreshed:
        if offer['source_type'] != 'DEMO':
            offer['amount_minor'] += 100
            offer['retrieved_at'] = (NOW + timedelta(seconds=1)).isoformat()
    second = assemble_plan(state, refreshed, previous_plan=first, now=NOW + timedelta(seconds=1))
    assert second['total_minor'] == first['total_minor'] + 300
    assert second['ready_for_simulated_checkout'] and validate_quote(second, state, now=NOW + timedelta(seconds=1)) == []
    assert any(i['code'] == 'source_snapshot_changed' for i in validate_quote(first, state,
        current_offers=refreshed, now=NOW + timedelta(seconds=1)))
    assert second['comparison']['requires_new_confirmation']
    state.search_policy.demo_fallback = False
    issues = {i['code'] for i in validate_quote(second, state, now=NOW + timedelta(seconds=1))}
    assert {'stale_quote_state', 'unknown_demo_snapshot'}.issubset(issues)


def test_native_RUB_transfer_and_foreign_budget_require_reference_FX_without_relabeling_prices():
    state = dynamic_trip()
    initial = search(state)['plans'][0]
    state.search_policy.guest_nationality = 'RU'
    state.budget = type(sample_trip().budget)(amount_minor=100_000_000, currency='EUR')
    unpriced = assemble_plan(state, external_offers(initial), demo_fallback=True, now=NOW)
    assert 'fx_required' in codes(unpriced) and not unpriced['ready_for_simulated_checkout']
    raw = (b'<ValCurs Date="27.10.2026"><Valute><CharCode>EUR</CharCode><Nominal>1</Nominal><Value>90.0935</Value></Valute>'
        b'<Valute><CharCode>USD</CharCode><Nominal>1</Nominal><Value>80.4909</Value></Valute></ValCurs>')
    fx = parse_cbr_xml(raw, NOW)
    plan = assemble_plan(state, external_offers(initial), demo_fallback=True, now=NOW, fx_snapshot=fx)
    transfer = next(c for c in plan['components'] if c['provider'] == PLACE_TRANSFER_PROVIDER)
    assert transfer['currency'] == 'RUB' and transfer['amount_minor'] == 40000
    assert transfer['budget_currency'] == 'EUR' and transfer['budget_amount_minor'] == convert_minor(40000, 'RUB', 'EUR', fx, NOW)
    assert plan['currency_totals'] == {'RUB': 3_040_000} and plan['ready_for_simulated_checkout']
    assert plan['fx_reference_only'] and not plan['ready_for_real_booking']
