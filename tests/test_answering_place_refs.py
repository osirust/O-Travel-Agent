"""Pure answers from declared canonical CityRef fixtures; no live source proof."""

import hashlib
import json
import re
from copy import deepcopy

import pytest

from otravel.answering import READONLY_SCHEMA, canonical_geography_answer, facts_projection
from otravel.domain import Operation, TripState, apply_operations
from otravel.place_refs import CityRefSnapshot

# Captured from answering-before-place-refs.py, SHA256 969f05c081365b6f0c3724440589bfa1804a0b5c695dadc0e6eecb5e74724e39.
LEGACY_FACTS_SHA = "8ac525814247aba72febbb7ee12a60fe8a54ff30121db60cb40d7afbb4b0e1f8"
READONLY_SCHEMA_SHA = "73ebe6e8aa6683d4dfba88fe749f0dc857fd968db998aa2bcc24954177f007b4"


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _place(identifier, name, country, country_name, region, timezone, latitude, longitude):
    return CityRefSnapshot.model_validate({
        "place_id": f"geo:openmeteo:{identifier}",
        "ref_version": 1,
        "canonical_name": name,
        "country_iso2": country,
        "country_name": country_name,
        "geographic_source": {"provider": "openmeteo", "external_id": identifier},
        "latitude": latitude,
        "longitude": longitude,
        "timezone": timezone,
        "feature_code": "PPL",
        "administrative_areas": [{"level": 1, "external_id": identifier + 100, "name": region}],
        "source_fact_sha256": hashlib.sha256(f"declared-fixture-{identifier}".encode()).hexdigest(),
    })


MOSCOW_US = _place(5601538, "Москва", "US", "США", "Айдахо", "America/Los_Angeles", 46.73, -117.0)
CORDOBA_AR = _place(3860259, "Кордова", "AR", "Аргентина", "Провинция Кордова",
                    "America/Argentina/Cordoba", -31.42, -64.19)
CORDOBA_ES = _place(2519240, "Кордова", "ES", "Испания", "Андалусия", "Europe/Madrid", 37.89, -4.78)
KYOTO_JP = _place(1857910, "Киото", "JP", "Япония", "Префектура Киото", "Asia/Tokyo", 35.01, 135.77)


def _trip(*, origin=None, stays=(), end=None):
    refs = {ref.place_id: ref for ref in (origin, *stays, end) if ref is not None}
    operations = []
    if origin is not None:
        operations.append(Operation(type="set_origin", value={"city": origin.canonical_name, "place_id": origin.place_id}))
    for index, ref in enumerate(stays, 1):
        operations.append(Operation(type="upsert_stay", target_id=f"stop-{index}",
            value={"city": ref.canonical_name, "place_id": ref.place_id}))
    if end is not None:
        operations.append(Operation(type="set_route_policy", value={"kind": "open_jaw",
            "end_city": end.canonical_name, "end_place_id": end.place_id}))
    current, _ = apply_operations(TripState(trip_id="answering-declared-places"), operations, resolved_places=refs)
    payload = current.model_dump(mode="json")
    assert TripState.model_validate(payload).model_dump(mode="json") == payload
    return payload


def _facts(state, question):
    return facts_projection(state, [], [], [], [], question=question)


def _assert_place_text(text, ref):
    assert isinstance(text, str) and text
    assert ref.canonical_name in text
    assert (ref.country_name or ref.country_iso2) in text
    assert ref.administrative_areas[0].name in text
    assert ref.timezone in text


@pytest.mark.parametrize("question", ["В какой стране Москва?", "Москва в России?"])
def test_current_moscow_us_ref_overrides_the_russian_seed_country(question):
    state = _trip(stays=(MOSCOW_US,))
    before = deepcopy(state)
    reply = canonical_geography_answer(question, state)
    _assert_place_text(reply, MOSCOW_US)
    assert "Москва — Россия" not in reply
    assert "Москва — Россия" not in _facts(state, question)["sections"]["geography"]
    assert state == before


