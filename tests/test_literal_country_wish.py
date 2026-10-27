"""Pure literal country intent; actual source FAIL remains a separate record."""
from copy import deepcopy

import pytest

from otravel.country_intake import literal_country_wish
from otravel.domain import TripState, sample_trip


@pytest.mark.parametrize(('text', 'code'), [
    ('хочу в Сербию', 'RS'), ('ХОЧУ В СЕРБИЮ!', 'RS'), ('в Сербию хочу', 'RS'),
    ('я хочу поехать в Сербию', 'RS'), ('Мы хотим полететь в Турцию.', 'TR'),
    ('еду в ОАЭ', 'AE'), ('летим в Россию', 'RU'), ('поедем в Казахстан', 'KZ'),
    ('хочу в Российскую Федерацию', 'RU'), ('  хочу\tв\nСербию...  ', 'RS'),
    ('I want to go to Serbia', 'RS'), ('we would like to travel to Turkey!', 'TR'),
    ('go to UAE', 'AE'), ('travel to Russia', 'RU'), ('I want to visit Japan.', 'JP'),
    ('поехать в Сербию', 'RS'), ('полететь в Турцию', 'TR'),
])
def test_complete_fresh_explicit_country_wish_is_an_owned_city_question(text, code):
    state = TripState().model_dump(mode='json')
    before = deepcopy(state)
    answer = literal_country_wish(state, text)
    assert answer['requested_destination']['kind'] == 'country'
    assert answer['requested_destination']['country_code'] == code
    assert 'город' in answer['question'].casefold() and '?' in answer['question']
    assert set(answer) == {'requested_destination', 'question'}
    assert state == before


@pytest.mark.parametrize('text', [
    'Сербия', 'в Сербию', 'Сербия страна?', 'хочу в Сербию?', 'Хочу в Сербию, можно?',
    'Если захочу в Сербию', 'Если хочу в Сербию, что нужно?', 'я бы хотел в Сербию',
    'Не хочу в Сербию', 'не еду в Сербию', 'хочу не в Сербию', 'хочу в Сербию или Турцию',
    'хочу в Сербию. Отправление из Москвы.', 'хочу в Сербию из Москвы',
    'хочу в Сербию, один взрослый', 'хочу в Сербию, бюджет 80000 рублей',
    'хочу в Сербию, детей нет', 'хочу в Сербию, даты ещё не выбрал',
    'Хочу в Сербию. Подожди.', 'подожди', 'да', 'нет', 'продолжай', 'отмена',
    'хочу в Стамбул', 'хочу в Москву', 'хочу в Белград', 'Стамбул в Сербии',
    'хочу в неизвестную страну', 'хочу в Georgia', 'хочу в Америку',
    '«хочу в Сербию»', 'Раньше хочу в Сербию', 'Напомни: хочу в Сербию',
    'I want to go to Serbia if possible', 'Do I want to go to Serbia?',
    'go to Serbia and add a child', '', ' ', 'хочу в Сербию' + '!' * 300,
])
def test_other_speech_acts_compounds_city_requests_and_controls_are_not_swallowed(text):
    assert literal_country_wish(TripState(), text) is None


@pytest.mark.parametrize('field', ['origin', 'budget', 'travelers', 'groups', 'stays', 'legs', 'constraints', 'preferences'])
def test_any_saved_trip_fact_keeps_the_normal_path(field):
    state = TripState().model_dump(mode='json')
    reference = sample_trip().model_dump(mode='json')
    reference['preferences'] = [{'key': 'price_priority', 'value': 'lowest_total'}]
    state[field] = deepcopy(reference[field])
    assert state[field]
    before = deepcopy(state)
    assert literal_country_wish(state, 'хочу в Сербию') is None
    assert state == before


@pytest.mark.parametrize('text', [None, True, 1, [], {}, b'go to Serbia'])
def test_non_text_input_does_not_get_coerced(text):
    assert literal_country_wish(TripState(), text) is None


def test_results_are_independent_and_do_not_propose_a_city_or_operations():
    first = literal_country_wish(TripState(), 'хочу в Сербию')
    first['requested_destination']['name'] = 'caller-only'
    second = literal_country_wish(TripState(), 'в Сербию хочу')
    assert second['requested_destination']['name'] == 'Сербия'
    assert 'operations' not in second and 'target_id' not in second
