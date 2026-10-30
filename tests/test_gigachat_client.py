"""Personal GigaChat transport with synthetic secrets/MockTransport, no API."""

import asyncio
import base64
import json
import ssl
from copy import deepcopy
from uuid import UUID

import httpx
import pytest

from otravel.gigachat_client import BASE_URL, OAUTH_URL, PERSONAL_SCOPE, GigaChatClient, GigaChatError

NOW = 1_800_000_000
AUTH = base64.b64encode(b"synthetic-client:synthetic-secret").decode()
TOKEN = "synthetic.access-token.1"
PRIVATE = "NEVER_EXPOSE_ACCOUNT_BODY_REASONING_TOKEN"
MODELS = {"data": [{"id": "GigaChat-3-Ultra", "object": "model"}], "object": "list"}
BALANCE = {"balance": [{"usage": "GigaChat-3-Ultra", "value": 1_000_000}]}
COMPLETION = {"choices": [{"message": {"role": "assistant", "content": '{"answer":"Да"}'},
    "finish_reason": "stop"}], "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}}
MESSAGES = [{"role": "system", "content": "Return JSON."}, {"role": "user", "content": "Привет 🏖️"}]


def oauth(*, expires=(NOW + 1800) * 1000, token=TOKEN):
    return httpx.Response(200, json={"access_token": token, "expires_at": expires})


def client(handler, **kwargs):
    return GigaChatClient(AUTH, transport=httpx.MockTransport(handler), clock=lambda: NOW, **kwargs)


def route(request):
    if str(request.url) == OAUTH_URL:
        return oauth()
    if request.url.path == "/v1/models":
        return httpx.Response(200, json=MODELS)
    if request.url.path == "/v1/balance":
        return httpx.Response(200, json=BALANCE)
    if request.url.path == "/v1/chat/completions":
        return httpx.Response(200, json=COMPLETION)
    raise AssertionError("Unreviewed endpoint")


@pytest.mark.asyncio
async def test_fixed_oauth_and_api_endpoints_cache_token_and_preserve_response_usage_and_inputs():
    calls, registered = [], []
    def handler(request):
        calls.append(request)
        if str(request.url) == OAUTH_URL:
            assert request.method == "POST" and request.headers["authorization"] == "Basic " + AUTH
            assert request.headers["content-type"] == "application/x-www-form-urlencoded"
            assert request.content == b"scope=GIGACHAT_API_PERS"
            assert UUID(request.headers["rquid"]).version == 4
        else:
            assert request.headers["authorization"] == "Bearer " + TOKEN
            assert AUTH not in request.content.decode()
        return route(request)
    c = client(handler, register_secret=registered.append)
    before, fmt = deepcopy(MESSAGES), {"type": "json_object"}
    try:
        assert await c.models() == MODELS
        assert await c.balance() == BALANCE
        assert await c.complete("GigaChat-3-Ultra", MESSAGES, fmt, 2048) == COMPLETION
        assert MESSAGES == before and fmt == {"type": "json_object"}
        assert [str(x.url) for x in calls] == [OAUTH_URL, BASE_URL + "/models", BASE_URL + "/balance", BASE_URL + "/chat/completions"]
        assert json.loads(calls[-1].content) == {"model": "GigaChat-3-Ultra", "messages": MESSAGES,
            "max_tokens": 2048, "response_format": {"type": "json_object"}, "stream": False}
        assert registered == [AUTH, TOKEN] and PERSONAL_SCOPE == "GIGACHAT_API_PERS"
    finally:
        await c.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds", [False, True])
async def test_authoritative_seconds_or_milliseconds_expiry_and_new_nonce_on_refresh(seconds):
    clock, nonces, authorizations = [NOW], [], []
    def handler(request):
        if str(request.url) == OAUTH_URL:
            nonces.append(request.headers["rquid"])
            expiry = clock[0] + 1800
            return oauth(expires=expiry if seconds else expiry * 1000, token="token." + str(len(nonces)))
        authorizations.append(request.headers["authorization"])
        return httpx.Response(200, json=MODELS)
    c = GigaChatClient(AUTH, transport=httpx.MockTransport(handler), clock=lambda: clock[0])
    try:
        await c.models()
        clock[0] = NOW + 1769
        await c.models()  # 31s remains: cache remains usable.
        clock[0] = NOW + 1771
        await c.models()  # 29s remains: explicit next flow refreshes once.
        assert len(nonces) == len(set(nonces)) == 2
        assert all(UUID(value).version == 4 for value in nonces)
        assert authorizations == ["Bearer token.1", "Bearer token.1", "Bearer token.2"]
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_concurrent_models_balance_and_generation_have_one_oauth_and_no_overlapping_flows():
    active, maximum, calls = 0, 0, []
    async def handler(request):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        calls.append(str(request.url))
        await asyncio.sleep(0.002)
        active -= 1
        return route(request)
    c = client(handler)
    try:
        out = await asyncio.gather(c.models(), c.balance(), c.complete("GigaChat-3-Ultra", MESSAGES, None, 2048))
        assert out == [MODELS, BALANCE, COMPLETION]
        assert maximum == 1 and active == 0 and calls.count(OAUTH_URL) == 1 and len(calls) == 4
    finally:
        await c.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("expiry", [None, True, "1800001800000", 0, -1, NOW, (NOW - 1) * 1000,
    (NOW + 3601) * 1000, NOW + 3601, 9_999_999_999, 10**18])
async def test_invalid_expired_or_eternal_oauth_expiry_never_dispatches_authorized_request(expiry):
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return oauth(expires=expiry)
    c = client(handler)
    try:
        with pytest.raises(GigaChatError) as failed:
            await c.models()
        assert failed.value.code == "INVALID_RESPONSE" and calls == [OAUTH_URL]
        assert c._token is None
    finally:
        await c.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, "", "Bearer " + TOKEN, "bad\r\nheader", "токен", 3, TOKEN * 2000])
