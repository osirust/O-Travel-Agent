"""GigaChat gateway/dispatch/isolated PG with intercepted HTTP, never live keys."""

import base64
import hashlib
import json
import time
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from jsonschema import Draft202012Validator
from test_api import _headers, _ModelPolicyStore, _session, _snapshot, _wait
from test_api import api_factory as controlled_api_factory
from test_local_model_budget import ledger as controlled_ledger
from test_local_model_budget import new_run, reserve, seed, started
from test_slot_continuation import _message

from otravel import providers
from otravel.answering import READONLY_MODE
from otravel.api import _clean
from otravel.config import Settings, provider_status
from otravel.domain import sample_trip
from otravel.gigachat_client import BASE_URL, OAUTH_URL
from otravel.harness import Harness
from otravel.llm_contract import build_operation_schema
from otravel.providers import LLMGateway, ProviderError

api_factory = controlled_api_factory
ledger = controlled_ledger
MODEL = "GigaChat-3-Ultra"
AUTH = base64.b64encode(b"synthetic-integration-client:synthetic-integration-secret").decode()
TOKEN = "synthetic.gigachat.integration.access-token"
PRIVATE = "PRIVATE_GIGA_ACCOUNT_BODY_NOT_PUBLIC"
CANONICAL = {"operations": [{"type": "update_traveler", "target_id": "child-1", "value": {"age": 4}}],
    "control": {"action": "continue"}, "reply": "Возраст ребёнка изменён.", "unknowns": [], "conflicts": [],
    "speech_act": "CHANGE", "information_subject": None}


@pytest.fixture(autouse=True)
def synthetic_config_only(monkeypatch):
    for name in providers._LLM_CREDENTIAL_FIELDS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "gigachat")
    monkeypatch.setenv("GIGACHAT_AUTH_KEY", AUTH)
    monkeypatch.setenv("GIGACHAT_FREEMIUM_CONFIRMED", "true")
    monkeypatch.setenv("GIGACHAT_MODEL", MODEL)
    monkeypatch.delenv("GIGACHAT_CA_FILE", raising=False)
    monkeypatch.setattr(providers, "_SECRETS", set())


def wire(canonical=CANONICAL):
    value = deepcopy(canonical)
    value["operations"] = [{"type": op["type"], "target_id": op.get("target_id"),
        "value_json": json.dumps(op["value"], ensure_ascii=False)} for op in canonical["operations"]]
    return value


def gateway(calls, *, canonical=CANONICAL, completion_status=200, payload=None, **overrides):
    def handler(request):
        calls.append(request)
        if str(request.url) == OAUTH_URL:
            assert request.headers["authorization"] == "Basic " + AUTH
            assert request.content == b"scope=GIGACHAT_API_PERS"
            return httpx.Response(200, json={"access_token": TOKEN, "expires_at": int(time.time() * 1000) + 1_800_000})
        assert str(request.url) == BASE_URL + "/chat/completions", "No fallback, inventory or unreviewed endpoint"
        assert request.headers["authorization"] == "Bearer " + TOKEN
        if completion_status != 200:
            return httpx.Response(completion_status, headers={"retry-after": "2"},
                json={"error": {"message": PRIVATE + AUTH + TOKEN, "code": PRIVATE, "reasoning": PRIVATE}})
        actual = wire(canonical) if payload is None else deepcopy(payload)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(actual, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 151, "completion_tokens": 61, "total_tokens": 212}})
    return LLMGateway({"LLM_PROVIDER": "gigachat", "GIGACHAT_AUTH_KEY": AUTH,
        "GIGACHAT_MODEL": MODEL, "GIGACHAT_FREEMIUM_CONFIRMED": True,
        "http_transport": httpx.MockTransport(handler), **overrides})


@pytest.mark.asyncio
async def test_gateway_oauth_strict_wire_and_canonical_extraction_preserve_authority_and_usage():
    calls = []
    g = gateway(calls)
    schema = build_operation_schema(sample_trip())
    try:
        answer = await g.extract_operations({"current_message": "Ребёнку четыре года.", "__response_schema": schema,
            "authorization": AUTH, "GIGACHAT_AUTH_KEY": AUTH})
        assert dict(answer) == CANONICAL and len(calls) == 2
        body = json.loads(calls[1].content)
        assert body["model"] == MODEL and body["stream"] is False
        assert body["response_format"]["type"] == "json_schema" and body["response_format"]["strict"] is True
        Draft202012Validator(body["response_format"]["schema"]).validate(wire())
        assert "Wire format for this provider" in body["messages"][0]["content"]
        assert all(secret not in calls[1].content.decode() for secret in (AUTH, TOKEN))
        assert Draft202012Validator(schema).is_valid(answer)
        meta = answer.call_metadata
        assert meta["provider"] == "gigachat" and meta["model"] == MODEL
        assert meta["usage"] == {"input_tokens": 151, "output_tokens": 61, "total_tokens": 212}
        assert meta["structured_transport"]["format"] == "gigachat_strict_operations_v1"
        assert meta["structured_transport"]["validated_output_representation"] == "decoded_canonical_contract"
        assert meta["structured_transport"]["wire_final_sha256"] == hashlib.sha256(json.dumps(wire(), ensure_ascii=False).encode()).hexdigest()
        assert {AUTH, TOKEN} <= providers._SECRETS
        assert all(secret not in json.dumps(meta) for secret in (AUTH, TOKEN))
    finally:
        await g.close()


