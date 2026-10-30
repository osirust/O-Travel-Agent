"""Bounded diagnostics: no credentials, raw HTTP bodies, or model reasoning."""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import hashlib
import json
import re
from typing import Any


MODEL_FIELDS = frozenset({"operations", "control", "reply", "unknowns", "conflicts", "speech_act", "information_subject"})
PRIVATE_FIELDS = frozenset({"api_key", "apikey", "api_token", "authorization", "password", "access_token", "refresh_token", "secret", "headers",
    "reasoning", "reasoning_content", "thinking", "raw_response", "raw_request"})


def count(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value <= 10**12 else None


def usage_counts(provider: str, body: dict) -> dict[str, int]:
    """Keep only vendor-reported counts; never estimate tokens from characters."""
    if provider == "gemini":
        data = body.get("usageMetadata")
        fields = {"promptTokenCount": "input_tokens", "candidatesTokenCount": "output_tokens",
            "totalTokenCount": "total_tokens", "cachedContentTokenCount": "cached_input_tokens",
            "thoughtsTokenCount": "reasoning_tokens"}
    elif provider == "ollama":
        data = body
        fields = {"prompt_eval_count": "input_tokens", "eval_count": "output_tokens"}
    else:
        data = body.get("usage")
        fields = {"prompt_tokens": "input_tokens", "completion_tokens": "output_tokens", "total_tokens": "total_tokens"}
    if not isinstance(data, Mapping):
        return {}
    return {target: number for source, target in fields.items() if (number := count(data.get(source))) is not None}


def request_stats(system: str, prompt: str, schema: dict) -> dict:
    encoded = json.dumps(schema, ensure_ascii=False, separators=(",", ":")).encode()
    return {"system_utf8_bytes": len(system.encode()), "context_utf8_bytes": len(prompt.encode()),
        "schema_utf8_bytes": len(encoded), "schema_sha256": hashlib.sha256(encoded).hexdigest(),
        "prompt_sha256": hashlib.sha256((system + "\0" + prompt).encode()).hexdigest()}


def seconds_hint(value: Any, *, duration: bool = False) -> int | None:
    """Parse documented seconds/reset durations; round fractions upwards."""
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 60:
        return None
    text = value.strip()
    try:
        if re.fullmatch(r"[0-9]{1,8}(?:\.[0-9]{1,9})?", text):
            amount = Decimal(text)
        elif duration and re.fullmatch(r"(?:(?:[0-9]{1,8}(?:\.[0-9]{1,9})?)h)?(?:(?:[0-9]{1,8}(?:\.[0-9]{1,9})?)m)?(?:(?:[0-9]{1,8}(?:\.[0-9]{1,9})?)s)?", text):
            parts = re.findall(r"([0-9]+(?:\.[0-9]+)?)([hms])", text)
            if not parts:
                return None
            amount = sum((Decimal(number) * {"h": 3600, "m": 60, "s": 1}[unit] for number, unit in parts), Decimal(0))
        else:
            return None
        if not amount.is_finite() or not 0 < amount <= 86400:
            return None
        return int(amount.to_integral_value(rounding=ROUND_CEILING))
    except (InvalidOperation, ValueError, OverflowError):
        return None


def groq_rate_facts(headers: Mapping, body: Any) -> dict:
    """Whitelist rate counters and reset hints from the fixed Groq endpoint."""
    result = {}
    for header, target in (("x-ratelimit-limit-requests", "request_limit"),
        ("x-ratelimit-remaining-requests", "requests_remaining"),
        ("x-ratelimit-limit-tokens", "token_limit"), ("x-ratelimit-remaining-tokens", "tokens_remaining")):
        value = headers.get(header)
        if isinstance(value, str) and re.fullmatch(r"[0-9]{1,12}", value.strip()):
            result[target] = int(value.strip())
    for header, target in (("x-ratelimit-reset-requests", "requests_reset_seconds"),
        ("x-ratelimit-reset-tokens", "tokens_reset_seconds")):
        if (seconds := seconds_hint(headers.get(header), duration=True)) is not None:
            result[target] = seconds
    error = body.get("error") if isinstance(body, Mapping) else None
    if isinstance(error, Mapping) and isinstance(error.get("type"), str) and error["type"] in {"tokens", "requests"}:
        result["quota_kind"] = error["type"]
    retry = seconds_hint(headers.get("retry-after"))
    if retry is None and result.get("quota_kind"):
        retry = result.get("tokens_reset_seconds" if result["quota_kind"] == "tokens" else "requests_reset_seconds")
    if retry is not None:
        result["retry_after_seconds"] = retry
    # Error counters are diagnostics only. The documented token window must
    # be explicit; neither arbitrary numbers nor an organisation/model name
    # infer a quota, reset time, retry policy, or provider allowance.
    message = error.get("message") if isinstance(error, Mapping) else None
    if isinstance(message, str) and len(message) <= 4096 and error.get("type") != "requests":
        windows = list(re.finditer(r"\btokens(?:\s+per\s+(minute|day))?\s*\(\s*(TPM|TPD)\s*\)\s*:",
                                  message, re.IGNORECASE))
        if len(windows) == 1:
            window = windows[0]
            period, unit = window[1], window[2].upper()
            consistent_window = period is None or period.casefold() == {"TPM": "minute", "TPD": "day"}[unit]
            values = {}
            valid = consistent_window
            counters = message[window.end():]
            fields = list(re.finditer(r"\b(Limit|Requested|Used)\b(?:\s*:\s*|\s+)([^\s,;]+)",
                                      counters, re.IGNORECASE))
            if len(fields) != len(re.findall(r"\b(?:Limit|Requested|Used)\b", counters, re.IGNORECASE)):
                valid = False
            for match in fields:
                label, raw = match[1].casefold(), match[2]
                if match.end() + 1 < len(counters) and counters[match.end()] in {",", ";"} and counters[match.end() + 1].isdigit():
                    valid = False
                    break
                if re.match(r"\s+[0-9]", counters[match.end():]):
                    valid = False
                    break
                # A sentence-ending dot is punctuation; fractional/exponent/
                # signed values still fail the full bounded integer match.
                raw = raw.removesuffix(".")
                if re.fullmatch(r"[0-9]{1,12}", raw) is None:
                    valid = False
                    break
                number = int(raw)
                if label in values and values[label] != number:
                    valid = False
                    break
                values[label] = number
            if valid and {"limit", "requested"}.issubset(values):
                result.update(token_window=unit, token_limit_from_error=values["limit"],
                              tokens_requested_from_error=values["requested"])
                if "used" in values:
                    result["tokens_used_from_error"] = values["used"]
    return result


def model_snapshot(value: Mapping, settings: Mapping | None = None, *, limit: int = 65536) -> dict:
    """Preserve the structured contract for attribution, excluding hidden data."""
    secrets = [item for key, item in (settings or {}).items() if isinstance(item, str) and item
        and re.search(r"(?:API_KEY|AUTH_KEY|API_SECRET|ACCESS_TOKEN|API_TOKEN|PASSWORD)$", str(key), re.I)]
    def clean(item):
        if isinstance(item, Mapping):
            return {str(key): clean(part) for key, part in item.items()
                if str(key).casefold().replace("-", "_") not in PRIVATE_FIELDS
                and not str(key).casefold().replace("-", "_").endswith(("_api_key", "_api_secret", "_access_token", "_api_token", "_password", "_secret", "_refresh_token", "_auth_key"))}
        if isinstance(item, (list, tuple)):
            return [clean(part) for part in item]
        if isinstance(item, str):
            for secret in sorted(secrets, key=len, reverse=True):
                item = item.replace(secret, "[REDACTED]")
        return item
    selected = clean({key: part for key, part in value.items() if key in MODEL_FIELDS})
    try:
        encoded = json.dumps(selected, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    except (TypeError, ValueError):
        return {"captured": False, "reason": "non_json_contract"}
    base = {"sha256": hashlib.sha256(encoded).hexdigest(), "utf8_bytes": len(encoded)}
    return {**base, "captured": True, "value": selected} if len(encoded) <= limit else {**base, "captured": False, "reason": "size_limit"}
