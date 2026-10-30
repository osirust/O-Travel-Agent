"""Pure literal-country evidence from pinned CLDR data; no runtime directory calls."""

import hashlib
import json
import re
from pathlib import Path

import pytest

from otravel.country_lexicon import literal_country_mentions

DATA_PATH = Path(__file__).resolve().parents[1] / "otravel" / "data" / "cldr_territories_ru_en.json"
DATA_SHA256 = "74dd233f299cb6f5f3a620dd53f62d1a778aff4bb4799872725b384109eb5fa4"
SOURCE_COMMIT = "7d2b9cb11cec08f4d333ad73209deb3df0711c3b"


def _data():
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def _expected(text, literal, code):
    start = text.index(literal)
    return {"code": code, "name": _data()["countries"][code]["ru"], "span": (start, start + len(literal))}


@pytest.mark.parametrize("text", ["'Едем в США", '"Едем в США', "Беларуся", "Сербиюг"])
def test_unfinished_quotes_and_unverified_word_variants_do_not_authorize_country_evidence(text):
    assert literal_country_mentions(text) == []


@pytest.mark.parametrize("text,literal,code", [
    ("Москва находится в США.", "США", "US"),
    ("Едем в Аргентину.", "Аргентину", "AR"),
    ("На территории Аргентины.", "Аргентины", "AR"),
    ("На территории Сербии.", "Сербии", "RS"),
    ("Едем в Германию.", "Германию", "DE"),
    ("На территории России.", "России", "RU"),
])
def test_explicit_russian_country_labels_and_case_endings_use_cldr_names(text, literal, code):
    assert literal_country_mentions(text) == [_expected(text, literal, code)]


@pytest.mark.parametrize("text,literal,code", [
    ("We travel to United States.", "United States", "US"),
    ("We travel to UK.", "UK", "GB"),
    ("We travel in Germany.", "Germany", "DE"),
])
def test_english_names_and_uk_alias_do_not_turn_in_preposition_into_india(text, literal, code):
    assert literal_country_mentions(text) == [_expected(text, literal, code)]


def test_spans_use_original_unicode_character_offsets_including_emoji_and_spaces():
    text = "🚆  План: едем в Аргентину!"
    result = literal_country_mentions(text)
    assert result == [_expected(text, "Аргентину", "AR")]
    start, end = result[0]["span"]
    assert type(result[0]["span"]) is tuple
    assert text[start:end] == "Аргентину"
    assert start != len(text[:start].encode("utf-8"))


@pytest.mark.parametrize("text,expected", [
    ("Ирландия", [("Ирландия", "IE")]),
    ("Ираназавр", []),
])
def test_country_names_require_full_tokens_without_substring_matches(text, expected):
    assert literal_country_mentions(text) == [_expected(text, literal, code) for literal, code in expected]


def test_longest_multiword_country_suppresses_its_nested_sudan_match():
    text = "We travel to South Sudan."
    assert literal_country_mentions(text) == [_expected(text, "South Sudan", "SS")]


@pytest.mark.parametrize("text", [
    "Направление: «Германия».",
    "Не едем в Сербию.",
    "Раньше ездили в Аргентину.",
    "А если поехать в Германию?",
    "У нас гражданство России.",
    "У меня паспорт Германии.",
], ids=["quotation", "negation", "history", "hypothetical", "nationality", "passport"])
def test_noncurrent_quoted_negated_or_personal_country_is_not_destination_evidence(text):
    assert literal_country_mentions(text) == []


@pytest.mark.parametrize("text", [
    "Летим из России в США.",
    "Гражданство России, едем в США.",
])
def test_explicit_destination_survives_a_separate_origin_or_nationality_clause(text):
    assert literal_country_mentions(text) == [_expected(text, "США", "US")]


def test_reserved_macroregion_and_non_iso_territory_codes_are_not_countries():
    assert literal_country_mentions("Страны EU, UN, ZZ, XK.") == []


@pytest.mark.parametrize("text", ["Georgia", "Едем в Georgia."])
def test_ambiguous_georgia_requires_an_explicit_country_cue(text):
    assert literal_country_mentions(text) == []


