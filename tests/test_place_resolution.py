"""Coordinator choices with real directory parsing and controlled catalog only."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from otravel.domain import TripState, apply_operations
from otravel.geo_catalog import _envelope, _fact_snapshot
from otravel.place_directory import MemoryPlaceCache, PlaceDirectory
from otravel.place_resolution import PlaceResolutionCoordinator, interpret_short_reply

NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)
BASE = {'id': 3435910, 'name': 'Буэнос-Айрес', 'country_code': 'AR', 'country': 'Аргентина',
    'latitude': -34.61315, 'longitude': -58.37723, 'timezone': 'America/Argentina/Buenos_Aires', 'feature_code': 'PPLC'}
SECOND = {**BASE, 'id': 3863777, 'name': 'Buenos Aires', 'latitude': -25.81846,
    'longitude': -63.06261, 'timezone': 'America/Argentina/Cordoba', 'feature_code': 'PPL'}


class Catalog(MemoryPlaceCache):
    def __init__(self):
        super().__init__()
        self.admissions = []

    async def admit_place(self, key, external_id, *, expected_source_fact_sha256,
            expected_response_sha256, now, witness):
        shown = await self.get_directory(witness['directory_key'], now=now)
        assert shown and shown['response_sha256'] == witness['expected_response_sha256']
        ru = next(c for c in shown['data']['candidates'] if c['geographic_source']['external_id'] == external_id)
        assert ru['source_fact_sha256'] == witness['expected_source_fact_sha256']
        current = _envelope(key, await self.get_directory(key, now=now), now, fresh=True)
        assert current['response_sha256'] == expected_response_sha256
        fact = next(c for c in current['data']['candidates'] if c['geographic_source']['external_id'] == external_id)
        assert fact['source_fact_sha256'] == expected_source_fact_sha256 and fact['language'] == 'en'
        self.admissions.append((deepcopy(key), external_id, deepcopy(witness)))
        return _fact_snapshot(fact, 1)


class Countries:
    def __init__(self):
        self.calls = 0
    async def list_countries(self):
        self.calls += 1
        return [{'country_iso2': 'AR', 'name': 'Argentina'}, {'country_iso2': 'GB', 'name': 'United Kingdom'},
            {'country_iso2': 'RS', 'name': 'Serbia'}]


def state(**updates):
    return TripState.model_validate({'trip_id': 'own-trip', **updates}).model_dump(mode='json')


def request(**updates):
    return {'kind': 'city', 'query': 'Buenos Aires', 'role': 'destination', **updates}


def selection(packet, index=0):
    p = packet['pending_context']['place_resolution']
    return {'resolution_id': p['resolution_id'], 'candidate_id': p['candidates'][index]['candidate_id']}


class System:
    def __init__(self, rows=None, mutate_en=None):
        self.clock = NOW
        self.catalog, self.travel = Catalog(), Countries()
        self.rows = [BASE, SECOND] if rows is None else rows
        self.wires, self.budgets = [], []
        def handler(req):
            self.wires.append(req)
            if req.url.path.endswith('search'):
                return httpx.Response(200, json={'results': deepcopy(self.rows)})
            chosen = next(r for r in self.rows if r['id'] == int(req.url.params['id']))
            en = {**chosen, 'name': 'Buenos Aires', 'country': 'Argentina'}
            if mutate_en:
                en.update(mutate_en)
            return httpx.Response(200, json=en)
        self.directory = PlaceDirectory(cache=self.catalog, transport=httpx.MockTransport(handler), clock=lambda: self.clock)
        self.coordinator = PlaceResolutionCoordinator(self.catalog, self.directory, self.travel, clock=lambda: self.clock)

    async def before(self, event):
        self.budgets.append(deepcopy(event))
        return 4

    async def close(self):
        await self.directory.close()


@pytest.mark.asyncio
async def test_city_hint_is_not_country_authority_and_all_homonyms_are_shown_without_mutation():
    system, original = System(), state()
    before = deepcopy(original)
    try:
        packet = await system.coordinator.resolve(request(country_iso2='GB'), original, before_call=system.before)
        assert packet['action'] == 'ask' and len(packet['pending_context']['place_resolution']['candidates']) == 2
        assert 'countryCode' not in system.wires[0].url.params
        assert packet['operations'] == [] and packet['resolved_places'] == {} and original == before
        assert system.catalog.admissions == [] and system.travel.calls == 0
    finally:
        await system.close()


@pytest.mark.asyncio
async def test_explicit_choice_gets_same_id_en_and_emits_only_trusted_setter_and_refs():
    system, original = System(), state()
    try:
        ask = await system.coordinator.resolve(request(), original, before_call=system.before)
        packet = await system.coordinator.select(selection(ask), ask['pending_context'], original, before_call=system.before)
        assert packet['action'] == 'selected' and len(system.wires) == 2
        assert system.wires[1].url.params['id'] == '3435910' and system.wires[1].url.params['language'] == 'en'
        assert packet['operations'] == [{'type': 'upsert_stay', 'target_id': 'new-stay-1',
            'value': {'city': 'Buenos Aires', 'place_id': 'geo:openmeteo:3435910'}}]
        changed, _ = apply_operations(TripState.model_validate(original), packet['operations'], resolved_places=packet['resolved_places'])
        assert changed.stays[0].city == 'Buenos Aires' and changed.stays[0].place_id == 'geo:openmeteo:3435910'
        assert original['stays'] == [] and len(system.catalog.admissions) == 1
        assert system.catalog.admissions[0][2]['directory_key']['language'] == 'ru'
    finally:
        await system.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('reply', ['да', 'верно', 'yes'])
async def test_yes_cannot_choose_first_of_multiple_candidates_and_costs_no_new_wire(reply):
    system = System()
    try:
        ask = await system.coordinator.resolve(request(), state(), before_call=system.before)
        packet = await system.coordinator.select(reply, ask['pending_context'], state(), before_call=system.before)
        assert packet['action'] == 'ask' and not packet['operations']
        assert len(system.wires) == 1 and system.catalog.admissions == []
    finally:
        await system.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('reply,index', [('1', 0), ('второй', 1), ('id:3863777', 1)])
async def test_short_reply_selects_only_its_bound_number_ordinal_or_unique_id(reply, index):
    system = System()
    try:
        ask = await system.coordinator.resolve(request(), state(), before_call=system.before)
        answer = interpret_short_reply(ask['pending_context'], reply, state(), now=NOW)
        assert answer['selection'] == selection(ask, index)
    finally:
        await system.close()


@pytest.mark.asyncio
async def test_single_yes_is_only_allowed_when_one_candidate_was_shown():
    system = System(rows=[BASE])
    try:
        ask = await system.coordinator.resolve(request(role='origin'), state(), before_call=system.before)
        packet = await system.coordinator.select('да', ask['pending_context'], state(), before_call=system.before)
        assert packet['action'] == 'selected'
        assert packet['operations'][0]['type'] == 'set_origin'
        assert packet['operations'][0]['value']['city'] == 'Buenos Aires'
    finally:
        await system.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['foreign_trip', 'version', 'expired', 'foreign_candidate', 'tampered_label'])
async def test_stale_or_foreign_choice_never_gets_an_en_response_or_changes_state(change):
    system, original = System(), state()
    before = deepcopy(original)
    try:
        ask = await system.coordinator.resolve(request(), original, before_call=system.before)
        pending, choose = deepcopy(ask['pending_context']), selection(ask)
        if change == 'foreign_trip': original['trip_id'] = 'other-trip'
        elif change == 'version': original['state_version'] += 1
        elif change == 'expired': system.clock += timedelta(minutes=16)
        elif change == 'foreign_candidate': choose['candidate_id'] = 'pc-'+'f'*32
        elif change == 'tampered_label': pending['place_resolution']['candidates'][0]['label'] = 'Another city'
        expected = deepcopy(original)
        result = await system.coordinator.select(choose, pending, original, before_call=system.before)
        assert result['action'] == 'ask' and result['operations'] == []
        assert len(system.wires) == 1 and system.catalog.admissions == [] and original == expected
        assert before['stays'] == original['stays']
    finally:
        await system.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('changed', [{'country_code': 'GB'}, {'latitude': -30.0}, {'timezone': 'Europe/London'}, {'id': 123}])
async def test_same_id_english_fetch_cannot_change_physical_country_coords_timezone_or_id(changed):
    system = System(mutate_en=changed)
    try:
        ask = await system.coordinator.resolve(request(), state(), before_call=system.before)
        result = await system.coordinator.select(selection(ask), ask['pending_context'], state(), before_call=system.before)
        assert result['action'] in {'ask', 'unavailable'} and result['operations'] == []
        assert system.catalog.admissions == []
    finally:
        await system.close()


@pytest.mark.asyncio
async def test_country_exact_match_asks_city_and_confirmed_ref_survives_coordinator_restart():
    system, original = System(), state()
    try:
        packet = await system.coordinator.resolve(request(kind='country', query='Argentina', country_iso2='AR'), original, before_call=system.before)
        assert packet['action'] == 'ask' and 'город' in packet['question'] and not packet['operations']
        saved = packet['pending_context']['place_resolution']
        assert saved['stage'] == 'city_query' and saved['country_ref']['country_iso2'] == 'AR'
        assert system.wires == [] and system.travel.calls == 1
        restarted = PlaceResolutionCoordinator(system.catalog, system.directory, Countries(), clock=lambda: NOW)
        result = await restarted.resolve(request(country_ref=saved['country_ref'], stay_id=saved['stay_id']), original, before_call=system.before)
        assert result['action'] == 'ask' and system.wires[0].url.params['countryCode'] == 'AR'
        assert restarted.travel.calls == 0
    finally:
        await system.close()


@pytest.mark.asyncio
async def test_unknown_country_hint_requires_confirmation_and_unknown_iso_never_picks_a_capital():
    system = System()
    try:
        packet = await system.coordinator.resolve(request(kind='country', query='Аргентина', country_iso2='AR'), state(), before_call=system.before)
        assert packet['pending_context']['place_resolution']['stage'] == 'country_choice'
        confirmed = await system.coordinator.select(selection(packet), packet['pending_context'], state(), before_call=system.before)
        assert confirmed['pending_context']['place_resolution']['stage'] == 'city_query'
        assert system.wires == []
        missing = await system.coordinator.resolve(request(kind='country', query='XX', country_iso2='XX'), state(), before_call=system.before)
        assert missing['action'] == 'no_match' and missing['operations'] == []
        assert system.travel.calls == 1
    finally:
        await system.close()


@pytest.mark.asyncio
async def test_country_hint_mismatch_does_not_silently_replace_the_named_country():
    system = System()
    try:
        packet = await system.coordinator.resolve(request(kind='country', query='Сербия', country_iso2='AR'), state(), before_call=system.before)
        countries = packet['pending_context']['place_resolution']['candidates']
        assert {c['country_iso2'] for c in countries} == {'RS', 'AR'}
        assert interpret_short_reply(packet['pending_context'], 'да', state(), now=NOW)['selection'] is None
        assert not packet['operations'] and not system.wires
    finally:
        await system.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('request_updates', [{'stay_id': 'foreign-stay'}, {'role': 'origin', 'stay_id': 'foreign-stay'}])
async def test_foreign_stay_target_is_rejected_before_any_directory_request(request_updates):
    system = System()
    try:
        packet = await system.coordinator.resolve(request(**request_updates), state(), before_call=system.before)
        assert packet['action'] == 'ask' and packet['operations'] == []
        assert not system.wires and not system.budgets
    finally:
        await system.close()


@pytest.mark.asyncio
async def test_multicity_omitted_target_asks_instead_of_replacing_first_stay():
    system = System()
    original = state(stays=[{'id': 'first', 'city': 'Казань'}, {'id': 'second', 'city': 'Стамбул'}])
    before = deepcopy(original)
    try:
        result = await system.coordinator.resolve(request(), original, before_call=system.before)
        assert result['action'] == 'ask' and result['error']['code'] == 'stay_target_required'
        assert original == before and not system.wires and not result['operations']
    finally:
        await system.close()


@pytest.mark.asyncio
async def test_route_end_does_not_create_a_stay_and_no_match_does_not_mutate_trip():
    system = System(rows=[BASE])
    try:
        ask = await system.coordinator.resolve(request(role='route_end'), state(), before_call=system.before)
        selected = await system.coordinator.select(selection(ask), ask['pending_context'], state(), before_call=system.before)
        assert selected['operations'] == [{'type': 'set_route_policy', 'value': {'kind': 'open_jaw',
            'end_city': 'Buenos Aires', 'end_place_id': 'geo:openmeteo:3435910'}}]
    finally:
        await system.close()
    empty = System(rows=[])
    try:
        packet = await empty.coordinator.resolve(request(), state(), before_call=empty.before)
        assert packet['action'] == 'no_match' and packet['operations'] == [] and packet['resolved_places'] == {}
    finally:
        await empty.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('changed', ['version', 'role', 'stay', 'expiry'])
async def test_confirmed_country_is_bound_to_trip_revision_role_and_stay(changed):
    system, original = System(), state()
    try:
        country = await system.coordinator.resolve(request(kind='country', query='Argentina'), original, before_call=system.before)
        saved = country['pending_context']['place_resolution']
        ref = deepcopy(saved['country_ref'])
        if changed == 'version': ref['state_version'] += 1
        elif changed == 'role': ref['role'] = 'origin'
        elif changed == 'stay': ref['stay_id'] = 'foreign'
        elif changed == 'expiry': ref['expires_at'] = (NOW-timedelta(seconds=1)).isoformat()
        result = await system.coordinator.resolve(request(country_ref=ref), original, before_call=system.before)
        assert result['action'] == 'ask' and result['error']['code'] == 'stale_country_ref'
        assert system.wires == [] and not result['operations'] and original == state()
    finally:
        await system.close()


@pytest.mark.asyncio
async def test_country_budget_denial_prevents_wire_and_private_failure_is_sanitized():
    system = System()
    async def denied(_event):
        raise RuntimeError('secret-key=not-for-output https://private.invalid/token')
    try:
        result = await system.coordinator.resolve(request(kind='country', query='Argentina'), state(), before_call=denied)
        assert result['action'] == 'unavailable' and result['error']['code'] == 'resolution_unavailable'
        assert 'secret' not in str(result) and system.travel.calls == 0 and system.wires == []
    finally:
        await system.close()


@pytest.mark.asyncio
async def test_city_budget_denial_prevents_wire_and_does_not_admit_any_place():
    system = System()
    async def denied(_event):
        return 0
    try:
        result = await system.coordinator.resolve(request(), state(), before_call=denied)
        assert result['action'] == 'unavailable' and not result['operations']
        assert system.wires == [] and system.catalog.admissions == []
    finally:
        await system.close()


@pytest.mark.asyncio
async def test_switching_owned_stay_city_preserves_other_requirements_and_unpins_old_hotel():
    system = System(rows=[BASE])
    original = state(stays=[{'id': 'owned-stay', 'city': 'Казань', 'hotel_id': 'kzn-family',
        'check_in': '2026-11-10', 'check_out': '2026-11-13'}], origin='Москва',
        travelers=[{'id': 'adult', 'type': 'adult'}], budget={'amount_minor': 20000000, 'currency': 'RUB'})
    before = deepcopy(original)
    try:
        ask = await system.coordinator.resolve(request(stay_id='owned-stay'), original, before_call=system.before)
        result = await system.coordinator.select(selection(ask), ask['pending_context'], original, before_call=system.before)
        assert result['action'] == 'selected' and result['operations'][0]['target_id'] == 'owned-stay'
        assert result['operations'][0]['value']['hotel_id'] is None and original == before
        changed, _ = apply_operations(TripState.model_validate(original), result['operations'], resolved_places=result['resolved_places'])
        after = changed.model_dump(mode='json')
        assert changed.stays[0].city == 'Buenos Aires' and changed.stays[0].hotel_id is None
        assert after['travelers'] == before['travelers'] and after['budget'] == before['budget']
        assert after['origin'] == before['origin']
        assert after['stays'][0]['check_in'] == before['stays'][0]['check_in'] and after['stays'][0]['check_out'] == before['stays'][0]['check_out']
    finally:
        await system.close()


@pytest.mark.asyncio
async def test_choice_expiring_during_en_lookup_cannot_admit_place_or_emit_setter():
    system = System(rows=[BASE])
    try:
        ask = await system.coordinator.resolve(request(), state(), before_call=system.before)
        system.clock = NOW+timedelta(seconds=899)
        async def advance_during_en(event):
            system.clock = NOW+timedelta(seconds=901)
            return await system.before(event)
        result = await system.coordinator.select(selection(ask), ask['pending_context'], state(), before_call=advance_during_en)
        assert result['action'] == 'ask' and result['error']['code'] == 'expired_choice'
        assert not result['operations'] and not result['resolved_places']
        assert len(system.wires) == 2 and system.catalog.admissions == []
    finally:
        await system.close()
