"""Request-local model metrics via MockTransport; no real keys/network/DB."""

import asyncio
import hashlib
import json

import httpx
import pytest

from otravel.providers import LLMGateway, ProviderError


RESULT = {"operations": [], "control": {"action": "none"}, "reply": "Уточните дату.", "unknowns": [], "conflicts": []}
SCHEMA = {"type": "object", "required": list(RESULT), "additionalProperties": False,
          "properties": {key: {"type": "array" if isinstance(value, list) else "object" if isinstance(value, dict) else "string"}
                         for key, value in RESULT.items()}}
CANARY = "gsk_provider_metrics_SYNTHETIC_ONLY_0123456789abcdef0123456789abcdef"


def configuration(provider, handler):
    return {"LLM_PROVIDER": provider, "GROQ_API_KEY": CANARY, "GROQ_FREE_TIER_CONFIRMED": True,
        "GROQ_MODEL": "qwen/qwen3.8-27b", "GEMINI_API_KEY": CANARY, "GEMINI_FREE_TIER_CONFIRMED": True,
        "GEMINI_MODEL": "gemini-3.8-flash", "OPENROUTER_API_KEY": CANARY,
        "OPENROUTER_MODEL": "synthetic/model:free", "http_transport": httpx.MockTransport(handler)}


def wire_messages(provider, body):
    if provider == "gemini":
        return body["systemInstruction"]["parts"][0]["text"], body["contents"][0]["parts"][0]["text"]
    return body["messages"][0]["content"], body["messages"][1]["content"]


def wire_fingerprint(system, prompt):
    return hashlib.sha256((system + "\0" + prompt).encode("utf-8")).hexdigest()


