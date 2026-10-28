"""Z.ai standard free-model contracts with synthetic credentials and fake HTTP."""

from copy import deepcopy
import json
import logging

import httpx
import pytest

from otravel.domain import sample_trip
from otravel.llm_contract import build_operation_schema
from otravel.providers import LLMGateway, ProviderError


ENDPOINT = "https://api.z.ai/api/paas/v4/chat/completions"
SECRET = "synthetic-zai-credential-not-a-real-key"
MODEL = "glm-4.7-flash"
RESULT = {"operations": [{"type": "update_traveler", "target_id": "child-1", "value": {"age": 4}}],
    "control": {"action": "none"}, "reply": "Возраст ребёнка сохранён.", "unknowns": [], "conflicts": [],
    "speech_act": "CHANGE", "information_subject": None}


def config(handler, **overrides):
    return {"LLM_PROVIDER": "zai", "ZAI_API_KEY": SECRET, "ZAI_MODEL": MODEL,
        "ZAI_FREE_TIER_CONFIRMED": True, "http_transport": httpx.MockTransport(handler), **overrides}


def forbidden(request):
    pytest.fail("Configuration must block before HTTP")


def completion(payload=RESULT, *, finish="stop", usage=None, model=MODEL):
    body = {"model": model, "choices": [{"finish_reason": finish, "message": {
        "content": json.dumps(payload, ensure_ascii=False), "reasoning_content": SECRET}}]}
    if usage is not None:
        body["usage"] = usage
    return httpx.Response(200, json=body)


async def extract(gateway, context=None):
    try:
        return await gateway.extract_operations({} if context is None else context)
    finally:
        await gateway.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["glm-4.7-flash", "glm-4.5-flash"])
async def test_zai_standard_endpoint_free_model_json_schema_and_disabled_thinking(model):
    calls = []
    schema = build_operation_schema(sample_trip())
    def handler(request):
        calls.append(request)
        assert request.method == "POST" and str(request.url) == ENDPOINT
        assert request.headers["authorization"] == "Bearer " + SECRET
        body = json.loads(request.content)
        assert set(body) == {"model", "messages", "response_format", "max_tokens", "thinking"}
        assert body["model"] == model and body["max_tokens"] == 2048
        assert body["response_format"] == {"type": "json_object"}
        assert body["thinking"] == {"type": "disabled"}
        assert [message["role"] for message in body["messages"]] == ["system", "user"]
        wire_schema = body["messages"][0]["content"].split("Required output JSON Schema:\n", 1)[1]
        assert json.loads(wire_schema) == schema
        assert "__response_schema" not in body["messages"][1]["content"]
        assert SECRET not in request.content.decode()
        assert "separate-synthetic-zai-context-key" not in request.content.decode()
        return completion(model=model, usage={"prompt_tokens": 21, "completion_tokens": 7, "total_tokens": 28})
    settings = config(handler, ZAI_MODEL=model, LLM_MAX_OUTPUT_TOKENS=8192, LLM_REASONING_ENABLED=True,
        ZAI_BASE_URL="https://untrusted.invalid", ZAI_BACKUP_MODEL="glm-5.3-flash")
    untouched = dict(settings)
    gateway = LLMGateway(settings)
    output = await extract(gateway, {"current_message": "Ему четыре года", "__response_schema": schema,
        "ZAI_API_KEY": "separate-synthetic-zai-context-key"})
    assert output == RESULT and len(calls) == 1 and settings == untouched
    assert output.call_metadata["provider"] == "zai" and output.call_metadata["model"] == model
    assert output.call_metadata["usage"] == {"input_tokens": 21, "output_tokens": 7, "total_tokens": 28}
    assert output.call_metadata["request_metrics"]["schema_utf8_bytes"] > 0
    assert SECRET not in json.dumps(output.call_metadata)


@pytest.mark.asyncio
async def test_zai_default_model_is_free_flash_without_automatic_switch(monkeypatch):
    monkeypatch.delenv("ZAI_MODEL", raising=False)
    calls = []
    def handler(request):
        calls.append(request)
        assert json.loads(request.content)["model"] == MODEL
        return completion()
    settings = config(handler)
    del settings["ZAI_MODEL"]
    assert await extract(LLMGateway(settings)) == RESULT
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmed", [None, False, "false", "0", "", "unknown"])
async def test_zai_free_confirmation_required_before_http(confirmed, monkeypatch):
    monkeypatch.delenv("ZAI_FREE_TIER_CONFIRMED", raising=False)
    with pytest.raises(ProviderError) as failure:
        await extract(LLMGateway(config(forbidden, ZAI_FREE_TIER_CONFIRMED=confirmed)))
    assert failure.value.code == "CONFIG_BLOCKED" and failure.value.provider == "zai"


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["", " ", None])
async def test_zai_missing_key_cannot_fall_back_to_another_provider(key, monkeypatch):
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    with pytest.raises(ProviderError) as failure:
        await extract(LLMGateway(config(forbidden, ZAI_API_KEY=key, OPENROUTER_API_KEY="synthetic-unused-key")))
    assert failure.value.code == "CONFIG_MISSING"


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["", "glm-5.3", "glm-5.3-flash", "glm-4.7", "glm-4.7-flashx",
    "glm-4.7-flash:free", "GLM-4.7-FLASH", "glm-4.6v-flash", "unknown/model"])
