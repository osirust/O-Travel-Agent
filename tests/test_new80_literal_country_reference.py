"""Actual coordinator + directory boundary after the fresh013 retest failed.

HTTP is MockTransport; the CountryRef uses the packaged verified CLDR path.
No source candidate is selected or admitted and no model call is made.
"""
from copy import deepcopy

import httpx
import pytest
from test_new80_geo_date_helpers import ROWS
from test_place_resolution import NOW, Catalog, Countries
from test_place_run import Harness, context

from otravel.country_lexicon import DATA_SHA256
from otravel.domain import TripState
from otravel.place_directory import PlaceDirectory
from otravel.place_resolution import PlaceResolutionCoordinator
from otravel.place_run import finish_place_run

RUSSIA = {'id': 498817, 'name': 'Санкт-Петербург', 'country_code': 'RU', 'country': 'Россия',
    'latitude': 59.9386, 'longitude': 30.3141, 'timezone': 'Europe/Moscow', 'feature_code': 'PPLA',
    'admin1': 'Санкт-Петербург', 'admin1_id': 536203}
HOMONYM = {'id': 3578038, 'name': 'Санкт-Петербург', 'country_code': 'MS', 'country': 'Montserrat',
    'latitude': 16.8, 'longitude': -62.2, 'timezone': 'America/Montserrat', 'feature_code': 'PPL'}


async def exercise(text, *, request_hint=None, request_source_text=None, ignore_filter=False, control='none'):
    row = ROWS['ZAI100-013:T3']
    state, session, run = context(TripState.model_validate(row['before_trip']), deepcopy(row['before_pending']), 'clarification')
    run['input_text'] = text
    calls = []
    def wire(request):
        calls.append(request)
        rows = [RUSSIA] if request.url.params.get('countryCode') == 'RU' and not ignore_filter else [RUSSIA, HOMONYM]
        return httpx.Response(200, json={'results': deepcopy(rows)})
    catalog = Catalog()
    countries = Countries()
    directory = PlaceDirectory(cache=catalog, transport=httpx.MockTransport(wire), clock=lambda: NOW)
    coordinator = PlaceResolutionCoordinator(catalog, directory, countries, clock=lambda: NOW, offline_countries=True)
    harness = Harness([])
    harness.place_resolution = coordinator
    harness.geo_catalog = catalog
    request = {'kind': 'city', 'role': 'destination', 'query': 'Санкт-Петербург', 'stay_id': 'new-stay-1'}
    if request_hint is not None:
        request['country_iso2'] = request_hint
    if request_source_text is not None:
        request['source_text'] = request_source_text
    try:
        await finish_place_run(harness, session, run, state, request=request,
            operations=row['model_contracts']['MODEL_OPERATIONS_CHECKED']['operations'], control=control)
        return calls, harness.store.calls, catalog.admissions, countries.calls
    finally:
        await directory.close()


@pytest.mark.asyncio
async def test_actual013_literal_country_reaches_real_directory_as_verified_cldr_ref_without_selecting():
    calls, saved, admissions, external_country_calls = await exercise(ROWS['ZAI100-013:T3']['user'])
    assert len(calls) == 1 and calls[0].url.params.get('countryCode') == 'RU'
    assert external_country_calls == 0 and admissions == []
    args, kwargs = saved[0]
    assert args[2]['stays'][0]['city'] == 'Санкт-Петербург'
    assert not args[2].get('location_refs') and not args[2]['stays'][0].get('place_id')
    packet = kwargs['pending_question']['context']['place_resolution']
    assert packet['stage'] == 'city_choice'
    assert [candidate['country_iso2'] for candidate in packet['candidates']] == ['RU']
    country = packet['country_ref']
    assert country['source_provider'] == 'unicode-cldr' and country['source_sha256'] == DATA_SHA256
    assert country['trip_id'] == args[2]['trip_id'] and country['state_version'] == args[2]['state_version']
    assert country['role'] == 'destination' and country['stay_id'] == 'new-stay-1'
    assert country['country_iso2'] == 'RU'
    assert kwargs['result']['place_resolution']['action'] == 'ask'


@pytest.mark.asyncio
async def test_provider_returning_a_foreign_city_despite_country_filter_is_rejected_not_admitted():
    calls, saved, admissions, _ = await exercise(ROWS['ZAI100-013:T3']['user'], ignore_filter=True)
    assert len(calls) == 1 and calls[0].url.params.get('countryCode') == 'RU'
    assert admissions == []
    assert saved[0][1]['result']['place_resolution']['action'] == 'unavailable'
    assert not saved[0][0][2].get('location_refs')


@pytest.mark.asyncio
async def test_current_literal_and_conflicting_model_hint_never_query_the_wrong_country():
    calls, saved, admissions, _ = await exercise(ROWS['ZAI100-013:T3']['user'], request_hint='US')
    assert calls == [] and admissions == []
    assert saved[0][1]['result']['place_resolution']['action'] == 'unavailable'
    assert not saved[0][0][2].get('location_refs')


@pytest.mark.asyncio
async def test_verified_literal_country_is_deferred_during_pause_with_zero_directory_calls():
    calls, saved, admissions, country_calls = await exercise(ROWS['ZAI100-013:T3']['user'], control='wait')
    assert calls == [] and admissions == [] and country_calls == 0
    pending = saved[0][1]['pending_question']
    assert pending['reason'] == 'user_pause' and pending['field'] == 'place.deferred'
    ref = pending['context']['place_deferred_action']['request']['country_ref']
    assert ref['country_iso2'] == 'RU' and ref['source_provider'] == 'unicode-cldr'
    assert ref['state_version'] == saved[0][0][2]['state_version']


@pytest.mark.asyncio
@pytest.mark.parametrize('text', [
    'Продолжай.',
    'Если выбрать Санкт-Петербург в России?',
    'Раньше выбирал Санкт-Петербург в России.',
    '«Выбираю Санкт-Петербург в России».',
    'Не выбираю Санкт-Петербург в России.',
    'Из Санкт-Петербурга в России хочу в Стамбул.',
    'Выбираю Стамбул в Турции.',
    'Выбираю Санкт-Петербург в России или Казань.',
])
async def test_model_hint_alone_wrong_role_or_noncurrent_pair_does_not_become_country_authority(text):
    calls, saved, admissions, country_calls = await exercise(text, request_hint='RU',
        request_source_text='Выбираю Санкт-Петербург в России.')
    assert len(calls) == 1 and 'countryCode' not in calls[0].url.params
    assert admissions == [] and country_calls == 0
    packet = saved[0][1]['pending_question']['context']['place_resolution']
    assert packet['country_ref'] is None
    assert {c['country_iso2'] for c in packet['candidates']} == {'RU', 'MS'}
