"""Documented business codes only; no provider prose or guessed quota reset."""
from typing import Any

ZAI_ERRORS = 'https://docs.z.ai/api-reference/api-code'
CF_ERRORS = 'https://developers.cloudflare.com/workers-ai/platform/errors/'
CF_PRICING = 'https://developers.cloudflare.com/workers-ai/platform/pricing/'
_KINDS = {
    'zai': {1113: 'billing', 1302: 'request_rate', 1305: 'capacity',
            1308: 'usage_quota', 1309: 'billing', 1310: 'usage_quota',
            1311: 'billing', 1313: 'usage_quota', 1314: 'billing', 1315: 'billing',
            **{code: 'usage_quota' for code in range(1316, 1322)}},
    'cloudflare': {3036: 'daily_quota', 3040: 'capacity'},
}


def classify_rate_failure(provider: str, http_status: int, body: Any = None) -> dict | None:
    if provider not in _KINDS or type(http_status) is not int or http_status != 429:
        return None
    candidates = []
    if isinstance(body, dict):
        error = body.get('error')
        if isinstance(error, dict) and 'code' in error:
            candidates.append(error['code'])
        if provider == 'cloudflare' and isinstance(body.get('errors'), list):
            if len(body['errors']) > 10:
                candidates.append(None)  # A truncated array cannot attest a unique code.
            candidates.extend(error.get('code') for error in body['errors'][:10] if isinstance(error, dict))
    codes = set()
    for candidate in candidates:
        if type(candidate) is int:
            code = candidate
        elif isinstance(candidate, str) and len(candidate) == 4 and candidate.isascii() and candidate.isdecimal():
            code = int(candidate)
        else:
            codes.add(None)
            continue
        codes.add(code if code in _KINDS[provider] else None)
    code = next(iter(codes)) if len(codes) == 1 else None
    kind = _KINDS[provider].get(code, 'unknown')
    return {'business_code': code, 'failure_kind': kind,
            'daily_quota_confirmed': provider == 'cloudflare' and code == 3036,
            'reset_basis': 'next_utc_midnight' if provider == 'cloudflare' and code == 3036 else None,
            'engineering_wait_seconds': 300 if kind in {'unknown', 'request_rate', 'capacity'} else None,
            'automatic_retry': False, 'requires_explicit_new_request': True}
