"""Cloudflare adapter contracts via fake HTTP only; no real account or inference."""

from copy import deepcopy
import json
import logging

import httpx
import pytest

from otravel.domain import sample_trip
from otravel.llm_contract import build_operation_schema
from otravel.providers import LLMGateway, ProviderError


ACCOUNT = "0123456789abcdef0123456789abcdef"
MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
REASONING_MODELS = ("@cf/google/gemma-4-26b-a4b-it", "@cf/nvidia/nemotron-3-120b-a12b")
APPROVED_MODELS = (MODEL, *REASONING_MODELS)
SECRET = "synthetic-cloudflare-token-never-a-real-key"
ENDPOINT = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/ai/v1/chat/completions"
RESULT = {"operations": [{"type": "update_traveler", "target_id": "child-1", "value": {"age": 4}}],
          "control": {"action": "none"}, "reply": "Возраст ребёнка сохранён.", "unknowns": [], "conflicts": [],
          "speech_act": "CHANGE", "information_subject": None}


def config(handler, **overrides):
    return {"LLM_PROVIDER": "cloudflare", "CLOUDFLARE_API_TOKEN": SECRET,
        "CLOUDFLARE_ACCOUNT_ID": ACCOUNT, "CLOUDFLARE_MODEL": MODEL,
        "CLOUDFLARE_FREE_TIER_CONFIRMED": True, "http_transport": httpx.MockTransport(handler), **overrides}


def forbidden(request):
    pytest.fail("Configuration must block before an HTTP attempt")


def completion(payload=RESULT, *, finish="stop", usage=None, model=MODEL):
    body = {"model": model, "choices": [{"finish_reason": finish,
        "message": {"content": json.dumps(payload, ensure_ascii=False)}}]}
    if usage is not None:
        body["usage"] = usage
    return httpx.Response(200, json=body)


async def extract(gateway, context=None):
    try:
        return await gateway.extract_operations({} if context is None else context)
    finally:
        await gateway.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("model", APPROVED_MODELS)