async def test_zai_paid_new_or_unqualified_models_are_refused_before_http(model):
    with pytest.raises(ProviderError) as failure:
        await extract(LLMGateway(config(forbidden, ZAI_MODEL=model)))
    assert failure.value.code == "CONFIG_BLOCKED"


@pytest.mark.asyncio
@pytest.mark.parametrize("tokens", [8192, 2048, 512, 1, -100, "bad", None])
async def test_zai_output_bound_does_not_enable_paid_tools_or_routing(tokens):
    def handler(request):
        body = json.loads(request.content)
        assert 256 <= body["max_tokens"] <= 2048
        assert body["thinking"] == {"type": "disabled"}
        assert not {"tools", "models", "provider", "stream", "reasoning", "web_search"} & set(body)
        return completion()
    assert await extract(LLMGateway(config(handler, LLM_MAX_OUTPUT_TOKENS=tokens))) == RESULT


@pytest.mark.asyncio
async def test_zai_json_mode_still_validates_dynamic_target_ids_and_keeps_usage_on_failure():
    bad = deepcopy(RESULT)
    bad["operations"][0]["target_id"] = None
    calls = []
    def handler(request):
        calls.append(request)
        return completion(bad, usage={"prompt_tokens": 13, "completion_tokens": 4, "private_key": SECRET})
    gateway = LLMGateway(config(handler))
    try:
        with pytest.raises(ProviderError) as failure:
            await gateway.extract_operations({"__response_schema": build_operation_schema(sample_trip())})
        assert failure.value.code == "INVALID_LLM_OPERATION" and failure.value.details["schema_errors"]
        assert gateway.call_metadata_for_current_call()["usage"] == {"input_tokens": 13, "output_tokens": 4}
        assert gateway.last_call == {} and len(calls) == 1
        assert SECRET not in json.dumps(failure.value.as_dict()) + json.dumps(gateway.call_metadata_for_current_call())
    finally:
        await gateway.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["json", "shape", "truncated", "filtered", "missing_choice"])
async def test_zai_consumed_invalid_generation_is_not_success_or_free_usage(kind):
    def handler(request):
        body = json.loads(completion(usage={"prompt_tokens": 12, "completion_tokens": 3}).content)
        if kind == "json":
            body["choices"][0]["message"]["content"] = "{"
        elif kind == "shape":
            body["choices"][0]["message"]["content"] = json.dumps({**RESULT, "reply": 4})
        elif kind in {"truncated", "filtered"}:
            body["choices"][0]["finish_reason"] = "length" if kind == "truncated" else "content_filter"
        else:
            body["choices"] = []
        return httpx.Response(200, json=body)
    gateway = LLMGateway(config(handler))
    try:
        with pytest.raises(ProviderError) as failure:
            await gateway.extract_operations({"current_message": "Сохрани возраст"})
        assert failure.value.code == "INVALID_LLM_RESPONSE"
        assert gateway.call_metadata_for_current_call()["usage"] == {"input_tokens": 12, "output_tokens": 3}
        assert gateway.last_call == {}
        assert SECRET not in json.dumps(gateway.call_metadata_for_current_call())
    finally:
        await gateway.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
