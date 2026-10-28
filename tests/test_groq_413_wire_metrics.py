"""Groq413 diagnostics and actual strict-wire fingerprints; MockTransport only."""

import asyncio
import hashlib
import json

import httpx
import pytest

from otravel.answering import READONLY_MODE
from otravel.domain import TripState
from otravel.llm_contract import build_operation_schema
from otravel.providers import LLMGateway, ProviderError, _HTTPGateway

URL = "https://api.groq.com/openai/v1/chat/completions"
SECRET = "synthetic-groq-413-private-key-ONLY"
PRIVATE = "PRIVATE_ORG_REASONING_FAILED_GENERATION_DO_NOT_EXPOSE"
ANSWER = {"operations": [], "control": {"action": "none"}, "reply": "Уточните поездку.",
          "unknowns": [], "conflicts": [], "speech_act": "ASK", "information_subject": None}
HEADERS = {"x-ratelimit-limit-requests": "1000", "x-ratelimit-remaining-requests": "999",
           "x-ratelimit-limit-tokens": "8000", "x-ratelimit-remaining-tokens": "0",
           "x-ratelimit-reset-tokens": "1m2s", "retry-after": "3",
           "x-private-account": PRIVATE}


def gateway(handler, *, strict=True):
    return LLMGateway({"LLM_PROVIDER": "groq", "GROQ_API_KEY": SECRET,
        "GROQ_MODEL": "qwen/qwen3.8-27b", "GROQ_FREE_TIER_CONFIRMED": True,
        "GROQ_STRUCTURED_TRANSPORT_ENABLED": strict, "http_transport": httpx.MockTransport(handler)})


def context(**extra):
    return {"current_message": "Запрос о поездке 🏖️", "__response_schema": build_operation_schema(TripState()), **extra}


def completion(payload=ANSWER):
    return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
        "message": {"content": json.dumps(payload, ensure_ascii=False)}}]})


def assert_fingerprint(metadata, body):
    assert metadata["wire_request_body_bytes"] == len(body)
    assert metadata["wire_request_body_sha256"] == hashlib.sha256(body).hexdigest()
    assert not {"headers", "body", "raw_request", "authorization"}.intersection(metadata)
    text = json.dumps(metadata, ensure_ascii=False)
    assert SECRET not in text and PRIVATE not in text and "Запрос о поездке" not in text


@pytest.mark.asyncio
async def test_413_is_nonretryable_http_error_with_safe_explicit_token_window_and_actual_body_hash():
    requests = []
    def handler(request):
        requests.append(request.content)
        assert request.headers["authorization"] == "Bearer " + SECRET
        return httpx.Response(413, headers=HEADERS, json={"error": {"type": "tokens",
            "code": PRIVATE, "message": PRIVATE + " tokens per minute (TPM): Limit 8000, Requested 9500, Used 1000. " + SECRET,
            "failed_generation": PRIVATE, "reasoning": PRIVATE}})
    g = gateway(handler)
    try:
        with pytest.raises(ProviderError) as failed:
            await g.extract_operations(context())
        problem = failed.value
        assert problem.code == "HTTP_ERROR" and problem.retryable is False and problem.provider == "groq"
        assert "413" in problem.message and "контекст" in problem.message
        assert problem.details["http_status"] == 413
        rates = problem.details["rate_limits"]
        assert rates == {"request_limit": 1000, "requests_remaining": 999, "token_limit": 8000,
            "tokens_remaining": 0, "tokens_reset_seconds": 62, "quota_kind": "tokens",
            "retry_after_seconds": 3, "token_window": "TPM", "token_limit_from_error": 8000,
            "tokens_requested_from_error": 9500, "tokens_used_from_error": 1000}
        assert problem.details["retry_after_seconds"] == 3
        encoded = json.dumps(problem.as_dict(), ensure_ascii=False)
        assert PRIVATE not in encoded and SECRET not in encoded
        assert "failed_generation" not in encoded and "reasoning" not in encoded
        metadata = g.call_metadata_for_current_call()
        assert "usage" not in metadata and len(requests) == 1 and g.last_call == {}
        assert_fingerprint(metadata["structured_transport"], requests[0])
    finally:
        await g.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    {"type": "tokens", "message": "Request too large, Limit8000 Requested9500 " + PRIVATE},
    {"type": "tokens", "message": "tokens per day (TPM): Limit8000 Requested9500"},
    {"type": "tokens", "message": "tokens(TPM): Limit8000 Requested9500; tokens(TPD): Limit200000 Requested1"},
    {"type": PRIVATE, "code": PRIVATE, "message": PRIVATE, "failed_generation": PRIVATE},
])
async def test_413_without_one_consistent_explicit_window_does_not_infer_tpm_or_daily_quota(error):
    g = gateway(lambda _: httpx.Response(413, headers=HEADERS, json={"error": error}))
    try:
        with pytest.raises(ProviderError) as failed:
            await g.extract_operations(context())
        details = failed.value.details
        assert failed.value.code == "HTTP_ERROR" and failed.value.retryable is False
        assert details["rate_limits"]["token_limit"] == 8000
        assert not {"token_window", "token_limit_from_error", "tokens_requested_from_error",
            "tokens_used_from_error", "daily_quota_confirmed", "reset_basis"}.intersection(details["rate_limits"])
        assert PRIVATE not in json.dumps(failed.value.as_dict())
    finally:
        await g.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"<html>" + PRIVATE.encode(), b"x" * 1_000_001,
    json.dumps({"error": {"message": "tokens(TPM): Limit8000 Requested9500" + "x" * 4096}}).encode()])
