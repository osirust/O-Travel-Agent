"""Groq wire/guard contracts via MockTransport only; no account/key/inference."""

from copy import deepcopy
import json
import logging

import httpx
import pytest

from otravel.config import Settings, provider_status
from otravel.domain import sample_trip
from otravel.llm_contract import build_operation_schema
from otravel.providers import LLMGateway, ProviderError


MODEL = "qwen/qwen3.8-27b"
SECRET = "synthetic-groq-secret-not-a-real-key"
RESULT = {"operations": [{"type": "update_traveler", "target_id": "child-1", "value": {"age": 4}}],
          "control": {"action": "none"}, "reply": "Возраст ребёнка сохранён.", "unknowns": [], "conflicts": [],
          "speech_act": "CHANGE", "information_subject": None}


def config(handler, **overrides):
    return {"LLM_PROVIDER": "groq", "GROQ_API_KEY": SECRET, "GROQ_MODEL": MODEL,
            "GROQ_FREE_TIER_CONFIRMED": True, "http_transport": httpx.MockTransport(handler), **overrides}


def forbidden(request):
    pytest.fail("Configuration must block before any HTTP attempt")


def completion(payload=RESULT, finish="stop"):
    return httpx.Response(200, json={"model": MODEL,
        "choices": [{"finish_reason": finish, "message": {"content": json.dumps(payload, ensure_ascii=False)}}]})


async def extract(gateway, context=None):
    try:
        return await gateway.extract_operations({} if context is None else context)
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_groq_literal_endpoint_minimal_json_body_and_dynamic_schema():
    calls = []
    schema = build_operation_schema(sample_trip())
    def handler(request):
        calls.append(request)
        assert request.method == "POST" and str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer " + SECRET
        body = json.loads(request.content)
        assert set(body) == {"model", "messages", "response_format", "max_tokens"}
        assert body["model"] == MODEL and body["max_tokens"] == 2048
        assert body["response_format"] == {"type": "json_object"}
        assert [message["role"] for message in body["messages"]] == ["system", "user"]
        assert json.dumps(schema, ensure_ascii=False) in body["messages"][0]["content"]
        assert "__response_schema" not in body["messages"][1]["content"]
        assert SECRET not in request.content.decode()
        return completion()
    settings = config(handler, LLM_MAX_OUTPUT_TOKENS=8192, LLM_REASONING_ENABLED=True)
    untouched = dict(settings)
    gateway = LLMGateway(settings)
    assert await extract(gateway, {"current_message": "Ему четыре года", "__response_schema": schema,
                                  "GROQ_API_KEY": "never-forward-this-context-secret"}) == RESULT
    assert {key: gateway.last_call[key] for key in ("provider", "model")} == {"provider": "groq", "model": MODEL}
    assert gateway.last_call["request_metrics"]["schema_utf8_bytes"] > 0
    assert len(calls) == 1 and settings == untouched


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmed", [None, False, "false", "0", "", "not-confirmed"])
async def test_groq_free_confirmation_required_before_http(confirmed, monkeypatch):
    monkeypatch.delenv("GROQ_FREE_TIER_CONFIRMED", raising=False)
    gateway = LLMGateway(config(forbidden, GROQ_FREE_TIER_CONFIRMED=confirmed))
    with pytest.raises(ProviderError) as error:
        await extract(gateway)
    assert error.value.code == "CONFIG_BLOCKED" and error.value.provider == "groq"


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["other/model", "qwen/qwen3.8-27b:free", "qwen/qwen3.8-27b:paid", ""])
async def test_groq_model_whitelist_has_no_paid_or_other_model_fallback(model):
    gateway = LLMGateway(config(forbidden, GROQ_MODEL=model))
    with pytest.raises(ProviderError) as error:
        await extract(gateway)
    assert error.value.code == "CONFIG_BLOCKED"


