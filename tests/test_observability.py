"""Pure privacy/counting contracts; no HTTP, model, DB or environment secrets."""

from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import time

import httpx
import pytest

from otravel.observability import groq_rate_facts, model_snapshot, request_stats, seconds_hint, usage_counts


CANARY = "gsk_observability_CANARY_ONLY_0123456789abcdef0123456789abcdef"


@pytest.mark.parametrize("provider,body,expected", [
    ("groq", {"usage": {"prompt_tokens": 123, "completion_tokens": 45, "total_tokens": 168}},
        {"input_tokens": 123, "output_tokens": 45, "total_tokens": 168}),
    ("openrouter", {"usage": {"prompt_tokens": 7, "completion_tokens": 2}},
        {"input_tokens": 7, "output_tokens": 2}),
    ("gemini", {"usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 3,
        "totalTokenCount": 20, "cachedContentTokenCount": 4, "thoughtsTokenCount": 5}},
        {"input_tokens": 12, "output_tokens": 3, "total_tokens": 20, "cached_input_tokens": 4, "reasoning_tokens": 5}),
    ("ollama", {"prompt_eval_count": 8, "eval_count": 0, "total_duration": 12345},
        {"input_tokens": 8, "output_tokens": 0}),
])
def test_only_reported_vendor_counts_are_retained_without_synthesizing_totals(provider, body, expected):
    assert usage_counts(provider, body) == expected


@pytest.mark.parametrize("bad", [True, -1, 1.5, "123", None, 10**12 + 1])
def test_malformed_vendor_counts_never_become_budget_usage(bad):
    assert usage_counts("groq", {"usage": {"prompt_tokens": bad, "completion_tokens": 2}}) == {"output_tokens": 2}
    assert usage_counts("gemini", {"usageMetadata": {"promptTokenCount": bad, "totalTokenCount": 0}}) == {"total_tokens": 0}
    assert usage_counts("ollama", {"prompt_eval_count": bad, "eval_count": 0}) == {"output_tokens": 0}


def test_missing_usage_and_arbitrary_text_do_not_generate_token_estimates():
    for provider in ("groq", "openrouter", "gemini", "ollama"):
        assert usage_counts(provider, {"content": "Длинный ответ " * 1000}) == {}
    for usage in (None, [], "123"):
        assert usage_counts("groq", {"usage": usage}) == {}
    assert usage_counts("groq", {"usage": {"prompt_tokens": 12}, "total_tokens": 9999}) == {"input_tokens": 12}


@pytest.mark.parametrize("value,duration,expected", [
    ("1", False, 1), ("0.001", False, 1), ("1.01", False, 2), ("86400", False, 86400),
    (" 2.5 ", False, 3), ("1h2m3.2s", True, 3724), ("1.5m", True, 90),
    ("0.001h", True, 4), ("23h59m59.1s", True, 86400), ("24h", True, 86400),
])
def test_retry_and_reset_hints_round_up_without_underwaiting(value, duration, expected):
    assert seconds_hint(value, duration=duration) == expected


@pytest.mark.parametrize("group", [
    [True, 1, 1.5, Decimal("1.1"), None, [], {}],
    ["", "0", "-1", "+1", "1e3", "NaN", "Infinity", "0.0000000001", "1.5 seconds", "1m2h", "1d"],
    ["86400.000000001", "25h", "999999999999999999999", "1" * 61],
])
def test_invalid_types_formats_and_unbounded_hints_are_ignored(group):
    for value in group:
        assert seconds_hint(value, duration=True) is None
    assert seconds_hint("1m", duration=False) is None


def test_http_date_hints_never_use_a_host_clock(monkeypatch):
    def forbidden_clock(*args, **kwargs):
        raise AssertionError("Seconds hint must not read a clock")
    with monkeypatch.context() as patch:
        patch.setattr(time, "time", forbidden_clock)
        patch.setattr(time, "monotonic", forbidden_clock)
        for date in ("Wed, 21 Oct 2015 07:28:00 GMT", "Sun, 25 Oct 2099 07:28:00 GMT"):
            assert seconds_hint(date) is None
            assert seconds_hint(date, duration=True) is None


def test_groq_rate_facts_whitelist_counters_and_hints_without_body_or_headers():
    headers = httpx.Headers({"X-RateLimit-Limit-Requests": "1000", "X-RateLimit-Remaining-Requests": "998",
        "X-RateLimit-Limit-Tokens": "8000", "X-RateLimit-Remaining-Tokens": " 1234 ",
        "X-RateLimit-Reset-Requests": "1m", "X-RateLimit-Reset-Tokens": "1.5s", "Retry-After": "2.1",
        "Authorization": "Bearer " + CANARY, "X-Organization-ID": "org-private-canary",
        "Set-Cookie": "private-cookie", "X-Arbitrary-Header": "never-copy-me"})
    body = {"organization": "org-private-canary", "api_key": CANARY,
            "error": {"type": "tokens", "message": CANARY, "request": {"headers": {"api-key": CANARY}}}}
    assert groq_rate_facts(headers, body) == {"request_limit": 1000, "requests_remaining": 998,
        "token_limit": 8000, "tokens_remaining": 1234, "requests_reset_seconds": 60,
        "tokens_reset_seconds": 2, "quota_kind": "tokens", "retry_after_seconds": 3}
    encoded = json.dumps(groq_rate_facts(headers, body))
    assert CANARY not in encoded and "org-private" not in encoded and "never-copy" not in encoded


