"""Default policy wiring only; no database, model or supplier HTTP."""

from copy import deepcopy
from dataclasses import replace

import httpx
import pytest

from otravel.api import create_app
from otravel.config import Settings


class CaptureStore:
    def __init__(self):
        self.states = []

    async def create_session(self, state, owner_token_hash):
        self.states.append(deepcopy(state))
        return {
            "session_id": "synthetic-session",
            "trip_id": state["trip_id"],
            "state_version": 0,
            "status": "TRIP_DRAFT",
            "state": deepcopy(state),
        }


def settings(**changes):
    return replace(Settings("postgresql+asyncpg://unused", "zai", 1, 8000, 0, 1), **changes)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [None, {}, {"initial_trip": {"origin": "Москва"}}])
async def test_new_trip_uses_explicit_server_hybrid_default_without_guessing_citizenship(body):
    repository = CaptureStore()
    app = create_app(
        settings(default_search_mode="HYBRID", default_flight_provider="duffel"),
        store=repository,
        llm=object(),
        travel=object(),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/sessions", json=body)
    assert response.status_code == 201
    policy = response.json()["trip"]["search_policy"]
    assert policy == {
        "mode": "HYBRID",
        "hotel_provider": "liteapi",
        "flight_provider": "duffel",
        "demo_fallback": True,
        "guest_nationality": None,
    }
    assert repository.states[0]["search_policy"] == policy


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["DEMO", "HYBRID"])
async def test_explicit_initial_policy_is_not_overwritten_by_server_default(mode):
    app = create_app(
        settings(default_search_mode="HYBRID", default_flight_provider="duffel"),
        store=CaptureStore(),
        llm=object(),
        travel=object(),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/sessions",
            json={"initial_trip": {"search_policy": {"mode": mode, "flight_provider": "serpapi"}}},
        )
    assert response.status_code == 201
    assert response.json()["trip"]["search_policy"]["mode"] == mode
    assert response.json()["trip"]["search_policy"]["flight_provider"] == "serpapi"


@pytest.mark.parametrize(
    "changes",
    [
        {"default_search_mode": "LIVE"},
        {"default_hotel_provider": "unknown"},
        {"default_flight_provider": "paid-provider"},
    ],
)
def test_unsupported_default_is_rejected(changes):
    with pytest.raises(ValueError, match="Unsupported default travel search configuration"):
        settings(**changes)


def test_env_search_defaults_are_explicit_and_demo_is_the_portable_default(monkeypatch):
    monkeypatch.delenv("DEFAULT_SEARCH_MODE", raising=False)
    assert Settings.from_env().default_search_mode == "DEMO"
    monkeypatch.setenv("DEFAULT_SEARCH_MODE", "HYBRID")
    monkeypatch.setenv("DEFAULT_FLIGHT_PROVIDER", "duffel")
    configured = Settings.from_env()
    assert configured.default_search_mode == "HYBRID" and configured.default_flight_provider == "duffel"
