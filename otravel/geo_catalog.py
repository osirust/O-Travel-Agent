"""Additive PostgreSQL geography catalog, outside pure domain validation.

Directory observations, place payloads and source-verification leases are append
only. TTL grants no hotel availability, price, user choice or booking consent.
Public input must only call verify_places; admit_place consumes a fresh cached
source fact selected by its exact external ID, never an arbitrary client DTO.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    func,
    select,
    tuple_,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from .place_refs import MAX_LOCATION_REFS, CityRefSnapshot, openmeteo_place_id

DIRECTORY_TTL_SECONDS = 24 * 60 * 60
NEGATIVE_TTL_SECONDS = 15 * 60
IDENTITY_TTL_SECONDS = 30 * 24 * 60 * 60
MAX_DIRECTORY_BYTES = 65_536
MAX_SNAPSHOT_BYTES = 16_384
_ENVELOPE = frozenset({"version", "provider", "kind", "key", "data", "retrieved_at", "expires_at", "response_sha256"})
_DATA = frozenset({"status", "candidates", "truncated"})
_HEX = re.compile(r"[0-9a-f]{64}\Z")

metadata = MetaData()
directory_entries = Table(
    "otravel_geo_directory_cache", metadata,
    Column("key_sha256", String(64), primary_key=True),
    Column("entry_version", BigInteger, primary_key=True),
    Column("cache_key", JSONB, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("payload_sha256", String(64), nullable=False),
    Column("status", String(16), nullable=False),
    Column("retrieved_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("stored_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("entry_version > 0", name="otravel_geo_directory_version_positive"),
    CheckConstraint("expires_at > retrieved_at", name="otravel_geo_directory_expiry_order"),
)
place_snapshots = Table(
    "otravel_geo_place_snapshots", metadata,
    Column("place_id", String(128), primary_key=True),
    Column("ref_version", BigInteger, primary_key=True),
    Column("payload", JSONB, nullable=False),
    Column("payload_sha256", String(64), nullable=False),
    Column("stored_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("place_id", "ref_version", "payload_sha256", name="uq_otravel_geo_snapshot_payload"),
    CheckConstraint("ref_version > 0", name="otravel_geo_snapshot_version_positive"),
)
verification_leases = Table(
    "otravel_geo_verification_leases", metadata,
    Column("lease_id", String(64), primary_key=True),
    Column("place_id", String(128), nullable=False),
    Column("ref_version", BigInteger, nullable=False),
    Column("payload_sha256", String(64), nullable=False),
    Column("key_sha256", String(64), nullable=False),
    Column("entry_version", BigInteger, nullable=False),
    Column("source_fact_sha256", String(64), nullable=False),
    Column("response_sha256", String(64), nullable=False),
    Column("verified_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(["place_id", "ref_version", "payload_sha256"],
        ["otravel_geo_place_snapshots.place_id", "otravel_geo_place_snapshots.ref_version", "otravel_geo_place_snapshots.payload_sha256"]),
    ForeignKeyConstraint(["key_sha256", "entry_version"],
        ["otravel_geo_directory_cache.key_sha256", "otravel_geo_directory_cache.entry_version"]),
    CheckConstraint("expires_at > verified_at", name="otravel_geo_lease_expiry_order"),
)

_ERRORS = {
    "invalid_key": "Ключ географического справочника не прошёл проверку.",
    "invalid_directory": "Ответ географического справочника не прошёл проверку.",
    "invalid_clock": "Для географического справочника нужно время с часовым поясом.",
    "stale_directory": "Нужен новый актуальный ответ географического справочника.",
    "candidate_not_found": "Выбранное место отсутствует в актуальном ответе источника.",
    "candidate_changed": "Географический кандидат изменился; нужен новый явный выбор.",
    "invalid_snapshot": "Географический снимок не прошёл проверку.",
    "snapshot_conflict": "Эта версия географического снимка уже содержит другие данные.",
    "invalid_version": "Нужна следующая последовательная версия географического снимка.",
    "unknown_snapshot": "Географический снимок не зарегистрирован сервером.",
    "stale_snapshot": "Для нового выбора нужен актуальный подтверждённый географический снимок.",
    "catalog_integrity": "Не удалось подтвердить целостность географического справочника.",
    "catalog_unavailable": "Географический справочник временно недоступен.",
}


class GeoCatalogError(ValueError):
    def __init__(self, code: str):
        self.code = code if isinstance(code, str) and code in _ERRORS else "catalog_integrity"
        self.message = _ERRORS[self.code]
        super().__init__(self.message)


def _canonical(value: Any, *, limit: int) -> bytes:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
            separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        raise GeoCatalogError("invalid_directory") from None
    if len(encoded) > limit:
        raise GeoCatalogError("invalid_directory")
    return encoded


def _sha(value: Any, *, limit: int = MAX_DIRECTORY_BYTES) -> str:
    return hashlib.sha256(_canonical(value, limit=limit)).hexdigest()


def _now(value: datetime | None) -> datetime:
    value = datetime.now(UTC) if value is None else value
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise GeoCatalogError("invalid_clock")
    return value.astimezone(UTC)


def _timestamp(value: Any) -> datetime:
    if value is None:
        raise GeoCatalogError("invalid_clock")
    try:
        return _now(datetime.fromisoformat(value)) if isinstance(value, str) else _now(value)
    except (ValueError, TypeError, OverflowError):
        raise GeoCatalogError("invalid_clock") from None


def _key(value: Any) -> dict:
    from .place_directory import validate_directory_key
    try:
        return validate_directory_key(value)
    except (ValueError, TypeError):
        raise GeoCatalogError("invalid_key") from None


def _envelope(key: dict, value: Any, now: datetime, *, fresh: bool) -> dict:
    from .place_directory import validate_directory_candidate
    if (not isinstance(value, dict) or set(value) != _ENVELOPE or type(value.get("version")) is not int
            or value["version"] != 1 or value.get("provider") != "openmeteo"
            or value.get("kind") != "city_directory" or _key(value.get("key")) != key
            or not isinstance(value.get("data"), dict) or set(value["data"]) != _DATA
            or not isinstance(value.get("response_sha256"), str) or _HEX.fullmatch(value["response_sha256"]) is None):
        raise GeoCatalogError("invalid_directory")
    data = value["data"]
    status, candidates, truncated = data.get("status"), data.get("candidates"), data.get("truncated")
    if (not isinstance(status, str) or status not in {"positive", "ambiguous", "no_match"} or type(truncated) is not bool
            or not isinstance(candidates, list) or len(candidates) > key["count"]):
        raise GeoCatalogError("invalid_directory")
    expected = "no_match" if not candidates else "ambiguous" if len(candidates) > 1 or truncated else "positive"
    if status != expected:
        raise GeoCatalogError("invalid_directory")
    if key["operation"] == "search_city" and len(candidates) == key["count"] and not truncated:
        raise GeoCatalogError("invalid_directory")
    if key["operation"] == "get_place" and truncated:
        raise GeoCatalogError("invalid_directory")
    clean, seen = [], set()
    for candidate in candidates:
        try:
            row = validate_directory_candidate(candidate)
        except (ValueError, TypeError):
            raise GeoCatalogError("invalid_directory") from None
        external_id = row["geographic_source"]["external_id"]
        if (external_id in seen or row["language"] != key["language"]
                or key["country_iso2"] is not None and row["country_iso2"] != key["country_iso2"]
                or key["operation"] == "get_place" and external_id != int(key["query"])):
            raise GeoCatalogError("invalid_directory")
        seen.add(external_id)
        clean.append(row)
    retrieved, expires = _timestamp(value.get("retrieved_at")), _timestamp(value.get("expires_at"))
    ttl = NEGATIVE_TTL_SECONDS if status == "no_match" else DIRECTORY_TTL_SECONDS
    if retrieved > now or not timedelta(0) < expires - retrieved <= timedelta(seconds=ttl):
        raise GeoCatalogError("invalid_directory")
    if fresh and expires <= now:
        raise GeoCatalogError("stale_directory")
    result = {**deepcopy(value), "key": key, "data": {"status": status, "candidates": clean, "truncated": truncated},
        "retrieved_at": retrieved.isoformat(), "expires_at": expires.isoformat()}
    _canonical(result, limit=MAX_DIRECTORY_BYTES)
    return result


def _snapshot(value: Any, *, exact: bool = False) -> CityRefSnapshot:
    try:
        snapshot = CityRefSnapshot.model_validate(value)
        payload = snapshot.model_dump(mode="json")
        if snapshot.ref_version > 2**63 - 1:
            raise ValueError
        _canonical(payload, limit=MAX_SNAPSHOT_BYTES)
        if exact and isinstance(value, dict) and _canonical(value, limit=MAX_SNAPSHOT_BYTES) != _canonical(payload, limit=MAX_SNAPSHOT_BYTES):
            raise ValueError
        return snapshot
    except (ValidationError, ValueError, TypeError, OverflowError):
        raise GeoCatalogError("invalid_snapshot") from None


def _fact_snapshot(fact: dict, version: int) -> CityRefSnapshot:
    fields = {key: deepcopy(value) for key, value in fact.items() if key != "language"}
    return _snapshot({**fields, "place_id": openmeteo_place_id(fact["geographic_source"]["external_id"]),
        "ref_version": version, "provider_bindings": {"liteapi": None, "duffel": None}})


def _physical_fact(fact: dict) -> dict:
    """Locale-independent fields that must describe the same physical city."""
    fields = {key: deepcopy(fact[key]) for key in (
        "geographic_source", "country_iso2", "latitude", "longitude", "timezone", "feature_code")}
    fields["administrative_areas"] = sorted(
        ({"level": row["level"], "external_id": row["external_id"]}
         for row in fact["administrative_areas"]), key=lambda row: row["level"])
    return fields


def _read_snapshot(row) -> CityRefSnapshot:
    if _sha(row["payload"], limit=MAX_SNAPSHOT_BYTES) != row["payload_sha256"]:
        raise GeoCatalogError("catalog_integrity")
    snapshot = _snapshot(row["payload"], exact=True)
    if (snapshot.place_id != row["place_id"] or snapshot.ref_version != row["ref_version"]
            or snapshot.snapshot_sha256() != row["payload_sha256"]):
        raise GeoCatalogError("catalog_integrity")
    return snapshot


class GeoCatalog:
    def __init__(self, engine: AsyncEngine):
        if not isinstance(engine, AsyncEngine) or engine.url.get_backend_name() != "postgresql":
            raise GeoCatalogError("catalog_unavailable")
        self.engine = engine

    async def initialize(self) -> None:
        """Create only these additive tables; no Store metadata/schema changes."""
        try:
            async with self.engine.begin() as conn:
                await self._lock(conn, "schema", "otravel_geo_catalog_v1")
                await conn.run_sync(metadata.create_all)
        except (SQLAlchemyError, OSError):
            raise GeoCatalogError("catalog_unavailable") from None

    async def _lock(self, conn, namespace: str, identity: str) -> None:
        value = int.from_bytes(hashlib.sha256((namespace + "\0" + identity).encode()).digest()[:8], "big", signed=True)
        await conn.execute(select(func.pg_advisory_xact_lock(value)))

    async def _directory(self, conn, key: dict):
        return (await conn.execute(select(directory_entries).where(directory_entries.c.key_sha256 == _sha(key))
            .order_by(directory_entries.c.entry_version.desc()).limit(1))).mappings().first()

    def _read_directory(self, row, key: dict, now: datetime) -> dict:
        if row["cache_key"] != key or _sha(row["payload"]) != row["payload_sha256"]:
            raise GeoCatalogError("catalog_integrity")
        value = _envelope(key, row["payload"], now, fresh=False)
        if (value["data"]["status"] != row["status"] or _timestamp(value["retrieved_at"]) != row["retrieved_at"]
                or _timestamp(value["expires_at"]) != row["expires_at"]):
            raise GeoCatalogError("catalog_integrity")
        return value

    async def get_directory(self, key: dict, *, now: datetime | None = None) -> dict | None:
        key, now = _key(key), _now(now)
        try:
            async with self.engine.connect() as conn:
                row = await self._directory(conn, key)
                if row is None:
                    return None
                value = self._read_directory(row, key, now)
                # Latest is selected BEFORE expiry. Never resurrect older data.
                return value if row["expires_at"] > now else None
        except (SQLAlchemyError, OSError):
            raise GeoCatalogError("catalog_unavailable") from None

    async def put_directory(self, key: dict, envelope: dict, *, now: datetime | None = None) -> dict:
        key, now = _key(key), _now(now)
        value = _envelope(key, envelope, now, fresh=True)
        digest = _sha(value)
        try:
            async with self.engine.begin() as conn:
                await self._lock(conn, "directory", _sha(key))
                previous = await self._directory(conn, key)
                if previous is not None:
                    self._read_directory(previous, key, now)
                    if previous["payload_sha256"] == digest:
                        return deepcopy(value)
                    if _timestamp(value["retrieved_at"]) < previous["retrieved_at"]:
                        raise GeoCatalogError("stale_directory")
                version = previous["entry_version"] + 1 if previous else 1
                await conn.execute(insert(directory_entries).values(key_sha256=_sha(key), entry_version=version,
                    cache_key=key, payload=value, payload_sha256=digest, status=value["data"]["status"],
                    retrieved_at=_timestamp(value["retrieved_at"]), expires_at=_timestamp(value["expires_at"]), stored_at=now))
                return deepcopy(value)
        except (SQLAlchemyError, OSError):
            raise GeoCatalogError("catalog_unavailable") from None

    async def _place(self, conn, place_id: str, version: int | None = None):
        query = select(place_snapshots).where(place_snapshots.c.place_id == place_id)
        if version is not None:
            query = query.where(place_snapshots.c.ref_version == version)
        return (await conn.execute(query.order_by(place_snapshots.c.ref_version.desc()).limit(1))).mappings().first()

    async def _put_snapshot(self, conn, snapshot: CityRefSnapshot, now: datetime) -> None:
        prior = await self._place(conn, snapshot.place_id, snapshot.ref_version)
        if prior is not None:
            saved = _read_snapshot(prior)
            if saved.model_dump(mode="json") != snapshot.model_dump(mode="json"):
                raise GeoCatalogError("snapshot_conflict")
            return
        latest = await self._place(conn, snapshot.place_id)
        if snapshot.ref_version != (latest["ref_version"] + 1 if latest else 1):
            raise GeoCatalogError("invalid_version")
        await conn.execute(insert(place_snapshots).values(place_id=snapshot.place_id, ref_version=snapshot.ref_version,
            payload=snapshot.model_dump(mode="json"), payload_sha256=snapshot.snapshot_sha256(), stored_at=now))

    async def _proof(self, conn, key: dict, external_id: int, expected_source_fact_sha256: str,
                     expected_response_sha256: str | None, now: datetime):
        if (type(external_id) is not int or not 0 < external_id <= 2_147_483_647
                or not isinstance(expected_source_fact_sha256, str) or _HEX.fullmatch(expected_source_fact_sha256) is None
                or expected_response_sha256 is not None and (not isinstance(expected_response_sha256, str)
                    or _HEX.fullmatch(expected_response_sha256) is None)):
            raise GeoCatalogError("candidate_changed")
        # Freeze this latest observation through admission's short DB commit;
        # a concurrent cache write cannot swap a candidate behind the choice.
        await self._lock(conn, "directory", _sha(key))
        row = await self._directory(conn, key)
        if row is None:
            raise GeoCatalogError("stale_directory")
        value = self._read_directory(row, key, now)
        if row["expires_at"] <= now:
            raise GeoCatalogError("stale_directory")
        if expected_response_sha256 is not None and value["response_sha256"] != expected_response_sha256:
            raise GeoCatalogError("candidate_changed")
        found = [candidate for candidate in value["data"]["candidates"]
                 if candidate["geographic_source"]["external_id"] == external_id]
        if len(found) != 1:
            raise GeoCatalogError("candidate_not_found")
        fact = found[0]
        if fact["source_fact_sha256"] != expected_source_fact_sha256:
            raise GeoCatalogError("candidate_changed")
        return row, value, fact

    async def _store_lease(self, conn, snapshot: CityRefSnapshot, row, envelope: dict, fact: dict,
                           verified: datetime, expires: datetime, proof_group: str | None = None) -> None:
        fields = {"place_id": snapshot.place_id, "ref_version": snapshot.ref_version,
            "payload_sha256": snapshot.snapshot_sha256(), "key_sha256": row["key_sha256"],
            "entry_version": row["entry_version"], "source_fact_sha256": fact["source_fact_sha256"],
            "response_sha256": envelope["response_sha256"], "verified_at": verified.isoformat(), "expires_at": expires.isoformat()}
        lease_id = _sha(fields) if proof_group is None else _sha({"lease": fields, "combined_proof": proof_group})
        await conn.execute(insert(verification_leases).values(**{**fields, "lease_id": lease_id,
            "verified_at": verified, "expires_at": expires}).on_conflict_do_nothing(index_elements=["lease_id"]))

    async def _lease(self, conn, snapshot: CityRefSnapshot, row, envelope: dict, fact: dict) -> None:
        expected = _fact_snapshot(fact, snapshot.ref_version)
        if expected.model_dump(mode="json") != snapshot.model_dump(mode="json"):
            raise GeoCatalogError("candidate_changed")
        # Re-admitting a cache hit cannot advance verified_at or extend expiry.
        # Only a new immutable directory observation can produce a new lease.
        verified = row["retrieved_at"]
        await self._store_lease(conn, snapshot, row, envelope, fact, verified,
            verified + timedelta(seconds=IDENTITY_TTL_SECONDS))

    async def put_place(self, ref: CityRefSnapshot | dict, *, now: datetime | None = None,
                        directory_key: dict | None = None, expected_source_fact_sha256: str | None = None,
                        expected_response_sha256: str | None = None) -> CityRefSnapshot:
        """Private immutable registration. Without a source proof it grants no fresh lease."""
        snapshot, now = _snapshot(ref), _now(now)
        key = _key(directory_key) if directory_key is not None else None
        try:
            async with self.engine.begin() as conn:
                await self._lock(conn, "place", snapshot.place_id)
                proof = (await self._proof(conn, key, snapshot.geographic_source.external_id,
                    expected_source_fact_sha256, expected_response_sha256, now)) if key is not None else None
                await self._put_snapshot(conn, snapshot, now)
                if proof is not None:
                    await self._lease(conn, snapshot, *proof)
                return snapshot
        except (SQLAlchemyError, OSError):
            raise GeoCatalogError("catalog_unavailable") from None

    async def admit_place(self, directory_key: dict, external_id: int, *, expected_source_fact_sha256: str,
                          expected_response_sha256: str | None = None, now: datetime | None = None,
                          witness: dict | None = None) -> CityRefSnapshot:
        """Mint/reuse the exact explicitly selected current cached source candidate.

        Session/proposal ownership and CAS remain the caller's responsibility.
        Nothing chooses the first, most populous or nearest result automatically.
        """
        key, now = _key(directory_key), _now(now)
        try:
            place_id = openmeteo_place_id(external_id)
        except ValueError:
            raise GeoCatalogError("invalid_snapshot") from None
        witness_key = None
        if witness is not None:
            if (not isinstance(witness, dict) or set(witness) != {"directory_key", "expected_source_fact_sha256", "expected_response_sha256"}
                    or key["operation"] != "get_place" or key["language"] != "en"
                    or expected_response_sha256 is None):
                raise GeoCatalogError("candidate_changed")
            witness_key = _key(witness["directory_key"])
            if witness_key["language"] != "ru" or witness["expected_response_sha256"] is None:
                raise GeoCatalogError("candidate_changed")
        try:
            async with self.engine.begin() as conn:
                await self._lock(conn, "place", place_id)
                if witness_key is not None:
                    # Both observations are frozen in one short transaction.
                    # Deterministic ordering prevents reciprocal directory-lock
                    # waits; one-proof calls still hold place -> one directory.
                    for identity in sorted({_sha(key), _sha(witness_key)}):
                        await self._lock(conn, "directory", identity)
                row, envelope, fact = await self._proof(conn, key, external_id,
                    expected_source_fact_sha256, expected_response_sha256, now)
                witness_proof = None
                if witness_key is not None:
                    witness_proof = await self._proof(conn, witness_key, external_id,
                        witness["expected_source_fact_sha256"], witness["expected_response_sha256"], now)
                    if _physical_fact(fact) != _physical_fact(witness_proof[2]):
                        raise GeoCatalogError("candidate_changed")
                latest = await self._place(conn, place_id)
                if latest is None:
                    snapshot = _fact_snapshot(fact, 1)
                else:
                    previous = _read_snapshot(latest)
                    proposed = _fact_snapshot(fact, previous.ref_version)
                    snapshot = previous if proposed == previous else _fact_snapshot(fact, previous.ref_version + 1)
                await self._put_snapshot(conn, snapshot, now)
                if witness_proof is None:
                    await self._lease(conn, snapshot, row, envelope, fact)
                else:
                    witness_row, witness_envelope, witness_fact = witness_proof
                    verified = max(row["retrieved_at"], witness_row["retrieved_at"])
                    expires = min(row["retrieved_at"], witness_row["retrieved_at"]) + timedelta(seconds=IDENTITY_TTL_SECONDS)
                    proof_group = _sha({"canonical_payload": snapshot.snapshot_sha256(),
                        "en": [row["key_sha256"], row["entry_version"], fact["source_fact_sha256"], envelope["response_sha256"]],
                        "ru": [witness_row["key_sha256"], witness_row["entry_version"], witness_fact["source_fact_sha256"], witness_envelope["response_sha256"]]})
                    await self._store_lease(conn, snapshot, row, envelope, fact, verified, expires, proof_group)
                    await self._store_lease(conn, snapshot, witness_row, witness_envelope, witness_fact, verified, expires, proof_group)
                return snapshot
        except (SQLAlchemyError, OSError):
            raise GeoCatalogError("catalog_unavailable") from None

    async def get_place(self, place_id: str, ref_version: int) -> CityRefSnapshot | None:
        """Read immutable history even after its verification lease expires."""
        if (not isinstance(place_id, str) or not 0 < len(place_id) <= 128
                or type(ref_version) is not int or not 0 < ref_version < 2**63):
            raise GeoCatalogError("invalid_snapshot")
        try:
            async with self.engine.connect() as conn:
                row = await self._place(conn, place_id, ref_version)
                return _read_snapshot(row) if row is not None else None
        except (SQLAlchemyError, OSError):
            raise GeoCatalogError("catalog_unavailable") from None

    async def verify_places(self, values, *, now: datetime | None = None,
                            require_fresh: bool = True, require_current: bool = True) -> dict[str, CityRefSnapshot]:
        """Exact payload admission, never a substitute for pending-choice ownership.

        Restoring a trusted persisted trip may explicitly request old versions
        with both flags False. Public new selection should keep both True.
        """
        now = _now(now)
        if type(require_fresh) is not bool or type(require_current) is not bool:
            raise GeoCatalogError("invalid_snapshot")
        sequence = list(values.values()) if isinstance(values, dict) else values
        if not isinstance(sequence, (list, tuple)) or len(sequence) > MAX_LOCATION_REFS:
            raise GeoCatalogError("invalid_snapshot")
        snapshots = [_snapshot(value, exact=True) for value in sequence]
        if len({ref.place_id for ref in snapshots}) != len(snapshots):
            raise GeoCatalogError("invalid_snapshot")
        if isinstance(values, dict) and set(values) != {ref.place_id for ref in snapshots}:
            raise GeoCatalogError("invalid_snapshot")
        if not snapshots:
            return {}
        pairs = [(ref.place_id, ref.ref_version) for ref in snapshots]
        ids = [ref.place_id for ref in snapshots]
        latest = (select(place_snapshots.c.place_id, func.max(place_snapshots.c.ref_version).label("latest_version"))
            .where(place_snapshots.c.place_id.in_(ids)).group_by(place_snapshots.c.place_id).subquery())
        lease_query = (select(verification_leases.c.place_id, verification_leases.c.ref_version,
            func.max(verification_leases.c.expires_at).label("lease_expires_at"))
            .where(tuple_(verification_leases.c.place_id, verification_leases.c.ref_version).in_(pairs)))
        if require_fresh:
            # A future observation is not a current proof, even when its expiry
            # is far in the future. Filter BEFORE MAX so a valid older lease
            # remains usable but a future lease cannot bridge an expired gap.
            lease_query = lease_query.where(verification_leases.c.verified_at <= now,
                verification_leases.c.expires_at > now)
        leases = lease_query.group_by(verification_leases.c.place_id, verification_leases.c.ref_version).subquery()
        query = (select(place_snapshots, latest.c.latest_version, leases.c.lease_expires_at)
            .join(latest, latest.c.place_id == place_snapshots.c.place_id)
            .outerjoin(leases, (leases.c.place_id == place_snapshots.c.place_id)
                & (leases.c.ref_version == place_snapshots.c.ref_version))
            .where(tuple_(place_snapshots.c.place_id, place_snapshots.c.ref_version).in_(pairs)))
        try:
            async with self.engine.connect() as conn:
                rows = (await conn.execute(query)).mappings().all()
        except (SQLAlchemyError, OSError):
            raise GeoCatalogError("catalog_unavailable") from None
        indexed = {(row["place_id"], row["ref_version"]): row for row in rows}
        result = {}
        for ref in snapshots:
            row = indexed.get((ref.place_id, ref.ref_version))
            if row is None:
                raise GeoCatalogError("unknown_snapshot")
            stored = _read_snapshot(row)
            if stored.model_dump(mode="json") != ref.model_dump(mode="json"):
                raise GeoCatalogError("snapshot_conflict")
            if (require_current and ref.ref_version != row["latest_version"] or require_fresh
                    and (row["lease_expires_at"] is None or row["lease_expires_at"] <= now)):
                raise GeoCatalogError("stale_snapshot")
            result[ref.place_id] = stored
        return result
