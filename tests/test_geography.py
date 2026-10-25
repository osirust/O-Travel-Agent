"""Pure geography regressions: no inventory, DB, HTTP or model inference."""

import pytest

from otravel.domain import CITIES, DomainError, canonical_city
from otravel.geography import normalize_destination, parse_destination


@pytest.mark.parametrize("phrase", ["Сербия", "Сербию", "Сербии", "SERBIA", "Serbia", "RS", "в сербию хочу", "Хочу поехать в Сербию.", "I want to go to Serbia"])
def test_serbia_is_country_and_asks_for_city(phrase):
    result = parse_destination(phrase)
    geo = result["requested_destination"]
    assert (geo["kind"], geo["name"], geo["country_code"]) == ("country", "Сербия", "RS")
    assert "город" in result["clarification"]
    assert geo["suggested_cities"] == [
        {"name": "Белград", "country_code": "RS", "available": False},
        {"name": "Нови-Сад", "country_code": "RS", "available": False},
    ]


@pytest.mark.parametrize("phrase,code", [("России", "RU"), ("РФ", "RU"), ("Russia", "RU"), ("Russian Federation", "RU"), ("Турцию", "TR"), ("Türkiye", "TR"), ("Turkey", "TR"), ("ОАЭ", "AE"), ("UAE", "AE"), ("United Arab Emirates", "AE")])
def test_common_country_aliases_require_city(phrase, code):
    result = parse_destination(phrase)
    assert result["requested_destination"]["kind"] == "country"
    assert result["requested_destination"]["country_code"] == code
    assert result["clarification"]
    assert all(city["name"] in CITIES and city["available"] for city in result["requested_destination"]["suggested_cities"])


@pytest.mark.parametrize("phrase,code", [("в Грузию хочу", "GE"), ("Belarus", "BY"), ("в Таиланд", "TH"), ("Египте", "EG"), ("China", "CN"), ("в Японию", "JP"), ("France", "FR"), ("Германии", "DE"), ("в Черногорию", "ME"), ("in Sri Lanka", "LK"), ("USA", "US"), ("United Kingdom", "GB")])
def test_other_curated_countries_never_invent_covered_city(phrase, code):
    result = parse_destination(phrase)
    assert result["requested_destination"] == {"kind": "country", "name": result["requested_destination"]["name"], "country_code": code}
    assert result["clarification"]


@pytest.mark.parametrize("phrase", ["СЕРБИЮ", "  в   Сербию  хочу! ", "«Сербия»", "to SERBIA", "I would like to travel to Serbia", "in Serbia"])
def test_country_normalization_case_spaces_and_request_wrappers(phrase):
    assert parse_destination(phrase)["requested_destination"]["country_code"] == "RS"


@pytest.mark.parametrize("phrase,city,code", [("Belgrade", "Белград", "RS"), ("Нови Сад", "Нови-Сад", "RS"), ("Novi Sad", "Нови-Сад", "RS"), ("в москву хочу", "Москва", "RU"), ("Istanbul", "Стамбул", "TR"), ("Dubai", "Дубай", "AE")])
def test_city_identity_independent_of_search_coverage(phrase, city, code):
    result = parse_destination(phrase)
    assert result == {"requested_destination": {"kind": "city", "name": city, "country_code": code}, "clarification": None}


@pytest.mark.parametrize("phrase", ["Georgia", "Джорджия", "Congo", "Корея", "America"])
def test_ambiguous_terms_do_not_guess_country_or_city(phrase):
    result = parse_destination(phrase)
    assert result["requested_destination"]["kind"] == "ambiguous"
    assert result["requested_destination"]["country_code"] is None
    assert result["clarification"]


@pytest.mark.parametrize("phrase", ["", "Атлантида", "не в Сербию", "not Serbia", "Сербия или Турция", "сербский ресторан в Москве", "Serbia and Dubai", "в любой город"])
def test_unknown_negation_and_compound_do_not_match_substrings(phrase):
    result = parse_destination(phrase)
    assert result["requested_destination"]["kind"] == "unknown"
    assert result["requested_destination"]["country_code"] is None
    assert result["clarification"]


def test_suggestions_follow_explicit_supported_city_list():
    geo = parse_destination("Турция", available_cities=["Istanbul"])["requested_destination"]
    assert geo["suggested_cities"] == [
        {"name": "Стамбул", "country_code": "TR", "available": True},
        {"name": "Анталья", "country_code": "TR", "available": False},
    ]


def test_geography_does_not_expand_domain_coverage():
    assert len(CITIES) == 7
    parse_destination("Белград")
    parse_destination("Сербия")
    for value in ("Сербия", "Белград", "Нови-Сад"):
        with pytest.raises(DomainError) as error:
            canonical_city(value)
        assert error.value.code == "unsupported_city"


def test_alias_and_bad_type():
    assert normalize_destination is parse_destination
    with pytest.raises(TypeError):
        parse_destination(None)
