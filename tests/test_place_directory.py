"""Geographic facts through MockTransport only; no live API, keys or DB."""
import asyncio
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import httpx
import pytest

import otravel.place_directory as directory
from otravel.place_directory import (
    MemoryPlaceCache,
    PlaceDirectory,
    PlaceDirectoryError,
    validate_directory_candidate,
    validate_directory_key,
)

NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)
CAPITAL = {'id': 3435910, 'name': 'Буэнос-Айрес', 'country_code': 'AR', 'country': 'Аргентина',
    'latitude': -34.61315, 'longitude': -58.37723, 'timezone': 'America/Argentina/Buenos_Aires', 'feature_code': 'PPLC'}
HOMONYM = {'id': 3863777, 'name': 'Buenos Aires', 'country_code': 'AR', 'country': 'Аргентина',
    'latitude': -25.81846, 'longitude': -63.06261, 'timezone': 'America/Argentina/Cordoba', 'feature_code': 'PPL'}
AIRPORT = {'id': 6300524, 'name': 'Airport', 'feature_code': 'AIRP', 'country_code': 'AR'}


class Admission:
    def __init__(self, seconds=4):
        self.calls = []
        self.seconds = seconds

    async def __call__(self, event):
        self.calls.append(deepcopy(event))
        return self.seconds


def key(**overrides):
    return {'provider': 'openmeteo', 'operation': 'search_city', 'role': 'city', 'country_iso2': 'AR',
        'query': 'Buenos Aires', 'language': 'ru', 'count': 5, **overrides}


@pytest.mark.asyncio
async def test_country_filtered_search_returns_all_city_candidates_without_selecting_capital_or_first():
    admit, wires = Admission(), []
    def handler(request):
        assert len(admit.calls) == 1, 'Admission must complete before any HTTP'
        wires.append(request)
        assert request.method == 'GET' and request.url.host == 'geocoding-api.open-meteo.com'
        assert request.url.path == '/v1/search'
        assert dict(request.url.params) == {'language': 'ru', 'format': 'json', 'name': 'buenos aires', 'count': '5', 'countryCode': 'AR'}
        assert 'authorization' not in request.headers
        return httpx.Response(200, json={'results': [HOMONYM, CAPITAL, AIRPORT]})
    client = PlaceDirectory(transport=httpx.MockTransport(handler), clock=lambda: NOW)
    try:
        result = await client.search_city('Buenos Aires', 'ar', before_call=admit)
        assert result['status'] == 'AMBIGUOUS' and result['selection_required'] is True
        assert [c['geographic_source']['external_id'] for c in result['candidates']] == [3863777, 3435910]
        assert 'selected' not in result and 'place_id' not in json.dumps(result)
        assert len(wires) == 1 and len(admit.calls) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_by_source_id_changes_only_language_labels_and_creates_no_internal_id():
    admit = Admission()
    def handler(request):
        assert request.url.path == '/v1/get' and request.url.params['id'] == '3435910'
        row = {**CAPITAL, 'name': 'Buenos Aires', 'country': 'Argentina'} if request.url.params['language'] == 'en' else CAPITAL
        return httpx.Response(200, json=row)
    client = PlaceDirectory(transport=httpx.MockTransport(handler), clock=lambda: NOW)
    try:
        ru = (await client.get_place(3435910, 'ru', before_call=admit))['candidates'][0]
        en = (await client.get_place(3435910, 'en', before_call=admit))['candidates'][0]
        assert ru['canonical_name'] == 'Буэнос-Айрес' and en['canonical_name'] == 'Buenos Aires'
        for field in ('geographic_source', 'latitude', 'longitude', 'country_iso2', 'timezone', 'feature_code'):
            assert ru[field] == en[field]
        assert ru['source_fact_sha256'] != en['source_fact_sha256']
        assert not {'place_id', 'ref_version', 'provider_bindings', 'price'}.intersection(en)
        assert len(admit.calls) == 2
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [{}, {'results': []}, {'results': [AIRPORT]}, {'results': [{'id': 3865483, 'feature_code': 'PCLI'}]}])
async def test_no_city_records_are_not_fabricated_from_airport_country_or_empty_response(body):
    client = PlaceDirectory(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)), clock=lambda: NOW)
    try:
        result = await client.search_city('Argentina', before_call=Admission())
        assert result['status'] == 'NO_MATCH' and result['candidates'] == [] and not result['selection_required']
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_cap_reached_is_ambiguous_even_with_one_city_and_filtered_airport_does_not_hide_truncation():
    cache = MemoryPlaceCache()
    client = PlaceDirectory(cache=cache, transport=httpx.MockTransport(lambda _: httpx.Response(200, json={'results': [CAPITAL]})), clock=lambda: NOW)
    try:
        result = await client.search_city('Buenos Aires', 'AR', count=1, before_call=Admission())
        assert result['truncated'] is True and result['status'] == 'AMBIGUOUS'
        cached = await cache.get_directory(validate_directory_key(key(count=1)), now=NOW)
        assert cached['data']['status'] == 'ambiguous'
    finally:
        await client.close()


