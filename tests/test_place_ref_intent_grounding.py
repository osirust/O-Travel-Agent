"""Declared existing-place identities must survive the legacy NLU guard.

Pure domain assertions only; fixtures do not prove live directory provenance.
"""
from copy import deepcopy

import pytest

from otravel.domain import Operation, TripState, apply_operations
from otravel.intent_grounding import ground_intent_operations
from otravel.place_refs import CityRefSnapshot


def _place(identifier, name, country):
    return CityRefSnapshot.model_validate({
        "place_id": f"geo:openmeteo:{identifier}", "ref_version": 1,
        "canonical_name": name, "country_iso2": country,
        "geographic_source": {"provider": "openmeteo", "external_id": identifier},
        "latitude": 1.0, "longitude": 2.0, "feature_code": "PPL", "source_fact_sha256": "a" * 64,
    })


MOSCOW_US = _place(5601538, "Москва", "US")
KYOTO = _place(1857910, "Киото", "JP")


def _state(*, origin_ref=MOSCOW_US, stay_ref=KYOTO):
    return apply_operations(TripState(trip_id="declared-ref-intent"), [
        Operation(type="set_origin", value={"city": origin_ref.canonical_name, "place_id": origin_ref.place_id}),
        Operation(type="upsert_stay", target_id="stop-1", value={"city": stay_ref.canonical_name,
            "place_id": stay_ref.place_id, "check_in": "2026-11-01", "check_out": "2026-11-08"}),
    ], resolved_places={origin_ref.place_id: origin_ref, stay_ref.place_id: stay_ref})[0]


def _ground(before, proposed, text):
    snapshot = before.model_dump(mode="json")
    original = deepcopy(proposed)
    grounded, meta = ground_intent_operations(before, proposed, text)
    after, changes = apply_operations(before, [Operation.model_validate(op) for op in grounded])
    assert before.model_dump(mode="json") == snapshot and proposed == original
    return after, grounded, meta, changes


@pytest.mark.parametrize("name", ["Москва", "Moscow", "Москвы"])
@pytest.mark.parametrize("supply_id", [False, True])
def test_existing_origin_echo_preserves_selected_us_identity(name, supply_id):
    before = _state()
    value = {"city": name}
    if supply_id:
        value["place_id"] = MOSCOW_US.place_id
    after, _ops, _meta, changes = _ground(before, [{"type": "set_origin", "value": value}],
        "Вылетаю из Москвы; остальное оставь как есть.")
    assert after.origin == MOSCOW_US.canonical_name and after.origin_place_id == MOSCOW_US.place_id
    assert after.location_refs[after.origin_place_id].country_iso2 == "US"
    assert not changes


@pytest.mark.parametrize("supply_id", [False, True])
def test_echo_of_existing_world_city_keeps_ref_while_applying_dates(supply_id):
    before = _state()
    value = {"city": "Киото", "check_in": "2026-11-02"}
    if supply_id:
        value["place_id"] = KYOTO.place_id
    after, _ops, _meta, changes = _ground(before, [{"type": "upsert_stay", "target_id": "stop-1", "value": value}],
        "В Киото заезд 2 ноября; выезд прежний.")
    assert after.stays[0].city == "Киото" and after.stays[0].place_id == KYOTO.place_id
    assert after.stays[0].check_in.isoformat() == "2026-11-02" and changes
    assert after.stays[0].check_out == before.stays[0].check_out


def test_generated_same_name_city_operation_preserves_current_ref():
    before = _state(stay_ref=MOSCOW_US, origin_ref=KYOTO)
    after, _ops, _meta, _changes = _ground(before, [{"type": "set_budget", "value": {"amount_minor": 9000000}}],
        "В Москву хочу, общий бюджет 90 тысяч рублей.")
    assert after.stays[0].city == "Москва" and after.stays[0].place_id == MOSCOW_US.place_id
    assert after.origin_place_id == KYOTO.place_id