def test_moscow_us_geography_section_is_grounded_in_the_current_snapshot():
    state = _trip(stays=(MOSCOW_US,))
    _assert_place_text(_facts(state, "В какой стране Москва?")["sections"]["geography"], MOSCOW_US)


def test_english_owned_name_uses_legacy_name_alias_without_copying_its_seed_country():
    ref = MOSCOW_US.model_copy(update={"canonical_name": "Moscow", "country_name": "United States"})
    state = _trip(stays=(ref,))
    _assert_place_text(canonical_geography_answer("В какой стране Москва?", state), ref)
    assert "Россия" not in canonical_geography_answer("Москва в России?", state)


def test_default_saved_geography_projection_uses_bound_ref_without_a_geography_question():
    state = _trip(stays=(MOSCOW_US,))
    for question in ("", "Сколько нас едет?"):
        _assert_place_text(_facts(state, question)["sections"]["geography"], MOSCOW_US)


def test_country_iso_disambiguates_same_named_owned_stays():
    state = _trip(stays=(CORDOBA_AR, CORDOBA_ES))
    reply = canonical_geography_answer("В каком регионе Кордова в AR?", state)
    _assert_place_text(reply, CORDOBA_AR)
    assert "Испания" not in reply and "Europe/Madrid" not in reply


@pytest.mark.parametrize("question", ["В какой стране Кордова?", "В каком регионе Кордова?"])
def test_same_name_refs_ask_which_stop_instead_of_choosing_a_country(question):
    state = _trip(stays=(CORDOBA_AR, CORDOBA_ES))
    before = deepcopy(state)
    reply = canonical_geography_answer(question, state)
    assert isinstance(reply, str) and "?" in reply
    assert re.search(r"останов|проживан", reply.casefold())
    assert re.search(r"какую|какой|какая|уточни|укажи|выбер", reply.casefold())
    assert state == before


@pytest.mark.parametrize("question", [
    "В какой стране Кордова во второй остановке?",
    "В каком регионе вторая остановка?",
    "Какой часовой пояс во второй остановке?",
])
def test_second_stop_uses_spain_ref_without_merging_the_argentinian_homonym(question):
    state = _trip(stays=(CORDOBA_AR, CORDOBA_ES))
    reply = canonical_geography_answer(question, state)
    _assert_place_text(reply, CORDOBA_ES)
    assert "Аргентина" not in reply
    assert "America/Argentina/Cordoba" not in reply
    _assert_place_text(_facts(state, question)["sections"]["geography"], CORDOBA_ES)


@pytest.mark.parametrize("question,ref", [
    ("В какой стране наше отправление?", MOSCOW_US),
    ("Какой часовой пояс нашего отправления?", MOSCOW_US),
    ("В какой стране наше возвращение?", KYOTO_JP),
])
def test_origin_and_route_end_are_resolved_by_their_own_bound_snapshot(question, ref):
    state = _trip(origin=MOSCOW_US, stays=(CORDOBA_AR, CORDOBA_ES), end=KYOTO_JP)
    before = deepcopy(state)
    _assert_place_text(canonical_geography_answer(question, state), ref)
    _assert_place_text(_facts(state, question)["sections"]["geography"], ref)
    assert state == before


def test_route_facts_keep_country_admin_timezone_and_distinct_same_name_ids():
    refs = (MOSCOW_US, CORDOBA_AR, CORDOBA_ES, KYOTO_JP)
    state = _trip(origin=MOSCOW_US, stays=(CORDOBA_AR, CORDOBA_ES), end=KYOTO_JP)
    before = deepcopy(state)
    facts = _facts(state, "В каких странах наш текущий маршрут?")
    assert isinstance(facts["route_geography"], list)
    projection = json.dumps(facts["route_geography"], ensure_ascii=False)
    for ref in refs:
        assert ref.place_id in projection
        assert ref.country_iso2 in projection and ref.country_name in projection
        assert ref.administrative_areas[0].name in projection and ref.timezone in projection
        assert ref.canonical_name in facts["route"] and ref.country_name in facts["route"]
        assert ref.country_name in facts["sections"]["route"]
    assert state == before


