"""Frozen CF029/043 shapes, controlled grounding only; not actual NLU scores."""
import asyncio
from copy import deepcopy

import pytest

from otravel.domain import CITIES, DomainError, TripState, apply_operations, canonical_city
from otravel.geography import parse_destination
from otravel.intent_grounding import ground_intent_operations
from otravel.orchestrator import TripSearch

CF029 = "Хочу из Москвы в Georgia. Еду один, я взрослый. Ты какую Georgia понимаешь?"
CF043 = "Хочу из Москвы в Тбилиси, еду один взрослый. Сможешь там искать или город пока недоступен?"


def partial_ops():
    return [{"type": "set_origin", "value": {"city": "Москва"}},
            {"type": "add_traveler", "value": {"id": "new-traveler-1", "type": "adult", "age": None}}]


def test_CF029_current_literal_Georgia_remains_ambiguous_while_origin_and_adult_are_preserved():
    before = TripState()
    operations, meta = ground_intent_operations(before, partial_ops(), CF029)
    after = apply_operations(before, operations)[0]
    assert after.origin == "Москва" and len(after.travelers) == 1 and after.stays == []
    assert meta["pending_context"]["requested_destination"]["kind"] == "ambiguous"
    assert meta["pending_context"]["requested_destination"]["country_code"] is None
    assert "Грузию" in meta["unknowns"][0] and "штат" in meta["unknowns"][0]
    assert not after.budget and not after.legs


def test_CF043_explicit_Tbilisi_is_preserved_and_coverage_limitation_replaces_generic_city_question():
    before = TripState()
    operations, meta = ground_intent_operations(before, partial_ops(), CF043)
    after = apply_operations(before, operations)[0]
    assert after.origin == "Москва" and len(after.travelers) == 1
    assert len(after.stays) == 1 and after.stays[0].city == "Тбилиси"
    assert after.stays[0].check_in is None and after.stays[0].rooms == []
    assert meta["pending_context"]["requested_destination"] == {"kind": "city", "name": "Тбилиси", "country_code": "GE"}
    assert "Тбилиси" in meta["unknowns"][0] and "не покрывают" in meta["unknowns"][0]
    assert "Грузия" in meta["unknowns"][0] and len(CITIES) == 7 and "Тбилиси" not in CITIES


def test_Tbilisi_curated_identity_does_not_extend_search_inventory_or_invent_a_country_capital():
    assert parse_destination("Tbilisi")["requested_destination"] == {"kind": "city", "name": "Тбилиси", "country_code": "GE"}
    assert canonical_city("Тбилиси") == "Тбилиси" and "Тбилиси" not in CITIES


@pytest.mark.parametrize("continuation", ["затем Анталья", "потом Анталья", "далее Анталья", "затем в Анталью"])
def test_positive_multicity_continuation_without_preposition_keeps_both_existing_proposals_and_room_allocation(continuation):
    before = TripState(travelers=[{"id": "adult", "type": "adult"}])
    proposed = [{"type": "set_origin", "value": {"city": "Москва"}},
        {"type": "upsert_stay", "target_id": "first", "value": {"city": "Стамбул", "rooms": [{"id": "r1", "traveler_ids": ["adult"]}]}},
        {"type": "upsert_stay", "target_id": "second", "value": {"city": "Анталья", "rooms": [{"id": "r2", "traveler_ids": ["adult"]}]}}]
    original = deepcopy(proposed)
    operations, meta = ground_intent_operations(before, proposed, "Из Москвы в Стамбул, " + continuation + ". Один взрослый.")
    after = apply_operations(before, operations)[0]
    assert after.origin == "Москва" and [(s.id, s.city) for s in after.stays] == [("first", "Стамбул"), ("second", "Анталья")]
    assert after.stays[1].rooms[0].id == "r2" and after.stays[1].rooms[0].traveler_ids == ["adult"]
    assert not any(n["code"] == "unanchored_destination_suppressed" for n in meta["notes"])
    assert proposed == original


@pytest.mark.parametrize("text", [
    "Если из Москвы в Стамбул, затем Анталья?", "Раньше из Москвы в Стамбул, затем Анталья.",
    "Не едем из Москвы в Стамбул, затем Анталья.", "Реклама «Из Москвы в Стамбул, затем Анталья».",
    "Почему из Москвы в Стамбул, затем Анталья?",
])
def test_bare_multicity_continuation_does_not_promote_noncurrent_route_to_changes(text):
    before = TripState()
    operations, _ = ground_intent_operations(before, [
        {"type": "upsert_stay", "target_id": "first", "value": {"city": "Стамбул"}},
        {"type": "upsert_stay", "target_id": "second", "value": {"city": "Анталья"}}], text)
    assert apply_operations(before, operations)[0].stays == []


@pytest.mark.parametrize("tail", ["затем Москва, отель в Москве не нужен.", "потом в Москву, отель в Москве не нужен."])
def test_no_hotel_terminal_city_is_not_converted_into_another_hotel_stay(tail):
    before = TripState()
    operations, _ = ground_intent_operations(before, [
        {"type": "upsert_stay", "target_id": "first", "value": {"city": "Стамбул"}},
        {"type": "upsert_stay", "target_id": "end", "value": {"city": "Москва"}}], "Из Казани в Стамбул, " + tail)
    assert [s.city for s in apply_operations(before, operations)[0].stays] == ["Стамбул"]