@pytest.mark.parametrize('changes', [{'count': True}, {'count': 6}, {'count': 0}, {'country_iso2': 'Argentina'},
    {'country_iso2': 1}, {'query': 'x'}, {'query': 'x'*161}, {'query': 'a\nsecret'}, {'language': 'de'},
    {'operation': []}, {'role': []}, {'provider': 'other'}, {'role': 'arbitrary'}])
def test_bad_key_input_fails_before_client_or_callback(changes):
    with pytest.raises(PlaceDirectoryError) as error:
        validate_directory_key(key(**changes))
    assert error.value.code == 'INVALID_REQUEST'


def test_key_normalization_is_shared_but_does_not_guess_spelling_country_or_city():
    assert validate_directory_key(key(query='  ＢUENOS   Aires  ', country_iso2='ar', language='RU')) == {
        **key(), 'query': 'buenos aires'}
    assert validate_directory_key(key(query='Buenoss Aires'))['query'] == 'buenoss aires'


@pytest.mark.asyncio
@pytest.mark.parametrize('remaining', [None, True, float('nan'), float('inf'), 0, -1])
async def test_unadmitted_or_expired_callback_never_opens_http(remaining):
    calls = []
    client = PlaceDirectory(transport=httpx.MockTransport(lambda request: calls.append(request)), clock=lambda: NOW)
    try:
        with pytest.raises(PlaceDirectoryError):
            await client.search_city('Paris', before_call=Admission(remaining))
        assert calls == [] and client._flights == {}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_budget_rejection_and_non_async_callback_are_private_and_do_not_send_requests():
    wires = []
    client = PlaceDirectory(transport=httpx.MockTransport(lambda r: wires.append(r)), clock=lambda: NOW)
    async def rejected(_):
        raise PlaceDirectoryError('BUDGET_EXHAUSTED')
    async def secret_error(_):
        raise RuntimeError('private-password-from-reservation')
    try:
        for before in (rejected, secret_error, lambda _: 4):
            with pytest.raises(PlaceDirectoryError) as error:
                await client.search_city('Paris', before_call=before)
            assert 'private-password' not in json.dumps(error.value.as_dict())
        assert wires == []
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_cache_hit_costs_zero_and_returned_objects_cannot_poison_the_cache():
    cache, admission, wires = MemoryPlaceCache(), Admission(), []
    def handler(request):
        wires.append(request)
        return httpx.Response(200, json={'results': [CAPITAL]})
    client = PlaceDirectory(cache=cache, transport=httpx.MockTransport(handler), clock=lambda: NOW)
    async def forbidden(_):
        raise AssertionError('A hit must not reserve or open another request')
    try:
        first = await client.search_city('Buenos Aires', 'AR', before_call=admission)
        first['candidates'][0]['canonical_name'] = 'caller mutation'
        hit = await client.search_city('BUENOS AIRES', 'ar', before_call=forbidden)
        assert hit['source']['cache'] == 'hit' and hit['candidates'][0]['canonical_name'] == CAPITAL['name']
        assert len(wires) == len(admission.calls) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('body,seconds', [({'results': [CAPITAL]}, 86400), ({'results': []}, 900)])
