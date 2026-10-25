"""Routing only: fake HTTP + fake ledger; durable cooldown has separate PG tests."""
import pytest
import json
import httpx

from otravel.providers import ProviderError
from test_api import _policy_gateway


@pytest.mark.asyncio
async def test_known_model_cooldown_skips_primary_without_spending_an_attempt():
    harness, gateway, store, session, run, models, primary = _policy_gateway("upstream_provider_shared_pool")
    # Return valid content on the first HTTP (which must already be backup).
    original = gateway.settings["http_transport"].handler
    def valid_first(request):
        models.append(json.loads(request.content)["model"])
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "operations": [], "control": {"action": "none"}, "reply": "", "unknowns": [], "conflicts": []})}}]})
    gateway.settings["http_transport"].handler = valid_first
    async def availability(provider, model):
        return {"available": model != primary, "allow_fallback": model == primary,
                "retry_after": 600, "scope": "model", "reason": "upstream_transient"}
    store.model_availability = availability
    try:
        _, metadata = await harness._extract(session, run)
    finally:
        gateway.settings["http_transport"].handler = original
        await gateway.close()
    assert models == ["test/backup:free"]
    assert len(store.reservations) == 1
    assert metadata["model"] == "test/backup:free"
    assert any(event["event_type"] == "MODEL_SKIPPED_COOLDOWN" for event in store.events)


@pytest.mark.asyncio
async def test_provider_wide_cooldown_never_tries_another_model():
    harness, gateway, store, session, run, models, _ = _policy_gateway("upstream_provider_shared_pool")
    async def availability(provider, model):
        return {"available": False, "allow_fallback": False, "retry_after": 3600,
                "scope": "provider", "reason": "unknown_quota"}
    store.model_availability = availability
    try:
        with pytest.raises(ProviderError) as error:
            await harness._extract(session, run)
        assert error.value.code == "MODEL_COOLDOWN"
    finally:
        await gateway.close()
    assert models == []
    assert store.reservations == []
