"""Server-owned geographic choices; no trip writes, model calls or defaults."""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import re
import unicodedata
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from .domain import MVP_LIMITS
from .geography import parse_destination
from .place_directory import validate_directory_candidate, validate_directory_key
from .place_refs import CityRefSnapshot

OWNER = "place_resolution.v1"
COUNTRY_OWNER = "place_resolution.country.v1"
CHOICE_TTL = 900
_ROLES = {"origin", "destination", "route_end"}
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_ORDINALS = {"первый": 1, "первую": 1, "первое": 1, "первой": 1, "второй": 2, "вторую": 2,
    "второе": 2, "третью": 3, "третий": 3, "третья": 3, "четвертый": 4, "четвертую": 4,
    "пятый": 5, "пятую": 5}
_YES = {"да", "верно", "подтверждаю", "yes"}


class PlaceResolutionError(ValueError):
    pass


def _require(value, code):
    if not value:
        raise PlaceResolutionError(code)


def _sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
        separators=(",", ":")).encode()).hexdigest()


def _key(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().replace("ё", "е").split()).strip(" .!")


def _date(value):
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    _require(isinstance(result, datetime) and result.utcoffset() is not None, "invalid_clock")
    return result.astimezone(UTC)


def _state(state):
    data = state.model_dump(mode="json") if hasattr(state, "model_dump") else deepcopy(state)
    _require(isinstance(data, dict) and isinstance(data.get("trip_id"), str) and 0 < len(data["trip_id"]) <= 128
        and type(data.get("state_version")) is int and data["state_version"] >= 0, "invalid_state")
    stays = data.get("stays", [])
    _require(isinstance(stays, list) and len(stays) <= MVP_LIMITS["stays"], "invalid_state")
    ids = [s.get("id") if isinstance(s, dict) else None for s in stays]
    _require(all(isinstance(i, str) and 0 < len(i) <= 200 for i in ids) and len(set(ids)) == len(ids), "invalid_state")
    return data


def _target(state, role, stay_id):
    _require(isinstance(role, str) and role in _ROLES, "invalid_role")
    if role != "destination":
        _require(stay_id is None, "foreign_stay_target")
        return None
    stays = state.get("stays", [])
    ids = {s["id"] for s in stays}
    aliases, index = [], 1
    while len(aliases) < max(0, MVP_LIMITS["stays"] - len(stays)):
        alias = f"new-stay-{index}"
        if alias not in ids:
            aliases.append(alias)
        index += 1
    if stay_id is not None:
        _require(isinstance(stay_id, str) and (stay_id in ids or stay_id in aliases), "foreign_stay_target")
        return stay_id
    if len(stays) == 1:
        return stays[0]["id"]
    _require(not stays and bool(aliases), "stay_target_required")
    return aliases[0]


def _packet(action, question=None, notice=None, context=None, operations=None, refs=None, error=None):
    result = {"action": action, "operations": deepcopy(operations or []), "resolved_places": deepcopy(refs or {}),
        "question": question, "notice": notice, "pending_context": deepcopy(context)}
    if error:
        result["error"] = {"code": error}
    return result


def _binding(packet):
    return {"field": "place.city_query" if packet["stage"] == "city_query" else "place.resolution",
        "target_id": packet["stay_id"], "place_resolution": deepcopy(packet)}


def _choice_id(packet, candidate):
    return "pc-" + _sha({"resolution_id": packet["resolution_id"], "trip_id": packet["trip_id"],
        "state_version": packet["state_version"], "role": packet["role"], "stay_id": packet["stay_id"],
        "candidate": {k: v for k, v in candidate.items() if k != "candidate_id"}})[:32]


def _read_pending(pending, state, now):
    _require(isinstance(pending, dict), "invalid_pending")
    context = pending.get("context", pending)
    packet = context.get("place_resolution") if isinstance(context, dict) else None
    _require(isinstance(packet, dict) and packet.get("owner") == OWNER and packet.get("trip_id") == state["trip_id"]
        and type(packet.get("state_version")) is int and packet["state_version"] == state["state_version"]
        and isinstance(packet.get("resolution_id"), str) and re.fullmatch(r"pr-[0-9a-f]{32}", packet["resolution_id"]), "foreign_or_stale_choice")
    _require(_date(packet["expires_at"]) > now, "expired_choice")
    _require(_target(state, packet["role"], packet.get("stay_id")) == packet.get("stay_id"), "foreign_stay_target")
    candidates = packet.get("candidates")
    _require(isinstance(candidates, list) and len(candidates) <= 5, "invalid_pending")
    ids = [c.get("candidate_id") if isinstance(c, dict) else None for c in candidates]
    _require(all(isinstance(i, str) and re.fullmatch(r"pc-[0-9a-f]{32}", i) for i in ids)
        and len(set(ids)) == len(ids), "invalid_pending")
    _require(all(c["candidate_id"] == _choice_id(packet, c) for c in candidates), "candidate_changed")
    return deepcopy(packet)


