"""Actual outbound JSON payload through MockTransport; no model/API calls."""
import json
from copy import deepcopy

import httpx
import pytest

from otravel import providers
from otravel.harness import OPERATION_GUIDE
from otravel.providers import LLMGateway, ProviderError, safe_model_context

CANARY = "synthetic-model-context-api-secret-0123456789"
REPLY = {"operations": [], "control": {"action": "none"}, "reply": "Уточните пожелание.",
         "unknowns": [], "conflicts": [], "speech_act": "ASK", "information_subject": None}


def context():
    return {"current_message": "Сохрани ранний вылет и минимальную стоимость, меняю только дату.",
        "trip": {"preferences": [{"key": "price_priority", "value": "lowest_total", "weight": 1},
                                  {"key": "departure_time", "value": "early_morning", "weight": 1}],
                 "constraints": [{"key": "breakfast", "value": True, "hard": False},
                                 {"key": "refundable", "value": True, "hard": True}]},
        "operation_contract": deepcopy(OPERATION_GUIDE)}


@pytest.mark.asyncio
async def test_CF_wire_prompt_preserves_preference_constraint_and_operation_guide_semantic_keys(monkeypatch):
    monkeypatch.setattr(providers, "_SECRETS", {CANARY})
    captured = []
    def handler(request):
        body = json.loads(request.content)
        captured.append(json.loads(body["messages"][1]["content"]))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(REPLY)}}]})
    gateway = LLMGateway({"LLM_PROVIDER": "cloudflare", "CLOUDFLARE_API_TOKEN": CANARY,
        "CLOUDFLARE_ACCOUNT_ID": "a" * 32, "CLOUDFLARE_FREE_TIER_CONFIRMED": True,
        "CLOUDFLARE_MODEL": "@cf/nvidia/nemotron-3-120b-a12b", "http_transport": httpx.MockTransport(handler)})
    incoming = context()
    original = deepcopy(incoming)
    try:
        await gateway.extract_operations(incoming)
    finally:
        await gateway.close()
    assert len(captured) == 1 and incoming == original
    sent = captured[0]
    assert [p["key"] for p in sent["trip"]["preferences"]] == ["price_priority", "departure_time"]
    assert [c["key"] for c in sent["trip"]["constraints"]] == ["breakfast", "refundable"]
    examples = sent["operation_contract"]["supported_operations"]
    assert examples["set_preference"]["value"]["key"] == "price_priority"
    assert examples["set_constraint"]["value"]["key"] == "breakfast"
    assert examples["clear_constraint"]["value"]["key"] == "breakfast"
    assert examples["clear_preference"]["value"]["key"] == "departure_time"


@pytest.mark.asyncio
async def test_CF_wire_context_removes_secret_fields_unknown_keys_and_registered_secrets_everywhere(monkeypatch):
    monkeypatch.setattr(providers, "_SECRETS", {CANARY})
    captures = []
    incoming = context()
    incoming["current_state"] = deepcopy(incoming["trip"])
    incoming["metadata"] = {"key": "UNKNOWN-SECRET-KEY", "api_key": "UNKNOWN-API-SECRET",
        "Authorization": "Bearer UNKNOWN-AUTH-SECRET", "api-token": "UNKNOWN-API-TOKEN", "key_unknown": CANARY,
        "note": CANARY, "token": "UNKNOWN-TOKEN", "nested": {"key": "breakfast"},
        "trip": {"preferences": [{"key": "price_priority", "value": "lowest_total"}]}}
    incoming["trip"]["preferences"][0]["note"] = CANARY
    incoming["trip"]["preferences"][0]["api_key"] = "UNKNOWN-ROW-KEY"
    incoming["trip"]["constraints"].append({"key": "UNKNOWN-CONSTRAINT-SECRET", "value": CANARY})
    incoming["operation_contract"]["metadata"] = {"key": "price_priority", "secret": CANARY}
    def handler(request):
        body = json.loads(request.content)
        captures.append(json.loads(body["messages"][1]["content"]))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(REPLY)}}]})
    gateway = LLMGateway({"LLM_PROVIDER": "cloudflare", "CLOUDFLARE_API_TOKEN": CANARY,
        "CLOUDFLARE_ACCOUNT_ID": "b" * 32, "CLOUDFLARE_FREE_TIER_CONFIRMED": True,
        "http_transport": httpx.MockTransport(handler)})
    try:
        await gateway.extract_operations(incoming)
    finally:
        await gateway.close()
    sent = captures[0]
    encoded = json.dumps(sent)
    assert all(secret not in encoded for secret in (CANARY, "UNKNOWN-SECRET-KEY", "UNKNOWN-API-SECRET",
        "UNKNOWN-AUTH-SECRET", "UNKNOWN-API-TOKEN", "UNKNOWN-TOKEN", "UNKNOWN-ROW-KEY", "UNKNOWN-CONSTRAINT-SECRET"))
    assert sent["trip"]["preferences"][0]["key"] == "price_priority"
    assert sent["current_state"]["constraints"][0]["key"] == "breakfast"
    assert sent["trip"]["preferences"][0]["note"] == "[REDACTED]"
    assert "key" not in sent["trip"]["constraints"][-1]
    assert sent["metadata"]["nested"] == {}
    assert "key" not in sent["metadata"]["trip"]["preferences"][0]
    assert sent["operation_contract"]["metadata"] == {}