def test_declared_openjaw_terminal_is_not_first_guessed_as_a_stay_from_continuation():
    before = TripState(route_policy={"kind": "open_jaw", "end_city": "Москва"})
    operations, _ = ground_intent_operations(before, [
        {"type": "upsert_stay", "target_id": "first", "value": {"city": "Стамбул"}},
        {"type": "upsert_stay", "target_id": "end", "value": {"city": "Москва"}}], "Из Казани в Стамбул, затем Москва.")
    assert [s.city for s in apply_operations(before, operations)[0].stays] == ["Стамбул"]


def test_ambiguous_Georgia_cannot_be_resolved_by_a_model_invented_Tbilisi_stay():
    operations, meta = ground_intent_operations(TripState(), [*partial_ops(),
        {"type": "upsert_stay", "target_id": "invented-capital", "value": {"city": "Тбилиси"}}], CF029)
    after = apply_operations(TripState(), operations)[0]
    assert after.origin == "Москва" and len(after.travelers) == 1 and after.stays == []
    assert meta["pending_context"]["requested_destination"]["country_code"] is None


@pytest.mark.parametrize("text", ["Georgia", "Джорджия", "Выбираю Georgia", "В Georgia"])
def test_owned_destination_slot_keeps_ambiguous_country_or_region_unresolved(text):
    before = TripState()
    operations, meta = ground_intent_operations(before,
        [{"type": "upsert_stay", "target_id": "capital", "value": {"city": "Тбилиси"}}], text,
        {"field": "destination.city", "state_version": before.state_version,
         "context": {"proposed_destination": {"kind": "city", "name": "Стамбул", "country_code": "TR"},
         "confirmation_required": True}})
    assert apply_operations(before, operations)[0].stays == []
    assert meta["pending_context"]["requested_destination"]["kind"] == "ambiguous"
    assert "confirmation_required" in meta["pending_context_clear"]


def test_explicit_country_qualifier_disambiguates_Georgia_but_never_chooses_Tbilisi():
    before = TripState()
    operations, meta = ground_intent_operations(before,
        [{"type": "upsert_stay", "target_id": "capital", "value": {"city": "Тбилиси"}}], "Хочу в страну Georgia")
    assert apply_operations(before, operations)[0].stays == []
    place = meta["pending_context"]["requested_destination"]
    assert place["kind"] == "country" and place["country_code"] == "GE"
    assert place["suggested_cities"] == [{"name": "Тбилиси", "country_code": "GE", "available": False}]


@pytest.mark.parametrize("text", [
    "А если из Москвы в Тбилиси, что изменится?", "Раньше из Москвы в Тбилиси летал.",
    "Реклама «Из Москвы в Тбилиси, еду один взрослый».", "Не едем из Москвы в Тбилиси.",
    "Ты Тбилиси знаешь?", "Что ты знаешь о Georgia?",
])
def test_pure_questions_hypotheses_history_quotes_and_negations_do_not_become_geo_commands(text):
    before = TripState()
    operations, meta = ground_intent_operations(before,
        [{"type": "upsert_stay", "target_id": "invented", "value": {"city": "Тбилиси"}}], text)
    assert apply_operations(before, operations)[0].stays == []
    assert not meta["unknowns"] and meta["pending_context"] is None


def test_known_Tbilisi_is_not_a_coverage_question_when_only_the_model_mentions_it():
    before = TripState()
    operations, meta = ground_intent_operations(before,
        [{"type": "upsert_stay", "target_id": "invented", "value": {"city": "Тбилиси"}}], "Бюджет и состав прежние.")
    assert apply_operations(before, operations)[0].stays == [] and meta["unknowns"] == []


def test_previously_unknown_Tbilisi_conditional_case_keeps_exact_text_and_trip_but_now_uses_curated_GE_identity():
    # Same input formerly lived in unknown-city tests. Only geography identity
    # changed after CF043; conditional unsupported addition still cannot commit.
    before = TripState(origin="Москва")
    operations, meta = ground_intent_operations(before,
        [{"type": "upsert_stay", "target_id": "extra", "value": {"city": "Тбилиси"}}],
        "Включи направление Тбилиси, если оно поддерживается")
    assert apply_operations(before, operations)[0] == before
    assert meta["pending_context"]["requested_destination"] == {"kind": "city", "name": "Тбилиси", "country_code": "GE"}
    assert "не покрывают" in meta["unknowns"][0]


def test_curated_city_addition_is_literal_not_global_fuzzy_geocoding():
    assert parse_destination("Тбилисси")["requested_destination"]["kind"] == "unknown"
    with pytest.raises(DomainError):
        canonical_city("Тбилисси")
    assert len(CITIES) == 7


def test_complete_Tbilisi_request_is_blocked_before_any_gateway_method_because_coverage_did_not_grow():
    class ForbiddenGateway:
        def __getattr__(self, name):
            pytest.fail("No gateway method should be resolved for unsupported geography")
    trip = TripState(origin="Москва", travelers=[{"id": "adult", "type": "adult"}],
        stays=[{"id": "tbs", "city": "Тбилиси", "check_in": "2026-11-05", "check_out": "2026-11-09",
                "rooms": [{"id": "one", "traveler_ids": ["adult"]}]}],
        legs=[{"id": "out", "origin": "Москва", "destination": "Тбилиси", "departure_date": "2026-11-05"},
              {"id": "back", "origin": "Тбилиси", "destination": "Москва", "departure_date": "2026-11-09"}],
        budget={"amount_minor": 8_000_000}, search_policy={"mode": "HYBRID", "hotel_provider": "liteapi", "guest_nationality": "RU"})
    response = asyncio.run(TripSearch(ForbiddenGateway()).search(trip))
    assert response["plans"] == [] and response["tool_calls_used"] == 0
    assert any(v["code"] == "unsupported_provider_geography" for v in response["violations"])
