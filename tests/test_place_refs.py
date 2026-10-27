"""Pure structural geographic-ref tests; fixture facts are declared, not API proof."""
import hashlib
import json
from datetime import date

import pytest
from pydantic import ValidationError

from otravel.domain import DomainError, Operation, TripState, apply_operations, route_edges, sample_trip
from otravel.place_refs import MAX_LOCATION_REFS, CityRefSnapshot, openmeteo_place_id


def place(identifier=3435910, name="Буэнос-Айрес", country="AR", **overrides):
    facts = {
        "place_id": f"geo:openmeteo:{identifier}", "ref_version": 1,
        "canonical_name": name, "country_iso2": country, "country_name": None,
        "geographic_source": {"provider": "openmeteo", "external_id": identifier},
        "latitude": -34.6, "longitude": -58.38, "timezone": "America/Argentina/Buenos_Aires",
        "feature_code": "PPLC", "source_fact_sha256": "a" * 64,
    }
    return CityRefSnapshot.model_validate({**facts, **overrides})


def fixture_trip(*, same_names=False):
    a = place(1, "Кордова" if same_names else "Первый город", "AR")
    b = place(2, "Кордова" if same_names else "Второй город", "ES", timezone="Europe/Madrid")
    state, _ = apply_operations(TripState(trip_id="declared-geo-fixture"), [
        Operation(type="set_origin", value={"city": "Москва"}),
        Operation(type="upsert_stay", target_id="a", value={"city": a.canonical_name,
            "place_id": a.place_id, "check_in": "2026-11-10", "check_out": "2026-11-12"}),
        Operation(type="upsert_stay", target_id="b", value={"city": b.canonical_name,
            "place_id": b.place_id, "check_in": "2026-11-12", "check_out": "2026-11-14"}),
    ], resolved_places={a.place_id: a, b.place_id: b})
    return state


def test_ref_snapshot_is_frozen_nested_exact_and_hash_is_canonical():
    ref = place(administrative_areas=[{"level": 1, "external_id": 9, "name": "Область"}],
        provider_bindings={"liteapi": {"country_iso2": "AR", "city_name": "Buenos Aires"},
            "duffel": {"country_iso2": "AR", "external_id": "city_1", "place_type": "city", "iata_code": "BUE"}})
    with pytest.raises(ValidationError):
        ref.canonical_name = "Другой город"
    with pytest.raises(ValidationError):
        ref.provider_bindings.liteapi.city_name = "Other"
    assert isinstance(ref.administrative_areas, tuple)
    payload = json.dumps(ref.model_dump(mode="json"), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False)
    assert ref.snapshot_sha256() == hashlib.sha256(payload.encode()).hexdigest()
    assert CityRefSnapshot.model_validate(ref.model_dump(mode="json")) == ref


@pytest.mark.parametrize("patch", [
    {"latitude": True}, {"latitude": float("nan")}, {"longitude": float("inf")}, {"latitude": 91},
    {"longitude": -181}, {"country_iso2": "ar"}, {"timezone": "Imagined/Timezone"},
    {"canonical_name": " hidden "}, {"canonical_name": "ab\x00cd"},
    {"feature_code": "PCLI"}, {"feature_code": "AIRP"}, {"ref_version": True},
    {"geographic_source": {"provider": "model", "external_id": 1}},
    {"geographic_source": {"provider": "openmeteo", "external_id": True}},
    {"source_fact_sha256": "not-source-facts"},
    {"provider_bindings": {"liteapi": {"country_iso2": "ES", "city_name": "Buenos Aires"}}},
    {"provider_bindings": {"duffel": {"country_iso2": "ES", "external_id": "x", "place_type": "city"}}},
    {"administrative_areas": [{"level": 1, "external_id": 1, "name": "a"},
        {"level": 1, "external_id": 2, "name": "b"}]},
])
def test_invalid_geography_facts_do_not_become_snapshot(patch):
    with pytest.raises(ValidationError):
        place(**patch)