def interpret_short_reply(pending, text, state, *, now=None):
    """Only a bounded explicit choice, never an ordinary new instruction."""
    no = {"selection": None, "question": None}
    if not isinstance(text, str) or not 0 < len(text) <= 200 or any(c in text for c in "?;\n\r"):
        return no
    try:
        data = _state(state)
        packet = _read_pending(pending, data, _date(now) if now is not None else datetime.now(UTC))
    except (ValueError, KeyError, TypeError):
        return {"selection": None, "question": "Выбор места устарел или относится к другой поездке. Укажите направление заново."}
    if packet["stage"] not in {"country_choice", "city_choice"}:
        return no
    candidates, token = packet["candidates"], _key(text)
    chosen = None
    if token in _YES:
        if len(candidates) != 1:
            return {"selection": None, "question": "Какой именно вариант выбрать? Укажите его номер или идентификатор."}
        chosen = candidates[0]
    else:
        exact = [c for c in candidates if token == c["candidate_id"].casefold()]
        source = token.removeprefix("id:").strip()
        by_source = [c for c in candidates if source == str(c["source_id"]).casefold()]
        ordinal = _ORDINALS.get(token)
        if token.isascii() and token.isdigit():
            ordinal = int(token)
        if ordinal is not None and 1 <= ordinal <= len(candidates):
            chosen = candidates[ordinal - 1]
            if by_source and by_source[0]["candidate_id"] != chosen["candidate_id"]:
                return {"selection": None, "question": "Уточните: это номер варианта или ID места? Для ID напишите id: перед ним."}
        elif len(exact) == 1:
            chosen = exact[0]
        elif len(by_source) == 1:
            chosen = by_source[0]
    if chosen is None:
        return no
    return {"selection": {"resolution_id": packet["resolution_id"], "candidate_id": chosen["candidate_id"]}, "question": None}


