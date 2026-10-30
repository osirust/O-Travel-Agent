"""Persistent catalog controls on otravel_test, synthetic source facts, no HTTP.

Only three additive app-prefixed tables are initialized. No table/row cleanup,
DROP, global cache, supplier/model call, native control or geography is inferred.
"""

import asyncio
import hashlib
import json
import os
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, insert, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from otravel.geo_catalog import (
    IDENTITY_TTL_SECONDS,
    GeoCatalog,
    GeoCatalogError,
    _envelope,
    _key,
    directory_entries,
    metadata,
    place_snapshots,
    verification_leases,
)
from otravel.place_directory import validate_directory_candidate
from otravel.place_refs import CityRefSnapshot, openmeteo_place_id
from otravel.store import Store, quotes

NOW = datetime(2040, 3, 1, 12, tzinfo=UTC)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def fact(identifier=1_234_567_890, **changes):
    result = {"geographic_source": {"provider": "openmeteo", "external_id": identifier},
        "canonical_name": "Buenos Aires", "country_iso2": "AR", "country_name": "Argentina",
        "latitude": -34.61, "longitude": -58.38, "timezone": "America/Argentina/Buenos_Aires",
        "feature_code": "PPLC", "administrative_areas": [], "language": "en", **changes}
    result["source_fact_sha256"] = digest(result)
    return validate_directory_candidate(result)


def key(**changes):
    return _key({"provider": "openmeteo", "operation": "search_city", "role": "city",
        "country_iso2": "AR", "query": "Buenos Aires", "language": "en", "count": 5, **changes})


def envelope(cache_key=None, candidates=None, *, now=NOW, ttl=600, truncated=False):
    cache_key = key() if cache_key is None else deepcopy(cache_key)
    candidates = [fact()] if candidates is None else deepcopy(candidates)
    status = "no_match" if not candidates else "ambiguous" if len(candidates) > 1 or truncated else "positive"
    return {"version": 1, "provider": "openmeteo", "kind": "city_directory", "key": cache_key,
        "data": {"status": status, "candidates": candidates, "truncated": truncated},
        "retrieved_at": now.isoformat(), "expires_at": (now + timedelta(seconds=ttl)).isoformat(),
        "response_sha256": digest({"synthetic_response_only": candidates, "at": now.isoformat()})}


def snapshot(candidate=None, version=1):
    candidate = fact() if candidate is None else deepcopy(candidate)
    return CityRefSnapshot.model_validate({**{k: v for k, v in candidate.items() if k != "language"},
        "place_id": openmeteo_place_id(candidate["geographic_source"]["external_id"]), "ref_version": version})


@pytest_asyncio.fixture
async def catalog():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not configured; no database contacted")
    if make_url(url).database != "otravel_test":
        pytest.fail("GeoCatalog tests require only otravel_test", pytrace=False)
    engine = create_async_engine(url, pool_pre_ping=True)
    store = GeoCatalog(engine)
    try:
        await store.initialize()
    except GeoCatalogError:
        await engine.dispose()
        pytest.fail("GeoCatalog test database unavailable; private details suppressed", pytrace=False)
    unique = uuid4().hex
    candidate = fact(1_000_000_000 + uuid4().int % 1_000_000_000)
    cache_key = key(query="Synthetic catalog case " + unique)
    try:
        yield store, engine, cache_key, candidate
    finally:
        await engine.dispose()


async def admit(store, cache_key, candidate, *, now=NOW):
    value = envelope(cache_key, [candidate], now=now)
    await store.put_directory(cache_key, value, now=now)
    ref = await store.admit_place(cache_key, candidate["geographic_source"]["external_id"],
        expected_source_fact_sha256=candidate["source_fact_sha256"],
        expected_response_sha256=value["response_sha256"], now=now)
    return ref, value