@pytest.mark.asyncio
async def test_groq_missing_key_does_not_reach_http():
    gateway = LLMGateway(config(forbidden, GROQ_API_KEY=""))
    with pytest.raises(ProviderError) as error:
        await extract(gateway)
    assert error.value.code == "CONFIG_MISSING"


@pytest.mark.asyncio
@pytest.mark.parametrize("tokens", [2049, 2048, 512, "bad", None])
async def test_groq_output_token_cap_does_not_change_other_profile_limit(tokens):
    def handler(request):
        body = json.loads(request.content)
        assert 256 <= body["max_tokens"] <= 2048
        assert "reasoning" not in body and "provider" not in body and "models" not in body
        return completion()
    assert await extract(LLMGateway(config(handler, LLM_MAX_OUTPUT_TOKENS=tokens))) == RESULT


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["length", "content_filter", "error"])
async def test_truncated_or_rejected_groq_response_is_not_valid(finish):
    with pytest.raises(ProviderError) as error:
        await extract(LLMGateway(config(lambda request: completion(finish=finish))))
    assert error.value.code == "INVALID_LLM_RESPONSE"


@pytest.mark.asyncio
async def test_groq_json_mode_still_requires_server_side_target_schema_validation():
    bad = deepcopy(RESULT)
    bad["operations"][0]["target_id"] = None
    gateway = LLMGateway(config(lambda request: completion(bad)))
    with pytest.raises(ProviderError) as error:
        await extract(gateway, {"__response_schema": build_operation_schema(sample_trip())})
    assert error.value.code == "INVALID_LLM_OPERATION"
    assert error.value.provider == "groq"
    assert error.value.details["schema_errors"]
    assert SECRET not in json.dumps(error.value.as_dict())


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["not-json", "[]", '{"operations": []}'])
async def test_groq_invalid_json_or_envelope_is_a_structured_failure(content):
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    with pytest.raises(ProviderError) as error:
        await extract(LLMGateway(config(handler)))
    assert error.value.code == "INVALID_LLM_RESPONSE"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429, 500])
async def test_groq_failure_never_retries_or_logs_credentials(status, caplog):
    calls = []
    caplog.set_level(logging.INFO, logger="httpx")
    def handler(request):
        calls.append(request)
        logging.getLogger("httpx").warning("Synthetic Authorization: Bearer %s", SECRET)
        return httpx.Response(status, json={"error": {"message": SECRET,
            "metadata": {"limit_source": "account_quota", "GROQ_API_KEY": SECRET}}})
    with pytest.raises(ProviderError) as error:
        await extract(LLMGateway(config(handler)))
    assert len(calls) == 1
    assert error.value.provider == "groq"
    assert error.value.code == ("RATE_LIMITED" if status == 429 else "HTTP_ERROR")
    assert SECRET not in caplog.text and "[REDACTED]" in caplog.text
    assert SECRET not in str(error.value) and SECRET not in json.dumps(error.value.as_dict())


@pytest.mark.asyncio
@pytest.mark.parametrize("header,seconds", [("1", 1), ("60", 60), ("86400", 86400), (" 120 ", 120), ("00060", 60), ("1.5", 2)])
async def test_groq_429_preserves_bounded_retry_after_rounded_up_to_seconds(header, seconds):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": header, "X-Private-Header": SECRET},
            json={"error": {"message": SECRET}})
    with pytest.raises(ProviderError) as error:
        await extract(LLMGateway(config(handler)))
    assert error.value.code == "RATE_LIMITED" and error.value.provider == "groq"
    assert error.value.details == {"http_status": 429, "limit_source": "unknown", "retry_after_seconds": seconds,
        "rate_limits": {"retry_after_seconds": seconds}}
    assert type(error.value.details["retry_after_seconds"]) is int
    assert len(calls) == 1 and SECRET not in json.dumps(error.value.as_dict())


@pytest.mark.asyncio
@pytest.mark.parametrize("header", [None, "", "0", "000", "-1", "+60", "1e2", "86401",
    "99999999999999999999", "120 seconds", "60, 120", "Wed, 21 Oct 2015 07:28:00 GMT", SECRET])
