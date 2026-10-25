"""Synthetic comparison credentials never reach public output or local Ollama."""
import json
from types import SimpleNamespace

import pytest

from otravel.api import _clean
from otravel.observability import model_snapshot
from otravel.providers import ProviderError
from scripts import local_backend


CREDENTIALS = ("ZAI_API_KEY", "COHERE_API_KEY", "CLOUDFLARE_API_TOKEN", "NVIDIA_API_KEY")


@pytest.mark.parametrize("name", CREDENTIALS)
def test_new_configured_credentials_are_removed_from_public_strings_and_model_traces(name, monkeypatch):
    secret = "synthetic-comparison-credential-" + name
    monkeypatch.setenv(name, secret)
    value = {"reply": "Credential echo: " + secret, "operations": [{"type": "set_preference", "value": {
        "key": "comfort", "value": secret, name: secret, "api_token": secret, "external_api_token": secret}}],
        "control": {"action": "none"}, "unknowns": [], "conflicts": []}
    cleaned = _clean(value)
    snapshot = model_snapshot(value, {name: secret})
    assert secret not in json.dumps(cleaned) and secret not in json.dumps(snapshot)
    assert cleaned["reply"] == "Credential echo: [REDACTED]"
    assert snapshot["value"]["operations"][0]["value"] == {"key": "comfort", "value": "[REDACTED]"}
    assert name not in cleaned["operations"][0]["value"]
    assert "external_api_token" not in cleaned["operations"][0]["value"]


def test_token_fields_and_query_strings_do_not_leak_in_provider_errors():
    error = ProviderError("synthetic", "Failed: https://example.invalid/?api_token=synthetic-token",
        details={"api_token": "synthetic-unregistered-token", "provider_api_token": "synthetic-other-token", "safe": 1})
    assert error.details == {"safe": 1}
    assert "synthetic-token" not in error.message and "[REDACTED]" in error.message


def test_local_backend_environment_excludes_all_comparison_credentials(monkeypatch):
    for name in (*CREDENTIALS, "CLOUDFLARE_ACCOUNT_ID"):
        monkeypatch.setenv(name, "synthetic-parent-credential")
    env = local_backend.environment(SimpleNamespace(password="synthetic-local-db-password", ip="127.0.0.1"))
    assert all(name in local_backend.KEYS and env[name] == "" for name in (*CREDENTIALS, "CLOUDFLARE_ACCOUNT_ID"))
    assert "synthetic-parent-credential" not in json.dumps(env)
    assert env["LLM_PROVIDER"] == "ollama" and env["MAX_LLM_CALLS_PER_DAY"] == "0"