async def counts(engine, cache_key, place_id):
    async with engine.connect() as conn:
        result = []
        for table, condition in ((directory_entries, directory_entries.c.key_sha256 == digest(cache_key)),
                (place_snapshots, place_snapshots.c.place_id == place_id),
                (verification_leases, verification_leases.c.place_id == place_id)):
            result.append((await conn.execute(select(func.count()).select_from(table).where(condition))).scalar_one())
        return tuple(result)


def test_only_separate_additive_app_tables_and_no_global_store_metadata():
    assert set(metadata.tables) == {"otravel_geo_directory_cache", "otravel_geo_place_snapshots", "otravel_geo_verification_leases"}
    assert all(name.startswith("otravel_geo_") for name in metadata.tables)


def test_cache_key_includes_request_disambiguators_and_case_normalization_not_city_selection():
    assert key(query="  Ｂuenos   AIRES  ", country_iso2="ar", language="EN") == key()
    base = digest(key())
    variants = [key(count=1), key(role="origin"), key(country_iso2=None), key(language="ru"),
        key(operation="get_place", count=1, country_iso2=None, query="1234567890")]
    assert len({base, *(digest(k) for k in variants)}) == 6


@pytest.mark.parametrize("mutation", ["headers", "raw_body", "query_key", "candidate_key", "bad_hash", "wrong_country", "wrong_language", "no_truncated", "fake_unique", "transport_error", "bad_get_id"])
def test_directory_allows_only_bounded_source_fact_protocol_not_secrets_errors_or_forged_uniqueness(mutation):
    k, value = key(), envelope()
    if mutation == "headers":
        value["headers"] = {"Authorization": "synthetic-private"}
    elif mutation == "raw_body":
        value["raw_response"] = "synthetic-private"
    elif mutation == "query_key":
        value["key"]["api_key"] = "synthetic-private"
    elif mutation == "candidate_key":
        value["data"]["candidates"][0]["access_token"] = "synthetic-private"
    elif mutation == "bad_hash":
        value["data"]["candidates"][0]["latitude"] = -30.0
    elif mutation == "wrong_country":
        value["data"]["candidates"] = [fact(country_iso2="RU")]
    elif mutation == "wrong_language":
        value["data"]["candidates"] = [fact(language="ru")]
    elif mutation == "no_truncated":
        value["data"].pop("truncated")
    elif mutation == "fake_unique":
        k = key(count=1)
        value = envelope(k)  # Reached cap1 but falsely promises untruncated uniqueness.
    elif mutation == "transport_error":
        value["data"]["status"] = "REQUEST_TIMEOUT"
    else:
        k = key(operation="get_place", count=1, country_iso2=None, query="123")
        value = envelope(k)
    with pytest.raises(GeoCatalogError) as failed:
        _envelope(k, value, NOW, fresh=True)
    assert "synthetic-private" not in str(failed.value)


@pytest.mark.parametrize("mutation", ["naive_now", "naive_retrieved", "missing_expiry", "future", "expired", "negative_long", "positive_long"])
def test_time_and_status_specific_ttl_do_not_infer_current_or_eternal_provenance(mutation):
    value, now = envelope(), NOW
    if mutation == "naive_now":
        now = NOW.replace(tzinfo=None)
    elif mutation == "naive_retrieved":
        value["retrieved_at"] = NOW.replace(tzinfo=None).isoformat()
    elif mutation == "missing_expiry":
        value["expires_at"] = None
    elif mutation == "future":
        value = envelope(now=NOW + timedelta(seconds=1))
    elif mutation == "expired":
        value = envelope(now=NOW - timedelta(seconds=601))
    elif mutation == "negative_long":
        value = envelope(candidates=[], ttl=901)
    else:
        value = envelope(ttl=86401)
    with pytest.raises(GeoCatalogError):
        from otravel.geo_catalog import _now
        _envelope(key(), value, _now(now), fresh=True)


def test_empty_capped_results_remain_bounded_no_match_and_cannot_become_a_city():
    value = envelope(candidates=[], truncated=True)
    checked = _envelope(key(), value, NOW, fresh=True)
    assert checked["data"] == {"status": "no_match", "candidates": [], "truncated": True}