@pytest.mark.asyncio
async def test_readonly_giga_sections_become_server_noop_without_operations_or_consent():
    calls = []
    g = gateway(calls, payload={"sections": ["party", "budget", "orders"]})
    try:
        result = await g.extract_operations({"__response_mode": READONLY_MODE, "current_message": "Напомни состав, бюджет и заказ."})
        assert result["operations"] == [] and result["control"] == {"action": "none"}
        assert result["speech_act"] == "ASK" and result["reply"] == "party,budget,orders"
        assert result.call_metadata["structured_transport"]["format"] == "gigachat_strict_readonly_v1"
        assert len(calls) == 2
    finally:
        await g.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("settings,code", [({"GIGACHAT_AUTH_KEY": ""}, "CONFIG_MISSING"),
    ({"GIGACHAT_FREEMIUM_CONFIRMED": False}, "CONFIG_BLOCKED"), ({"GIGACHAT_FREEMIUM_CONFIRMED": "no"}, "CONFIG_BLOCKED"),
    ({"GIGACHAT_MODEL": "unapproved-paid-model"}, "CONFIG_BLOCKED"), ({"GIGACHAT_MODEL": ""}, "CONFIG_BLOCKED")])
async def test_missing_key_unconfirmed_free_or_unapproved_model_never_dispatches_oauth(settings, code):
    calls = []
    g = gateway(calls, **settings)
    try:
        with pytest.raises(ProviderError) as error:
            await g.extract_operations({"__response_schema": build_operation_schema(sample_trip())})
        assert error.value.code == code and error.value.provider == "gigachat" and not calls
    finally:
        await g.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["null_target", "foreign_id", "string_age"])
async def test_bad_wire_or_canonical_operation_is_rejected_once_with_correct_provider_message(mutation):
    value = deepcopy(CANONICAL)
    if mutation == "null_target":
        value["operations"][0]["target_id"] = None
    elif mutation == "foreign_id":
        value["operations"][0]["target_id"] = "foreign-child"
    else:
        value["operations"][0]["value"]["age"] = "4"
    calls = []
    g = gateway(calls, canonical=value)
    try:
        with pytest.raises(ProviderError) as error:
            await g.extract_operations({"__response_schema": build_operation_schema(sample_trip())})
        assert error.value.code == ("INVALID_LLM_OPERATION" if mutation == "string_age" else "INVALID_LLM_RESPONSE")
        assert error.value.provider == "gigachat" and "Groq" not in error.value.message
        assert len(calls) == 2 and g.last_call == {}
        assert g.call_metadata_for_current_call()["usage"]["total_tokens"] == 212
    finally:
        await g.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("secret", [AUTH, TOKEN])
async def test_registered_credential_echo_in_valid_model_contract_is_rejected_before_return(secret):
    calls = []
    g = gateway(calls, canonical={**deepcopy(CANONICAL), "reply": "Не выводить " + secret})
    try:
        with pytest.raises(ProviderError) as error:
            await g.extract_operations({"__response_schema": build_operation_schema(sample_trip())})
        assert error.value.code == "INVALID_LLM_RESPONSE" and len(calls) == 2
        assert secret not in json.dumps(error.value.as_dict()) and g.last_call == {}
    finally:
        await g.close()


def test_api_public_clean_removes_runtime_registered_access_token_and_auth_field():
    providers._SECRETS.add(TOKEN)
    assert _clean({"note": "echo " + TOKEN, "gigachat_auth_key": AUTH}) == {"note": "echo [REDACTED]"}