def test_nullable_timezone_and_country_label_do_not_infer_airport_city_facts():
    ref = place(timezone=None, country_name=None, provider_bindings={"duffel": {
        "country_iso2": "AR", "external_id": "airport_1", "place_type": "airport", "iata_code": "EZE"}})
    assert ref.timezone is None
    assert ref.country_name is None


def test_legacy_empty_and_sample_json_shape_contains_no_new_fields():
    for state in (TripState(trip_id="legacy"), sample_trip()):
        payload = state.model_dump(mode="json")
        assert "location_refs" not in payload and "origin_place_id" not in payload
        assert "end_place_id" not in payload["route_policy"]
        assert all("place_id" not in stay for stay in payload["stays"])
        assert all("origin_place_id" not in leg and "destination_place_id" not in leg for leg in payload["legs"])
        assert TripState.model_validate(payload).model_dump_json() == state.model_dump_json()


def test_server_resolved_bundle_applies_before_first_dynamic_operation():
    ref = place()
    old = TripState(trip_id="atomic")
    changed, edits = apply_operations(old, [
        Operation(type="set_origin", value={"city": ref.canonical_name, "place_id": ref.place_id}),
        Operation(type="upsert_stay", target_id="s1", value={"city": "Казань", "check_in": "2026-11-10", "check_out": "2026-11-12"}),
    ], resolved_places={ref.place_id: ref})
    assert changed.origin_place_id == ref.place_id
    assert changed.legs[0].origin_place_id == ref.place_id
    assert changed.legs[-1].destination_place_id == ref.place_id
    assert edits[0]["field"] == "location_refs"
    assert old.location_refs == {} and old.origin is None


@pytest.mark.parametrize("field", ["origin", "stay", "route_end", "leg"])
def test_unknown_ref_label_requires_matching_parent_snapshot(field):
    ref = place()
    data = {"trip_id": "invalid-parent", "location_refs": {ref.place_id: ref.model_dump(mode="json")}}
    if field == "origin":
        data.update(origin="Other", origin_place_id=ref.place_id)
    elif field == "stay":
        data["stays"] = [{"id": "s", "city": "Other", "place_id": ref.place_id}]
    elif field == "route_end":
        data["route_policy"] = {"kind": "open_jaw", "end_city": "Other", "end_place_id": ref.place_id}
    else:
        data["legs"] = [{"id": "l", "origin": "Other", "origin_place_id": ref.place_id}]
    with pytest.raises(ValidationError):
        TripState.model_validate(data)


@pytest.mark.parametrize("field", ["origin", "stay", "route_end", "leg"])
def test_unknown_unreferenced_city_is_never_blindly_accepted(field):
    data = {"trip_id": "unknown"}
    if field == "origin":
        data["origin"] = "Unknown worldwide city"
    elif field == "stay":
        data["stays"] = [{"id": "s", "city": "Unknown worldwide city"}]
    elif field == "route_end":
        data["route_policy"] = {"kind": "open_jaw", "end_city": "Unknown worldwide city"}
    else:
        data["legs"] = [{"id": "l", "destination": "Unknown worldwide city"}]
    with pytest.raises(ValidationError):
        TripState.model_validate(data)


def test_name_homonyms_keep_distinct_route_edges_and_leg_modes():
    state = fixture_trip(same_names=True)
    assert len(state.legs) == 3
    middle = state.legs[1]
    assert middle.origin == middle.destination == "Кордова"
    assert middle.origin_place_id != middle.destination_place_id
    assert route_edges(state)[1] == ("Кордова", "Кордова", date(2026, 11, 12))
    changed, _ = apply_operations(state, [Operation(type="set_leg_mode", target_id=middle.id, value={"mode": "train"})])
    after, _ = apply_operations(changed, [Operation(type="upsert_stay", target_id="b", value={"check_out": "2026-11-16"})])
    assert [(leg.id, leg.mode) for leg in after.legs] == [(leg.id, leg.mode) for leg in changed.legs]
    assert after.legs[1].origin_place_id == middle.origin_place_id
    assert after.legs[1].destination_place_id == middle.destination_place_id