@pytest.mark.asyncio
async def test_pg_directory_and_place_restart_readback_require_no_ram_registration(catalog):
    store, engine, k, candidate = catalog
    ref, value = await admit(store, k, candidate)
    other_engine = create_async_engine(engine.url)
    other = GeoCatalog(other_engine)
    try:
        await other.initialize()
        assert await other.get_directory(k, now=NOW) == value
        assert await other.get_place(ref.place_id, 1) == ref
        assert await other.verify_places([ref.model_dump(mode="json")], now=NOW) == {ref.place_id: ref}
        assert await counts(other_engine, k, ref.place_id) == (1, 1, 1)
    finally:
        await other_engine.dispose()


@pytest.mark.asyncio
async def test_pg_expired_latest_negative_does_not_backfill_older_positive_and_records_are_immutable(catalog):
    store, engine, k, candidate = catalog
    original = envelope(k, [candidate], ttl=600)
    await store.put_directory(k, original, now=NOW)
    await store.put_directory(k, original, now=NOW)  # Exact duplicate, not another observation.
    later = envelope(k, [], now=NOW + timedelta(seconds=10), ttl=10)
    await store.put_directory(k, later, now=NOW + timedelta(seconds=10))
    assert await store.get_directory(k, now=NOW + timedelta(seconds=21)) is None
    with pytest.raises(GeoCatalogError, match="актуальный"):
        await store.admit_place(k, candidate["geographic_source"]["external_id"],
            expected_source_fact_sha256=candidate["source_fact_sha256"], now=NOW + timedelta(seconds=21))
    async with engine.connect() as conn:
        rows = (await conn.execute(select(directory_entries).where(directory_entries.c.key_sha256 == digest(k))
            .order_by(directory_entries.c.entry_version))).mappings().all()
    assert [r["entry_version"] for r in rows] == [1, 2]
    assert rows[0]["payload"] == original and rows[1]["payload"] == later
    with pytest.raises(GeoCatalogError) as failed:
        await store.put_directory(k, original, now=NOW + timedelta(seconds=11))
    assert failed.value.code == "stale_directory"


@pytest.mark.asyncio
async def test_pg_expired_identity_is_readable_and_fresh_same_fact_appends_lease_without_changing_snapshot(catalog):
    store, engine, k, candidate = catalog
    ref, _ = await admit(store, k, candidate)
    expired = NOW + timedelta(seconds=IDENTITY_TTL_SECONDS + 1)
    assert await store.get_place(ref.place_id, 1) == ref
    with pytest.raises(GeoCatalogError) as error:
        await store.verify_places([ref], now=expired)
    assert error.value.code == "stale_snapshot"
    assert await store.verify_places([ref], now=expired, require_fresh=False, require_current=False) == {ref.place_id: ref}
    refreshed, _ = await admit(store, k, candidate, now=expired)
    assert refreshed == ref and refreshed.snapshot_sha256() == ref.snapshot_sha256()
    assert await store.verify_places([ref], now=expired) == {ref.place_id: ref}
    assert await counts(engine, k, ref.place_id) == (2, 1, 2)


@pytest.mark.asyncio
async def test_pg_cache_hit_admission_is_idempotent_and_cannot_extend_lease_from_read_time(catalog):
    store, engine, k, candidate = catalog
    ref, _ = await admit(store, k, candidate)
    await store.admit_place(k, candidate["geographic_source"]["external_id"],
        expected_source_fact_sha256=candidate["source_fact_sha256"], now=NOW + timedelta(seconds=500))
    assert await counts(engine, k, ref.place_id) == (1, 1, 1)
    async with engine.connect() as conn:
        lease, = (await conn.execute(select(verification_leases).where(verification_leases.c.place_id == ref.place_id))).mappings().all()
    assert lease["verified_at"] == NOW and lease["expires_at"] == NOW + timedelta(seconds=IDENTITY_TTL_SECONDS)