async def test_expired_positive_and_negative_cache_entries_require_new_admission(body, seconds):
    clock = [NOW]
    admission = Admission()
    wires = []
    def handler(request):
        wires.append(request)
        return httpx.Response(200, json=body)
    client = PlaceDirectory(cache=MemoryPlaceCache(), transport=httpx.MockTransport(handler), clock=lambda: clock[0])
    try:
        await client.search_city('Buenos Aires', 'AR', before_call=admission)
        clock[0] += timedelta(seconds=seconds+1)
        await client.search_city('Buenos Aires', 'AR', before_call=admission)
        assert len(wires) == len(admission.calls) == 2
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_count_is_part_of_cache_key_so_cached_single_window_cannot_suppress_other_candidates():
    admission, wires = Admission(), []
    def handler(request):
        wires.append(request)
        rows = [CAPITAL] if request.url.params['count'] == '1' else [HOMONYM, CAPITAL]
        return httpx.Response(200, json={'results': rows})
    client = PlaceDirectory(cache=MemoryPlaceCache(), transport=httpx.MockTransport(handler), clock=lambda: NOW)
    try:
        await client.search_city('Buenos Aires', 'AR', count=1, before_call=admission)
        result = await client.search_city('Buenos Aires', 'AR', count=5, before_call=admission)
        assert len(result['candidates']) == 2 and len(wires) == 2
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [302, 400, 401, 403, 429, 503])
async def test_http_errors_never_echo_raw_reason_follow_redirects_or_retry(status):
    wires = []
    def handler(request):
        wires.append(request)
        return httpx.Response(status, json={'error': True, 'reason': 'private-upstream-secret'},
            headers={'Retry-After': '16', 'Location': 'https://foreign.example/steal'})
    client = PlaceDirectory(transport=httpx.MockTransport(handler), clock=lambda: NOW)
    try:
        with pytest.raises(PlaceDirectoryError) as error:
            await client.search_city('Buenos Aires', 'AR', before_call=Admission())
        assert len(wires) == 1
        assert error.value.details['http_status'] == status
        assert 'private-upstream-secret' not in str(error.value) + json.dumps(error.value.as_dict())
        assert error.value.retryable is (status == 429 or status >= 500)
        assert error.value.details['retry_after_seconds'] == 16
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('hint', ['0', '-1', '100000', 'not-a-number', '1e999'])
async def test_bad_retry_hints_are_omitted(hint):
    client = PlaceDirectory(transport=httpx.MockTransport(lambda _: httpx.Response(429, headers={'Retry-After': hint})), clock=lambda: NOW)
    try:
        with pytest.raises(PlaceDirectoryError) as error:
            await client.search_city('Paris', before_call=Admission())
        assert 'retry_after_seconds' not in error.value.details
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('rows', [[{**CAPITAL, 'id': True}], [{**CAPITAL, 'id': '3435910'}],
    [{**CAPITAL, 'timezone': 'Mars/Olympus'}], [{**CAPITAL, 'latitude': 91}],
    [{**CAPITAL, 'admin1_id': True}], [{**CAPITAL, 'admin1_id': 123}], [CAPITAL, CAPITAL],
    [{**CAPITAL, 'feature_code': 'PPL'+'X'*1000}]])
async def test_malformed_city_or_duplicate_id_does_not_become_silent_unique_match(rows):
    client = PlaceDirectory(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={'results': rows})), clock=lambda: NOW)
    try:
        with pytest.raises(PlaceDirectoryError) as error:
            await client.search_city('Buenos Aires', 'AR', before_call=Admission())
        assert error.value.code == 'INVALID_RESPONSE'
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_country_mismatch_and_get_id_mismatch_are_rejected_without_fallback():
    client = PlaceDirectory(transport=httpx.MockTransport(lambda request: httpx.Response(200,
        json={'results': [{**CAPITAL, 'country_code': 'US'}]} if request.url.path.endswith('search') else {**CAPITAL, 'id': 123})), clock=lambda: NOW)
    try:
        with pytest.raises(PlaceDirectoryError) as error:
            await client.search_city('Buenos Aires', 'AR', before_call=Admission())
        assert error.value.code == 'COUNTRY_MISMATCH'
        with pytest.raises(PlaceDirectoryError) as error:
            await client.get_place(3435910, before_call=Admission())
        assert error.value.code == 'INVALID_RESPONSE'
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('raw', [b'{"results":[],"results":[]}', b'{"results":NaN}', b'[]', b'{"results":null}'])
async def test_bad_json_shape_duplicate_keys_and_nonfinite_values_are_private_errors(raw):
    client = PlaceDirectory(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=raw)), clock=lambda: NOW)
    try:
        with pytest.raises(PlaceDirectoryError):
            await client.search_city('Paris', before_call=Admission())
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_singleflight_shares_one_success_and_releases_all_inflight_state():
    entered, release = asyncio.Event(), asyncio.Event()
    wires, admission = [], Admission()
    async def handler(request):
        wires.append(request); entered.set(); await release.wait()
        return httpx.Response(200, json={'results': [CAPITAL]})
    client = PlaceDirectory(transport=httpx.MockTransport(handler), clock=lambda: NOW)
    try:
        owner = asyncio.create_task(client.search_city('Buenos Aires', 'AR', before_call=admission))
        await entered.wait()
        peers = [asyncio.create_task(client.search_city('Buenos Aires', 'AR', before_call=admission)) for _ in range(3)]
        await asyncio.sleep(0)
        assert len(wires) == len(admission.calls) == 1
        release.set()
        results = await asyncio.gather(owner, *peers)
        assert len(wires) == 1 and client._flights == {}
        results[0]['candidates'][0]['canonical_name'] = 'mutation'
        assert results[1]['candidates'][0]['canonical_name'] == CAPITAL['name']
        assert [r['source']['cache'] for r in results] == ['miss', 'shared', 'shared', 'shared']
    finally:
        release.set(); await client.close()


