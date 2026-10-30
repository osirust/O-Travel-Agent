"""Small explicit Personal GigaChat transport; no model choice or automatic retry.

The access token stays in RAM. One lock serializes OAuth and all API flows.
TLS verification is mandatory; a caller may supply a verified CA context.
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import re
import ssl
import time
from collections.abc import Callable
from copy import deepcopy
from typing import Any
from uuid import uuid4

import httpx

OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
BASE_URL = "https://api.giga.chat/v1"
PERSONAL_SCOPE = "GIGACHAT_API_PERS"
MAX_RESPONSE_BYTES = 2_000_000
MAX_REQUEST_BYTES = 1_000_000
MAX_TOKEN_TTL_SECONDS = 3600
REFRESH_SKEW_SECONDS = 30
_ID = re.compile(r"[A-Za-z0-9_.:/-]{1,200}\Z")
_MESSAGES = {
    "CONFIG_BLOCKED": "Настройки безопасного подключения GigaChat не прошли проверку.",
    "CLIENT_CLOSED": "Подключение GigaChat закрыто.",
    "INVALID_REQUEST": "Запрос GigaChat не прошёл проверку параметров.",
    "INVALID_RESPONSE": "GigaChat вернул некорректный ответ.",
    "AUTH_FAILED": "GigaChat отклонил авторизацию. Проверьте ключ личного проекта.",
    "ACCESS_DENIED": "GigaChat не разрешил доступ к запрошенному ресурсу.",
    "RATE_LIMITED": "GigaChat временно ограничил запросы.",
    "HTTP_ERROR": "GigaChat отклонил запрос.",
    "REQUEST_TIMEOUT": "GigaChat не ответил за отведённое время.",
    "NETWORK_ERROR": "Не удалось безопасно подключиться к GigaChat.",
}


class GigaChatError(Exception):
    """Only fixed text/codes and explicitly allowed numeric diagnostics."""

    def __init__(self, code: str, *, status: int | None = None, retryable: bool = False,
                 details: dict | None = None):
        self.code = code if isinstance(code, str) and code in _MESSAGES else "INVALID_RESPONSE"
        self.message = _MESSAGES[self.code]
        self.http_status = status if type(status) is int and 100 <= status <= 599 else None
        self.status = self.http_status
        self.retryable = retryable is True
        self.details = {}
        if self.http_status is not None:
            self.details["http_status"] = self.http_status
        for key in ("retry_after_seconds",):
            value = (details or {}).get(key)
            if type(value) is int and 0 < value <= 86400:
                self.details[key] = value
        super().__init__(self.message)

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "http_status": self.http_status,
            "retryable": self.retryable, "details": dict(self.details)}


def _json_bytes(value: Any, *, request: bool = False) -> bytes:
    try:
        content = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        raise GigaChatError("INVALID_REQUEST" if request else "INVALID_RESPONSE") from None
    if len(content) > (MAX_REQUEST_BYTES if request else MAX_RESPONSE_BYTES):
        raise GigaChatError("INVALID_REQUEST" if request else "INVALID_RESPONSE")
    return content


def _body(response: httpx.Response) -> dict:
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise GigaChatError("INVALID_RESPONSE", status=response.status_code)

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate field")
            result[key] = value
        return result

    try:
        value = json.loads(response.content, object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(value, dict):
            raise TypeError
        stack, count = [(value, 0)], 0
        while stack:
            node, depth = stack.pop()
            count += 1
            if depth > 32 or count > 40_000:
                raise ValueError
            if isinstance(node, dict):
                stack.extend((child, depth + 1) for child in node.values())
            elif isinstance(node, list):
                stack.extend((child, depth + 1) for child in node)
            elif isinstance(node, float) and not math.isfinite(node):
                raise ValueError
            elif isinstance(node, str):
                node.encode("utf-8")
        return value
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        raise GigaChatError("INVALID_RESPONSE", status=response.status_code) from None


class GigaChatClient:
    def __init__(self, auth_key: str, *, transport=None, ssl_context: ssl.SSLContext | None = None,
                 clock: Callable[[], float] = time.time, register_secret: Callable[[str], None] | None = None,
                 timeout: float = 25):
        try:
            if not isinstance(auth_key, str) or not 1 <= len(auth_key) <= 4096:
                raise ValueError
            decoded = base64.b64decode(auth_key, validate=True)
            client_id, separator, secret = decoded.partition(b":")
            if not separator or not client_id.strip() or not secret.strip():
                raise ValueError
            if (ssl_context is not None and (not isinstance(ssl_context, ssl.SSLContext)
                    or ssl_context.verify_mode != ssl.CERT_REQUIRED or not ssl_context.check_hostname)):
                raise ValueError
            if type(timeout) not in {int, float} or not math.isfinite(timeout) or not 1 <= timeout <= 45:
                raise ValueError
            if not callable(clock) or register_secret is not None and not callable(register_secret):
                raise ValueError
        except (ValueError, TypeError, UnicodeError):
            raise GigaChatError("CONFIG_BLOCKED") from None
        self._auth_key = auth_key
        self._clock = clock
        self._ssl_context = ssl_context
        self._timeout = float(timeout)
        self._register_secret = register_secret
        self._remember(auth_key)
        self._token: str | None = None
        self._expires_ms = 0
        self._closed = False
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False,
            verify=ssl_context if ssl_context is not None else True,
            timeout=httpx.Timeout(float(timeout), connect=min(10.0, timeout)))

    def _remember(self, value: str) -> None:
        if self._register_secret is not None:
            try:
                self._register_secret(value)
            except Exception:  # noqa: BLE001 - callback errors must never expose credential-bearing text.
                raise GigaChatError("CONFIG_BLOCKED") from None

    def _now_ms(self) -> int:
        try:
            value = self._clock()
        except Exception:  # noqa: BLE001 - injected clock exceptions have no public diagnostic authority.
            raise GigaChatError("CONFIG_BLOCKED") from None
        if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
            raise GigaChatError("CONFIG_BLOCKED")
        return int(value * 1000)

    def _require_open(self) -> None:
        if self._closed:
            raise GigaChatError("CLIENT_CLOSED")

    async def _send(self, method: str, url: str, **kwargs) -> dict:
        if (self._ssl_context is not None and (self._ssl_context.verify_mode != ssl.CERT_REQUIRED
                or not self._ssl_context.check_hostname)):
            raise GigaChatError("CONFIG_BLOCKED")
        try:
            response = await asyncio.wait_for(self._client.request(method, url, **kwargs), self._timeout)
        except (httpx.TimeoutException, TimeoutError):
            raise GigaChatError("REQUEST_TIMEOUT", retryable=True) from None
        except httpx.HTTPError:
            raise GigaChatError("NETWORK_ERROR", retryable=True) from None
        status = response.status_code
        if status >= 300:
            if status == 401:
                self._token, self._expires_ms = None, 0
            code = ("AUTH_FAILED" if status == 401 else "ACCESS_DENIED" if status == 403 else
                    "RATE_LIMITED" if status == 429 else "HTTP_ERROR")
            details = {}
            retry = response.headers.get("retry-after", "")
            if re.fullmatch(r"[0-9]{1,5}(?:\.[0-9]{1,6})?", retry):
                number = math.ceil(float(retry))
                if 0 < number <= 86400:
                    details["retry_after_seconds"] = number
            raise GigaChatError(code, status=status, retryable=status == 429 or status >= 500, details=details)
        return _body(response)

    async def _access_locked(self) -> str:
        now = self._now_ms()
        remaining = self._expires_ms - now
        if self._token is not None and REFRESH_SKEW_SECONDS * 1000 < remaining <= MAX_TOKEN_TTL_SECONDS * 1000:
            return self._token
        self._token, self._expires_ms = None, 0
        body = await self._send("POST", OAUTH_URL, data={"scope": PERSONAL_SCOPE},
            headers={"Authorization": "Basic " + self._auth_key, "RqUID": str(uuid4()),
                "Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"})
        token, expires = body.get("access_token"), body.get("expires_at")
        if not isinstance(token, str) or re.fullmatch(r"[A-Za-z0-9_.~+/=-]{1,16384}", token) is None:
            raise GigaChatError("INVALID_RESPONSE")
        self._remember(token)
        if type(expires) is not int or expires <= 0:
            raise GigaChatError("INVALID_RESPONSE")
        # The official SDK also accepts historical epoch-seconds responses.
        expires_ms = expires * 1000 if expires < 10**10 else expires
        if not 0 < expires_ms - self._now_ms() <= MAX_TOKEN_TTL_SECONDS * 1000:
            raise GigaChatError("INVALID_RESPONSE")
        self._token, self._expires_ms = token, expires_ms
        return token

    async def _authorized_locked(self, method: str, path: str, **kwargs) -> dict:
        token = await self._access_locked()
        # A 401 clears the RAM token but never replays this request/generation.
        return await self._send(method, BASE_URL + path,
            headers={"Authorization": "Bearer " + token, "Accept": "application/json"}, **kwargs)

    async def models(self) -> dict:
        async with self._lock:
            self._require_open()
            body = await self._authorized_locked("GET", "/models")
            rows = body.get("data")
            if not isinstance(rows, list) or len(rows) > 128:
                raise GigaChatError("INVALID_RESPONSE")
            ids = []
            for row in rows:
                identifier = row.get("id") if isinstance(row, dict) else None
                if not isinstance(identifier, str) or _ID.fullmatch(identifier) is None:
                    raise GigaChatError("INVALID_RESPONSE")
                ids.append(identifier)
            if len(ids) != len(set(ids)):
                raise GigaChatError("INVALID_RESPONSE")
            return body

    async def balance(self) -> dict:
        async with self._lock:
            self._require_open()
            body = await self._authorized_locked("GET", "/balance")
            rows = body.get("balance")
            if not isinstance(rows, list) or len(rows) > 128:
                raise GigaChatError("INVALID_RESPONSE")
            usages = []
            for row in rows:
                if (not isinstance(row, dict) or not isinstance(row.get("usage"), str)
                        or _ID.fullmatch(row["usage"]) is None or type(row.get("value")) is not int
                        or not 0 <= row["value"] <= 10**15):
                    raise GigaChatError("INVALID_RESPONSE")
                usages.append(row["usage"])
            if len(usages) != len(set(usages)):
                raise GigaChatError("INVALID_RESPONSE")
            return body

    async def complete(self, model: str, messages: list[dict], response_format: dict | None,
                       max_tokens: int) -> dict:
        if (not isinstance(model, str) or _ID.fullmatch(model) is None
                or not isinstance(messages, list) or not 1 <= len(messages) <= 64
                or type(max_tokens) is not int or not 1 <= max_tokens <= 8192
                or response_format is not None and not isinstance(response_format, dict)):
            raise GigaChatError("INVALID_REQUEST")
        for message in messages:
            if (not isinstance(message, dict) or set(message) != {"role", "content"}
                    or not isinstance(message.get("role"), str)
                    or message["role"] not in {"system", "user", "assistant"}
                    or not isinstance(message.get("content"), str)):
                raise GigaChatError("INVALID_REQUEST")
        payload = {"model": model, "messages": deepcopy(messages), "stream": False, "max_tokens": max_tokens}
        if response_format is not None:
            payload["response_format"] = deepcopy(response_format)
        _json_bytes(payload, request=True)
        async with self._lock:
            self._require_open()
            body = await self._authorized_locked("POST", "/chat/completions", json=payload)
            choices = body.get("choices")
            if not isinstance(choices, list) or not 1 <= len(choices) <= 8:
                raise GigaChatError("INVALID_RESPONSE")
            for choice in choices:
                message = choice.get("message") if isinstance(choice, dict) else None
                if not isinstance(message, dict) or not isinstance(message.get("content"), str):
                    raise GigaChatError("INVALID_RESPONSE")
            return body

    async def close(self) -> None:
        async with self._lock:
            if not self._closed:
                self._closed = True
                self._token, self._expires_ms, self._auth_key = None, 0, ""
                await self._client.aclose()