@pytest.mark.parametrize("collection,key", [
    ("preferences", "price_priority"), ("preferences", "departure_time"),
    ("constraints", "breakfast"), ("constraints", "refundable"), ("constraints", "pets"),
    ("constraints", "pets_allowed"), ("constraints", "nonstop"),
])
@pytest.mark.parametrize("root", ["trip", "current_state"])
def test_semantic_keys_require_a_known_collection_row_shape(root, collection, key):
    original = {root: {collection: [{"key": key, "value": {"kind": "dog", "weight_kg": 5}}]}}
    snapshot = deepcopy(original)
    assert safe_model_context(original) == original and original == snapshot
    assert "key" not in safe_model_context({root: {collection: [{"key": key}]}})[root][collection][0]


@pytest.mark.parametrize("root,collection,key", [
    ("metadata", "preferences", "price_priority"), ("state", "preferences", "price_priority"),
    ("trip", "preferences", "breakfast"), ("trip", "constraints", "price_priority"),
    ("trip", "preferences", "UNKNOWN"), ("trip", "constraints", None),
])
def test_known_value_or_familiar_field_names_do_not_bypass_context_path_restrictions(root, collection, key):
    incoming = {root: {collection: [{"key": key, "value": True}]}}
    assert "key" not in safe_model_context(incoming)[root][collection][0]


@pytest.mark.parametrize("operation,key", [("set_preference", "departure_time"), ("clear_preference", "price_priority"),
    ("set_constraint", "pets"), ("clear_constraint", "nonstop")])
@pytest.mark.parametrize("nested", [False, True])
def test_exact_code_owned_operation_examples_keep_known_semantic_keys(operation, key, nested):
    guide = {operation: {"value": {"key": key}}}
    if nested:
        guide = {"supported_operations": guide}
    incoming = {"operation_contract": guide}
    assert safe_model_context(incoming) == incoming


@pytest.mark.parametrize("incoming", [
    {"operation_contract": {"supported_operations": {"set_origin": {"value": {"key": "breakfast"}}}}},
    {"operation_contract": {"set_preference": {"value": {"key": "breakfast"}}}},
    {"operation_contract": {"set_constraint": {"value": {"key": "departure_time"}}}},
    {"operation_contract": {"supported_operations": {"set_preference": {"value": {"key": "UNKNOWN-KEY"}}}}},
    {"trip": {"preferences": {"0": {"key": "price_priority", "value": "lowest_total"}}}},
])
def test_arbitrary_operation_or_wrong_row_shapes_cannot_smuggle_a_generic_key(incoming):
    assert '"key"' not in json.dumps(safe_model_context(incoming))


def test_generic_error_and_log_safe_redaction_stays_strict_even_for_semantic_values(monkeypatch):
    monkeypatch.setattr(providers, "_SECRETS", {CANARY})
    incoming = context()
    incoming["metadata"] = {"key": CANARY, "api_key": CANARY, "note": CANARY}
    assert '"key"' not in json.dumps(providers._safe(incoming))
    error = ProviderError("TEST", CANARY, details=incoming)
    assert CANARY not in json.dumps(error.as_dict())
    assert '"key"' not in json.dumps(error.details)
    assert error.message == "[REDACTED]"