async def test_invalid_oauth_token_is_not_used_or_exposed(token):
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return oauth(token=token)
    c = client(handler)
    try:
        with pytest.raises(GigaChatError) as failed:
            await c.models()
        assert failed.value.code == "INVALID_RESPONSE" and calls == [OAUTH_URL]
        assert TOKEN not in str(failed.value) + json.dumps(failed.value.as_dict())
    finally:
        await c.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code,retryable", [(400, "HTTP_ERROR", False), (401, "AUTH_FAILED", False),
    (403, "ACCESS_DENIED", False), (413, "HTTP_ERROR", False), (429, "RATE_LIMITED", True), (500, "HTTP_ERROR", True)])
async def test_generation_failure_has_fixed_private_diagnostics_and_no_oauth_or_generation_retry(status, code, retryable):
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return oauth() if str(request.url) == OAUTH_URL else httpx.Response(status,
            headers={"retry-after": "2.1", "x-private": PRIVATE},
            json={"error": {"code": PRIVATE, "message": PRIVATE + AUTH + TOKEN,
                            "reasoning": PRIVATE, "failed_generation": PRIVATE}})
    c = client(handler)
    try:
        with pytest.raises(GigaChatError) as failed:
            await c.complete("GigaChat-3-Ultra", MESSAGES, {"type": "json_object"}, 2048)
        error = failed.value
        assert error.code == code and error.http_status == error.status == status and error.retryable is retryable
        assert error.details == {"http_status": status, "retry_after_seconds": 3}
        assert all(x not in str(error) + repr(error) + json.dumps(error.as_dict()) for x in (PRIVATE, AUTH, TOKEN))
        assert calls == [OAUTH_URL, BASE_URL + "/chat/completions"]
        if status == 401:
            assert c._token is None
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_oauth_401_does_not_fall_back_to_password_scope_or_another_host():
    calls = []
    c = client(lambda request: calls.append(str(request.url)) or httpx.Response(401, json={"message": AUTH + PRIVATE}))
    try:
        with pytest.raises(GigaChatError) as failed:
            await c.models()
        assert failed.value.code == "AUTH_FAILED" and calls == [OAUTH_URL]
    finally:
        await c.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("oauth_redirect", [True, False])
