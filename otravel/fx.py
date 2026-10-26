"""CBR reference-only RUB/USD/EUR snapshots; no payment or bank-rate promise.

No network on import. Native offer amounts/currencies are never modified. A
checksum detects changed snapshots; it is not a CBR signature or user consent.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from fractions import Fraction
from typing import Any
from urllib.parse import urlencode
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import httpx

CBR_URL = "https://www.cbr.ru/scripts/XML_daily.asp"
SUPPORTED_CURRENCIES = frozenset({"RUB", "USD", "EUR"})
DEFAULT_TTL_SECONDS = 6 * 60 * 60
MAX_SOURCE_AGE_DAYS = 7
MAX_XML_BYTES = 1_000_000
_MSK = ZoneInfo("Europe/Moscow")
_FIELDS = frozenset({"schema_version", "provider", "reference_only", "fees_unknown", "real_booking_supported",
    "source_url", "raw_source_sha256", "requested_on", "as_of", "retrieved_at", "expires_at", "ttl_seconds",
    "max_source_age_days", "rates_rub_per_unit", "source_quotes", "snapshot_id"})


class FxUnavailable(ValueError):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message}


class FxSnapshot(dict):
    """JSON-serializable frozen public interface, including nested rates."""
    def __init__(self, values):
        dict.__init__(self, {key: FxSnapshot(value) if isinstance(value, dict) else value for key, value in values.items()})

    def _immutable(self, *args, **kwargs):
        raise TypeError("FX snapshot is immutable")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _immutable

    def __deepcopy__(self, memo):
        return self


def _fail(code: str, message: str):
    raise FxUnavailable(code, message)


def _utc(now: datetime) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        _fail("fx_invalid_clock", "Для проверки курса нужно время с часовым поясом.")
    return now.astimezone(UTC)


def _ttl(seconds: int) -> int:
    if type(seconds) is not int or not 1 <= seconds <= DEFAULT_TTL_SECONDS:
        _fail("fx_invalid_ttl", "Срок справочного снимка должен быть не более шести часов.")
    return seconds


def _source_url(requested: date) -> str:
    return CBR_URL + "?" + urlencode({"date_req": requested.strftime("%d/%m/%Y")})


def _decimal_string(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _digest(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _iso_day(value: Any, field: str) -> date:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        _fail("fx_invalid_snapshot", f"Некорректная дата снимка: {field}.")
    try:
        return date.fromisoformat(value)
    except ValueError:
        _fail("fx_invalid_snapshot", f"Некорректная дата снимка: {field}.")


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        _fail("fx_invalid_snapshot", "Не подтверждено время получения или истечения курса.")
    try:
        instant = datetime.fromisoformat(value)
    except ValueError:
        _fail("fx_invalid_snapshot", "Не подтверждено время получения или истечения курса.")
    if instant.tzinfo is None or instant.utcoffset() != timedelta(0):
        _fail("fx_invalid_snapshot", "Время снимка должно быть задано в UTC.")
    return instant.astimezone(UTC)


def _rates(value: Any) -> dict[str, Decimal]:
    if not isinstance(value, dict) or not SUPPORTED_CURRENCIES.issubset(value):
        _fail("fx_rate_missing", "В справочном снимке отсутствует нужная валюта.")
    if set(value) != SUPPORTED_CURRENCIES:
        _fail("fx_invalid_snapshot", "Поддержаны только RUB, USD и EUR.")
    result = {}
    for currency, raw in value.items():
        if not isinstance(raw, str) or re.fullmatch(r"(?:0|[1-9][0-9]{0,100})(?:\.[0-9]{1,200})?", raw) is None:
            _fail("fx_invalid_rate", "Курс должен быть положительным десятичным числом без float.")
        number = Decimal(raw)
        if not number.is_finite() or number <= 0 or _decimal_string(number) != raw:
            _fail("fx_invalid_rate", "Не подтверждён положительный справочный курс.")
        result[currency] = number
    if result["RUB"] != 1:
        _fail("fx_invalid_rate", "Базовый курс RUB должен равняться единице.")
    return result


def _quotes(value: Any) -> dict[str, tuple[Decimal, int]]:
    if not isinstance(value, dict) or not SUPPORTED_CURRENCIES.issubset(value):
        _fail("fx_rate_missing", "В снимке отсутствует точная котировка нужной валюты.")
    if set(value) != SUPPORTED_CURRENCIES:
        _fail("fx_invalid_snapshot", "Поддержаны только RUB, USD и EUR.")
    result = {}
    for currency, quote in value.items():
        if not isinstance(quote, dict) or set(quote) != {"value_rub", "nominal"}:
            _fail("fx_invalid_snapshot", "Не подтверждены исходные Value и Nominal валюты.")
        amount, nominal = quote["value_rub"], quote["nominal"]
        if (not isinstance(amount, str) or re.fullmatch(r"(?:0|[1-9][0-9]{0,100})(?:\.[0-9]{1,200})?", amount) is None
                or type(nominal) is not int or not 1 <= nominal <= 999_999_999_999):
            _fail("fx_invalid_rate", "Не подтверждены положительные Value и Nominal валюты.")
        amount = Decimal(amount)
        if not amount.is_finite() or amount <= 0 or _decimal_string(amount) != quote["value_rub"]:
            _fail("fx_invalid_rate", "Исходная котировка должна быть положительным десятичным числом.")
        result[currency] = (amount, nominal)
    if result["RUB"] != (Decimal(1), 1):
        _fail("fx_invalid_rate", "Базовая котировка RUB должна быть 1/1.")
    return result


def _display_rate(amount: Decimal, nominal: int) -> str:
    # Per-unit strings are a display projection; repeating decimals must NEVER
    # become the arithmetic input for exact HALF_UP minor-unit conversion.
    with localcontext() as context:
        context.prec = 96
        context.rounding = ROUND_HALF_EVEN
        return _decimal_string(amount / Decimal(nominal))


def validate_snapshot(snapshot: Any, now: datetime) -> FxSnapshot:
    instant = _utc(now)
    if not isinstance(snapshot, dict) or set(snapshot) != _FIELDS:
        _fail("fx_invalid_snapshot", "Структура справочного снимка курса не подтверждена.")
    if (type(snapshot["schema_version"]) is not int or snapshot["schema_version"] != 1
            or snapshot["provider"] != "cbr" or snapshot["reference_only"] is not True
            or snapshot["fees_unknown"] is not True or snapshot["real_booking_supported"] is not False
            or type(snapshot["max_source_age_days"]) is not int or snapshot["max_source_age_days"] != MAX_SOURCE_AGE_DAYS):
        _fail("fx_invalid_snapshot", "Снимок не соответствует справочному режиму ЦБ.")
    ttl = _ttl(snapshot["ttl_seconds"])
    requested, as_of = _iso_day(snapshot["requested_on"], "requested_on"), _iso_day(snapshot["as_of"], "as_of")
    retrieved, expiry = _timestamp(snapshot["retrieved_at"]), _timestamp(snapshot["expires_at"])
    today = instant.astimezone(_MSK).date()
    if as_of > today or requested > today or as_of > requested or retrieved > instant:
        _fail("fx_future_snapshot", "Снимок курса или дата его источника находятся в будущем.")
    if requested != retrieved.astimezone(_MSK).date():
        _fail("fx_invalid_snapshot", "Дата запроса курса не связана с получением снимка.")
    if (today - as_of).days > MAX_SOURCE_AGE_DAYS:
        _fail("fx_source_too_old", "Данные источника курса старше семи календарных дней.")
    if expiry - retrieved != timedelta(seconds=ttl):
        _fail("fx_invalid_snapshot", "Срок снимка не соответствует сохранённому TTL.")
    if instant >= expiry:
        _fail("fx_snapshot_expired", "Справочный курс устарел; нужна новая перепроверка.")
    if snapshot["source_url"] != _source_url(requested):
        _fail("fx_invalid_source", "Источник курса не соответствует фиксированному адресу ЦБ.")
    if not isinstance(snapshot["raw_source_sha256"], str) or re.fullmatch(r"[a-f0-9]{64}", snapshot["raw_source_sha256"]) is None:
        _fail("fx_invalid_snapshot", "Не подтверждена контрольная сумма ответа ЦБ.")
    _rates(snapshot["rates_rub_per_unit"])
    quotes = _quotes(snapshot["source_quotes"])
    if any(snapshot["rates_rub_per_unit"][currency] != _display_rate(*quote) for currency, quote in quotes.items()):
        _fail("fx_snapshot_tampered", "Курс на единицу не соответствует исходным Value и Nominal.")
    payload = {key: value for key, value in snapshot.items() if key != "snapshot_id"}
    if snapshot["snapshot_id"] != "cbr-" + _digest(payload):
        _fail("fx_snapshot_tampered", "Контрольная сумма справочного курса не совпадает.")
    return snapshot if isinstance(snapshot, FxSnapshot) else FxSnapshot(snapshot)


def parse_cbr_xml(raw: bytes, now: datetime, requested_date: date | None = None,
                  *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> FxSnapshot:
    instant, ttl = _utc(now), _ttl(ttl_seconds)
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_XML_BYTES:
        _fail("fx_invalid_xml_size", "Ответ ЦБ пуст или превышает один мегабайт.")
    # NUL stripping is for declaration detection only; parsing uses original
    # bytes. This also catches UTF-16/32 DOCTYPE/ENTITY declarations.
    if re.search(rb"<!\s*(?:DOCTYPE|ENTITY)\b", raw.replace(b"\x00", b""), re.IGNORECASE):
        _fail("fx_unsafe_xml", "XML с DOCTYPE или ENTITY не принимается.")
    try:
        root = ElementTree.fromstring(raw)
    except (ElementTree.ParseError, ValueError, LookupError):
        _fail("fx_invalid_xml", "ЦБ не вернул ожидаемый XML курсов.")
    if root.tag != "ValCurs" or re.fullmatch(r"[0-9]{2}\.[0-9]{2}\.[0-9]{4}", root.get("Date", "")) is None:
        _fail("fx_invalid_source_date", "Не подтверждена дата публикации курса ЦБ.")
    try:
        as_of = datetime.strptime(root.attrib["Date"], "%d.%m.%Y").date()
    except ValueError:
        _fail("fx_invalid_source_date", "Не подтверждена дата публикации курса ЦБ.")
    requested = instant.astimezone(_MSK).date() if requested_date is None else requested_date
    if type(requested) is not date:
        _fail("fx_invalid_source_date", "Нужна явная календарная дата запроса курса.")
    rates, quotes = {"RUB": "1"}, {"RUB": {"value_rub": "1", "nominal": 1}}
    for row in root.findall("Valute"):
        currency = row.findtext("CharCode")
        if currency not in {"USD", "EUR"}:
            continue
        if currency in rates:
            _fail("fx_ambiguous_rate", "Источник вернул несколько курсов одной валюты.")
        nominal, value = row.findtext("Nominal", "").strip(), row.findtext("Value", "").strip()
        if re.fullmatch(r"[0-9]{1,12}", nominal) is None or int(nominal) <= 0:
            _fail("fx_invalid_nominal", "Не подтверждён положительный номинал валюты.")
        if re.fullmatch(r"[0-9]{1,60}(?:[.,][0-9]{1,60})?", value) is None:
            _fail("fx_invalid_rate", "Не подтверждён положительный десятичный курс валюты.")
        amount = Decimal(value.replace(",", "."))
        if not amount.is_finite() or amount <= 0:
            _fail("fx_invalid_rate", "Не подтверждён положительный справочный курс.")
        quotes[currency] = {"value_rub": _decimal_string(amount), "nominal": int(nominal)}
        rates[currency] = _display_rate(amount, int(nominal))
    snapshot = {"schema_version": 1, "provider": "cbr", "reference_only": True,
        "fees_unknown": True, "real_booking_supported": False, "source_url": _source_url(requested),
        "raw_source_sha256": hashlib.sha256(raw).hexdigest(), "requested_on": requested.isoformat(),
        "as_of": as_of.isoformat(), "retrieved_at": instant.isoformat(),
        "expires_at": (instant + timedelta(seconds=ttl)).isoformat(), "ttl_seconds": ttl,
        "max_source_age_days": MAX_SOURCE_AGE_DAYS, "rates_rub_per_unit": rates, "source_quotes": quotes}
    snapshot["snapshot_id"] = "cbr-" + _digest(snapshot)
    return validate_snapshot(snapshot, instant)


def convert_minor(native_amount: int, from_currency: str, to_currency: str,
                  snapshot: dict, now: datetime) -> int:
    if type(native_amount) is not int or native_amount < 0:
        _fail("fx_invalid_amount", "Сумма должна быть целым неотрицательным числом minor units.")
    if (not isinstance(from_currency, str) or not isinstance(to_currency, str)
            or from_currency not in SUPPORTED_CURRENCIES or to_currency not in SUPPORTED_CURRENCIES):
        _fail("fx_unsupported_currency", "Для справочного пересчёта поддержаны RUB, USD и EUR.")
    validated = validate_snapshot(snapshot, now)
    quotes = _quotes(validated["source_quotes"])
    source_value, source_nominal = quotes[from_currency]
    target_value, target_nominal = quotes[to_currency]
    amount = Fraction(native_amount) * Fraction(source_value) * target_nominal / (source_nominal * Fraction(target_value))
    whole, remainder = divmod(amount.numerator, amount.denominator)
    # Exact nonnegative ROUND_HALF_UP: ties increase the integer, without a
    # float, epsilon, or rounded per-unit rate affecting the half boundary.
    return whole + int(2 * remainder >= amount.denominator)


class CBRFxClient:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None,
                 clock: Callable[[], datetime] | None = None, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self._ttl = _ttl(ttl_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._client = httpx.AsyncClient(timeout=10.0, follow_redirects=False, trust_env=False, transport=transport)

    async def fetch_snapshot(self, now: datetime | None = None) -> FxSnapshot:
        requested_at = _utc(self._clock() if now is None else now)
        requested_date = requested_at.astimezone(_MSK).date()
        try:
            async with self._client.stream("GET", CBR_URL, params={"date_req": requested_date.strftime("%d/%m/%Y")}) as response:
                if response.status_code != 200:
                    _fail("fx_source_http_error", "ЦБ не предоставил справочный курс; автоматического повтора нет.")
                content_length = response.headers.get("content-length", "")
                if content_length.isdecimal() and int(content_length) > MAX_XML_BYTES:
                    _fail("fx_invalid_xml_size", "Ответ ЦБ превышает один мегабайт.")
                parts, size = [], 0
                async for part in response.aiter_bytes():
                    size += len(part)
                    if size > MAX_XML_BYTES:
                        _fail("fx_invalid_xml_size", "Ответ ЦБ превышает один мегабайт.")
                    parts.append(part)
        except httpx.HTTPError:
            _fail("fx_source_unavailable", "Справочный источник курса временно недоступен.")
        retrieved = requested_at if now is not None else _utc(self._clock())
        # A request crossing Moscow midnight is still tied to its original
        # date_req. Reject rather than silently relabel its provenance.
        if retrieved.astimezone(_MSK).date() != requested_date:
            _fail("fx_date_changed_during_fetch", "Дата запроса курса изменилась; нужен новый явный запрос.")
        return parse_cbr_xml(b"".join(parts), retrieved, requested_date, ttl_seconds=self._ttl)

    async def close(self):
        await self._client.aclose()