def test_public_semantic_enum_survives_short_secret_collision_but_other_values_are_redacted(monkeypatch):
    monkeypatch.setattr(providers, "_SECRETS", {"price"})
    cleaned = safe_model_context({"trip": {"preferences": [{"key": "price_priority", "value": "price"}]},
                                  "metadata": {"key": "price_priority", "note": "price"}})
    assert cleaned["trip"]["preferences"][0] == {"key": "price_priority", "value": "[REDACTED]"}
    assert cleaned["metadata"] == {"note": "[REDACTED]"}


@pytest.mark.asyncio
async def test_fresh_gateway_redacts_its_configured_secret_before_the_first_prompt_is_serialized(monkeypatch):
    monkeypatch.setattr(providers, "_SECRETS", set())
    captured = []
    def handler(request):
        captured.append(json.loads(request.content)["messages"][1]["content"])
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(REPLY)}}]})
    gateway = LLMGateway({"LLM_PROVIDER": "cloudflare", "CLOUDFLARE_API_TOKEN": CANARY,
        "CLOUDFLARE_ACCOUNT_ID": "c" * 32, "CLOUDFLARE_FREE_TIER_CONFIRMED": True,
        "http_transport": httpx.MockTransport(handler)})
    try:
        await gateway.extract_operations({"current_message": "Случайно вставлен ключ " + CANARY})
    finally:
        await gateway.close()
    assert len(captured) == 1 and CANARY not in captured[0]
    assert "[REDACTED]" in captured[0]


@pytest.mark.asyncio
async def test_readonly_wire_facts_keep_semantic_preference_and_constraint_keys(monkeypatch):
    monkeypatch.setattr(providers, "_SECRETS", {CANARY})
    captured = []
    def handler(request):
        captured.append(json.loads(json.loads(request.content)["messages"][1]["content"]))
        reply = {**REPLY, "reply": "preferences"}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply)}}]})
    gateway = LLMGateway({"LLM_PROVIDER": "cloudflare", "CLOUDFLARE_API_TOKEN": CANARY,
        "CLOUDFLARE_ACCOUNT_ID": "d" * 32, "CLOUDFLARE_FREE_TIER_CONFIRMED": True,
        "http_transport": httpx.MockTransport(handler)})
    try:
        await gateway.extract_operations({"__response_mode": "readonly_answer_v1", "current_message": "Какие пожелания сохранены?",
            "facts": {"preferences": [{"key": "departure_time", "value": "early_morning"}],
                      "constraints": [{"key": "nonstop", "value": True, "hard": True}]}})
    finally:
        await gateway.close()
    assert captured[0]["facts"]["preferences"][0]["key"] == "departure_time"
    assert captured[0]["facts"]["constraints"][0]["key"] == "nonstop"


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["GEMINI_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY", "CLOUDFLARE_API_TOKEN", "ZAI_API_KEY"])
async def test_constructor_registers_only_known_credential_fields_not_arbitrary_settings_values(field, monkeypatch):
    monkeypatch.setattr(providers, "_SECRETS", set())
    for name in providers._LLM_CREDENTIAL_FIELDS:
        monkeypatch.delenv(name, raising=False)
    gateway = LLMGateway({field.lower(): CANARY, "unknown_api_key": "UNTRUSTED-SETTINGS-VALUE",
                          "metadata": {"api_key": "UNTRUSTED-NESTED-VALUE"}})
    try:
        assert providers._SECRETS == {CANARY}
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_first_prompt_redacts_named_environment_credential_without_scanning_environment(monkeypatch):
    monkeypatch.setattr(providers, "_SECRETS", set())
    for name in providers._LLM_CREDENTIAL_FIELDS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", CANARY)
    captured = []
    def handler(request):
        captured.append(json.loads(request.content)["messages"][1]["content"])
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(REPLY)}}]})
    gateway = LLMGateway({"LLM_PROVIDER": "cloudflare", "CLOUDFLARE_ACCOUNT_ID": "e" * 32,
        "CLOUDFLARE_FREE_TIER_CONFIRMED": True, "http_transport": httpx.MockTransport(handler)})
    try:
        await gateway.extract_operations({"current_message": "Опечатка " + CANARY})
    finally:
        await gateway.close()
    assert len(captured) == 1 and CANARY not in captured[0]
