"""Proof validation precedes bounded ranking; no provider/model/SQL calls."""
import asyncio
import json
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pytest
from test_dynamic_place_suppliers import NOW, DynamicFakeTravel, dynamic_trip

from otravel.domain import Operation, apply_operations
from otravel.orchestrator import TripSearch
from otravel.place_refs import CityRefSnapshot
from otravel.provider_facts import lite_rate_facts

FIXTURE = Path(__file__).parents[1] / 'fixtures/regression/lite-fees-included-projection-20261027.json'


def data():
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


class CandidateRows(DynamicFakeTravel):
    """Declared120-row hotel layout with original failure indices82/84."""
    def __init__(self, *, rows=120, good=(81, 83), defect=None):
        super().__init__()
        self.rows, self.good, self.defect = rows, good, defect

    async def resolve_hotels(self, request, limit=3):
        original = await super().resolve_hotels(request, limit)
        return [{**original[0], 'id': 'controlled-hotel-' + str(i)} for i in range(3)]

    async def search_hotels(self, request, provider='liteapi'):
        self.calls.append(('search_hotels', provider, deepcopy(request)))
        projection = data()
        ref = CityRefSnapshot.model_validate(request['place_ref'])
        result = []
        for index in range(self.rows):
            offer = self.offer(request, provider, 'hotel')
            offer['offer_id'] = 'controlled-rate-' + str(index)
            offer['details']['hotel_id'] = request['hotelIds'][0 if index < 80 else 1 if index == 80 else 2]
            offer['details'].update(place_id=ref.place_id, place_ref_sha256=ref.snapshot_sha256())
            offer['amount_minor'] = 1  # Invalid cheap offers must never win.
            offer['details'].update(mandatory_fees_known=False, fees_included=False)
            if index in self.good:
                captured = projection['included_offers'][list(self.good).index(index) % 2]
                facts = lite_rate_facts(captured['rates'], request['occupancies'], NOW)
                offer['details'].update(facts)
                offer['amount_minor'] = round(captured['offerRetailRate']['amount'] * 100)
                if self.defect == 'fees':
                    offer['details'].update(mandatory_fees_known=False, fees_included=False)
                elif self.defect == 'capacity':
                    rates = deepcopy(captured['rates'])
                    rates[0]['maxOccupancy'] = 0
                    offer['details'].update(lite_rate_facts(rates, request['occupancies'], NOW))
                elif self.defect == 'expired':
                    offer['expires_at'] = (NOW - timedelta(seconds=1)).isoformat()
                elif self.defect == 'source':
                    offer['source_type'] = 'UNKNOWN'
            result.append(offer)
        return result


def searched(adapter, state=None):
    state = state or dynamic_trip()
    if state.budget is None:
        state, _ = apply_operations(state, [Operation(type='set_budget', value={'amount_minor': 30000000, 'currency': 'RUB'})])
    return asyncio.run(TripSearch(adapter, clock=lambda: NOW).search(state))


def hotel_stats(result):
    return next(row for row in result['selection_diagnostics'] if row['kind'] == 'hotel')


def test_eligible_later_hotel_quotes_are_not_lost_after_80_fee_excluded_offers():
    adapter = CandidateRows()
    result = searched(adapter)
    assert result['plans'], 'Valid included-fee hotel82/84 must be considered after the ineligible prefix'
    plan, = result['plans']
    hotel, = [o for o in plan['offers'] if o.get('details', {}).get('kind') == 'hotel']
    assert hotel['offer_id'] == 'controlled-rate-81' and hotel['amount_minor'] == 17266427
    assert hotel['details']['fees_included'] is True and hotel['details']['allocation_verified'] is True
    # Included29964.93 is already inside172664.27, never added a second time.
    assert hotel['amount_minor'] != 17266427 + 2996493
    assert plan['total_minor'] == 19306427 and plan['ready_for_simulated_checkout']
    assert not plan['ready_for_real_booking'] and plan['source_type'] == 'MIXED'
    assert result['tool_calls_used'] == len(adapter.calls) == 5
    stats = hotel_stats(result)
    assert stats['source_rows_received'] == stats['source_rows_examined'] == 120
    assert stats['eligible_rows'] == 2 and stats['source_rows_not_examined'] == 0 and not stats['scan_truncated']
    assert 'mandatory_fees_unknown' in stats['rejected_reasons']
    assert result['comparison_scope'] == 'first_600_returned_offers_per_component_not_global_market_minimum'
    assert all('selection_diagnostics' not in offer and 'source_rows_examined' not in offer for offer in plan['offers'])