@pytest.mark.asyncio
async def test_pg_future_verification_cannot_grant_current_selection_or_bridge_an_expired_lease(catalog):
    store, _engine, k, candidate = catalog
    ref, _ = await admit(store, k, candidate)
    with pytest.raises(GeoCatalogError) as failed:
        await store.verify_places([ref], now=NOW - timedelta(seconds=1))
    assert failed.value.code == "stale_snapshot"
    future = NOW + timedelta(days=31)
    again, _ = await admit(store, k, candidate, now=future)
    assert again == ref
    # Ignore the future MAX expiry without throwing away a still-valid earlier
    # observation; once that earlier lease expires the future one grants nothing.
    assert await store.verify_places([ref], now=NOW + timedelta(days=1)) == {ref.place_id: ref}
    with pytest.raises(GeoCatalogError) as failed:
        await store.verify_places([ref], now=NOW + timedelta(days=30, seconds=1))
    assert failed.value.code == "stale_snapshot"
    assert await store.get_place(ref.place_id, 1) == ref


@pytest.mark.asyncio
async def test_pg_changed_fact_creates_new_version_without_overwriting_expired_or_current_history(catalog):
    store, engine, k, candidate = catalog
    first, _ = await admit(store, k, candidate)
    changed = fact(candidate["geographic_source"]["external_id"], latitude=-34.62)
    second, _ = await admit(store, k, changed, now=NOW + timedelta(seconds=1))
    assert second.place_id == first.place_id and (first.ref_version, second.ref_version) == (1, 2)
    assert await store.get_place(first.place_id, 1) == first and await store.get_place(first.place_id, 2) == second
    with pytest.raises(GeoCatalogError) as failed:
        await store.verify_places([first], now=NOW + timedelta(seconds=1))
    assert failed.value.code == "stale_snapshot"
    assert await store.verify_places([first], now=NOW, require_current=False) == {first.place_id: first}
    assert await counts(engine, k, first.place_id) == (2, 2, 2)


@pytest.mark.asyncio
async def test_pg_same_version_forgery_and_unregistered_payloads_cannot_enter_public_admission(catalog):
    store, _engine, k, candidate = catalog
    ref, _ = await admit(store, k, candidate)
    forged = ref.model_dump(mode="json")
    forged["canonical_name"] = "Forged same source ID"
    with pytest.raises(GeoCatalogError) as failed:
        await store.put_place(forged, now=NOW)
    assert failed.value.code == "snapshot_conflict"
    with pytest.raises(GeoCatalogError) as failed:
        await store.verify_places([forged], now=NOW)
    assert failed.value.code == "snapshot_conflict"
    unknown = snapshot(fact(candidate["geographic_source"]["external_id"] - 1))
    with pytest.raises(GeoCatalogError) as failed:
        await store.verify_places([unknown], now=NOW)
    assert failed.value.code == "unknown_snapshot"
    assert await store.get_place(ref.place_id, 1) == ref


@pytest.mark.asyncio
async def test_pg_direct_private_registration_has_no_fresh_source_authority_and_no_version_gaps(catalog):
    store, _engine, _k, candidate = catalog
    ref = snapshot(candidate)
    await store.put_place(ref, now=NOW)
    assert await store.get_place(ref.place_id, 1) == ref
    with pytest.raises(GeoCatalogError) as failed:
        await store.verify_places([ref], now=NOW)
    assert failed.value.code == "stale_snapshot"
    with pytest.raises(GeoCatalogError) as failed:
        await store.put_place(snapshot(candidate, 3), now=NOW)
    assert failed.value.code == "invalid_version"


