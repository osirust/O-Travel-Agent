"""Wire JSON compaction preserves public facts/schema; intercepted HTTP only."""
import json
from copy import deepcopy

import httpx
import pytest
from test_provider_metrics import RESULT, SCHEMA, response, wire_messages

from otravel.providers import LLMGateway, _EXTRACT_PROMPT

SECRET = "gsk_compaction_SYNTHETIC_ONLY_never_an_actual_key_02468"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["groq", "openrouter", "gemini", "zai", "cloudflare", "ollama"])
async def test_compact_wire_context_and_embedded_schema_decode_to_exact_original_public_values(provider):
    public = {"trip": {"trip_id": "trip-owned", "state_version": 4, "origin": "Москва",
        "budget": {"amount_minor": 13_000_000, "currency": "RUB"},
        "travelers": [{"id": "adult-1", "type": "adult", "age": None}],
        "stays": [{"id": "stay-1", "city": "Казань", "check_in": "2026-11-06", "check_out": "2026-11-10",
                   "rooms": [{"id": "room-1", "traveler_ids": ["adult-1"]}]}],
        "constraints": [{"key": "breakfast", "value": True, "hard": True, "stay_id": "stay-1", "room_id": "room-1"}],
        "preferences": [{"key": "departure_time", "value": "early_morning", "weight": 0.7}]},
        "current_message": "  Заезд 6 ноября.  Бюджет не меняй, пожалуйста.\nСохрани всё остальное. 🏖️",
        "context": {"children_known": True, "children_count": 0, "notes": ["Два  пробела внутри строки", "новая\nстрока"]}}
    context = {**deepcopy(public), "GROQ_API_KEY": SECRET, "__response_schema": deepcopy(SCHEMA)}
    frozen = deepcopy(context)
    seen = []

    def handler(request):
        wire = json.loads(request.content)
        system, prompt = wire_messages(provider, wire)
        assert json.loads(prompt) == public
        assert len(prompt.encode()) < len(json.dumps(public, ensure_ascii=False).encode())
        assert json.loads(prompt)["current_message"] == public["current_message"]
        assert json.loads(prompt)["trip"]["preferences"] == public["trip"]["preferences"]
        assert json.loads(prompt)["trip"]["constraints"] == public["trip"]["constraints"]
        assert SECRET not in prompt and "__response_schema" not in prompt
        prefix, embedded_schema = system.split("\nRequired output JSON Schema:\n", 1)
        assert prefix == _EXTRACT_PROMPT
        assert json.loads(embedded_schema) == SCHEMA
        seen.append(prompt)
        return response(provider)

    settings = {"LLM_PROVIDER": provider, "GROQ_API_KEY": SECRET, "GROQ_FREE_TIER_CONFIRMED": True,
        "GROQ_MODEL": "qwen/qwen3.8-27b", "OPENROUTER_API_KEY": SECRET,
        "OPENROUTER_MODEL": "synthetic/compaction:free", "GEMINI_API_KEY": SECRET,
        "GEMINI_MODEL": "gemini-3.8-flash", "GEMINI_FREE_TIER_CONFIRMED": True,
        "ZAI_API_KEY": SECRET, "ZAI_MODEL": "glm-4.5-flash", "ZAI_FREE_TIER_CONFIRMED": True,
        "CLOUDFLARE_API_TOKEN": SECRET, "CLOUDFLARE_ACCOUNT_ID": "1234567890abcdef1234567890abcdef",
        "CLOUDFLARE_MODEL": "@cf/nvidia/nemotron-3-120b-a12b", "CLOUDFLARE_FREE_TIER_CONFIRMED": True,
        "http_transport": httpx.MockTransport(handler)}
    gateway = LLMGateway(settings)
    try:
        result = await gateway.extract_operations(context)
    finally:
        await gateway.close()
    assert dict(result) == RESULT and context == frozen and len(seen) == 1
    assert result.call_metadata["request_metrics"]["context_utf8_bytes"] == len(seen[0].encode())
