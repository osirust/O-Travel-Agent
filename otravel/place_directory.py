"""Bounded keyless geographic facts, independent of trip edits and suppliers.

Search never selects a city. Only source IDs can enter normalized candidates;
Catalog assigns internal identity/version later. Every cache miss requires an
async admission callback before the one HTTP request. No retries or fallback.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import re
import unicodedata
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

BASE_URL = "https://geocoding-api.open-meteo.com"
MAX_RESPONSE_BYTES = 2_000_000
MAX_QUERY_CHARS = 160
MAX_INFLIGHT = 32
MAX_WAITERS = 128
POSITIVE_TTL_SECONDS = 86400
NEGATIVE_TTL_SECONDS = 900
_ROLES = {"city", "origin", "destination", "stay", "route_end"}
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_CITY = re.compile(r"PPL[A-Z0-9]*\Z")
_FACT_FIELDS = {"geographic_source", "canonical_name", "country_iso2", "country_name", "latitude", "longitude",
    "timezone", "feature_code", "administrative_areas", "source_fact_sha256", "language"}
_MESSAGES = {
    "INVALID_REQUEST": "Параметры поиска места не прошли проверку.",
    "INVALID_RESPONSE": "Географический справочник вернул неподтверждённые данные.",
    "COUNTRY_MISMATCH": "Ответ справочника не соответствует выбранной стране.",
    "CLIENT_CLOSED": "Географический справочник закрыт.",
    "ADMISSION_DENIED": "Обращение к справочнику не разрешено текущим запуском.",
    "BUDGET_EXHAUSTED": "Лимит обращений к справочнику исчерпан.",
    "DEADLINE_EXCEEDED": "Время обращения к справочнику истекло.",
    "BUSY": "Справочник занят; место не выбрано.",
    "CACHE_UNAVAILABLE": "Хранилище географических фактов временно недоступно.",
    "REQUEST_TIMEOUT": "Справочник не ответил за отведённое время.",
    "NETWORK_ERROR": "Не удалось безопасно обратиться к справочнику.",
    "RATE_LIMITED": "Справочник временно ограничил запросы.",
    "HTTP_ERROR": "Справочник отклонил запрос.",
    "UNAVAILABLE": "Географический справочник временно недоступен.",
    "INVALID_CLOCK": "Для справочника требуется корректное время UTC.",
}


class PlaceDirectoryError(ValueError):
    """Fixed codes/text and numeric facts only, never query/body/headers."""
    def __init__(self, code, *, status=None, retryable=False, retry_after_seconds=None):
        self.code = code if isinstance(code, str) and code in _MESSAGES else "INVALID_RESPONSE"
        self.message = _MESSAGES[self.code]
        self.retryable = retryable is True
        self.details = {}
        if type(status) is int and 100 <= status <= 599:
            self.details["http_status"] = status
        if type(retry_after_seconds) is int and 0 < retry_after_seconds <= 86400:
            self.details["retry_after_seconds"] = retry_after_seconds
        super().__init__(self.code)

    def as_dict(self):
        return {"code": self.code, "message": self.message, "retryable": self.retryable, "details": dict(self.details)}


def _fail(code="INVALID_RESPONSE"):
    raise PlaceDirectoryError(code)


def _json_bytes(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
            separators=(",", ":")).encode()
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        _fail()


def _text(value, maximum=160, *, optional=False):
    if value is None and optional:
        return None
    if (type(value) is not str or not 0 < len(value) <= maximum or value.strip() != value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        _fail()
    try:
        value.encode("utf-8")
    except UnicodeError:
        _fail()
    return value


def _source_id(value):
    if type(value) is not int or not 0 < value < 2**31:
        _fail()
    return value


def _finite(value):
    try:
        return type(value) in {int, float} and math.isfinite(value)
    except OverflowError:
        return False


def validate_directory_key(value) -> dict:
    """One canonical bounded key shared by the client and persistent Catalog."""
    required = {"provider", "operation", "role", "country_iso2", "query", "language", "count"}
    if type(value) is not dict or set(value) != required or value["provider"] != "openmeteo":
        _fail("INVALID_REQUEST")
    if (type(value["operation"]) is not str or value["operation"] not in {"search_city", "get_place"}
            or type(value["role"]) is not str or value["role"] not in _ROLES):
        _fail("INVALID_REQUEST")
    if type(value["language"]) is not str or value["language"].lower() not in {"ru", "en"}:
        _fail("INVALID_REQUEST")
    country = value["country_iso2"]
    if country is not None and (type(country) is not str or not re.fullmatch(r"[A-Za-z]{2}", country)):
        _fail("INVALID_REQUEST")
    if type(value["count"]) is not int or not 1 <= value["count"] <= 5:
        _fail("INVALID_REQUEST")
    query = value["query"]
    if type(query) is not str or not 0 < len(query) <= MAX_QUERY_CHARS or any(ord(c) < 32 for c in query):
        _fail("INVALID_REQUEST")
    try:
        query = " ".join(unicodedata.normalize("NFKC", query).casefold().split())
        query.encode("utf-8")
    except UnicodeError:
        _fail("INVALID_REQUEST")
    if value["operation"] == "get_place":
        if not re.fullmatch(r"[1-9][0-9]{0,9}", query) or not 0 < int(query) < 2**31 or value["count"] != 1 or country is not None:
            _fail("INVALID_REQUEST")
    elif not 2 <= len(query) <= MAX_QUERY_CHARS:
        _fail("INVALID_REQUEST")
    return {**value, "query": query, "country_iso2": country.upper() if country is not None else None,
        "language": value["language"].lower()}


def validate_directory_candidate(value) -> dict:
    """Validate raw geographic facts/hash, not external provenance or consent."""
    if type(value) is not dict or set(value) != _FACT_FIELDS:
        _fail()
    source = value["geographic_source"]
    if type(source) is not dict or set(source) != {"provider", "external_id"} or source["provider"] != "openmeteo":
        _fail()
    _source_id(source["external_id"])
    _text(value["canonical_name"])
    if type(value["country_iso2"]) is not str or not re.fullmatch(r"[A-Z]{2}", value["country_iso2"]):
        _fail()
    _text(value["country_name"], optional=True)
    for field, bound in (("latitude", 90), ("longitude", 180)):
        number = value[field]
        if not _finite(number) or not -bound <= number <= bound:
            _fail()
    zone = _text(value["timezone"], 200, optional=True)
    if zone is not None:
        try:
            ZoneInfo(zone)
        except (ValueError, ZoneInfoNotFoundError):
            _fail()
    if (type(value["feature_code"]) is not str or not 3 <= len(value["feature_code"]) <= 16
            or not _CITY.fullmatch(value["feature_code"])):
        _fail()
    areas = value["administrative_areas"]
    if type(areas) is not list or len(areas) > 4:
        _fail()
    levels = []
    for area in areas:
        if type(area) is not dict or set(area) != {"level", "external_id", "name"}:
            _fail()
        if type(area["level"]) is not int or not 1 <= area["level"] <= 4:
            _fail()
        levels.append(area["level"])
        _source_id(area["external_id"])
        _text(area["name"])
    if levels != sorted(set(levels)) or type(value["language"]) is not str or value["language"] not in {"ru", "en"}:
        _fail()
    digest = value["source_fact_sha256"]
    facts = {k: v for k, v in value.items() if k != "source_fact_sha256"}
    if type(digest) is not str or not _HEX.fullmatch(digest) or hashlib.sha256(_json_bytes(facts)).hexdigest() != digest:
        _fail()
    return deepcopy(value)


def _candidate(row, language):
    if (type(row) is not dict or type(row.get("feature_code")) is not str
            or not 1 <= len(row["feature_code"]) <= 16):
        _fail()
    if not _CITY.fullmatch(row["feature_code"]):
        return None
    areas = []
    for level in range(1, 5):
        identifier, name = row.get(f"admin{level}_id"), row.get(f"admin{level}")
        if identifier is not None and type(identifier) is not int:
            _fail()
        if identifier in (None, 0) and name in (None, ""):
            continue
        areas.append({"level": level, "external_id": _source_id(identifier), "name": _text(name)})
    result = {"geographic_source": {"provider": "openmeteo", "external_id": _source_id(row.get("id"))},
        "canonical_name": row.get("name"), "country_iso2": row.get("country_code"),
        "country_name": row.get("country"), "latitude": row.get("latitude"), "longitude": row.get("longitude"),
        "timezone": row.get("timezone"), "feature_code": row["feature_code"], "administrative_areas": areas,
        "language": language}
    result["source_fact_sha256"] = hashlib.sha256(_json_bytes(result)).hexdigest()
    return validate_directory_candidate(result)


def _parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                _fail()
            result[key] = value
        return result
    try:
        body = json.loads(raw, object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        stack, nodes = [(body, 0)], 0
        while stack:
            item, depth = stack.pop()
            nodes += 1
            if depth > 32 or nodes > 40_000:
                _fail()
            if isinstance(item, dict):
                stack.extend((child, depth + 1) for child in item.values())
            elif isinstance(item, list):
                stack.extend((child, depth + 1) for child in item)
            elif isinstance(item, str):
                item.encode("utf-8")
            elif type(item) is float and not math.isfinite(item):
                _fail()
        if type(body) is not dict or body.get("error") is True:
            _fail()
        return body
    except PlaceDirectoryError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        _fail()


def _date(value):
    try:
        date = datetime.fromisoformat(value) if type(value) is str else value
        if not isinstance(date, datetime) or date.utcoffset() is None:
            raise ValueError
        return date.astimezone(UTC)
    except (ValueError, TypeError, OverflowError):
        _fail()


def _cached(envelope, key, now):
    expected = {"version", "provider", "kind", "key", "data", "retrieved_at", "expires_at", "response_sha256"}
    if type(envelope) is not dict or set(envelope) != expected or type(envelope["version"]) is not int or envelope["version"] != 1:
        _fail()
    if envelope["provider"] != "openmeteo" or envelope["kind"] != "city_directory" or validate_directory_key(envelope["key"]) != key:
        _fail()
    data = envelope["data"]
    if type(data) is not dict or set(data) != {"status", "candidates", "truncated"} or type(data["truncated"]) is not bool:
        _fail()
    candidates = data["candidates"]
    if type(candidates) is not list or len(candidates) > key["count"]:
        _fail()
    candidates = [validate_directory_candidate(c) for c in candidates]
    ids = [c["geographic_source"]["external_id"] for c in candidates]
    if len(set(ids)) != len(ids) or any(c["language"] != key["language"]
            or key["country_iso2"] is not None and c["country_iso2"] != key["country_iso2"] for c in candidates):
        _fail()
    if key["operation"] == "get_place" and (data["truncated"] or any(i != int(key["query"]) for i in ids)):
        _fail()
    if key["operation"] == "search_city" and len(candidates) == key["count"] and not data["truncated"]:
        _fail()
    status = "no_match" if not candidates else "ambiguous" if len(candidates) > 1 or data["truncated"] else "positive"
    if data["status"] != status or not isinstance(envelope["response_sha256"], str) or not _HEX.fullmatch(envelope["response_sha256"]):
        _fail()
    retrieved, expiry = _date(envelope["retrieved_at"]), _date(envelope["expires_at"])
    maximum = NEGATIVE_TTL_SECONDS if status == "no_match" else POSITIVE_TTL_SECONDS
    if not retrieved <= now < expiry or not 0 < (expiry - retrieved).total_seconds() <= maximum:
        _fail()
    return deepcopy(envelope)


class MemoryPlaceCache:
    """Optional bounded async cache; persistent Catalog implements the same API."""
    def __init__(self, max_entries=128):
        if type(max_entries) is not int or not 1 <= max_entries <= 1024:
            _fail("INVALID_REQUEST")
        self._entries = OrderedDict()
        self.max_entries = max_entries

    async def get_directory(self, key, *, now):
        saved = self._entries.get(_json_bytes(validate_directory_key(key)))
        if saved is None:
            return None
        try:
            return _cached(saved, validate_directory_key(key), _date(now))
        except PlaceDirectoryError:
            return None

    async def put_directory(self, key, envelope, *, now):
        normalized = validate_directory_key(key)
        saved = _cached(envelope, normalized, _date(now))
        identity = _json_bytes(normalized)
        self._entries[identity] = saved
        self._entries.move_to_end(identity)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)
        return deepcopy(saved)


@dataclass
class _Flight:
    future: asyncio.Future
    waiters: int = 1


class PlaceDirectory:
    """One-loop HTTP client with per-call admission and bounded single-flight."""
    def __init__(self, *, cache=None, transport=None, timeout=4, clock=None):
        if not _finite(timeout) or not 0 < timeout <= 10:
            _fail("INVALID_REQUEST")
        if clock is not None and not callable(clock):
            _fail("INVALID_REQUEST")
        self.cache = cache
        self.timeout = float(timeout)
        self.clock = clock or (lambda: datetime.now(UTC))
        self._closed = False
        self._flights = {}
        selected_transport = transport if transport is not None else httpx.AsyncHTTPTransport(retries=0, trust_env=False)
        self._client = httpx.AsyncClient(transport=selected_transport, trust_env=False, follow_redirects=False,
            timeout=self.timeout)

    def _now(self):
        try:
            return _date(self.clock())
        except Exception:  # noqa: BLE001 - injected clocks must not expose their exception text.
            raise PlaceDirectoryError("INVALID_CLOCK") from None

    def _open(self):
        if self._closed:
            _fail("CLIENT_CLOSED")

    async def close(self):
        self._closed = True
        await self._client.aclose()

    async def search_city(self, query, country_code=None, language="ru", *, before_call, count=5, role="city"):
        key = validate_directory_key({"provider": "openmeteo", "operation": "search_city", "role": role,
            "country_iso2": country_code, "query": query, "language": language, "count": count})
        return await self._resolve(key, before_call)

    async def get_place(self, identifier, language="en", *, before_call, role="city"):
        if type(identifier) is not int or not 0 < identifier < 2**31:
            _fail("INVALID_REQUEST")
        key = validate_directory_key({"provider": "openmeteo", "operation": "get_place", "role": role,
            "country_iso2": None, "query": str(identifier), "language": language, "count": 1})
        return await self._resolve(key, before_call)

    def _resolution(self, saved, cache_kind):
        data = saved["data"]
        status = "NO_MATCH" if data["status"] == "no_match" else "AMBIGUOUS" if data["status"] == "ambiguous" else (
            "RESOLVED" if saved["key"]["operation"] == "get_place" else "CANDIDATES")
        return {"status": status, "candidates": deepcopy(data["candidates"]), "truncated": data["truncated"],
            "selection_required": saved["key"]["operation"] == "search_city" and bool(data["candidates"]),
            "source": {"provider": "openmeteo", "cache": cache_kind, "retrieved_at": saved["retrieved_at"],
                "expires_at": saved["expires_at"], "response_sha256": saved["response_sha256"],
                "cache_key_sha256": hashlib.sha256(_json_bytes(saved["key"])).hexdigest()}}

    async def _cache_get(self, key):
        if self.cache is None:
            return None
        try:
            async with asyncio.timeout(self.timeout):
                saved = await self.cache.get_directory(deepcopy(key), now=self._now())
        except Exception:  # noqa: BLE001 - cache/SQL exceptions may contain connection secrets.
            raise PlaceDirectoryError("CACHE_UNAVAILABLE", retryable=True) from None
        if saved is None:
            return None
        try:
            return _cached(saved, key, self._now())
        except PlaceDirectoryError:
            return None  # Corrupt/stale data is a miss, never trusted identity.

    async def _resolve(self, key, before_call):
        self._open()
        self._now()
        saved = await self._cache_get(key)
        self._open()
        if saved is not None:
            return self._resolution(saved, "hit")
        identity = _json_bytes(key)
        flight = self._flights.get(identity)
        if flight is not None:
            if flight.waiters >= MAX_WAITERS:
                _fail("BUSY")
            flight.waiters += 1
            try:
                async with asyncio.timeout(self.timeout):
                    shared = await asyncio.shield(flight.future)
                return self._resolution(shared, "shared")
            except TimeoutError:
                raise PlaceDirectoryError("REQUEST_TIMEOUT", retryable=True) from None
            finally:
                flight.waiters -= 1
        if len(self._flights) >= MAX_INFLIGHT:
            _fail("BUSY")
        flight = _Flight(asyncio.get_running_loop().create_future())
        self._flights[identity] = flight
        try:
            saved = await self._fetch(key, before_call)
            flight.future.set_result(deepcopy(saved))
            return self._resolution(saved, "miss")
        except BaseException as error:
            if isinstance(error, asyncio.CancelledError):
                flight.future.cancel()
            else:
                safe = error if isinstance(error, PlaceDirectoryError) else PlaceDirectoryError("INVALID_RESPONSE")
                flight.future.set_exception(safe)
                flight.future.exception()  # Retrieve even when there are no waiters.
                if safe is not error:
                    raise safe from None
            raise
        finally:
            if self._flights.get(identity) is flight:
                self._flights.pop(identity)

    async def _fetch(self, key, before_call):
        event = {"provider": "openmeteo", "operation": key["operation"], "method": "GET", "attempt": 1,
            "endpoint": "/v1/search" if key["operation"] == "search_city" else "/v1/get",
            "request_sha256": hashlib.sha256(_json_bytes(key)).hexdigest()}
        if not callable(before_call):
            _fail("ADMISSION_DENIED")
        try:
            async with asyncio.timeout(self.timeout):
                permission = before_call(deepcopy(event))
                if not inspect.isawaitable(permission):
                    _fail("ADMISSION_DENIED")
                remaining = await permission
        except PlaceDirectoryError:
            raise
        except TimeoutError:
            raise PlaceDirectoryError("DEADLINE_EXCEEDED") from None
        except Exception:  # noqa: BLE001 - admission/SQL exceptions may contain private run details.
            raise PlaceDirectoryError("ADMISSION_DENIED") from None
        if not _finite(remaining):
            _fail("ADMISSION_DENIED")
        if remaining <= 0:
            _fail("DEADLINE_EXCEEDED")
        self._open()
        params = {"language": key["language"], "format": "json"}
        if key["operation"] == "search_city":
            params.update(name=key["query"], count=key["count"])
            if key["country_iso2"] is not None:
                params["countryCode"] = key["country_iso2"]
        else:
            params["id"] = key["query"]
        status, raw, retry = await self._wire(event["endpoint"], params, min(self.timeout, remaining))
        if status != 200 and not (status == 404 and key["operation"] == "get_place"):
            code = "RATE_LIMITED" if status == 429 else "UNAVAILABLE" if status >= 500 else "HTTP_ERROR"
            raise PlaceDirectoryError(code, status=status, retryable=status == 429 or status >= 500,
                retry_after_seconds=retry)
        body = _parse(raw) if status == 200 else {}
        if key["operation"] == "search_city":
            rows = body.get("results", [])
            if type(rows) is not list or len(rows) > key["count"]:
                _fail()
            truncated = len(rows) == key["count"]
        else:
            rows, truncated = ([body] if status == 200 else []), False
            if rows and body.get("id") != int(key["query"]):
                _fail()
        candidates = []
        seen_ids = set()
        for row in rows:
            if type(row) is not dict:
                _fail()
            identifier = _source_id(row.get("id"))
            if identifier in seen_ids:
                _fail()
            seen_ids.add(identifier)
            candidate = _candidate(row, key["language"])
            if candidate is None:
                continue
            if key["country_iso2"] is not None and candidate["country_iso2"] != key["country_iso2"]:
                _fail("COUNTRY_MISMATCH")
            candidates.append(candidate)
        self._open()
        data_status = "no_match" if not candidates else "ambiguous" if len(candidates) > 1 or truncated else "positive"
        retrieved = self._now()
        ttl = NEGATIVE_TTL_SECONDS if data_status == "no_match" else POSITIVE_TTL_SECONDS
        saved = {"version": 1, "provider": "openmeteo", "kind": "city_directory", "key": deepcopy(key),
            "data": {"status": data_status, "candidates": candidates, "truncated": truncated},
            "retrieved_at": retrieved.isoformat(), "expires_at": (retrieved + timedelta(seconds=ttl)).isoformat(),
            "response_sha256": hashlib.sha256(raw).hexdigest()}
        if self.cache is not None:
            try:
                async with asyncio.timeout(self.timeout):
                    await self.cache.put_directory(deepcopy(key), deepcopy(saved), now=retrieved)
            except Exception:  # noqa: BLE001 - cache/SQL exceptions may contain connection secrets.
                raise PlaceDirectoryError("CACHE_UNAVAILABLE", retryable=True) from None
        return saved

    async def _wire(self, endpoint, params, timeout):
        try:
            async with asyncio.timeout(timeout):
                async with self._client.stream("GET", BASE_URL + endpoint, params=params,
                        headers={"Accept": "application/json"}, timeout=timeout) as response:
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                            _fail()
                        raw.extend(chunk)
                    hint = response.headers.get("retry-after", "")
                    retry = int(hint) if re.fullmatch(r"[0-9]{1,5}", hint) and 0 < int(hint) <= 86400 else None
                    return response.status_code, bytes(raw), retry
        except PlaceDirectoryError:
            raise
        except (TimeoutError, httpx.TimeoutException):
            raise PlaceDirectoryError("REQUEST_TIMEOUT", retryable=True) from None
        except Exception:  # noqa: BLE001 - transport exceptions must never echo request/query details.
            raise PlaceDirectoryError("NETWORK_ERROR", retryable=True) from None