@pytest.mark.asyncio
async def test_pg_explicit_candidate_and_bound_fact_response_hashes_prevent_stale_or_first_result_selection(catalog):
    store, engine, k, candidate = catalog
    other = fact(candidate["geographic_source"]["external_id"] - 1, canonical_name="Another Buenos Aires")
    value = envelope(k, [other, candidate])
    await store.put_directory(k, value, now=NOW)
    with pytest.raises(GeoCatalogError) as failed:
        await store.admit_place(k, candidate["geographic_source"]["external_id"], expected_source_fact_sha256="0" * 64, now=NOW)
    assert failed.value.code == "candidate_changed"
    with pytest.raises(GeoCatalogError) as failed:
        await store.admit_place(k, candidate["geographic_source"]["external_id"],
            expected_source_fact_sha256=candidate["source_fact_sha256"], expected_response_sha256="0" * 64, now=NOW)
    assert failed.value.code == "candidate_changed"
    chosen = await store.admit_place(k, candidate["geographic_source"]["external_id"],
        expected_source_fact_sha256=candidate["source_fact_sha256"], expected_response_sha256=value["response_sha256"], now=NOW)
    assert chosen.place_id == openmeteo_place_id(candidate["geographic_source"]["external_id"])
    assert await store.get_place(openmeteo_place_id(other["geographic_source"]["external_id"]), 1) is None
    assert await counts(engine, k, chosen.place_id) == (1, 1, 1)


@pytest.mark.asyncio
async def test_pg_unverified_supplier_binding_cannot_acquire_a_geocoder_lease_and_transaction_rolls_back(catalog):
    store, _engine, k, candidate = catalog
    first, value = await admit(store, k, candidate)
    changed = first.model_dump(mode="json")
    changed.update(ref_version=2, provider_bindings={"liteapi": {"country_iso2": "AR", "city_name": "Invented supplier city"}, "duffel": None})
    with pytest.raises(GeoCatalogError) as failed:
        await store.put_place(changed, now=NOW, directory_key=k,
            expected_source_fact_sha256=candidate["source_fact_sha256"], expected_response_sha256=value["response_sha256"])
    assert failed.value.code == "candidate_changed"
    assert await store.get_place(first.place_id, 2) is None and await store.get_place(first.place_id, 1) == first


@pytest.mark.asyncio
async def test_pg_concurrent_identical_admissions_mint_one_version_and_one_immutable_lease(catalog):
    store, engine, k, candidate = catalog
    value = envelope(k, [candidate])
    await asyncio.gather(*(store.put_directory(k, value, now=NOW) for _ in range(3)))
    refs = await asyncio.gather(*(store.admit_place(k, candidate["geographic_source"]["external_id"],
        expected_source_fact_sha256=candidate["source_fact_sha256"], now=NOW) for _ in range(5)))
    assert all(ref == refs[0] and ref.ref_version == 1 for ref in refs)
    assert await counts(engine, k, refs[0].place_id) == (1, 1, 1)


@pytest.mark.asyncio
async def test_pg_geography_updates_leave_existing_trip_quote_and_audit_byte_equal(catalog):
    store, engine, k, candidate = catalog
    repository = Store(engine.url.render_as_string(hide_password=False))
    try:
        await repository.initialize()
        # Explicit controlled existing data, not a claimed valid supplier quote.
        initial = {"trip_id": "catalog-preservation-" + uuid4().hex, "state_version": 0,
            "origin": None, "travelers": [], "stays": [], "legs": [], "constraints": [], "preferences": []}
        session = await repository.create_session(initial)
        sid, qid = session["session_id"], str(uuid4())
        async with engine.begin() as conn:
            await conn.execute(insert(quotes).values(id=qid, session_id=sid, quote_version=1,
                state_version=0, plan={"fixture": "synthetic_existing_quote_not_checkout"}, status="active",
                expires_at=NOW + timedelta(hours=1), created_at=NOW))
        before = await repository.get_session(sid), await repository.get_quote(qid), await repository.list_events(sid)
        first, _ = await admit(store, k, candidate)
        await admit(store, k, fact(candidate["geographic_source"]["external_id"], longitude=-58.39), now=NOW + timedelta(seconds=1))
        after = await repository.get_session(sid), await repository.get_quote(qid), await repository.list_events(sid)
        assert before == after and await store.get_place(first.place_id, 1) == first
    finally:
        await repository.close()