@pytest.mark.asyncio
async def test_singleflight_fans_out_one_failure_without_waiter_retries():
    entered, release = asyncio.Event(), asyncio.Event()
    calls, admission = [], Admission()
    async def handler(request):
        calls.append(request); entered.set(); await release.wait()
        return httpx.Response(429, headers={'Retry-After': '7'})
    client = PlaceDirectory(transport=httpx.MockTransport(handler), clock=lambda: NOW)
    try:
        owner = asyncio.create_task(client.search_city('Paris', before_call=admission))
        await entered.wait()
        peers = [asyncio.create_task(client.search_city('Paris', before_call=admission)) for _ in range(2)]
        await asyncio.sleep(0); release.set()
        results = await asyncio.gather(owner, *peers, return_exceptions=True)
        assert all(isinstance(e, PlaceDirectoryError) and e.code == 'RATE_LIMITED' for e in results)
        assert len(calls) == len(admission.calls) == 1 and client._flights == {}
    finally:
        release.set(); await client.close()


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_cancel_the_owners_http():
    entered, release = asyncio.Event(), asyncio.Event()
    async def handler(_):
        entered.set(); await release.wait()
        return httpx.Response(200, json={'results': [CAPITAL]})
    client = PlaceDirectory(transport=httpx.MockTransport(handler), clock=lambda: NOW)
    try:
        owner = asyncio.create_task(client.search_city('Paris', before_call=Admission()))
        await entered.wait()
        waiter = asyncio.create_task(client.search_city('Paris', before_call=Admission()))
        await asyncio.sleep(0); waiter.cancel()
        with pytest.raises(asyncio.CancelledError): await waiter
        release.set(); assert (await owner)['status'] == 'CANDIDATES'
        assert client._flights == {}
    finally:
        release.set(); await client.close()


@pytest.mark.asyncio
async def test_network_deadline_is_total_and_cleans_singleflight_state():
    async def handler(_):
        await asyncio.sleep(1)
        return httpx.Response(200, json={'results': [CAPITAL]})
    client = PlaceDirectory(transport=httpx.MockTransport(handler), timeout=0.03, clock=lambda: NOW)
    try:
        with pytest.raises(PlaceDirectoryError) as error:
            await client.search_city('Paris', before_call=Admission(0.03))
        assert error.value.code == 'REQUEST_TIMEOUT' and client._flights == {}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_body_is_bounded_during_streaming_and_closed_on_overflow():
    class Large(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            yield b'{'
            yield b'x' * directory.MAX_RESPONSE_BYTES
            raise AssertionError('Overflow must stop reading')
        async def aclose(self):
            self.closed = True
    stream = Large()
    client = PlaceDirectory(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream)), clock=lambda: NOW)
    try:
        with pytest.raises(PlaceDirectoryError) as error:
            await client.search_city('Paris', before_call=Admission())
        assert error.value.code == 'INVALID_RESPONSE' and stream.closed
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_lock_cardinality_limit_refuses_new_query_without_admission(monkeypatch):
    monkeypatch.setattr(directory, 'MAX_INFLIGHT', 1)
    entered, release = asyncio.Event(), asyncio.Event()
    async def handler(_):
        entered.set(); await release.wait()
        return httpx.Response(200, json={})
    client = PlaceDirectory(transport=httpx.MockTransport(handler), clock=lambda: NOW)
    denied = Admission()
    try:
        owner = asyncio.create_task(client.search_city('Paris', before_call=Admission()))
        await entered.wait()
        with pytest.raises(PlaceDirectoryError) as error:
            await client.search_city('Berlin', before_call=denied)
        assert error.value.code == 'BUSY' and denied.calls == []
        release.set(); await owner
        assert client._flights == {}
    finally:
        release.set(); await client.close()


