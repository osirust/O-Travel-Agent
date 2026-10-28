"""Literal comma pairs use verified source country filtering, not auto choice."""
from copy import deepcopy

import httpx
import pytest
from test_place_resolution import NOW, Catalog, Countries
from test_place_run import Harness, context

from otravel.domain import TripState
from otravel.place_directory import PlaceDirectory
from otravel.place_resolution import PlaceResolutionCoordinator
from otravel.place_run import finish_place_run


@pytest.mark.asyncio
@pytest.mark.parametrize('text,query,code,good,bad', [
    ('Выбираем London, Канада.', 'London', 'CA',
     {'id': 6058560, 'name': 'Лондон', 'country_code': 'CA', 'country': 'Канада',
      'latitude': 42.98339, 'longitude': -81.23304, 'timezone': 'America/Toronto', 'feature_code': 'PPLA2'},
     {'id': 2643743, 'name': 'Лондон', 'country_code': 'GB', 'country': 'Великобритания',
      'latitude': 51.50853, 'longitude': -0.12574, 'timezone': 'Europe/London', 'feature_code': 'PPLC'}),
    ('Выбираем город Сочи, Россия.', 'Сочи', 'RU',
     {'id': 491422, 'name': 'Сочи', 'country_code': 'RU', 'country': 'Россия',
      'latitude': 43.59699, 'longitude': 39.72477, 'timezone': 'Europe/Moscow', 'feature_code': 'PPLA2'},
     {'id': 3530757, 'name': 'Сочимилько', 'country_code': 'MX', 'country': 'Мексика',
      'latitude': 19.25785, 'longitude': -99.10513, 'timezone': 'America/Mexico_City', 'feature_code': 'PPL'}),
])
@pytest.mark.parametrize('ignore_filter', [False, True])
async def test_literal_country_reaches_directory_with_proof_and_never_admits_candidate(text, query, code, good, bad, ignore_filter):
    before, session, run = context(TripState(), None, None)
    run['input_text'] = text
    calls = []

    def wire(request):
        calls.append(request)
        assert request.url.params.get('countryCode') == code
        return httpx.Response(200, json={'results': deepcopy([good, bad] if ignore_filter else [good])})

    catalog, countries = Catalog(), Countries()
    directory = PlaceDirectory(cache=catalog, transport=httpx.MockTransport(wire), clock=lambda: NOW)
    coordinator = PlaceResolutionCoordinator(catalog, directory, countries, clock=lambda: NOW, offline_countries=True)
    harness = Harness([])
    harness.place_resolution, harness.geo_catalog = coordinator, catalog
    try:
        await finish_place_run(harness, session, run, before,
            request={'kind': 'city', 'role': 'destination', 'query': query, 'stay_id': 'new-stay-1'}, operations=[])
        assert len(calls) == 1 and countries.calls == 0 and catalog.admissions == []
        args, result = harness.store.calls[0]
        assert args[2] is None, 'Showing candidates must not update the trip'
        outcome = result['result']['place_resolution']
        assert outcome['action'] == ('unavailable' if ignore_filter else 'ask')
        if not ignore_filter:
            packet = result['pending_question']['context']['place_resolution']
            assert packet['country_ref']['country_iso2'] == code
            assert packet['country_ref']['source_provider'] == 'unicode-cldr'
            assert packet['country_ref']['trip_id'] == before.trip_id
            assert packet['country_ref']['state_version'] == before.state_version
            assert packet['country_ref']['role'] == 'destination'
            assert packet['country_ref']['stay_id'] == 'new-stay-1'
            assert [candidate['country_iso2'] for candidate in packet['candidates']] == [code]
    finally:
        await directory.close()