async def test_413_nonjson_oversized_or_unbounded_error_text_still_keeps_only_safe_headers(body):
    g = gateway(lambda _: httpx.Response(413, headers=HEADERS, content=body))
    try:
        with pytest.raises(ProviderError) as failed:
            await g.extract_operations(context())
        assert failed.value.code == "HTTP_ERROR" and failed.value.retryable is False
        assert failed.value.details["rate_limits"]["requests_remaining"] == 999
        assert "token_window" not in failed.value.details["rate_limits"]
        assert PRIVATE not in json.dumps(failed.value.as_dict())
    finally:
        await g.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,method,url", [
    ("groq", "GET", URL), ("groq", "POST", URL + "?private=" + PRIVATE),
    ("groq", "POST", "https://example.invalid/chat/completions"), ("zai", "POST", URL),
])
async def test_413_diagnostic_is_scoped_to_one_provider_method_and_exact_endpoint(provider, method, url):
    g = _HTTPGateway({"http_transport": httpx.MockTransport(lambda _: httpx.Response(413,
        headers=HEADERS, json={"error": {"message": "tokens(TPM): Limit8000 Requested9500"}}))})
    try:
        with pytest.raises(ProviderError) as failed:
            await g._request(provider, method, url)
        assert failed.value.code == "HTTP_ERROR" and failed.value.retryable is False
        assert failed.value.message == "Провайдер отклонил запрос."
        assert failed.value.details == {"http_status": 413}
    finally:
        await g.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("readonly", [False, True])
async def test_successful_operation_and_readonly_wire_metrics_match_actual_httpx_content(readonly):
    requests = []
    def handler(request):
        requests.append(request.content)
        return completion({"sections": ["budget", "source"]} if readonly else ANSWER)
    g = gateway(handler)
    try:
        out = await g.extract_operations(context(**({"__response_mode": READONLY_MODE} if readonly else {})))
        assert len(requests) == 1
        meta = out.call_metadata["structured_transport"]
        assert meta["validated_output_representation"] == "decoded_canonical_contract"
        assert_fingerprint(meta, requests[0])
        assert "wire_final_sha256" in meta
    finally:
        await g.close()


@pytest.mark.asyncio
async def test_default_json_object_body_is_unchanged_and_has_no_strict_wire_tracking():
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return completion()
    g = gateway(handler, strict=False)
    try:
        out = await g.extract_operations(context())
        assert set(requests[0]) == {"model", "messages", "response_format", "max_tokens"}
        assert requests[0]["response_format"] == {"type": "json_object"}
        assert "structured_transport" not in out.call_metadata
    finally:
        await g.close()


@pytest.mark.asyncio
async def test_later_unrelated_requests_cannot_overwrite_the_completed_wire_fingerprint():
    captured = []
    def handler(request):
        captured.append(request.content)
        return completion()
    g = gateway(handler)
    try:
        out = await g.extract_operations(context())
        expected = dict(out.call_metadata["structured_transport"])
        assert_fingerprint(expected, captured[0])
        # Standalone client calls and non-strict low-level requests are not
        # this completed extraction even if they use the same Groq endpoint.
        await g.client.post(URL, json={"other": PRIVATE})
        await g._request("groq", "POST", URL, json={"response_format": {"type": "json_object"}, "other": PRIVATE})
        assert g.call_metadata_for_current_call()["structured_transport"] == expected
        assert out.call_metadata["structured_transport"] == expected
    finally:
        await g.close()


@pytest.mark.asyncio
async def test_concurrent_200_and_413_keep_their_own_serialized_body_hash_and_reset_parent_context():
    released = asyncio.Event()
    observed = {}
    async def handler(request):
        label = json.loads(json.loads(request.content)["messages"][1]["content"])["label"]
        observed[label] = request.content
        if label == "slow":
            await released.wait()
            return httpx.Response(413, json={"error": {"message": PRIVATE}})
        released.set()
        return completion()
    g = gateway(handler)
    async def worker(label):
        try:
            out = await g.extract_operations(context(label=label))
            return out.call_metadata, None
        except ProviderError as error:
            return g.call_metadata_for_current_call(), error.code
    try:
        slow, fast = await asyncio.wait_for(asyncio.gather(worker("slow"), worker("fast")), timeout=3)
        assert slow[1] == "HTTP_ERROR" and fast[1] is None
        assert_fingerprint(slow[0]["structured_transport"], observed["slow"])
        assert_fingerprint(fast[0]["structured_transport"], observed["fast"])
        assert slow[0]["structured_transport"]["wire_request_body_sha256"] != fast[0]["structured_transport"]["wire_request_body_sha256"]
        assert g.call_metadata_for_current_call() == {}
        assert g._wire_request_metadata.get() is None
    finally:
        await g.close()