def test_country_name_missing_uses_iso_instead_of_guessing_from_the_city_name():
    ref = MOSCOW_US.model_copy(update={"country_name": None})
    ref = CityRefSnapshot.model_validate(ref.model_dump(mode="json"))
    state = _trip(stays=(ref,))
    reply = canonical_geography_answer("В какой стране Москва?", state)
    assert reply and "US" in reply and "Россия" not in reply


@pytest.mark.parametrize("question", ["В какой стране Стамбул?", "Стамбул в Турции?"])
def test_unrelated_literal_known_city_keeps_the_legacy_faq_path(question):
    state = _trip(stays=(MOSCOW_US,))
    reply = canonical_geography_answer(question, state)
    assert reply and "Стамбул" in reply and "Турция" in reply
    assert "США" not in reply and "Айдахо" not in reply
    assert "Турция" in _facts(state, question)["sections"]["geography"]


def test_unbound_stored_snapshot_does_not_override_a_legacy_current_city():
    state = TripState.model_validate({"trip_id": "unbound-ref-answer", "stays": [{"id": "legacy", "city": "Москва"}],
        "location_refs": {MOSCOW_US.place_id: MOSCOW_US.model_dump(mode="json")}}).model_dump(mode="json")
    reply = canonical_geography_answer("В какой стране Москва?", state)
    assert reply and "Россия" in reply and "США" not in reply


@pytest.mark.parametrize("explicit_empty_refs", [False, True])
def test_empty_refs_keep_exact_legacy_facts_bytes_and_readonly_schema(explicit_empty_refs):
    state = TripState.model_validate({"trip_id": "legacy-answering-places", "origin": "Казань",
        "stays": [{"id": "legacy-stay", "city": "Стамбул", "check_in": "2026-11-10", "check_out": "2026-11-13"}]}).model_dump(mode="json")
    if explicit_empty_refs:
        state["location_refs"] = {}
    before = deepcopy(state)
    facts = _facts(state, "В какой стране Стамбул?")
    assert hashlib.sha256(_encoded(facts)).hexdigest() == LEGACY_FACTS_SHA
    assert "route_geography" not in facts
    assert hashlib.sha256(_encoded(READONLY_SCHEMA)).hexdigest() == READONLY_SCHEMA_SHA
    assert state == before


@pytest.mark.parametrize("question", [
    "В какой стране Москва? Измени бюджет на 50000.",
    "В какой стране Кордова? Найди отель.",
    "В какой стране Москва? Добавь ребёнка.",
    "В какой стране Москва? Забронируй гостиницу.",
])
def test_mixed_edit_or_supplier_request_does_not_become_a_geography_faq(question):
    state = _trip(origin=MOSCOW_US, stays=(CORDOBA_AR, CORDOBA_ES))
    before = deepcopy(state)
    assert canonical_geography_answer(question, state) is None
    assert state == before


def test_geography_answers_add_no_price_visa_hotel_availability_or_state_mutations():
    state = _trip(origin=MOSCOW_US, stays=(CORDOBA_AR, CORDOBA_ES), end=KYOTO_JP)
    before, schema_before = deepcopy(state), deepcopy(READONLY_SCHEMA)
    for question in ("В какой стране наше отправление?", "В какой стране Кордова во второй остановке?"):
        reply = canonical_geography_answer(question, state)
        facts = _facts(state, question)
        assert isinstance(reply, str)
        for text in (reply, facts["sections"]["geography"]):
            assert not re.search(r"\b(?:RUB|USD|EUR)\b|[₽$€]", text)
            assert not re.search(r"безвиз|виза не (?:нужна|требуется)|бронь подтверждена|наличие подтверждено|"
                r"отел\w*\s+(?:доступ|свобод)|номера\s+(?:есть|доступ)|цена\s*[:—]?\s*\d", text.casefold())
    assert state == before and READONLY_SCHEMA == schema_before