async def translated_proofs(store, cache_key, candidate, *, ru_at=NOW, en_at=NOW):
    identifier = candidate["geographic_source"]["external_id"]
    en_key = key(operation="get_place", query=str(identifier), country_iso2=None, count=1)
    ru_key = {**cache_key, "language": "ru"}
    ru_fact = fact(identifier, canonical_name="Буэнос-Айрес", country_name="Аргентина", language="ru",
        administrative_areas=[{"level": 1, "external_id": 1234, "name": "Область"}])
    en_fact = fact(identifier, administrative_areas=[{"level": 1, "external_id": 1234, "name": "Province"}])
    en_value = envelope(en_key, [en_fact], now=en_at)
    ru_value = envelope(ru_key, [ru_fact], now=ru_at)
    await store.put_directory(en_key, en_value, now=en_at)
    await store.put_directory(ru_key, ru_value, now=ru_at)
    return en_key, en_fact, en_value, ru_key, ru_fact, ru_value


def ru_witness(k, candidate, value):
    return {"directory_key": k, "expected_source_fact_sha256": candidate["source_fact_sha256"],
        "expected_response_sha256": value["response_sha256"]}


@pytest.mark.asyncio
async def test_pg_atomic_translated_admission_mints_canonical_en_and_both_bound_proofs_without_clock_extension(catalog):
    store, engine, k, candidate = catalog
    en_k, en_fact, en_value, ru_k, ru_fact, ru_value = await translated_proofs(store, k, candidate,
        en_at=NOW + timedelta(seconds=20))
    now = NOW + timedelta(seconds=25)
    kwargs = {"expected_source_fact_sha256": en_fact["source_fact_sha256"],
        "expected_response_sha256": en_value["response_sha256"], "witness": ru_witness(ru_k, ru_fact, ru_value)}
    chosen = await store.admit_place(en_k, candidate["geographic_source"]["external_id"], now=now, **kwargs)
    assert chosen == snapshot(en_fact) and chosen.canonical_name == "Buenos Aires"
    assert chosen.country_name == "Argentina" and chosen.source_fact_sha256 == en_fact["source_fact_sha256"]
    again = await store.admit_place(en_k, candidate["geographic_source"]["external_id"],
        now=NOW + timedelta(seconds=50), **kwargs)
    assert again == chosen
    async with engine.connect() as conn:
        leases = (await conn.execute(select(verification_leases).where(
            verification_leases.c.place_id == chosen.place_id))).mappings().all()
    assert len(leases) == 2
    assert {r["key_sha256"] for r in leases} == {digest(en_k), digest(ru_k)}
    assert {r["verified_at"] for r in leases} == {NOW + timedelta(seconds=20)}
    assert {r["expires_at"] for r in leases} == {NOW + timedelta(seconds=IDENTITY_TTL_SECONDS)}
    assert {r["payload_sha256"] for r in leases} == {chosen.snapshot_sha256()}
    assert await store.verify_places([chosen], now=now) == {chosen.place_id: chosen}
    for invalid_time in (NOW + timedelta(seconds=10), NOW + timedelta(seconds=IDENTITY_TTL_SECONDS + 1)):
        with pytest.raises(GeoCatalogError) as failed:
            await store.verify_places([chosen], now=invalid_time)
        assert failed.value.code == "stale_snapshot"
    assert await store.get_place(chosen.place_id, 1) == chosen


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_side", ["ru", "en"])
async def test_pg_refreshed_translation_proof_after_read_rejects_atomic_admission_without_any_write(catalog, changed_side):
    store, engine, k, candidate = catalog
    en_k, en_fact, en_value, ru_k, ru_fact, ru_value = await translated_proofs(store, k, candidate)
    changed_key, changed_fact = (ru_k, ru_fact) if changed_side == "ru" else (en_k, en_fact)
    changed = envelope(changed_key, [changed_fact], now=NOW + timedelta(seconds=1))
    await store.put_directory(changed_key, changed, now=NOW + timedelta(seconds=1))
    identifier = candidate["geographic_source"]["external_id"]
    place_id = openmeteo_place_id(identifier)
    async def all_rows():
        async with engine.connect() as conn:
            return [(await conn.execute(select(table).where(condition).order_by(*table.primary_key.columns))).mappings().all()
                for table, condition in (
                    (directory_entries, directory_entries.c.key_sha256.in_([digest(en_k), digest(ru_k)])),
                    (place_snapshots, place_snapshots.c.place_id == place_id),
                    (verification_leases, verification_leases.c.place_id == place_id))]
    before = await all_rows()
    with pytest.raises(GeoCatalogError) as failed:
        await store.admit_place(en_k, identifier, expected_source_fact_sha256=en_fact["source_fact_sha256"],
            expected_response_sha256=en_value["response_sha256"], witness=ru_witness(ru_k, ru_fact, ru_value),
            now=NOW + timedelta(seconds=2))
    assert failed.value.code == "candidate_changed"
    assert await all_rows() == before and await store.get_place(place_id, 1) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["country_iso2", "latitude", "longitude", "timezone", "feature_code", "admin_id"])