class PlaceResolutionCoordinator:
    def __init__(self, catalog, directory, travel, clock=None):
        self.catalog, self.directory, self.travel = catalog, directory, travel
        self.clock = clock or (lambda: datetime.now(UTC))
        self._countries = None
        self._country_lock = asyncio.Lock()

    def _now(self):
        return _date(self.clock())

    def _new(self, state, request, stage, expiry=None):
        now = self._now()
        return {"owner": OWNER, "trip_id": state["trip_id"], "state_version": state["state_version"],
            "resolution_id": "pr-" + uuid4().hex, "role": request["role"], "stay_id": request.get("stay_id"),
            "query": request["query"], "country_ref": deepcopy(request.get("country_ref")), "stage": stage,
            "candidates": [], "expires_at": min(now + timedelta(seconds=CHOICE_TTL), expiry).isoformat()
                if expiry else (now + timedelta(seconds=CHOICE_TTL)).isoformat()}

    def _country_ref(self, state, request, country, metadata):
        return {"owner": COUNTRY_OWNER, "trip_id": state["trip_id"], "state_version": state["state_version"],
            "role": request["role"], "stay_id": request.get("stay_id"), "country_iso2": country["country_iso2"],
            "name": country["name"], "fact_sha256": _sha(country), "source_sha256": metadata["source_sha256"],
            "retrieved_at": metadata["retrieved_at"], "expires_at": metadata["expires_at"]}

    def _checked_country_ref(self, ref, state, request):
        _require(isinstance(ref, dict) and ref.get("owner") == COUNTRY_OWNER and ref.get("trip_id") == state["trip_id"]
            and type(ref.get("state_version")) is int and ref["state_version"] == state["state_version"]
            and ref.get("role") == request["role"] and ref.get("stay_id") == request.get("stay_id"), "stale_country_ref")
        code, name = ref.get("country_iso2"), ref.get("name")
        _require(isinstance(code, str) and re.fullmatch(r"[A-Z]{2}", code) and isinstance(name, str)
            and 0 < len(name) <= 160 and ref.get("fact_sha256") == _sha({"country_iso2": code, "name": name})
            and isinstance(ref.get("source_sha256"), str) and _HEX.fullmatch(ref["source_sha256"])
            and _date(ref["retrieved_at"]) <= self._now() < _date(ref["expires_at"]), "stale_country_ref")
        return code

    async def _list_countries(self, before_call):
        now = self._now()
        async with self._country_lock:
            if self._countries and _date(self._countries["expires_at"]) > now:
                return deepcopy(self._countries)
            event = {"provider": "liteapi", "method": "GET", "endpoint": "/v3.0/data/countries",
                "operation": "country_directory", "attempt": 1}
            permission = before_call(deepcopy(event))
            _require(inspect.isawaitable(permission), "admission_denied")
            remaining = await permission
            _require(type(remaining) in {int, float} and math.isfinite(remaining) and remaining > 0, "admission_denied")
            async with asyncio.timeout(min(4, remaining)):
                rows = await self.travel.list_countries()
            _require(isinstance(rows, list) and 0 < len(rows) <= 300, "invalid_countries")
            seen, clean = set(), []
            for row in rows:
                _require(isinstance(row, dict) and set(row) == {"country_iso2", "name"}, "invalid_countries")
                code, name = row["country_iso2"], row["name"]
                _require(isinstance(code, str) and re.fullmatch(r"[A-Z]{2}", code) and code not in seen
                    and isinstance(name, str) and 0 < len(name) <= 160 and name.strip() == name
                    and not any(unicodedata.category(c).startswith("C") for c in name), "invalid_countries")
                seen.add(code); clean.append(deepcopy(row))
            retrieved = self._now()
            self._countries = {"rows": clean, "retrieved_at": retrieved.isoformat(),
                "expires_at": (retrieved + timedelta(hours=24)).isoformat(), "source_sha256": _sha(clean)}
            return deepcopy(self._countries)

    def _city_question(self, packet):
        choices = []
        for index, candidate in enumerate(packet["candidates"], 1):
            areas = ", ".join(a["name"] for a in candidate.get("administrative_areas", []))
            choices.append(f"{index}. {candidate['label']} ({candidate['country_iso2']}" + (f", {areas}" if areas else "") + ")")
        return "Выберите конкретное место по номеру или ID: " + "; ".join(choices) + "."

    def _ask_city(self, state, request, ref):
        packet = self._new(state, {**request, "country_ref": ref}, "city_query", _date(ref["expires_at"]))
        return _packet("ask", question=f"В какой город страны «{ref['name']}» хотите поехать?",
            context=_binding(packet))

    async def resolve(self, request, state, *, before_call):
        """Return choices; no setter is produced until explicit city selection."""
        try:
            data = _state(state)
            _require(isinstance(request, dict) and set(request) <= {"kind", "query", "role", "stay_id", "country_iso2", "country_ref", "source_text"}, "invalid_request")
            req = deepcopy(request)
            _require(req.get("kind") in {"country", "city"} and isinstance(req.get("query"), str)
                and 2 <= len(req["query"]) <= 160 and not any(ord(c) < 32 for c in req["query"]), "invalid_request")
            req["stay_id"] = _target(data, req.get("role"), req.get("stay_id"))
            hint = req.get("country_iso2")
            _require(hint is None or isinstance(hint, str) and re.fullmatch(r"[A-Z]{2}", hint), "invalid_country_hint")
            if req["kind"] == "country":
                metadata = await self._list_countries(before_call)
                country_alias = parse_destination(req["query"])["requested_destination"]
                code = country_alias.get("country_code") if country_alias.get("kind") == "country" else None
                matches = [r for r in metadata["rows"] if _key(req["query"]) in {_key(r["name"]), r["country_iso2"].casefold()}
                    or r["country_iso2"] == code]
                hinted = [r for r in metadata["rows"] if r["country_iso2"] == hint]
                if len(matches) == 1 and (hint is None or hint == matches[0]["country_iso2"]):
                    return self._ask_city(data, req, self._country_ref(data, req, matches[0], metadata))
                candidates = matches + [r for r in hinted if r not in matches]
                if not candidates:
                    return _packet("no_match", question="Какую страну вы имеете в виду? Укажите полное название или ISO-2 код.")
                _require(len(candidates) <= 5, "too_many_country_matches")
                packet = self._new(data, req, "country_choice", _date(metadata["expires_at"]))
                packet["country_source"] = {k: metadata[k] for k in ("retrieved_at", "expires_at", "source_sha256")}
                for row in candidates:
                    packet["candidates"].append({"kind": "country",
                        "source_id": row["country_iso2"], "country_iso2": row["country_iso2"], "label": row["name"],
                        "fact_sha256": _sha(row), "response_sha256": metadata["source_sha256"], "key": None})
                for candidate in packet["candidates"]:
                    candidate["candidate_id"] = _choice_id(packet, candidate)
                return _packet("ask", question=self._city_question(packet),
                    notice="Подтвердите страну: предложенный код сам по себе не определяет ваш выбор.", context=_binding(packet))
            country = self._checked_country_ref(req["country_ref"], data, req) if req.get("country_ref") is not None else None
            # A model's hint is display-only until an owned country choice exists.
            result = await self.directory.search_city(req["query"], country, "ru", before_call=before_call, role=req["role"])
            if not result.get("candidates"):
                packet = self._new(data, req, "city_query")
                return _packet("no_match", question="Не удалось подтвердить город. Уточните название, страну или регион.", context=_binding(packet))
            key = validate_directory_key({"provider": "openmeteo", "operation": "search_city", "role": req["role"],
                "country_iso2": country, "query": req["query"], "language": "ru", "count": 5})
            source = result["source"]
            packet = self._new(data, req, "city_choice", _date(source["expires_at"]))
            facts = [validate_directory_candidate(c) for c in result["candidates"]]
            _require(len(facts) <= 5 and isinstance(source.get("response_sha256"), str) and _HEX.fullmatch(source["response_sha256"]), "invalid_directory")
            envelope = {"version": 1, "provider": "openmeteo", "kind": "city_directory", "key": key,
                "data": {"status": "ambiguous" if len(facts) > 1 or result["truncated"] else "positive",
                    "candidates": facts, "truncated": result["truncated"]},
                "retrieved_at": source["retrieved_at"], "expires_at": source["expires_at"], "response_sha256": source["response_sha256"]}
            await self.catalog.put_directory(key, envelope, now=self._now())
            for fact in facts:
                packet["candidates"].append({"kind": "city",
                    "source_id": fact["geographic_source"]["external_id"], "fact_sha256": fact["source_fact_sha256"],
                    "response_sha256": source["response_sha256"], "key": key, "label": fact["canonical_name"],
                    "country_iso2": fact["country_iso2"], "country_name": fact["country_name"],
                    "administrative_areas": deepcopy(fact["administrative_areas"])})
            for candidate in packet["candidates"]:
                candidate["candidate_id"] = _choice_id(packet, candidate)
            return _packet("ask", question=self._city_question(packet), context=_binding(packet),
                notice="Показаны проверенные географические кандидаты; отели и билеты ещё не искались.")
        except Exception as error:  # noqa: BLE001 - directory/cache/callback exceptions may contain private data.
            code = str(error) if type(error) is PlaceResolutionError else "resolution_unavailable"
            question = "Для какой остановки выбрать город? Укажите её точный ID." if code == "stay_target_required" else "Не удалось завершить выбор места. Уточните направление или повторите позже."
            return _packet("ask" if isinstance(error, PlaceResolutionError) else "unavailable", question=question, error=code)

    async def _proof(self, candidate):
        key = validate_directory_key(candidate["key"])
        envelope = await self.catalog.get_directory(key, now=self._now())
        _require(isinstance(envelope, dict) and envelope.get("response_sha256") == candidate["response_sha256"], "source_changed")
        found = [validate_directory_candidate(f) for f in envelope["data"]["candidates"]
            if f.get("geographic_source", {}).get("external_id") == candidate["source_id"]]
        _require(len(found) == 1 and found[0]["source_fact_sha256"] == candidate["fact_sha256"], "source_changed")
        _require(candidate["label"] == found[0]["canonical_name"] and candidate["country_iso2"] == found[0]["country_iso2"]
            and candidate.get("country_name") == found[0]["country_name"]
            and candidate.get("administrative_areas") == found[0]["administrative_areas"], "source_changed")
        return found[0]

    async def select(self, selection, pending, state, *, before_call):
        try:
            data = _state(state)
            packet = _read_pending(pending, data, self._now())
            if isinstance(selection, str):
                answer = interpret_short_reply(pending, selection, data, now=self._now())
                if answer["selection"] is None:
                    return _packet("ask", question=answer["question"] or self._city_question(packet), context=_binding(packet))
                selection = answer["selection"]
            _require(isinstance(selection, dict) and set(selection) == {"resolution_id", "candidate_id"}
                and selection["resolution_id"] == packet["resolution_id"], "foreign_selection")
            chosen = [c for c in packet["candidates"] if c["candidate_id"] == selection["candidate_id"]]
            _require(len(chosen) == 1, "foreign_selection")
            candidate = chosen[0]
            req = {"role": packet["role"], "stay_id": packet["stay_id"], "query": packet["query"]}
            if packet["stage"] == "country_choice":
                row = {"country_iso2": candidate["country_iso2"], "name": candidate["label"]}
                _require(candidate["kind"] == "country" and candidate["source_id"] == row["country_iso2"]
                    and candidate["fact_sha256"] == _sha(row)
                    and candidate["response_sha256"] == packet["country_source"]["source_sha256"], "source_changed")
                return self._ask_city(data, req, self._country_ref(data, req, row, packet["country_source"]))
            _require(packet["stage"] == "city_choice" and candidate["kind"] == "city", "invalid_selection")
            russian = await self._proof(candidate)
            english_result = await self.directory.get_place(candidate["source_id"], "en", before_call=before_call, role=packet["role"])
            english = [validate_directory_candidate(c) for c in english_result.get("candidates", [])]
            _require(len(english) == 1, "source_changed")
            english = english[0]
            physical = lambda f: (f["geographic_source"], f["country_iso2"], f["latitude"], f["longitude"], f["timezone"], f["feature_code"],
                [(a["level"], a["external_id"]) for a in f["administrative_areas"]])
            _require(physical(russian) == physical(english), "source_changed")
            await self._proof(candidate)  # Recheck shown RU after the separate EN HTTP.
            _read_pending(pending, data, self._now())
            key = validate_directory_key({"provider": "openmeteo", "operation": "get_place", "role": packet["role"],
                "country_iso2": None, "query": str(candidate["source_id"]), "language": "en", "count": 1})
            source = english_result["source"]
            envelope = {"version": 1, "provider": "openmeteo", "kind": "city_directory", "key": key,
                "data": {"status": "positive", "candidates": [english], "truncated": False},
                "retrieved_at": source["retrieved_at"], "expires_at": source["expires_at"], "response_sha256": source["response_sha256"]}
            await self.catalog.put_directory(key, envelope, now=self._now())
            snapshot = await self.catalog.admit_place(key, candidate["source_id"],
                expected_source_fact_sha256=english["source_fact_sha256"], expected_response_sha256=source["response_sha256"], now=self._now(),
                witness={"directory_key": candidate["key"], "expected_source_fact_sha256": candidate["fact_sha256"],
                    "expected_response_sha256": candidate["response_sha256"]})
            ref = CityRefSnapshot.model_validate(snapshot)
            _require(ref.canonical_name == english["canonical_name"] and ref.country_iso2 == english["country_iso2"], "source_changed")
            payload = ref.model_dump(mode="json")
            _read_pending(pending, data, self._now())
            existing = data.get("location_refs", {}).get(ref.place_id)
            _require(existing is None or existing == payload, "existing_ref_revision_changed")
            value = {"city": ref.canonical_name, "place_id": ref.place_id}
            if packet["role"] == "origin":
                operation = {"type": "set_origin", "value": value}
            elif packet["role"] == "route_end":
                operation = {"type": "set_route_policy", "value": {"kind": "open_jaw", "end_city": ref.canonical_name, "end_place_id": ref.place_id}}
            else:
                previous = next((s for s in data.get("stays", []) if s["id"] == packet["stay_id"]), None)
                if previous and (previous.get("place_id") != ref.place_id or previous.get("city") != ref.canonical_name):
                    value["hotel_id"] = None
                operation = {"type": "upsert_stay", "target_id": packet["stay_id"], "value": value}
            return _packet("selected", notice=f"Выбрано место «{candidate['label']}» ({candidate['country_iso2']}).",
                operations=[operation], refs={ref.place_id: payload})
        except Exception as error:  # noqa: BLE001 - catalog/transport errors must never expose raw details.
            code = str(error) if type(error) is PlaceResolutionError else "selection_unavailable"
            return _packet("ask" if isinstance(error, PlaceResolutionError) else "unavailable",
                question="Выбор места устарел или не подтверждён. Укажите направление заново.", error=code)