async def test_redirects_never_forward_basic_or_bearer_to_another_host(oauth_redirect):
    calls = []
    def handler(request):
        calls.append(str(request.url))
        if str(request.url) == OAUTH_URL and not oauth_redirect:
            return oauth()
        return httpx.Response(302, headers={"location": "https://untrusted.invalid/" + PRIVATE})
    c = client(handler)
    try:
        with pytest.raises(GigaChatError) as failed:
            await c.models()
        assert failed.value.code == "HTTP_ERROR" and failed.value.retryable is False
        assert calls == [OAUTH_URL] if oauth_redirect else calls == [OAUTH_URL, BASE_URL + "/models"]
        assert PRIVATE not in json.dumps(failed.value.as_dict())
    finally:
        await c.close()


@pytest.mark.parametrize("context", [False, True, "trusted.pem", ssl._create_unverified_context()])
def test_tls_verification_cannot_be_disabled_or_replaced_by_a_path(context):
    with pytest.raises(GigaChatError) as failed:
        GigaChatClient(AUTH, ssl_context=context)
    assert failed.value.code == "CONFIG_BLOCKED"


@pytest.mark.asyncio
async def test_trusted_context_is_accepted_without_mutation_and_later_unverified_mutation_refuses_http():
    context = ssl.create_default_context()
    settings = context.verify_mode, context.check_hostname, context.cert_store_stats()
    calls = []
    c = client(lambda request: calls.append(request) or route(request), ssl_context=context)
    try:
        await c.models()
        assert settings == (context.verify_mode, context.check_hostname, context.cert_store_stats())
        context.check_hostname = False
        with pytest.raises(GigaChatError) as failed:
            await c.balance()
        assert failed.value.code == "CONFIG_BLOCKED" and len(calls) == 2
    finally:
        await c.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["timeout", "network"])
async def test_transport_exceptions_do_not_return_request_headers_urls_or_secrets(kind):
    calls = []
    def handler(request):
        calls.append(request)
        if str(request.url) == OAUTH_URL:
            return oauth()
        error = httpx.ReadTimeout if kind == "timeout" else httpx.ConnectError
        raise error(PRIVATE + AUTH + TOKEN, request=request)
    c = client(handler)
    try:
        with pytest.raises(GigaChatError) as failed:
            await c.complete("GigaChat-3-Ultra", MESSAGES, None, 2048)
        assert failed.value.code == ("REQUEST_TIMEOUT" if kind == "timeout" else "NETWORK_ERROR")
        assert failed.value.retryable is True and len(calls) == 2
        assert all(x not in str(failed.value) + json.dumps(failed.value.as_dict()) for x in (AUTH, TOKEN, PRIVATE))
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_wall_timeout_cancels_one_generation_and_releases_serialization_lock_without_replay():
    calls, cancelled = [], []
    async def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/v1/chat/completions":
            try:
                await asyncio.sleep(2)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
        return route(request)
    c = client(handler, timeout=1)
    try:
        with pytest.raises(GigaChatError) as failed:
            await c.complete("GigaChat-3-Ultra", MESSAGES, None, 2048)
        assert failed.value.code == "REQUEST_TIMEOUT" and cancelled == [True]
        assert await c.models() == MODELS
        assert calls == [OAUTH_URL, BASE_URL + "/chat/completions", BASE_URL + "/models"]
    finally:
        await c.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, {"data": None}, {"data": {}}, {"data": [{"id": "has space"}]},
    {"data": [{"id": "a"}, {"id": "a"}]}, {"data": [{"id": "a"}] * 129}, {"data": [{"id": 1}]}])
async def test_model_inventory_shape_and_unique_bounded_ids_are_checked(body):
    c = client(lambda request: oauth() if str(request.url) == OAUTH_URL else httpx.Response(200, json=body))
    try:
        with pytest.raises(GigaChatError) as failed:
            await c.models()
        assert failed.value.code == "INVALID_RESPONSE"
    finally:
        await c.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("row", [{"usage": "GigaChat", "value": v} for v in [True, None, "123", -1, 1.5, 10**15 + 1]]
    + [{"usage": "bad id", "value": 1}, {"value": 1}])