def test_groq_rate_counter_strings_must_be_nonnegative_integers_only():
    for bad in (True, 12, "-1", "1.2", "NaN", "1e3", "1000000000000", "12 requests", [], {}):
        assert groq_rate_facts({"x-ratelimit-remaining-requests": bad}, {}) == {}
    assert groq_rate_facts({"x-ratelimit-remaining-requests": "0"}, {}) == {"requests_remaining": 0}


@pytest.mark.parametrize("error", [None, [], {"type": []}, {"type": {}}, {"type": True}, {"type": "untrusted-quota"}])
def test_malformed_or_unhashable_error_type_does_not_crash_or_choose_reset(error):
    facts = groq_rate_facts({"x-ratelimit-reset-requests": "1m", "x-ratelimit-reset-tokens": "2s"}, {"error": error})
    assert facts == {"requests_reset_seconds": 60, "tokens_reset_seconds": 2}


@pytest.mark.parametrize("kind,expected", [("tokens", 2), ("requests", 60)])
def test_reset_fallback_uses_only_the_explicit_quota_kind(kind, expected):
    facts = groq_rate_facts({"retry-after": "not-a-duration", "x-ratelimit-reset-requests": "1m",
                             "x-ratelimit-reset-tokens": "1.5s"}, {"error": {"type": kind}})
    assert facts["retry_after_seconds"] == expected and facts["quota_kind"] == kind


def contract():
    return {"operations": [{"type": "set_budget", "value": {"amount_minor": 10_000_000, "currency": "RUB"}}],
        "control": {"action": "none"}, "reply": "Сумма сохранена.", "unknowns": [], "conflicts": [],
        "speech_act": "CHANGE", "information_subject": None}


def test_model_snapshot_selects_contract_fields_and_hashes_redacted_json_not_hidden_data():
    value = contract()
    value.update(reasoning="do-not-save-reasoning", chain_of_thought="do-not-save-thoughts",
                 raw_response={"api_key": CANARY}, headers={"Authorization": CANARY}, usage={"prompt_tokens": 999})
    expected = contract()
    encoded = json.dumps(expected, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    snapshot = model_snapshot(value, {"GROQ_API_KEY": CANARY})
    assert snapshot == {"captured": True, "value": expected, "utf8_bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()}
    assert "reasoning" not in json.dumps(snapshot) and CANARY not in json.dumps(snapshot)


def test_nested_private_and_hidden_fields_cannot_escape_inside_operation_values():
    value = contract()
    value["reply"] = "Configured key: " + CANARY
    value["operations"][0]["value"].update({
        "GROQ_API_KEY": CANARY, "Authorization": CANARY, "headers": {"X-API-Key": CANARY},
        "provider_password": "unconfigured-private-password", "client_secret": "unconfigured-client-secret",
        "oauth_refresh_token": "unconfigured-refresh-token", "reasoning": "nested-private-reasoning",
        "raw_request": {"headers": {"Authorization": CANARY}}, "raw_response": "nested-private-http",
        "safe_metadata": [{"api-key": "unconfigured-private-key", "label": CANARY}],
    })
    original = deepcopy(value)
    snapshot = model_snapshot(value, {"GROQ_API_KEY": CANARY})
    assert snapshot["captured"] is True
    captured = snapshot["value"]
    assert captured["reply"] == "Configured key: [REDACTED]"
    assert captured["operations"][0]["value"] == {"amount_minor": 10_000_000, "currency": "RUB",
        "safe_metadata": [{"label": "[REDACTED]"}]}
    assert value == original
    encoded = json.dumps(snapshot, ensure_ascii=False)
    assert CANARY not in encoded and "unconfigured-private" not in encoded and "nested-private" not in encoded


def test_size_cap_uses_utf8_bytes_and_preserves_fingerprint_without_truncated_value():
    value = contract()
    value["reply"] = "Проверка 🏖️ " * 50
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    small = model_snapshot(value, limit=len(encoded) - 1)
    exact = model_snapshot(value, limit=len(encoded))
    assert small == {"captured": False, "reason": "size_limit", "utf8_bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()}
    assert exact["captured"] is True and exact["value"] == value
    assert exact["sha256"] == small["sha256"] and exact["utf8_bytes"] == small["utf8_bytes"]


def test_non_json_or_nan_contract_is_not_serialized_as_an_accepted_snapshot():
    for bad in (float("nan"), float("inf"), float("-inf"), {1, 2}, object()):
        value = contract()
        value["operations"][0]["value"]["amount_minor"] = bad
        assert model_snapshot(value) == {"captured": False, "reason": "non_json_contract"}


def test_request_stats_measure_bytes_and_hashes_without_token_estimation():
    system, prompt, schema = "Правила", "Ребёнку четыре 🏖️", {"type": "string", "description": "Возраст"}
    stats = request_stats(system, prompt, schema)
    encoded = json.dumps(schema, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert stats == {"system_utf8_bytes": len(system.encode("utf-8")), "context_utf8_bytes": len(prompt.encode("utf-8")),
        "schema_utf8_bytes": len(encoded), "schema_sha256": hashlib.sha256(encoded).hexdigest(),
        "prompt_sha256": hashlib.sha256((system + "\0" + prompt).encode("utf-8")).hexdigest()}
    assert not any("tokens" in name for name in stats)
