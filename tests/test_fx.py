"""CBR-reference arithmetic/integrity/HTTP contracts. No actual HTTP or keys."""
import hashlib
import importlib.util
import json
import socket
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from otravel import fx
from otravel.fx import CBRFxClient, FxUnavailable, convert_minor, parse_cbr_xml, validate_snapshot

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def xml(day="26.10.2026", usd="95,0000", eur="100,0000", nominal="1", extra=""):
    return (f'<?xml version="1.0" encoding="windows-1251"?>'
        f'<ValCurs Date="{day}" name="Курсы валют">'
        f'<Valute ID="R01235"><CharCode>USD</CharCode><Nominal>{nominal}</Nominal><Value>{usd}</Value></Valute>'
        f'<Valute ID="R01239"><CharCode>EUR</CharCode><Nominal>1</Nominal><Value>{eur}</Value></Valute>'
        + extra + '</ValCurs>').encode("cp1251")


def mutable(snapshot):
    return json.loads(json.dumps(snapshot))


def test_EUR_minor_to_RUB_and_cross_USD_math_preserve_native_offer_and_snapshot():
    raw = xml()
    snapshot = parse_cbr_xml(raw, NOW)
    offer = {"amount_minor": 12345, "currency": "EUR"}
    before = deepcopy(offer)
    original = json.dumps(snapshot)
    assert convert_minor(offer["amount_minor"], offer["currency"], "RUB", snapshot, NOW) == 1_234_500
    assert convert_minor(10000, "USD", "EUR", snapshot, NOW) == 9500
    assert convert_minor(10000, "EUR", "USD", snapshot, NOW) == 10526
    assert convert_minor(0, "RUB", "USD", snapshot, NOW) == 0
    assert offer == before and json.dumps(snapshot) == original
    assert snapshot["raw_source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert snapshot["reference_only"] is True and snapshot["fees_unknown"] is True
    assert snapshot["real_booking_supported"] is False and snapshot["rates_rub_per_unit"]["RUB"] == "1"


def test_Nominal_is_divided_before_conversion_instead_of_assuming_one_unit():
    snapshot = parse_cbr_xml(xml(usd="12345,67", nominal="100"), NOW)
    assert snapshot["rates_rub_per_unit"]["USD"] == "123.4567"
    assert convert_minor(12345, "USD", "RUB", snapshot, NOW) == 1_524_073
    assert parse_cbr_xml(xml(usd="700,00", nominal="7"), NOW)["rates_rub_per_unit"]["USD"] == "100"


def test_nonterminating_nominals_do_not_move_an_exact_half_minor_cross_rate_below_half():
    raw = xml(usd="1", eur="2", nominal="3").replace(
        b'<CharCode>EUR</CharCode><Nominal>1</Nominal>', b'<CharCode>EUR</CharCode><Nominal>3</Nominal>')
    snapshot = parse_cbr_xml(raw, NOW)
    assert convert_minor(1, "USD", "EUR", snapshot, NOW) == 1  # (1/3)/(2/3) = exactly 1/2 minor.
    assert convert_minor(3, "USD", "RUB", parse_cbr_xml(xml(usd="1", nominal="6"), NOW), NOW) == 1


@pytest.mark.parametrize("usd,eur,amount,expected", [("100", "200", 1, 1), ("125", "50", 1, 3), ("95", "100", 1, 1)])
def test_half_minor_rounding_is_HALF_UP_not_float_or_bankers(usd, eur, amount, expected):
    snapshot = parse_cbr_xml(xml(usd=usd, eur=eur), NOW)
    assert convert_minor(amount, "USD", "EUR", snapshot, NOW) == expected


def test_snapshot_is_JSON_serializable_immutable_and_revalidates_after_JSON_roundtrip():
    snapshot = parse_cbr_xml(xml(), NOW)
    with pytest.raises(TypeError, match="immutable"):
        snapshot["as_of"] = "2026-10-25"
    with pytest.raises(TypeError, match="immutable"):
        snapshot["rates_rub_per_unit"]["EUR"] = "999"
    with pytest.raises(TypeError, match="immutable"):
        snapshot["source_quotes"]["USD"]["nominal"] = 100
    with pytest.raises(TypeError, match="immutable"):
        snapshot.update(expires_at="2026-10-27")
    restored = validate_snapshot(mutable(snapshot), NOW)
    assert restored == snapshot and deepcopy(snapshot) is snapshot


def test_expiry_is_exclusive_and_checks_are_not_skipped_for_same_currency_or_zero_amount():
    snapshot = parse_cbr_xml(xml(), NOW)
    assert convert_minor(1, "RUB", "RUB", snapshot, NOW + timedelta(hours=6, microseconds=-1)) == 1
    for amount in (0, 1):
        with pytest.raises(FxUnavailable) as failure:
            convert_minor(amount, "RUB", "RUB", snapshot, NOW + timedelta(hours=6))
        assert failure.value.code == "fx_snapshot_expired"


def test_Moscow_calendar_date_not_UTC_date_controls_future_source_and_seven_day_age():
    now = datetime(2026, 10, 26, 23, 30, tzinfo=UTC)  # Moscow is already 27 Oct.
    fresh = parse_cbr_xml(xml(day="27.10.2026"), now)
    assert fresh["as_of"] == fresh["requested_on"] == "2026-10-27"
    assert parse_qs(urlparse(fresh["source_url"]).query) == {"date_req": ["27/10/2026"]}
    assert parse_cbr_xml(xml(day="20.10.2026"), now)["as_of"] == "2026-10-20"
    with pytest.raises(FxUnavailable) as old:
        parse_cbr_xml(xml(day="19.10.2026"), now)
    assert old.value.code == "fx_source_too_old"
    with pytest.raises(FxUnavailable) as future:
        parse_cbr_xml(xml(day="28.10.2026"), now)
    assert future.value.code == "fx_future_snapshot"


@pytest.mark.parametrize("change", [
    lambda s: s["rates_rub_per_unit"].update(EUR="101"), lambda s: s.update(raw_source_sha256="a" * 64),
    lambda s: s.update(snapshot_id="cbr-" + "0" * 64), lambda s: s.update(expires_at=(NOW + timedelta(hours=5)).isoformat()),
    lambda s: s.update(reference_only=False), lambda s: s.update(fees_unknown=False),
    lambda s: s.update(real_booking_supported=True), lambda s: s.update(source_url="https://remote.invalid/steal"),
    lambda s: s.update(as_of="2026-10-27"), lambda s: s.update(requested_on="2026-10-25"),
    lambda s: s.update(retrieved_at=(NOW + timedelta(seconds=1)).isoformat()),
])
def test_tampered_snapshot_is_rejected_before_any_conversion(change):
    snapshot = mutable(parse_cbr_xml(xml(), NOW))
    change(snapshot)
    with pytest.raises(FxUnavailable):
        convert_minor(12345, "EUR", "RUB", snapshot, NOW)


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity", "1e2", 100.0, True, None])
def test_invalid_or_float_rate_never_becomes_a_reference_price(value):
    snapshot = mutable(parse_cbr_xml(xml(), NOW))
    snapshot["rates_rub_per_unit"]["EUR"] = value
    with pytest.raises(FxUnavailable):
        convert_minor(1, "EUR", "RUB", snapshot, NOW)


@pytest.mark.parametrize("amount", [-1, True, False, 1.0, "100", None])
def test_only_nonnegative_integer_minor_units_are_accepted(amount):
    with pytest.raises(FxUnavailable) as failure:
        convert_minor(amount, "EUR", "RUB", parse_cbr_xml(xml(), NOW), NOW)
    assert failure.value.code == "fx_invalid_amount"


@pytest.mark.parametrize("currency", ["usd", "JPY", None, [], True])
def test_only_explicit_supported_currency_codes_are_accepted(currency):
    with pytest.raises(FxUnavailable):
        convert_minor(1, currency, "RUB", parse_cbr_xml(xml(), NOW), NOW)


@pytest.mark.parametrize("now", [datetime(2026, 10, 26), None, date(2026, 10, 26)])
def test_clock_requires_an_aware_datetime(now):
    with pytest.raises(FxUnavailable):
        parse_cbr_xml(xml(), now)


@pytest.mark.parametrize("nominal", ["0", "-1", "1.0", "true", "", "1e2"])
def test_bad_Nominal_is_not_assumed_to_equal_one(nominal):
    with pytest.raises(FxUnavailable):
        parse_cbr_xml(xml(nominal=nominal), NOW)


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity", "1e2", "not a rate"])
def test_bad_XML_value_fails_instead_of_using_another_currency_or_fake_rate(value):
    with pytest.raises(FxUnavailable):
        parse_cbr_xml(xml(usd=value), NOW)


def test_missing_or_duplicate_currency_and_wrong_RUB_base_are_rejected():
    duplicate = '<Valute><CharCode>USD</CharCode><Nominal>1</Nominal><Value>1</Value></Valute>'
    with pytest.raises(FxUnavailable) as error:
        parse_cbr_xml(xml(extra=duplicate), NOW)
    assert error.value.code == "fx_ambiguous_rate"
    raw = xml().replace(b'<CharCode>EUR</CharCode>', b'<CharCode>JPY</CharCode>')
    with pytest.raises(FxUnavailable) as missing:
        parse_cbr_xml(raw, NOW)
    assert missing.value.code == "fx_rate_missing"
    snapshot = mutable(parse_cbr_xml(xml(), NOW))
    snapshot["rates_rub_per_unit"]["RUB"] = "2"
    with pytest.raises(FxUnavailable):
        validate_snapshot(snapshot, NOW)


@pytest.mark.parametrize("change", [
    lambda s: s["source_quotes"]["USD"].update(nominal=True),
    lambda s: s["source_quotes"]["USD"].update(nominal=0),
    lambda s: s["source_quotes"]["USD"].update(nominal=1.0),
    lambda s: s["source_quotes"]["USD"].update(value_rub="NaN"),
    lambda s: s["source_quotes"]["USD"].update(value_rub=95.0),
    lambda s: s["source_quotes"]["USD"].update(value_rub="96"),
    lambda s: s["source_quotes"]["RUB"].update(value_rub="2", nominal=2),
    lambda s: s["source_quotes"].pop("EUR"),
])
def test_exact_source_quotes_cannot_be_changed_or_removed_while_display_rates_look_valid(change):
    snapshot = mutable(parse_cbr_xml(xml(), NOW))
    change(snapshot)
    with pytest.raises(FxUnavailable):
        convert_minor(100, "USD", "EUR", snapshot, NOW)


def test_rate_id_changes_when_publication_raw_body_or_snapshot_lifetime_changes():
    first = parse_cbr_xml(xml(), NOW)
    assert first["snapshot_id"] != parse_cbr_xml(xml(day="25.10.2026"), NOW)["snapshot_id"]
    assert first["snapshot_id"] != parse_cbr_xml(xml(extra="<!-- new source bytes -->"), NOW)["snapshot_id"]
    assert first["snapshot_id"] != parse_cbr_xml(xml(), NOW, ttl_seconds=3600)["snapshot_id"]
    assert first["snapshot_id"] != parse_cbr_xml(xml(), NOW + timedelta(seconds=1))["snapshot_id"]


@pytest.mark.parametrize("raw", [
    b'<!DOCTYPE ValCurs><ValCurs Date="26.10.2026"/>',
    b'<!ENTITY secret SYSTEM "file:///private"><ValCurs/>',
    '<!DOCTYPE ValCurs><ValCurs/>'.encode("utf-16"),
    '<!DOCTYPE ValCurs><ValCurs/>'.encode("utf-32"),
    b'<!doctype ValCurs><ValCurs/>',
])
def test_DTD_and_entities_are_rejected_including_null_interleaved_XML_encodings(raw):
    with pytest.raises(FxUnavailable) as failure:
        parse_cbr_xml(raw, NOW)
    assert failure.value.code == "fx_unsafe_xml"


@pytest.mark.parametrize("ttl", [0, -1, True, 21601, 3600.0, None])
def test_caller_cannot_increase_reference_TTL_above_six_hours(ttl):
    with pytest.raises(FxUnavailable):
        parse_cbr_xml(xml(), NOW, ttl_seconds=ttl)


@pytest.mark.asyncio
async def test_public_HTTP_contract_fixed_host_Moscow_date_no_auth_proxy_redirect_or_retry(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://untrusted.invalid:8888")
    calls = []
    now = datetime(2026, 10, 26, 23, 30, tzinfo=UTC)
    def handler(request):
        calls.append(request)
        assert request.method == "GET" and request.url.scheme == "https"
        assert request.url.host == "www.cbr.ru" and request.url.path == "/scripts/XML_daily.asp"
        assert dict(request.url.params) == {"date_req": "27/10/2026"}
        assert "authorization" not in request.headers and "cookie" not in request.headers
        assert not request.content
        return httpx.Response(200, content=xml(day="27.10.2026"))
    client = CBRFxClient(transport=httpx.MockTransport(handler), clock=lambda: now)
    try:
        result = await client.fetch_snapshot()
        assert result["source_url"] == str(calls[0].url)
        assert result["as_of"] == "2026-10-27" and len(calls) == 1
        # The official endpoint was observed returning valid XML after 11.1s;
        # keep a finite 20s bound without changing host, retries or provenance.
        assert client._client.timeout.read == 20 and client._client.follow_redirects is False
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 302, 403, 429, 500])
async def test_source_failure_or_redirect_never_retries_or_contacts_another_host(status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://remote.invalid/steal"}, content=b"PRIVATE RAW BODY")
    client = CBRFxClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(FxUnavailable) as error:
            await client.fetch_snapshot(NOW)
        assert len(calls) == 1 and "PRIVATE" not in str(error.value.as_dict())
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_size_limit_applies_to_stream_even_if_content_length_lies():
    client = CBRFxClient(transport=httpx.MockTransport(lambda r: httpx.Response(200,
        content=b"x" * (fx.MAX_XML_BYTES + 1), headers={"Content-Length": "0"})))
    try:
        with pytest.raises(FxUnavailable) as error:
            await client.fetch_snapshot(NOW)
        assert error.value.code == "fx_invalid_xml_size"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_source_timeout_is_explicit_and_not_a_fake_snapshot():
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("PRIVATE NETWORK DETAIL")
    client = CBRFxClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(FxUnavailable) as error:
            await client.fetch_snapshot(NOW)
        assert error.value.code == "fx_source_unavailable" and len(calls) == 1
        assert "PRIVATE" not in str(error.value)
    finally:
        await client.close()


def test_import_does_not_create_a_client_or_perform_network(monkeypatch):
    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: pytest.fail("Network on import"))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: pytest.fail("Client on import"))
    spec = importlib.util.spec_from_file_location("fx_no_import_side_effects", Path(fx.__file__))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.CBR_URL == fx.CBR_URL