def test_actual_two_included_rate_DTOs_prove_full_amount_and_allocation_without_fee_relaxation():
    value = data()
    assert value['source_hotel_bundle_counts'] == [80, 1, 39]
    assert value['source_first30_fee_known'] == 0 and value['source_total_fee_known'] == 2
    assert [o['normalized_offer_index0'] for o in value['included_offers']] == [81, 83]
    for offer in value['included_offers']:
        facts = lite_rate_facts(offer['rates'], value['occupancies'], NOW)
        assert facts['mandatory_fees_known'] and facts['fees_included']
        assert facts['allocation_verified'] and facts['capacity_verified'] and facts['issues'] == []
        assert facts['constraint_evidence']['breakfast'] is False
        assert offer['offerRetailRate']['currency'] == 'RUB'


@pytest.mark.parametrize('defect,reason', [('fees', 'mandatory_fees_unknown'), ('capacity', 'allocation_unverified'),
    ('expired', 'stale_source_snapshot'), ('source', 'unknown_source_type')])
def test_late_quotes_still_need_fee_capacity_lifetime_and_source_proof(defect, reason):
    result = searched(CandidateRows(defect=defect))
    assert result['plans'] == [] and not result['partial_plan']['ready_for_simulated_checkout']
    assert hotel_stats(result)['eligible_rows'] == 0
    assert reason in hotel_stats(result)['rejected_reasons']
    assert not any(o.get('details', {}).get('kind') == 'hotel' for o in result['partial_plan']['offers'])


def test_late_non_breakfast_quotes_cannot_satisfy_a_hard_breakfast_request():
    state = dynamic_trip()
    state, _ = apply_operations(state, [Operation(type='set_constraint', value={'key': 'breakfast', 'value': True, 'hard': True})])
    result = searched(CandidateRows(), state)
    assert not result['plans'] and hotel_stats(result)['eligible_rows'] == 0
    assert 'hard_constraint_unverified' in hotel_stats(result)['rejected_reasons']


@pytest.mark.parametrize('good_index,expected_ready', [(599, True), (600, False)])
def test_scan_limit600_is_inclusive_of_row600_and_does_not_claim_unexamined_rows_are_valid(good_index, expected_ready):
    result = searched(CandidateRows(rows=601, good=(good_index,)))
    stats = hotel_stats(result)
    assert stats['scan_limit'] == stats['source_rows_examined'] == 600
    assert stats['source_rows_received'] == 601 and stats['source_rows_not_examined'] == 1 and stats['scan_truncated']
    assert bool(result['plans']) is expected_ready
    assert stats['eligible_rows'] == int(expected_ready)
    if not expected_ready:
        assert result['partial_plan']['coverage']['missing'] == ['hotel:stay-world:room-1', 'transfer:stay-world']
        assert any('source_offer_scan_limit' in error['message'] for error in result['component_errors'])


def test_rank_uses_cheapest_eligible_inside_the_whole_bounded_scan_not_only_first30_eligible():
    class ManyEligible(CandidateRows):
        async def search_hotels(self, request, provider='liteapi'):
            rows = await super().search_hotels(request, provider)
            for index, row in enumerate(rows):
                row['details'].update(mandatory_fees_known=True, fees_included=True)
                row['amount_minor'] = 20000000 - index * 1000
            return rows
    result = searched(ManyEligible(rows=100))
    hotel = next(o for o in result['plans'][0]['offers'] if o.get('details', {}).get('kind') == 'hotel')
    assert hotel['offer_id'] == 'controlled-rate-99'
    assert hotel_stats(result)['eligible_rows'] == 100


def test_more_eligible_quotes_do_not_relax_the_whole_trip_budget():
    state = dynamic_trip()
    state, _ = apply_operations(state, [Operation(type='set_budget', value={'amount_minor': 19000000, 'currency': 'RUB'})])
    result = searched(CandidateRows(), state)
    assert not result['plans'] and not result['partial_plan']['ready_for_simulated_checkout']
    assert any(issue['code'] == 'budget_exceeded' for issue in result['violations'])
    assert result['partial_plan']['total_minor'] == 19306427