async def test_balance_values_are_integer_nonnegative_bounded_counts_not_guessed_money(row):
    c = client(lambda request: oauth() if str(request.url) == OAUTH_URL else httpx.Response(200, json={"balance": [row]}))
    try:
        with pytest.raises(GigaChatError) as failed:
            await c.balance()
        assert failed.value.code == "INVALID_RESPONSE"
    finally:
        await c.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b'{"balance":[{"usage":"GigaChat","value":NaN}]}',
    b'{"balance":[{"usage":"GigaChat","value":1e400}]}', b'{"data":[],"data":[]}', b"[]", b"x" * 2_000_001])
async def test_unbounded_nonfinite_duplicate_or_nonobject_success_json_is_not_treated_as_inventory(content):
    c = client(lambda request: oauth() if str(request.url) == OAUTH_URL else httpx.Response(200, content=content))
    try:
        with pytest.raises(GigaChatError) as failed:
            await c.models()
        assert failed.value.code == "INVALID_RESPONSE"
    finally:
        await c.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [{"model": ""}, {"messages": []}, {"messages": [{"role": "tool", "content": "x"}]},
    {"messages": [{"role": "user", "content": "x", "headers": PRIVATE}]}, {"max_tokens": True},
    {"max_tokens": 0}, {"max_tokens": 8193}, {"response_format": []}, {"response_format": {"value": float("nan")}}])
async def test_invalid_generation_input_refuses_before_oauth_and_does_not_choose_a_default_model(override):
    calls = []
    c = client(lambda request: calls.append(request) or route(request))
    args = {"model": "GigaChat-3-Ultra", "messages": MESSAGES, "response_format": None, "max_tokens": 2048, **override}
    try:
        with pytest.raises(GigaChatError) as failed:
            await c.complete(**args)
        assert failed.value.code == "INVALID_REQUEST" and calls == []
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_close_is_idempotent_clears_cached_credentials_and_blocks_later_flows():
    c = client(route)
    await c.models()
    await c.close()
    await c.close()
    assert c._token is None and c._expires_ms == 0 and c._auth_key == ""
    with pytest.raises(GigaChatError) as failed:
        await c.models()
    assert failed.value.code == "CLIENT_CLOSED"


def test_error_public_fields_are_fixed_text_and_allowlisted_numeric_values_only():
    error = GigaChatError(PRIVATE, status=True, details={"message": PRIVATE, "token": TOKEN, "retry_after_seconds": True})
    assert error.code == "INVALID_RESPONSE" and error.http_status is None and error.details == {}
    assert PRIVATE not in json.dumps(error.as_dict()) and TOKEN not in str(error)


def test_secret_registration_failure_is_closed_and_does_not_expose_callback_text():
    def failed(_value):
        raise RuntimeError(PRIVATE + AUTH)
    with pytest.raises(GigaChatError) as error:
        GigaChatClient(AUTH, register_secret=failed)
    assert error.value.code == "CONFIG_BLOCKED"
    assert PRIVATE not in str(error.value) and AUTH not in json.dumps(error.value.as_dict())


@pytest.mark.asyncio
async def test_injected_clock_failure_is_sanitized_before_auth():
    calls = []
    def clock():
        raise RuntimeError(PRIVATE)
    c = GigaChatClient(AUTH, transport=httpx.MockTransport(lambda request: calls.append(request) or route(request)), clock=clock)
    try:
        with pytest.raises(GigaChatError) as error:
            await c.models()
        assert error.value.code == "CONFIG_BLOCKED" and not calls
        assert PRIVATE not in str(error.value)
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_duplicate_oauth_fields_are_rejected_without_reusing_or_forwarding_a_token():
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=(
            '{"access_token":"token.first","access_token":"token.second","expires_at":1800001800000}'))
    c = client(handler)
    try:
        with pytest.raises(GigaChatError) as error:
            await c.models()
        assert error.value.code == "INVALID_RESPONSE" and calls == [OAUTH_URL] and c._token is None
    finally:
        await c.close()
