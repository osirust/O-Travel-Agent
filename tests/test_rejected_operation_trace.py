"""Invalid final JSON can be attributed without storing credentials/thinking."""
import json
from copy import deepcopy

import httpx
import pytest

from otravel.domain import sample_trip
from otravel.llm_contract import build_operation_schema
from otravel.providers import LLMGateway, ProviderError

SECRET = "synthetic-rejected-contract-test-only-key"
INVALID = {"operations": [{"type": "update_traveler", "target_id": "unknown-person", "value": {"age": 4}}],
    "control": {"action": "continue"}, "speech_act": "CHANGE", "information_subject": None,
    "reply": "Предлагаю изменить возраст.", "unknowns": [], "conflicts": []}


@pytest.mark.asyncio
async def test_invalid_operation_keeps_bounded_final_contract_and_schema_path():
    payload = deepcopy(INVALID)
    def handler(_request):
        return httpx.Response(200, json={"model": "glm-4.5-flash", "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(payload), "reasoning_content": SECRET}}]})
    gateway = LLMGateway({"LLM_PROVIDER": "zai", "ZAI_MODEL": "glm-4.5-flash", "ZAI_API_KEY": SECRET,
        "ZAI_FREE_TIER_CONFIRMED": True, "http_transport": httpx.MockTransport(handler)})
    try:
        with pytest.raises(ProviderError) as caught:
            await gateway.extract_operations({"current_message": "Четыре года", "__response_schema": build_operation_schema(sample_trip())})
        error = caught.value
        assert error.code == "INVALID_LLM_OPERATION" and error.details["schema_errors"]
        snapshot = error.details["rejected_structured_contract"]
        assert snapshot["captured"] is True and snapshot["value"] == payload
        assert SECRET not in json.dumps(error.details) and "reasoning_content" not in json.dumps(error.details)
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_credential_echo_is_rejected_before_invalid_contract_can_be_recorded():
    payload = deepcopy(INVALID)
    payload["reply"] = SECRET
    gateway = LLMGateway({"LLM_PROVIDER": "zai", "ZAI_MODEL": "glm-4.5-flash", "ZAI_API_KEY": SECRET,
        "ZAI_FREE_TIER_CONFIRMED": True, "http_transport": httpx.MockTransport(lambda _request: httpx.Response(200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload)}}]}))})
    try:
        with pytest.raises(ProviderError) as caught:
            await gateway.extract_operations({"__response_schema": build_operation_schema(sample_trip())})
        assert caught.value.code == "INVALID_LLM_RESPONSE"
        assert "rejected_structured_contract" not in caught.value.details
        assert SECRET not in str(caught.value)
    finally:
        await gateway.close()
