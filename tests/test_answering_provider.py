import asyncio
import json
from copy import deepcopy

import httpx
import pytest
from test_answering import answer

from otravel.answering import READONLY_MODE, READONLY_SCHEMA, READONLY_SYSTEM
from otravel.providers import LLMGateway


@pytest.mark.asyncio
async def test_concurrent_answer_and_extraction_have_request_local_system_schema_metrics():
    requests = []

    async def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        await asyncio.sleep(0)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer())}}]})

    settings = {
        "LLM_PROVIDER": "openrouter",
        "OPENROUTER_API_KEY": "synthetic-test-key",
        "http_transport": httpx.MockTransport(handler),
    }
    original = deepcopy({key: value for key, value in settings.items() if key != "http_transport"})
    gateway = LLMGateway(settings)
    try:
        qa, edit = await asyncio.gather(
            gateway.extract_operations(
                {
                    "current_message": "Напомни состав и бюджет",
                    "facts": {},
                    "__response_mode": READONLY_MODE,
                    "__response_schema": READONLY_SCHEMA,
                }
            ),
            gateway.extract_operations({"current_message": "Измени бюджет"}),
        )
        systems = [request["messages"][0]["content"] for request in requests]
        assert sum(system.startswith(READONLY_SYSTEM) for system in systems) == 1
        for body in requests:
            assert "__response_mode" not in body["messages"][1]["content"]
            assert "__response_schema" not in body["messages"][1]["content"]
            if body["messages"][0]["content"].startswith(READONLY_SYSTEM):
                assert body["max_tokens"] <= 1200
        assert (
            qa.call_metadata["request_metrics"]["schema_sha256"]
            != edit.call_metadata["request_metrics"]["schema_sha256"]
        )
        assert {key: value for key, value in settings.items() if key != "http_transport"} == original
    finally:
        await gateway.close()