async def test_pg_translated_candidate_requires_physical_identity_not_only_matching_source_id(catalog, mutation):
    store, engine, k, candidate = catalog
    en_k, en_fact, en_value, ru_k, ru_fact, _ru_value = await translated_proofs(store, k, candidate)
    changes = {"country_iso2": "UY", "latitude": -34.62, "longitude": -58.39,
        "timezone": "UTC", "feature_code": "PPL"}
    patch = {mutation: changes[mutation]} if mutation != "admin_id" else {
        "administrative_areas": [{"level": 1, "external_id": 1235, "name": "Область"}]}
    modified = deepcopy(ru_fact)
    modified.update(patch)
    modified.pop("source_fact_sha256")
    modified["source_fact_sha256"] = digest(modified)
    if mutation == "country_iso2":
        ru_k = {**ru_k, "country_iso2": "UY", "query": k["query"] + " other country"}
    current = envelope(ru_k, [modified], now=NOW + timedelta(seconds=1))
    await store.put_directory(ru_k, current, now=NOW + timedelta(seconds=1))
    before = await counts(engine, en_k, openmeteo_place_id(candidate["geographic_source"]["external_id"]))
    with pytest.raises(GeoCatalogError) as failed:
        await store.admit_place(en_k, candidate["geographic_source"]["external_id"],
            expected_source_fact_sha256=en_fact["source_fact_sha256"], expected_response_sha256=en_value["response_sha256"],
            witness=ru_witness(ru_k, modified, current), now=NOW + timedelta(seconds=2))
    assert failed.value.code == "candidate_changed"
    assert await counts(engine, en_k, openmeteo_place_id(candidate["geographic_source"]["external_id"])) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("expired_side", ["ru", "en"])
async def test_pg_translated_admission_requires_both_latest_observations_still_fresh(catalog, expired_side):
    store, engine, k, candidate = catalog
    en_k, en_fact, en_value, ru_k, ru_fact, ru_value = await translated_proofs(store, k, candidate)
    fresh_key, fresh_fact = (en_k, en_fact) if expired_side == "ru" else (ru_k, ru_fact)
    fresh_value = envelope(fresh_key, [fresh_fact], now=NOW + timedelta(seconds=550))
    await store.put_directory(fresh_key, fresh_value, now=NOW + timedelta(seconds=550))
    if expired_side == "ru":
        en_value = fresh_value
    else:
        ru_value = fresh_value
    place_id = openmeteo_place_id(candidate["geographic_source"]["external_id"])
    before = await counts(engine, en_k, place_id)
    with pytest.raises(GeoCatalogError) as failed:
        await store.admit_place(en_k, candidate["geographic_source"]["external_id"],
            expected_source_fact_sha256=en_fact["source_fact_sha256"], expected_response_sha256=en_value["response_sha256"],
            witness=ru_witness(ru_k, ru_fact, ru_value), now=NOW + timedelta(seconds=601))
    assert failed.value.code == "stale_directory"
    assert await counts(engine, en_k, place_id) == before