async def test_zai_http_failure_is_one_attempt_without_secret_or_raw_error_body(status, caplog):
    calls = []
    caplog.set_level(logging.INFO, logger="httpx")
    def handler(request):
        calls.append(request)
        logging.getLogger("httpx").warning("Synthetic Authorization: Bearer %s", SECRET)
        return httpx.Response(status, json={"error": {"code": "1113", "message": SECRET},
            "usage": {"prompt_tokens": 999}})
    gateway = LLMGateway(config(handler, ZAI_BACKUP_MODEL="glm-4.5-flash",
        OPENROUTER_BACKUP_MODEL="synthetic/model:free"))
    try:
        with pytest.raises(ProviderError) as failure:
            await gateway.extract_operations({})
        assert failure.value.provider == "zai"
        assert failure.value.code == ("RATE_LIMITED" if status == 429 else "HTTP_ERROR")
        assert len(calls) == 1 and gateway.last_call == {}
        assert "usage" not in gateway.call_metadata_for_current_call()
        assert SECRET not in str(failure.value) + json.dumps(failure.value.as_dict()) + caplog.text
        assert "[REDACTED]" in caplog.text
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_zai_error_envelope_cannot_be_misread_as_a_successful_completion():
    with pytest.raises(ProviderError) as failure:
        await extract(LLMGateway(config(lambda request: httpx.Response(200,
            json={"error": {"code": "1113", "message": SECRET}}))))
    assert failure.value.code == "PROVIDER_RESPONSE_ERROR" and SECRET not in json.dumps(failure.value.as_dict())


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_zai_redirect_never_forwards_token_to_coding_or_foreign_endpoint(status):
    calls = []
    def handler(request):
        calls.append(request)
        assert str(request.url) == ENDPOINT
        return httpx.Response(status, headers={"Location": "https://untrusted.invalid/?token=" + SECRET})
    with pytest.raises(ProviderError) as failure:
        await extract(LLMGateway(config(handler)))
    assert failure.value.code == "HTTP_ERROR" and len(calls) == 1
    assert SECRET not in json.dumps(failure.value.as_dict())


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_type", [httpx.ReadTimeout, httpx.ConnectError])
async def test_zai_network_failure_has_no_retry_or_backup(failure_type):
    calls = []
    def handler(request):
        calls.append(request)
        raise failure_type(SECRET)
    with pytest.raises(ProviderError) as failure:
        await extract(LLMGateway(config(handler)))
    assert failure.value.code == ("REQUEST_TIMEOUT" if failure_type == httpx.ReadTimeout else "NETWORK_ERROR")
    assert failure.value.retryable is True and len(calls) == 1
    assert SECRET not in json.dumps(failure.value.as_dict())


@pytest.mark.asyncio
@pytest.mark.parametrize("header,seconds", [("1", 1), ("1.5", 2), ("86400", 86400)])
async def test_zai_429_retry_hint_is_bounded_and_does_not_trigger_automatic_retry(header, seconds):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": header, "X-Private": SECRET},
            json={"error": {"message": SECRET}})
    with pytest.raises(ProviderError) as failure:
        await extract(LLMGateway(config(handler)))
    assert failure.value.code == "RATE_LIMITED" and len(calls) == 1
    assert failure.value.details["retry_after_seconds"] == seconds
    assert type(failure.value.details["retry_after_seconds"]) is int
    assert SECRET not in json.dumps(failure.value.as_dict())


@pytest.mark.asyncio
@pytest.mark.parametrize("header", [None, "0", "-1", "86401", "1e2", "NaN", SECRET])
async def test_zai_429_untrusted_or_invalid_retry_hint_is_not_persisted(header):
    def handler(request):
        return httpx.Response(429, headers={} if header is None else {"Retry-After": header},
            json={"error": {"message": SECRET, "retry_after_seconds": 123}})
    with pytest.raises(ProviderError) as failure:
        await extract(LLMGateway(config(handler)))
    assert "retry_after_seconds" not in failure.value.details
    assert SECRET not in json.dumps(failure.value.as_dict())


@pytest.mark.asyncio
async def test_zai_retry_hint_from_another_endpoint_is_not_trusted():
    gateway = LLMGateway(config(lambda request: httpx.Response(429, headers={"Retry-After": "60"})))
    try:
        with pytest.raises(ProviderError) as failure:
            await gateway._request("zai", "POST", "https://untrusted.invalid/chat/completions")
        assert "retry_after_seconds" not in failure.value.details
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_zai_failure_and_next_call_do_not_inherit_previous_usage():
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 2:
            return httpx.Response(429, json={"error": {"message": "synthetic quota exhausted"}})
        return completion(usage={"prompt_tokens": 12, "completion_tokens": 3} if len(calls) == 1 else None)
    gateway = LLMGateway(config(handler))
    try:
        first = await gateway.extract_operations({"current_message": "Первый"})
        with pytest.raises(ProviderError):
            await gateway.extract_operations({"current_message": "Второй"})
        assert "usage" not in gateway.call_metadata_for_current_call()
        third = await gateway.extract_operations({"current_message": "Третий"})
        assert "usage" not in third.call_metadata and len(calls) == 3
        assert first.call_metadata["usage"] == {"input_tokens": 12, "output_tokens": 3}
    finally:
        await gateway.close()