def test_parent_rejects_swapped_same_name_endpoint_refs_even_when_text_matches():
    state = fixture_trip(same_names=True)
    data = state.model_dump(mode="json")
    edge = data["legs"][1]
    edge["origin_place_id"], edge["destination_place_id"] = edge["destination_place_id"], edge["origin_place_id"]
    with pytest.raises(ValidationError):
        TripState.model_validate(data)


def test_known_legacy_name_can_refer_to_verified_homonym_country():
    ref = place(10, "Москва", "US", timezone="America/Los_Angeles")
    state, _ = apply_operations(TripState(), [Operation(type="set_origin", value={"city": "Москва", "place_id": ref.place_id})], resolved_places={ref.place_id: ref})
    assert state.location_refs[ref.place_id].country_iso2 == "US"
    assert state.origin == "Москва"


def test_country_and_external_identity_do_not_come_from_display_name():
    a = place(1, "Кордова", "AR")
    b = place(2, "Кордова", "ES", timezone="Europe/Madrid")
    state = TripState(location_refs={a.place_id: a, b.place_id: b})
    assert len(state.location_refs) == 2
    bad = a.model_dump(mode="json")
    bad["place_id"] = "another-id"
    with pytest.raises(ValidationError):
        TripState(location_refs={a.place_id: a, bad["place_id"]: bad})


def test_atomic_failed_edit_cannot_persist_bundle_or_partial_party_budget():
    old = sample_trip()
    before = old.model_dump_json()
    ref = place()
    with pytest.raises(DomainError):
        apply_operations(old, [Operation(type="set_budget", value={"amount_minor": 80000}),
            Operation(type="upsert_stay", target_id="stay-istanbul", value={"city": "wrong", "place_id": ref.place_id})], resolved_places={ref.place_id: ref})
    assert old.model_dump_json() == before


def test_model_operation_cannot_install_or_replace_snapshot():
    ref = place()
    with pytest.raises(DomainError, match="Неизвестные поля"):
        apply_operations(TripState(), [Operation(type="upsert_stay", target_id="s", value={"city": ref.canonical_name, "location_refs": {ref.place_id: ref.model_dump(mode="json")}})])
    with pytest.raises(DomainError):
        apply_operations(TripState(), [Operation(type="set_origin", value={"city": ref.canonical_name, "place_id": ref.place_id})])


def test_selected_snapshot_is_immutable_and_unmodified_bundle_is_idempotent():
    ref = place()
    state, _ = apply_operations(TripState(), [], resolved_places={ref.place_id: ref})
    unchanged, edits = apply_operations(state, [], resolved_places={ref.place_id: ref})
    assert unchanged == state and edits == []
    revised = place(ref_version=2)
    with pytest.raises(DomainError) as error:
        apply_operations(state, [], resolved_places={ref.place_id: revised})
    assert error.value.code == "place_ref_conflict"


def test_forged_model_copy_cannot_bypass_snapshot_validation():
    ref = place().model_copy(update={"latitude": float("nan")})
    with pytest.raises(DomainError):
        apply_operations(TripState(), [], resolved_places={ref.place_id: ref})


def test_reference_retarget_is_atomic_and_clears_old_reference_on_legacy_city_change():
    state = fixture_trip()
    ref = place(3, "Третий город", "CL", timezone="America/Santiago")
    changed, _ = apply_operations(state, [Operation(type="upsert_stay", target_id="a", value={"city": ref.canonical_name, "place_id": ref.place_id})], resolved_places={ref.place_id: ref})
    assert changed.legs[0].destination_place_id == ref.place_id
    assert changed.legs[1].origin_place_id == ref.place_id
    assert state.stays[0].place_id != ref.place_id
    legacy, _ = apply_operations(changed, [Operation(type="upsert_stay", target_id="a", value={"city": "Казань"})])
    assert legacy.stays[0].place_id is None and legacy.legs[0].destination_place_id is None