@pytest.mark.parametrize("proposed_id", ["geo:openmeteo:123456", MOSCOW_US.place_id, None])
def test_unowned_identity_change_is_suppressed_as_pair_but_retains_independent_dates(proposed_id):
    before = _state()
    after, ops, meta, _changes = _ground(before, [{"type": "upsert_stay", "target_id": "stop-1", "value": {
        "city": "Москва", "place_id": proposed_id, "check_in": "2026-11-02"}}],
        "Перенеси заезд на 2 ноября, город оставь прежним.")
    assert after.stays[0].city == "Киото" and after.stays[0].place_id == KYOTO.place_id
    assert after.stays[0].check_in.isoformat() == "2026-11-02"
    assert all("place_id" not in op.get("value", {}) or "city" in op["value"] for op in ops)
    assert meta["unknowns"]


def test_model_cannot_assign_another_owned_role_ref_as_origin():
    before = _state()
    after, _ops, meta, _changes = _ground(before, [{"type": "set_origin", "value": {
        "city": "Киото", "place_id": KYOTO.place_id}}], "Уточним детали поездки.")
    assert after == before and meta["unknowns"]


def test_same_named_us_origin_with_russian_country_qualifier_needs_owned_clarification():
    before = _state()
    after, _ops, meta, changes = _ground(before, [{"type": "set_origin", "value": {
        "city": "Москва", "place_id": MOSCOW_US.place_id}}], "Вылетаю из Москвы в России.")
    assert after == before and not changes
    assert meta["pending_context"]["field"] == "origin.city"
    assert meta["unknowns"] and "US" in meta["unknowns"][0] and "RU" in meta["unknowns"][0]


def test_following_serbian_destination_is_not_attached_to_us_origin():
    before = _state()
    after, _ops, meta, _changes = _ground(before, [{"type": "set_origin", "value": {
        "city": "Москва", "place_id": MOSCOW_US.place_id}}], "Вылетаю из Москвы в Сербию.")
    assert after.origin_place_id == MOSCOW_US.place_id
    assert not any(note["code"] == "selected_place_country_qualifier_conflict" for note in meta["notes"])


def test_explicit_echo_of_selected_world_city_does_not_claim_it_is_unrecognised():
    before = _state()
    after, _ops, meta, _changes = _ground(before, [{"type": "upsert_stay", "target_id": "stop-1", "value": {
        "city": "Киото", "place_id": KYOTO.place_id}}], "Выбираю Киото")
    assert after == before and not meta["unknowns"]


def test_current_us_destination_does_not_export_a_russian_legacy_country_context():
    before = _state(stay_ref=MOSCOW_US, origin_ref=KYOTO)
    after, _ops, meta, _changes = _ground(before, [], "Хочу в Москву")
    assert after == before and not meta["unknowns"]
    assert not (meta["pending_context"] or {}).get("requested_destination")


def test_explicit_russian_qualifier_cannot_be_satisfied_by_same_named_us_destination():
    before = _state(stay_ref=MOSCOW_US, origin_ref=KYOTO)
    after, _ops, meta, changes = _ground(before, [], "Хочу в Москву в России")
    assert after == before and not changes and meta["unknowns"]
    assert meta["pending_context"]["field"] == "destination.city"
    assert "US" in meta["unknowns"][0] and "RU" in meta["unknowns"][0]


def test_unresolved_new_city_preserves_only_independent_draft_dates():
    before = TripState(trip_id="declared-ref-unknown")
    after, ops, meta, _changes = _ground(before, [{"type": "upsert_stay", "target_id": "new-stay-1", "value": {
        "city": "Киото", "place_id": KYOTO.place_id, "check_in": "2026-11-02"}}],
        "Хочу в Киото с 2 ноября.")
    assert len(after.stays) == 1 and after.stays[0].city is None and after.stays[0].place_id is None
    assert after.stays[0].check_in.isoformat() == "2026-11-02"
    assert "place_id" not in ops[0]["value"] and meta["unknowns"]


def test_legacy_without_refs_keeps_its_existing_literal_behavior():
    before = TripState(origin="Москва", stays=[{"id": "stop-1", "city": "Стамбул"}])
    after, _ops, meta, _changes = _ground(before, [{"type": "set_origin", "value": {"city": "Санкт-Петербург"}}],
        "Меняем город отправления: вместо Москвы Санкт-Петербург.")
    assert after.origin == "Санкт-Петербург" and after.stays == before.stays
    assert not meta["unknowns"]