def test_settings_status_keep_synthetic_giga_identity_and_independent_daily_budget(monkeypatch):
    monkeypatch.setenv("MAX_LLM_CALLS_PER_DAY", "3")
    monkeypatch.setenv("MAX_GIGACHAT_LLM_CALLS_PER_DAY", "731")
    config = Settings.from_env()
    assert config.llm_mode == "gigachat" and config.max_llm_calls_per_day == 3
    assert config.max_gigachat_llm_calls_per_day == 731
    assert config.provider_settings()["GIGACHAT_AUTH_KEY"] == AUTH
    status = provider_status()
    assert status["_runtime"] == {"llm_provider": "gigachat", "model": MODEL}
    assert status["gigachat"]["configured"] is True and status["gigachat"]["freemium_confirmed"] is True
    assert status["gigachat"]["paid_fallback"] is False and status["gigachat"]["parallel_flows"] == 1
    assert AUTH not in json.dumps(status)


def harness_setup(calls, *, daily=40, status=200, **overrides):
    g = gateway(calls, completion_status=status, **overrides)
    store = _ModelPolicyStore()
    async def record(_sid, _rid, limit, provider):
        store.reservations.append({"provider": provider, "limit": limit})
        return limit > 0
    store.reserve_model_call = record
    h = Harness(Settings("unused", "gigachat", 1, 8000, 2, 2,
        max_gigachat_llm_calls_per_day=daily), store, g, None)
    session = {"session_id": "policy-session", "state_version": 0,
        "state": sample_trip().model_dump(mode="json"), "pending_question": None}
    run = {"run_id": "policy-run", "input_message_id": "policy-message", "input_text": "Измени возраст ребёнка на четыре года."}
    return g, store, h, session, run


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 403, 429])
async def test_harness_dispatch_uses_giga_budget_identity_one_generation_and_no_router_fallback(status):
    calls = []
    g, store, h, session, run = harness_setup(calls, daily=731, status=status,
        OPENROUTER_API_KEY="synthetic-unselected-router", OPENROUTER_MODEL="synthetic/backup:free",
        OPENROUTER_BACKUP_MODEL="synthetic/other:free")
    try:
        if status == 200:
            result, meta = await h._extract(session, run)
            assert result.operations[0].target_id == "child-1"
            assert meta["provider"] == "gigachat" and meta["model"] == MODEL
        else:
            with pytest.raises(ProviderError) as error:
                await h._extract(session, run)
            assert error.value.provider == "gigachat"
            assert len(error.value.llm_meta["attempts"]) == 1
            assert all(secret not in json.dumps(error.value.as_dict()) for secret in (AUTH, TOKEN, PRIVATE))
        assert store.reservations == [{"provider": "gigachat", "limit": 731}]
        assert [request.url.host for request in calls] == ["ngw.devices.sberbank.ru", "api.giga.chat"]
    finally:
        await g.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("settings", [{"GIGACHAT_AUTH_KEY": ""}, {"GIGACHAT_FREEMIUM_CONFIRMED": False}])
async def test_harness_missing_or_unconfirmed_config_stops_before_budget_and_oauth(settings):
    calls = []
    g, store, h, session, run = harness_setup(calls, **settings)
    try:
        with pytest.raises(ProviderError) as error:
            await h._extract(session, run)
        assert error.value.code in {"CONFIG_MISSING", "CONFIG_BLOCKED"}
        assert store.reservations == [] and calls == []
    finally:
        await g.close()


@pytest.mark.asyncio
async def test_harness_zero_daily_budget_never_fetches_oauth_token():
    calls = []
    g, store, h, session, run = harness_setup(calls, daily=0)
    try:
        with pytest.raises(ProviderError) as error:
            await h._extract(session, run)
        assert error.value.code == "MODEL_BUDGET_EXHAUSTED" and not calls
        assert store.reservations == [{"provider": "gigachat", "limit": 0}]
    finally:
        await g.close()


def pg_settings(client, *, daily=1000):
    app = client._transport.app
    assert app.state.store.engine.url.database == "otravel_test"
    app.state.harness.settings = replace(app.state.harness.settings, llm_mode="gigachat", max_gigachat_llm_calls_per_day=daily)
    return app