async def test_cloudflare_fixed_account_endpoint_auth_and_json_mode_contract(model):
    calls = []
    schema = build_operation_schema(sample_trip())
    def handler(request):
        calls.append(request)
        assert request.method == "POST" and str(request.url) == ENDPOINT
        assert request.headers["authorization"] == "Bearer " + SECRET
        body = json.loads(request.content)
        if model in REASONING_MODELS:
            assert set(body) == {"model", "messages", "response_format", "max_completion_tokens", "chat_template_kwargs"}
            assert body["max_completion_tokens"] == 2048
            assert body["chat_template_kwargs"] == {"enable_thinking": False}
            assert not {"max_tokens", "reasoning_effort", "thinking", "reasoning"} & set(body)
        else:
            assert set(body) == {"model", "messages", "response_format", "max_tokens"}
            assert body["max_tokens"] == 2048
        assert body["model"] == model
        assert body["response_format"] == {"type": "json_object"}
        assert [message["role"] for message in body["messages"]] == ["system", "user"]
        assert json.dumps(schema, ensure_ascii=False) in body["messages"][0]["content"]
        assert "__response_schema" not in body["messages"][1]["content"]
        assert SECRET not in request.content.decode()
        assert "separate-synthetic-context-token" not in request.content.decode()
        return completion(model=model, usage={"prompt_tokens": 17, "completion_tokens": 5, "total_tokens": 22})
    settings = config(handler, CLOUDFLARE_MODEL=model, LLM_MAX_OUTPUT_TOKENS=8192, LLM_REASONING_ENABLED=True,
        CLOUDFLARE_BASE_URL="https://untrusted.invalid", OPENROUTER_BACKUP_MODEL="synthetic/paid")
    untouched = dict(settings)
    gateway = LLMGateway(settings)
    output = await extract(gateway, {"current_message": "Ему четыре года", "__response_schema": schema,
        "CLOUDFLARE_API_TOKEN": "separate-synthetic-context-token"})
    assert output == RESULT and settings == untouched and len(calls) == 1
    assert output.call_metadata["provider"] == "cloudflare" and output.call_metadata["model"] == model
    assert output.call_metadata["usage"] == {"input_tokens": 17, "output_tokens": 5, "total_tokens": 22}
    assert output.call_metadata["request_metrics"]["schema_utf8_bytes"] > 0
    assert len(output.call_metadata["request_metrics"]["prompt_sha256"]) == 64
    assert SECRET not in json.dumps(output.call_metadata)


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmed", [None, False, "false", "0", "", "unknown"])
async def test_cloudflare_requires_free_confirmation_before_http(confirmed, monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_FREE_TIER_CONFIRMED", raising=False)
    with pytest.raises(ProviderError) as failure:
        await extract(LLMGateway(config(forbidden, CLOUDFLARE_FREE_TIER_CONFIRMED=confirmed)))
    assert failure.value.code == "CONFIG_BLOCKED" and failure.value.provider == "cloudflare"


@pytest.mark.asyncio
@pytest.mark.parametrize("account", [None, ""])
async def test_cloudflare_missing_account_is_not_a_default_or_other_account(account, monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    with pytest.raises(ProviderError) as failure:
        await extract(LLMGateway(config(forbidden, CLOUDFLARE_ACCOUNT_ID=account)))
    assert failure.value.code == "CONFIG_MISSING"


@pytest.mark.asyncio
@pytest.mark.parametrize("account", ["a" * 31, "a" * 33, "g" * 32, "../other-account", "https://untrusted.invalid",
    ACCOUNT + "/ai/run", ACCOUNT + "?token=synthetic", 42])
async def test_cloudflare_account_path_injection_is_rejected_before_http(account):
    with pytest.raises(ProviderError) as failure:
        await extract(LLMGateway(config(forbidden, CLOUDFLARE_ACCOUNT_ID=account)))
    assert failure.value.code == "CONFIG_BLOCKED"


@pytest.mark.asyncio
async def test_cloudflare_missing_token_does_not_reach_http(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    with pytest.raises(ProviderError) as failure:
        await extract(LLMGateway(config(forbidden, CLOUDFLARE_API_TOKEN="")))
    assert failure.value.code == "CONFIG_MISSING" and failure.value.provider == "cloudflare"


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["", "other/model", MODEL + ":free", "@cf/zai-org/glm-5.2",
    "@cf/deepseek-ai/deepseek-v4.1-flash-1017", "@cf/zai-org/glm-4.7-flash",
    REASONING_MODELS[0] + ":paid", REASONING_MODELS[1] + ":free", "@cf/google/gemma-4-31b-it"])
async def test_cloudflare_only_approved_models_no_paid_or_unqualified_fallback(model):
    with pytest.raises(ProviderError) as failure:
        await extract(LLMGateway(config(forbidden, CLOUDFLARE_MODEL=model)))
    assert failure.value.code == "CONFIG_BLOCKED"


@pytest.mark.asyncio
@pytest.mark.parametrize("model", APPROVED_MODELS)
@pytest.mark.parametrize("tokens", [8192, 2048, 512, 1, -100, "bad", None])
async def test_cloudflare_output_is_bounded_and_does_not_enable_extra_features(tokens, model):
    def handler(request):
        body = json.loads(request.content)
        field = "max_completion_tokens" if model in REASONING_MODELS else "max_tokens"
        assert 256 <= body[field] <= 2048
        assert not {"reasoning", "reasoning_effort", "thinking", "tools", "models", "provider", "stream", "gateway"} & set(body)
        if model in REASONING_MODELS:
            assert "max_tokens" not in body
            assert body["chat_template_kwargs"] == {"enable_thinking": False}
        return completion(model=model)
    assert await extract(LLMGateway(config(handler, CLOUDFLARE_MODEL=model, LLM_MAX_OUTPUT_TOKENS=tokens))) == RESULT


@pytest.mark.asyncio
@pytest.mark.parametrize("model", APPROVED_MODELS)
async def test_cloudflare_json_mode_does_not_bypass_dynamic_target_schema(model):
    bad = deepcopy(RESULT)
    bad["operations"][0]["target_id"] = None
    gateway = LLMGateway(config(lambda request: completion(bad, model=model,
        usage={"prompt_tokens": 17, "completion_tokens": 5, "private_message": SECRET}), CLOUDFLARE_MODEL=model))
    try:
        with pytest.raises(ProviderError) as failure:
            await gateway.extract_operations({"__response_schema": build_operation_schema(sample_trip())})
        assert failure.value.code == "INVALID_LLM_OPERATION" and failure.value.details["schema_errors"]
        assert gateway.call_metadata_for_current_call()["usage"] == {"input_tokens": 17, "output_tokens": 5}
        assert gateway.last_call == {}
        assert SECRET not in json.dumps(failure.value.as_dict())
        assert SECRET not in json.dumps(gateway.call_metadata_for_current_call())
    finally:
        await gateway.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("model", APPROVED_MODELS)
@pytest.mark.parametrize("kind", ["json", "shape", "truncated", "missing_choice"])
async def test_cloudflare_invalid_output_keeps_consumed_usage_without_fabricating_success(kind, model):
    def handler(request):
        body = json.loads(completion(model=model, usage={"prompt_tokens": 11, "completion_tokens": 3}).content)
        if kind == "json":
            body["choices"][0]["message"]["content"] = "{"
        elif kind == "shape":
            body["choices"][0]["message"]["content"] = json.dumps({**RESULT, "reply": 4})
        elif kind == "truncated":
            body["choices"][0]["finish_reason"] = "length"
        else:
            body["choices"] = []
        return httpx.Response(200, json=body)
    gateway = LLMGateway(config(handler, CLOUDFLARE_MODEL=model))
    try:
        with pytest.raises(ProviderError) as failure:
            await gateway.extract_operations({"current_message": "Сохрани возраст"})
        assert failure.value.code == "INVALID_LLM_RESPONSE"
        assert gateway.call_metadata_for_current_call()["usage"] == {"input_tokens": 11, "output_tokens": 3}
        assert gateway.last_call == {}
    finally:
        await gateway.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("model", APPROVED_MODELS)
@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
async def test_cloudflare_http_error_is_one_attempt_without_secret_or_raw_body(status, model, caplog):
    calls = []
    caplog.set_level(logging.INFO, logger="httpx")
    def handler(request):
        calls.append(request)
        logging.getLogger("httpx").warning("Synthetic Authorization: Bearer %s", SECRET)
        return httpx.Response(status, headers={"X-Private-Header": SECRET},
            json={"errors": [{"message": SECRET}], "usage": {"prompt_tokens": 999},
                "CLOUDFLARE_API_TOKEN": SECRET})
    gateway = LLMGateway(config(handler, CLOUDFLARE_MODEL=model, OPENROUTER_API_KEY="synthetic-unused-backup",
        OPENROUTER_BACKUP_MODEL="synthetic/model:free"))
    try:
        with pytest.raises(ProviderError) as failure:
            await gateway.extract_operations({})
        assert failure.value.provider == "cloudflare"
        assert failure.value.code == ("RATE_LIMITED" if status == 429 else "HTTP_ERROR")
        assert len(calls) == 1 and gateway.last_call == {}
        assert "usage" not in gateway.call_metadata_for_current_call()
        assert SECRET not in str(failure.value) + json.dumps(failure.value.as_dict()) + caplog.text
        assert "[REDACTED]" in caplog.text
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_cloudflare_default_model_remains_llama(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_MODEL", raising=False)
    calls = []
    def handler(request):
        calls.append(request)
        body = json.loads(request.content)
        assert body["model"] == MODEL and "max_tokens" in body
        assert "max_completion_tokens" not in body and "chat_template_kwargs" not in body
        return completion()
    settings = config(handler)
    del settings["CLOUDFLARE_MODEL"]
    assert await extract(LLMGateway(settings)) == RESULT
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("model", REASONING_MODELS)
@pytest.mark.parametrize("missing", ["confirmation", "token"])
async def test_cloudflare_new_profiles_do_not_bypass_free_or_credential_guards(model, missing, monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_FREE_TIER_CONFIRMED", raising=False)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    overrides = {"CLOUDFLARE_MODEL": model,
        "CLOUDFLARE_FREE_TIER_CONFIRMED": False if missing == "confirmation" else True,
        "CLOUDFLARE_API_TOKEN": "" if missing == "token" else SECRET}
    with pytest.raises(ProviderError) as failure:
        await extract(LLMGateway(config(forbidden, **overrides)))
    assert failure.value.code == ("CONFIG_BLOCKED" if missing == "confirmation" else "CONFIG_MISSING")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_cloudflare_redirect_never_forwards_authorization_or_retries(status):
    calls = []
    def handler(request):
        calls.append(request)
        assert str(request.url) == ENDPOINT
        return httpx.Response(status, headers={"Location": "https://untrusted.invalid/capture?token=" + SECRET})
    with pytest.raises(ProviderError) as failure:
        await extract(LLMGateway(config(handler)))
    assert failure.value.code == "HTTP_ERROR" and failure.value.details["http_status"] == status
    assert len(calls) == 1 and SECRET not in json.dumps(failure.value.as_dict())


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_type", [httpx.ReadTimeout, httpx.ConnectError])
async def test_cloudflare_network_errors_have_no_hidden_retry(failure_type):
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
async def test_cloudflare_429_and_next_response_never_reuse_previous_usage():
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 2:
            return httpx.Response(429, json={"errors": [{"message": "quota exhausted"}]})
        return completion(usage={"prompt_tokens": 11, "completion_tokens": 3} if len(calls) == 1 else None)
    gateway = LLMGateway(config(handler))
    try:
        first = await gateway.extract_operations({"current_message": "Первый"})
        with pytest.raises(ProviderError):
            await gateway.extract_operations({"current_message": "Второй"})
        assert "usage" not in gateway.call_metadata_for_current_call()
        third = await gateway.extract_operations({"current_message": "Третий"})
        assert "usage" not in third.call_metadata
        assert first.call_metadata["usage"] == {"input_tokens": 11, "output_tokens": 3}
        assert len(calls) == 3
    finally:
        await gateway.close()