@pytest.mark.asyncio
async def test_candidate_hash_and_unknown_fields_cannot_be_used_as_cache_provenance():
    client = PlaceDirectory(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={'results': [CAPITAL]})), clock=lambda: NOW)
    try:
        fact = (await client.search_city('Buenos Aires', 'AR', before_call=Admission()))['candidates'][0]
        assert validate_directory_candidate(fact) == fact
        for mutation in ({**fact, 'source_fact_sha256': '0'*64}, {**fact, 'place_id': 'forged'},
                {**fact, 'language': []}):
            with pytest.raises(PlaceDirectoryError): validate_directory_candidate(mutation)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_closed_client_and_invalid_clock_do_not_open_wire():
    wires = []
    client = PlaceDirectory(transport=httpx.MockTransport(lambda r: wires.append(r)), clock=lambda: datetime(2026, 1, 1))
    try:
        with pytest.raises(PlaceDirectoryError) as error:
            await client.search_city('Paris', before_call=Admission())
        assert error.value.code == 'INVALID_CLOCK'
        await client.close()
        with pytest.raises(PlaceDirectoryError) as error:
            await client.search_city('Paris', before_call=Admission())
        assert error.value.code == 'CLIENT_CLOSED' and wires == []
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_owner_cancellation_closes_shared_flight_without_hidden_retry():
    entered = asyncio.Event()
    admission = Admission()
    wires = []
    async def handler(request):
        wires.append(request); entered.set(); await asyncio.Event().wait()
    client = PlaceDirectory(transport=httpx.MockTransport(handler), clock=lambda: NOW)
    try:
        owner = asyncio.create_task(client.search_city('Paris', before_call=admission))
        await entered.wait()
        waiter = asyncio.create_task(client.search_city('Paris', before_call=admission))
        await asyncio.sleep(0); owner.cancel()
        results = await asyncio.gather(owner, waiter, return_exceptions=True)
        assert all(isinstance(result, asyncio.CancelledError) for result in results)
        assert len(wires) == len(admission.calls) == 1 and client._flights == {}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_cache_backend_failures_are_private_and_a_read_failure_spends_no_http():
    class Broken:
        async def get_directory(self, key, *, now):
            raise RuntimeError('SQL-password-private')
    wires = []
    client = PlaceDirectory(cache=Broken(), transport=httpx.MockTransport(lambda request: wires.append(request)), clock=lambda: NOW)
    try:
        with pytest.raises(PlaceDirectoryError) as error:
            await client.search_city('Paris', before_call=Admission())
        assert error.value.code == 'CACHE_UNAVAILABLE'
        assert 'SQL-password' not in json.dumps(error.value.as_dict()) and wires == []
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_cache_transaction_is_finished_before_admission_or_wire():
    class Spy(MemoryPlaceCache):
        active = False
        async def get_directory(self, key, *, now):
            self.active = True
            try:
                return await super().get_directory(key, now=now)
            finally:
                self.active = False
        async def put_directory(self, key, envelope, *, now):
            self.active = True
            try:
                return await super().put_directory(key, envelope, now=now)
            finally:
                self.active = False
    cache = Spy()
    async def admit(_):
        assert cache.active is False
        return 4
    def handler(_):
        assert cache.active is False
        return httpx.Response(200, json={'results': [CAPITAL]})
    client = PlaceDirectory(cache=cache, transport=httpx.MockTransport(handler), clock=lambda: NOW)
    try:
        assert (await client.search_city('Buenos Aires', 'AR', before_call=admit))['candidates']
        assert cache.active is False
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_empty_truncated_window_is_negative_but_a_forged_unique_full_window_is_not_trusted():
    cache = MemoryPlaceCache()
    client = PlaceDirectory(cache=cache, transport=httpx.MockTransport(lambda _: httpx.Response(200, json={'results': [AIRPORT]})), clock=lambda: NOW)
    try:
        result = await client.search_city('Buenos Aires', 'AR', count=1, before_call=Admission())
        assert result['status'] == 'NO_MATCH' and result['truncated'] is True
        saved = await cache.get_directory(validate_directory_key(key(count=1)), now=NOW)
        assert saved['data'] == {'status': 'no_match', 'candidates': [], 'truncated': True}
        candidate = directory._candidate(CAPITAL, 'ru')
        saved['data'] = {'status': 'positive', 'candidates': [candidate], 'truncated': False}
        with pytest.raises(PlaceDirectoryError):
            await cache.put_directory(validate_directory_key(key(count=1)), saved, now=NOW)
    finally:
        await client.close()