def response(provider, usage=None):
    text = json.dumps(RESULT, ensure_ascii=False)
    if provider == "gemini":
        body = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": text}]}}]}
        if usage is not None:
            body["usageMetadata"] = usage
    elif provider == "ollama":
        body = {"done": True, "done_reason": "stop", "message": {"content": text}, **(usage or {})}
    else:
        body = {"choices": [{"finish_reason": "stop", "message": {"content": text}}]}
        if usage is not None:
            body["usage"] = usage
    return httpx.Response(200, json=body)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,vendor,expected", [
    ("groq", {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15},
        {"input_tokens": 11, "output_tokens": 4, "total_tokens": 15}),
    ("openrouter", {"prompt_tokens": 22, "completion_tokens": 6}, {"input_tokens": 22, "output_tokens": 6}),
    ("gemini", {"promptTokenCount": 13, "candidatesTokenCount": 7, "totalTokenCount": 21, "thoughtsTokenCount": 1},
        {"input_tokens": 13, "output_tokens": 7, "total_tokens": 21, "reasoning_tokens": 1}),
    ("ollama", {"prompt_eval_count": 8, "eval_count": 3, "total_duration": 123456}, {"input_tokens": 8, "output_tokens": 3}),
])
async def test_result_local_usage_and_request_hash_match_this_wire_request(provider, vendor, expected):
    observed = {}
    def handler(request):
        system, prompt = wire_messages(provider, json.loads(request.content))
        observed.update(system=system, prompt=prompt)
        return response(provider, vendor)
    gateway = LLMGateway(configuration(provider, handler))
    try:
        output = await gateway.extract_operations({"current_message": "Какая дата поездки? 🏖️", "__response_schema": SCHEMA})
        assert dict(output) == RESULT and "call_metadata" not in output
        metadata = output.call_metadata
        assert metadata["provider"] == provider and metadata["usage"] == expected
        stats = metadata["request_metrics"]
        assert stats["prompt_sha256"] == wire_fingerprint(observed["system"], observed["prompt"])
        assert stats["context_utf8_bytes"] == len(observed["prompt"].encode("utf-8"))
        assert stats["system_utf8_bytes"] == len(observed["system"].encode("utf-8"))
        schema_bytes = json.dumps(SCHEMA, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        assert stats["schema_sha256"] == hashlib.sha256(schema_bytes).hexdigest()
        encoded = json.dumps(metadata, ensure_ascii=False)
        assert CANARY not in encoded and "Какая дата" not in encoded
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_failed_call_and_later_missing_vendor_usage_never_reuse_prior_usage():
    calls, fingerprints = [], []
    def handler(request):
        system, prompt = wire_messages("groq", json.loads(request.content))
        calls.append(prompt)
        fingerprints.append(wire_fingerprint(system, prompt))
        if len(calls) == 2:
            return httpx.Response(429, json={"error": {"type": "tokens", "message": CANARY}})
        return response("groq", {"prompt_tokens": 11, "completion_tokens": 4} if len(calls) == 1 else None)
    gateway = LLMGateway(configuration("groq", handler))
    try:
        first = await gateway.extract_operations({"current_message": "Первый запрос", "__response_schema": SCHEMA})
        with pytest.raises(ProviderError) as failure:
            await gateway.extract_operations({"current_message": "Второй запрос с другой длиной", "__response_schema": SCHEMA})
        assert failure.value.code == "RATE_LIMITED"
        assert "usage" not in failure.value.details and CANARY not in json.dumps(failure.value.as_dict())
        assert gateway.request_metrics_for_current_call()["prompt_sha256"] == fingerprints[1] != fingerprints[0]
        third = await gateway.extract_operations({"current_message": "Третий", "__response_schema": SCHEMA})
        assert "usage" not in third.call_metadata
        assert third.call_metadata["request_metrics"]["prompt_sha256"] == fingerprints[2]
        assert first.call_metadata["usage"] == {"input_tokens": 11, "output_tokens": 4}
        assert len(calls) == 3
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_pre_http_failure_clears_request_metrics_instead_of_reusing_previous_hash():
    calls = []
    def handler(request):
        calls.append(request)
        return response("groq", {"prompt_tokens": 3})
    gateway = LLMGateway(configuration("groq", handler))
    try:
        first = await gateway.extract_operations({"current_message": "Корректный JSON"})
        assert first.call_metadata["usage"] == {"input_tokens": 3}
        with pytest.raises(ProviderError):
            await gateway.extract_operations({"non_json": {1, 2}})
        assert gateway.request_metrics_for_current_call() is None
        assert len(calls) == 1
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_two_concurrent_contexts_keep_local_usage_and_hash_despite_last_call_race():
    release_slow, both_done = asyncio.Event(), asyncio.Event()
    captured, completed = {}, []
    async def handler(request):
        body = json.loads(request.content)
        system, prompt = wire_messages("groq", body)
        label = json.loads(prompt)["label"]
        captured[label] = wire_fingerprint(system, prompt)
        if label == "slow":
            await release_slow.wait()
        return response("groq", {"prompt_tokens": 101 if label == "slow" else 202,
                                  "completion_tokens": 1 if label == "slow" else 2})
    gateway = LLMGateway(configuration("groq", handler))
    async def worker(label):
        output = await gateway.extract_operations({"label": label, "current_message": label, "__response_schema": SCHEMA})
        completed.append(label)
        if label == "fast":
            release_slow.set()  # Fast legacy last_call is guaranteed to be overwritten by slow.
        if len(completed) == 2:
            both_done.set()
        await both_done.wait()
        return output.call_metadata, gateway.request_metrics_for_current_call(), gateway.last_call
    try:
        assert gateway.request_metrics_for_current_call() is None
        slow, fast = await asyncio.wait_for(asyncio.gather(worker("slow"), worker("fast")), timeout=3)
        assert completed == ["fast", "slow"]
        for row, label, tokens in [(slow, "slow", 101), (fast, "fast", 202)]:
            metadata, local_metrics, shared_last = row
            assert metadata["usage"]["input_tokens"] == tokens
            assert metadata["request_metrics"]["prompt_sha256"] == captured[label]
            assert local_metrics["prompt_sha256"] == captured[label]
            assert shared_last["request_metrics"]["prompt_sha256"] == captured["slow"]
        assert fast[0]["request_metrics"]["prompt_sha256"] != fast[2]["request_metrics"]["prompt_sha256"]
        assert gateway.request_metrics_for_current_call() is None  # Child contexts do not leak into their parent.
    finally:
        await gateway.close()
