import json

import httpx
import pytest

from otravel.providers import LLMGateway, ProviderError


def test_database_password_roundtrips_special_characters(monkeypatch):
    from sqlalchemy.engine import make_url
    from otravel.config import Settings
    password = "synthetic@password:/#% +:?"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_PASSWORD", password)
    assert make_url(Settings.from_env().database_url).password == password


def test_probe_ceiling_is_atomic_across_concurrent_invocations(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from scripts.probe_llm import reserve_probe
    path = tmp_path / "usage.json"
    with ThreadPoolExecutor(max_workers=12) as pool:
        admitted = list(pool.map(lambda _: reserve_probe(path, "2026-10-25"), range(12)))
    assert admitted.count(True) == 5
    assert json.loads(path.read_text()) == {"2026-10-25": 5}


def test_concurrent_key_updates_preserve_other_settings(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from scripts import configure_keys
    monkeypatch.setattr(configure_keys, "PROJECT", tmp_path)
    updates = {"LITEAPI_API_KEY": "sand_synthetic", "DUFFEL_ACCESS_TOKEN": "duffel_test_synthetic",
               "SERPAPI_API_KEY": "synthetic-serp-key", "OPENROUTER_API_KEY": "synthetic-router-key"}
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda item: configure_keys.write_env(dict([item])), updates.items()))
    saved = dict(line.split("=", 1) for line in (tmp_path / ".env").read_text().splitlines() if line and not line.startswith("#"))
    assert all(saved[key] == value for key, value in updates.items())
    assert (tmp_path / ".env").stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_openrouter_json_mode_receives_explicit_extraction_contract():
    def handler(request):
        body = json.loads(request.content)
        system = body["messages"][0]["content"]
        assert "Required output JSON Schema" in system
        for name in ("operations", "control", "reply", "unknowns", "conflicts"):
            assert name in system
        assert body["model"].endswith(":free")
        assert body["provider"]["max_price"] == {"prompt": 0, "completion": 0, "request": 0}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "operations": [], "control": {"action": "none"}, "reply": "Проверка",
            "unknowns": [], "conflicts": [],
        })}}]})

    gateway = LLMGateway({"LLM_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "test-local-key",
                          "http_transport": httpx.MockTransport(handler)})
    try:
        result = await gateway.extract_operations({"current_message": "Привет"})
        assert result["operations"] == []
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_shared_pool_rate_limit_is_distinguishable_and_secret_safe():
    key = "test-key-never-echo-this"

    def handler(request):
        return httpx.Response(429, json={"error": {"message": key, "metadata": {
            "limit_source": "upstream_provider_shared_pool", "provider_name": "Google AI Studio",
            "raw": key,
        }}})

    gateway = LLMGateway({"LLM_PROVIDER": "openrouter", "OPENROUTER_API_KEY": key,
                          "http_transport": httpx.MockTransport(handler)})
    try:
        with pytest.raises(ProviderError) as caught:
            await gateway.extract_operations({"current_message": "Привет"})
        detail = caught.value.as_dict()
        assert detail["details"]["limit_source"] == "upstream_provider_shared_pool"
        assert key not in json.dumps(detail)
        assert "исчерпан" not in detail["message"]
    finally:
        await gateway.close()