@pytest.mark.asyncio
async def test_pg_worker_giga_operation_commits_one_revision_and_one_provider_reservation(api_factory):
    calls = []
    async with api_factory(gateway(calls)) as (client, _):
        app = pg_settings(client)
        sid, token, before = await _session(client, sample_trip().model_dump(mode="json"))
        run, after = await _message(client, sid, token, "Измени возраст нашего ребёнка на четыре года, остальных оставь.")
        assert run["status"] == "completed" and after["state_version"] == before["state_version"] + 1
        people = {p["id"]: p for p in after["trip"]["travelers"]}
        old = {p["id"]: p for p in before["trip"]["travelers"]}
        assert people["child-1"]["age"] == 4 and all(people[k] == value for k, value in old.items() if k != "child-1")
        assert after["trip"]["budget"] == before["trip"]["budget"]
        events = await app.state.store.list_events(sid)
        reserved = [e for e in events if e["event_type"] == "MODEL_CALL_STARTED"]
        assert len(reserved) == 1 and reserved[0]["payload"]["provider"] == "gigachat"
        received = [e for e in events if e["event_type"] == "MODEL_RESPONSE_RECEIVED"]
        assert len(received) == 1 and received[0]["payload"]["structured_transport"]["format"] == "gigachat_strict_operations_v1"
        assert len(calls) == 2 and await app.state.store.list_orders(sid) == []
        for path in (f"/api/v1/runs/{run['run_id']}", f"/api/v1/sessions/{sid}/events", f"/api/v1/sessions/{sid}/messages"):
            public = await client.get(path, headers=_headers(token))
            assert public.status_code == 200
            assert all(secret not in public.text for secret in (AUTH, TOKEN))


@pytest.mark.asyncio
async def test_pg_secret_echo_fails_before_trip_mutation_or_contract_persistence_and_public_leak(api_factory):
    calls = []
    poisoned = {**deepcopy(CANONICAL), "reply": "Секрет " + TOKEN}
    async with api_factory(gateway(calls, canonical=poisoned)) as (client, _):
        app = pg_settings(client)
        sid, token, before = await _session(client, sample_trip().model_dump(mode="json"))
        accepted = await client.post(f"/api/v1/sessions/{sid}/messages", headers=_headers(token),
            json={"client_message_id": str(uuid4()), "expected_version": before["state_version"], "text": "Измени возраст ребёнка на четыре года."})
        run = await _wait(client, accepted.json()["run_id"], token)
        after = await _snapshot(client, sid, token)
        assert run["status"] == "failed" and run["error"]["code"] == "INVALID_LLM_RESPONSE"
        assert after["trip"] == before["trip"] and len(calls) == 2
        events = await app.state.store.list_events(sid)
        assert not any(e["event_type"] in {"MODEL_EXTRACTION_VALIDATED", "MODEL_OPERATIONS_CHECKED", "TOOL_CALL_STARTED"} for e in events)
        assert await app.state.store.list_orders(sid) == []
        for suffix in ("events", "messages", "orders"):
            public = await client.get(f"/api/v1/sessions/{sid}/{suffix}", headers=_headers(token))
            assert public.status_code == 200 and AUTH not in public.text and TOKEN not in public.text


@pytest.mark.asyncio
async def test_pg_zero_giga_budget_does_not_relabel_request_as_injected_or_touch_network(api_factory):
    calls = []
    async with api_factory(gateway(calls)) as (client, _):
        app = pg_settings(client, daily=0)
        sid, token, before = await _session(client, sample_trip().model_dump(mode="json"))
        accepted = await client.post(f"/api/v1/sessions/{sid}/messages", headers=_headers(token),
            json={"client_message_id": str(uuid4()), "expected_version": before["state_version"], "text": "Измени возраст ребёнка на четыре года."})
        run = await _wait(client, accepted.json()["run_id"], token)
        after = await _snapshot(client, sid, token)
        assert run["status"] == "failed" and run["error"]["code"] == "MODEL_BUDGET_EXHAUSTED"
        assert after["trip"] == before["trip"] and not calls
        assert not any(e["event_type"] == "MODEL_CALL_STARTED" for e in await app.state.store.list_events(sid))


@pytest.mark.asyncio
async def test_pg_giga_daily_bucket_is_independent_and_hard_1000_cannot_be_expanded(ledger):
    await seed(ledger, ["gigachat"] * 999 + ["groq"] * 2 + ["openrouter"] * 2)
    assert await reserve(ledger, await new_run(ledger), provider="gigachat", limit=1000)
    assert not await reserve(ledger, await new_run(ledger), provider="gigachat", limit=100_000)
    assert await reserve(ledger, await new_run(ledger), provider="groq", limit=1000)
    assert sum(e["payload"]["provider"] == "gigachat" for e in await started(ledger)) == 1000


@pytest.mark.asyncio
async def test_pg_giga_single_attempt_blocks_any_other_remote_before_or_after_same_run(ledger):
    run = await new_run(ledger)
    assert await reserve(ledger, run, provider="gigachat", limit=1000)
    assert not await reserve(ledger, run, provider="gigachat", limit=1000)
    assert not await reserve(ledger, run, provider="groq", limit=1000)
    assert not await reserve(ledger, run, provider="openrouter", limit=40)
    reverse = await new_run(ledger)
    assert await reserve(ledger, reverse, provider="groq", limit=1000)
    assert not await reserve(ledger, reverse, provider="gigachat", limit=1000)