def test_georgia_with_country_cue_can_be_the_country_ge():
    text = "Страна Georgia."
    assert literal_country_mentions(text) == [_expected(text, "Georgia", "GE")]


def test_vendored_source_is_pinned_complete_249_country_data_with_unicode_license():
    raw = DATA_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == DATA_SHA256
    data = json.loads(raw)
    assert set(data) == {"version", "source", "countries"} and data["version"] == 1
    assert data["source"]["repository"] == "unicode-org/cldr-json"
    assert data["source"]["commit"] == SOURCE_COMMIT
    countries = data["countries"]
    assert isinstance(countries, dict) and len(countries) == 249
    assert all(re.fullmatch(r"[A-Z]{2}", code) for code in countries)
    assert not {"EU", "UN", "ZZ", "XK"} & countries.keys()
    assert {"US", "GB", "RU", "AR", "DE", "IR", "IE", "SS", "SD", "GE", "IN", "JP"} <= countries.keys()
    for row in countries.values():
        assert set(row) == {"ru", "en", "aliases"}
        assert set(row["aliases"]) == {"ru", "en"}
        for locale in ("ru", "en"):
            assert isinstance(row[locale], str) and row[locale]
            assert isinstance(row["aliases"][locale], list) and row[locale] in row["aliases"][locale]
            assert all(isinstance(alias, str) and alias for alias in row["aliases"][locale])
    for source in data["source"]["source_files"].values():
        assert f"/{SOURCE_COMMIT}/" in source["url"]
        assert re.fullmatch(r"[0-9a-f]{64}", source["sha256"])
    license_bytes = (DATA_PATH.parent / data["source"]["license_file"]).read_bytes()
    assert hashlib.sha256(license_bytes).hexdigest() == data["source"]["source_files"]["license"]["sha256"]
    assert "unicode" in license_bytes.decode("utf-8").casefold()


def test_returned_mentions_are_independent_and_do_not_mutate_the_vendored_source():
    text = "Едем в Аргентину."
    before = DATA_PATH.read_bytes()
    first = literal_country_mentions(text)
    assert first == [_expected(text, "Аргентину", "AR")]
    first[0].update(code="RU", name="injected", span=(0, 1))
    first.append({"code": "US", "name": "injected", "span": (0, 1)})
    assert literal_country_mentions(text) == [_expected(text, "Аргентину", "AR")]
    assert DATA_PATH.read_bytes() == before
@pytest.mark.parametrize('text',[
    'хочу в аргентину наверное', 'В Аргентину хочу', 'Я хочу поехать в Аргентину!',
])
def test_complete_country_wish_returns_only_a_directory_query(text):
    from otravel.country_lexicon import literal_country_destination_request
    request=literal_country_destination_request({'stays':[]},text)
    assert request=={'role':'destination','kind':'country','query':'AR','country_iso2':'AR','stay_id':'new-stay-1'}


@pytest.mark.parametrize('text',[
    'Аргентина?', 'хочу в Аргентину или Чили', 'не хочу в Аргентину',
    'Что есть в Аргентине?', 'из Аргентины хочу', 'хочу в Аргентину, бюджет 100 тысяч',
    'Раньше хотел в Аргентину', 'Если я хочу в Аргентину',
])
def test_country_wish_fast_path_does_not_swallow_other_meanings(text):
    from otravel.country_lexicon import literal_country_destination_request
    assert literal_country_destination_request({'stays':[]},text) is None


def test_country_wish_does_not_replace_a_chosen_or_ambiguous_route():
    from otravel.country_lexicon import literal_country_destination_request
    assert literal_country_destination_request({'stays':[{'id':'s1','city':'Москва'}]},'хочу в Аргентину') is None
    assert literal_country_destination_request({'stays':[{'id':'s1'},{'id':'s2'}]},'хочу в Аргентину') is None
    assert literal_country_destination_request({'stays':[{'id':'s1'}]},'хочу в Аргентину')['stay_id']=='s1'
