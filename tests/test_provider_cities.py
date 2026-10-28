"""Provider request capability is independent of DEMO7 and live availability."""
from copy import deepcopy

import pytest

from otravel.domain import CITIES, SearchPolicy, TripState
from otravel.provider_cities import available_search_cities, flight_iata, liteapi_city_query


def policy(mode='HYBRID', hotel='liteapi', flight='duffel'):
    return {'search_policy': {'mode': mode, 'hotel_provider': hotel, 'flight_provider': flight}}


def test_confirmed_hybrid_pair_has_belgrade_without_widening_demo_inventory():
    before = tuple(CITIES)
    assert len(before) == 7 and 'Белград' not in before
    source = policy()
    copy = deepcopy(source)
    assert available_search_cities(source) == (*before, 'Белград')
    assert source == copy and tuple(CITIES) == before
    typed = TripState(search_policy=SearchPolicy(mode='HYBRID', hotel_provider='liteapi', flight_provider='duffel'))
    assert available_search_cities(typed) == (*before, 'Белград')


@pytest.mark.parametrize('state', [None, {}, {'search_policy': None}, policy('DEMO'),
    policy(hotel='serpapi'), policy(flight='serpapi'), policy('LIVE'), policy('unknown'),
    {'search_policy': {'mode': 'HYBRID'}}])
def test_unqualified_pair_or_mode_keeps_exact_seven(state):
    assert available_search_cities(state) == CITIES


def test_capability_does_not_depend_on_missing_keys_nationality_or_fallback():
    source = policy()
    source['search_policy'].update(guest_nationality=None, demo_fallback=False)
    assert 'Белград' in available_search_cities(source)


def test_all_previous_provider_labels_and_codes_stay_unchanged():
    expected = {'Москва': ('RU', 'Moscow', 'MOW'), 'Санкт-Петербург': ('RU', 'St. Petersburg', 'LED'),
        'Сочи': ('RU', 'Sochi', 'AER'), 'Казань': ('RU', 'Kazan', 'KZN'),
        'Стамбул': ('TR', 'Istanbul', 'IST'), 'Анталья': ('TR', 'Antalya', 'AYT'), 'Дубай': ('AE', 'Dubai', 'DXB')}
    for city, (country, label, iata) in expected.items():
        assert liteapi_city_query(city) == (country, label)
        assert flight_iata(city) == iata
    assert liteapi_city_query('Белград') == ('RS', 'Belgrade')
    assert flight_iata('Белград') == 'BEG'


@pytest.mark.parametrize('city', ['Белгород', 'Нови-Сад', 'Тбилиси', 'unknown', None, []])
def test_unknown_or_unqualified_city_is_not_guessed_from_country_or_prefix(city):
    assert liteapi_city_query(city) is None and flight_iata(city) is None