def test_open_jaw_endpoint_reference_is_independent_and_propagated():
    state = fixture_trip()
    end = place(4, "Конечный город", "UY", timezone="America/Montevideo")
    changed, _ = apply_operations(state, [Operation(type="set_route_policy", value={"kind": "open_jaw", "end_city": end.canonical_name, "end_place_id": end.place_id})], resolved_places={end.place_id: end})
    assert changed.legs[-1].destination_place_id == end.place_id
    assert changed.legs[-1].destination == end.canonical_name
    with pytest.raises(DomainError):
        apply_operations(changed, [Operation(type="set_route_policy", value={"kind": "one_way", "end_city": end.canonical_name, "end_place_id": end.place_id})])


def test_ref_bundle_engineering_bound_is_not_city_coverage_policy():
    refs = {ref.place_id: ref for ref in (place(i, f"Город {i}") for i in range(1, MAX_LOCATION_REFS + 2))}
    with pytest.raises((DomainError, ValidationError)):
        apply_operations(TripState(), [], resolved_places=refs)


@pytest.mark.parametrize("value", [True, 0, -1, 2_147_483_648, "123", 1.5])
def test_server_place_id_factory_refuses_non_identity_values(value):
    with pytest.raises(ValueError):
        openmeteo_place_id(value)


def test_server_place_id_cannot_mislabel_another_physical_source_identity():
    assert openmeteo_place_id(3435910) == "geo:openmeteo:3435910"
    with pytest.raises(ValidationError):
        place(place_id="geo:openmeteo:999")


@pytest.mark.parametrize("path,value", [
    ("origin", {"city": "Буэнос-Айрес", "place_id": "geo:openmeteo:999"}),
    ("stay", {"city": "Буэнос-Айрес", "place_id": "geo:openmeteo:999"}),
    ("route_end", {"kind": "open_jaw", "end_city": "Буэнос-Айрес", "end_place_id": "geo:openmeteo:999"}),
])
def test_unknown_snapshot_reference_is_rejected_atomically(path, value):
    ref = place()
    old = sample_trip()
    before = old.model_dump_json()
    operation = {"origin": "set_origin", "stay": "upsert_stay", "route_end": "set_route_policy"}[path]
    with pytest.raises(DomainError):
        apply_operations(old, [Operation(type=operation, target_id="stay-istanbul" if path == "stay" else None, value=value)], resolved_places={ref.place_id: ref})
    assert old.model_dump_json() == before


def test_stay_ref_without_exact_city_pair_is_not_a_selection_operation():
    state = fixture_trip()
    with pytest.raises(DomainError) as error:
        apply_operations(state, [Operation(type="upsert_stay", target_id="a", value={"place_id": state.stays[1].place_id})])
    assert error.value.code == "place_pair_required"


def test_zero_operation_server_bundle_with_wrong_key_is_rejected():
    ref = place()
    with pytest.raises(DomainError) as error:
        apply_operations(TripState(), [], resolved_places={"another-key": ref})
    assert error.value.code == "place_ref_mismatch"


def test_exact_legacy_empty_and_sample_json_sha_from_committed_0bf71be():
    # These hashes were independently computed from git0bf71be's domain module.
    expected = [(TripState(trip_id="legacy"), "8177763b3f451edf9d64c8cd6995426274b6a7269639ae7eb436322a3f6bcce7"),
        (sample_trip(), "23b0e858d4e93ea9b478aa9622bb0357d65ee64a2448021eee5b908ce467cf3f")]
    for state, sha in expected:
        assert hashlib.sha256(state.model_dump_json().encode()).hexdigest() == sha
