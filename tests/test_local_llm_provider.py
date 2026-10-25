"""Ollama wire contract with a local-only mock; never downloads/runs a model."""
import asyncio
import json
import httpx
import pytest

from otravel.providers import LLMGateway, ProviderError


def result():
    return {"operations": [], "control": {"action": "none"}, "reply": "Уточните город.", "unknowns": ["Какой город?"], "conflicts": []}


@pytest.mark.asyncio
async def test_ollama_is_native_json_schema_without_cloud_keys_or_thinking():
    def handler(request):
        assert str(request.url) == "http://127.0.0.1:11434/api/chat"
        assert "authorization" not in request.headers
        body = json.loads(request.content)
        assert body["model"] == "qwen3.5:4b"
        assert body["stream"] is False and body["think"] is False
        assert body["format"]["required"] == ["operations", "control", "reply", "unknowns", "conflicts"]
        assert body["options"]["num_ctx"] == 16384
        assert body["options"]["num_predict"] <= 4096
        return httpx.Response(200, json={"done": True, "done_reason": "stop", "message": {"content": json.dumps(result())}})
    gateway = LLMGateway({"LLM_PROVIDER": "ollama", "http_transport": httpx.MockTransport(handler)})
    try:
        assert gateway.wall_timeout == 105
        assert await gateway.extract_operations({"current_message": "в Сербию"}) == result()
        assert gateway.last_call == {"provider": "ollama", "model": "qwen3.5:4b"}
    finally:
        await gateway.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["https://example.com", "http://169.254.169.254:11434", "http://localhost:5432", "http://localhost:11434/private", "http://x:y@localhost:11434", "http://localhost:11434?key=x"])
async def test_ollama_never_becomes_an_arbitrary_http_proxy(url):
    def forbidden(request):
        raise AssertionError("No request permitted for invalid local configuration")
    gateway = LLMGateway({"LLM_PROVIDER": "ollama", "OLLAMA_BASE_URL": url, "http_transport": httpx.MockTransport(forbidden)})
    try:
        with pytest.raises(ProviderError) as error:
            await gateway.extract_operations({})
        assert error.value.code == "CONFIG_BLOCKED"
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_local_truncation_is_not_a_valid_agent_answer():
    gateway = LLMGateway({"LLM_PROVIDER": "ollama", "http_transport": httpx.MockTransport(lambda request:
        httpx.Response(200, json={"done": True, "done_reason": "length", "message": {"content": json.dumps(result())}}))})
    try:
        with pytest.raises(ProviderError) as error:
            await gateway.extract_operations({})
        assert error.value.code == "INVALID_LLM_RESPONSE"
    finally:
        await gateway.close()