async def test_groq_missing_or_invalid_retry_after_is_omitted(header):
    def handler(request):
        headers = {} if header is None else {"Retry-After": header}
        return httpx.Response(429, headers=headers, json={"error": {"message": SECRET,
            "metadata": {"retry_after_seconds": 123, "raw_header": SECRET}}})
    with pytest.raises(ProviderError) as error:
        await extract(LLMGateway(config(handler)))
    assert error.value.details == {"http_status": 429, "limit_source": "unknown"}
    assert SECRET not in json.dumps(error.value.as_dict())


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 500, 503])
async def test_groq_retry_after_is_ignored_for_statuses_other_than_429(status):
    with pytest.raises(ProviderError) as error:
        await extract(LLMGateway(config(lambda request:
            httpx.Response(status, headers={"Retry-After": "120"}, json={"error": {"message": "synthetic"}}))))
    assert error.value.details == {"http_status": status}


@pytest.mark.asyncio
async def test_other_provider_retry_after_behavior_does_not_change():
    gateway = LLMGateway({"LLM_PROVIDER": "openrouter", "OPENROUTER_API_KEY": SECRET,
        "OPENROUTER_MODEL": "synthetic/model:free", "http_transport": httpx.MockTransport(lambda request:
            httpx.Response(429, headers={"Retry-After": "120"}, json={"error": {"message": "synthetic"}}))})
    with pytest.raises(ProviderError) as error:
        await extract(gateway)
    assert error.value.details == {"http_status": 429, "limit_source": "unknown"}


@pytest.mark.asyncio
async def test_groq_hint_is_not_trusted_from_any_other_endpoint():
    gateway = LLMGateway(config(lambda request:
        httpx.Response(429, headers={"Retry-After": "120"}, json={"error": {"message": "synthetic"}})))
    try:
        with pytest.raises(ProviderError) as error:
            await gateway._request("groq", "POST", "https://untrusted.invalid/chat/completions")
        assert error.value.details == {"http_status": 429, "limit_source": "unknown"}
    finally:
        await gateway.close()


def test_groq_settings_and_runtime_status_are_explicit_and_redacted(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", SECRET)
    monkeypatch.setenv("GROQ_FREE_TIER_CONFIRMED", "true")
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    settings = Settings.from_env()
    configured = settings.provider_settings()
    assert settings.llm_mode == configured["LLM_PROVIDER"] == "groq"
    assert configured["GROQ_API_KEY"] == SECRET and configured["GROQ_MODEL"] == MODEL
    assert configured["GROQ_FREE_TIER_CONFIRMED"] is True
    status = provider_status()
    assert status["_runtime"] == {"llm_provider": "groq", "model": MODEL}
    assert status["groq"] == {"configured": True, "model": MODEL, "free_tier_confirmed": True, "paid_fallback": False}
    assert SECRET not in json.dumps(status)


def test_groq_confirmation_is_false_by_default(monkeypatch):
    monkeypatch.delenv("GROQ_FREE_TIER_CONFIRMED", raising=False)
    assert Settings.from_env().provider_settings()["GROQ_FREE_TIER_CONFIRMED"] is False
    assert provider_status()["groq"]["free_tier_confirmed"] is False


@pytest.mark.parametrize("provider,setting,model", [("gemini", "GEMINI_MODEL", "gemini-3.8-flash"),
    ("ollama", "OLLAMA_MODEL", "qwen3.5:4b"), ("openrouter", "OPENROUTER_MODEL", "dots-studio/dots-3-note-preview:free")])
def test_groq_settings_do_not_change_other_runtime_profiles(provider, setting, model, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.setenv(setting, model)
    monkeypatch.setenv("GROQ_MODEL", "ignored-groq-setting")
    assert provider_status()["_runtime"] == {"llm_provider": provider, "model": model}
