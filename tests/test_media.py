"""Read-only hotel media contract: fake provider only, no image fetch or keys."""
import asyncio
import json
import socket
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

from otravel.media import MEDIA_CSP_SOURCES, MediaResolver, safe_media_url
from otravel.providers import ProviderError

NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)
CDN = 'https://snaphotelapi.com/hotels'
S3 = 'https://liteapi-travel-static-data.s3.amazonaws.com/images/hotels'
DTO_KEYS = {'url', 'thumbnailUrl', 'alt', 'kind', 'source', 'hotelId', 'sourceHotelId',
            'isActualPropertyPhoto', 'caption', 'gallery'}


def content(hotel_id='lp-1', images=None, **extra):
    if images is None:
        images = [{'url': f'{CDN}/image-1.jpg', 'thumbnailUrl': f'{CDN}/thumb-1.jpg',
                   'caption': 'Actual property fixture', 'defaultImage': True, 'order': 1}]
    return {'data': {'id': hotel_id, 'name': 'Fixture Hotel', 'hotelImages': deepcopy(images), **extra}}


def hotel(hotel_id='lp-1', offer_id='off-1', **extra):
    return {'offer_id': offer_id, 'component_id': 'stay-' + offer_id,
        'kind': 'hotel', 'provider': 'liteapi', 'source_type': 'SANDBOX', 'amount_minor': 12345,
        'currency': 'EUR', 'details': {'kind': 'hotel', 'binding': {'hotel_id': hotel_id}}, **extra}


class Gateway:
    def __init__(self, body=None, *, settings=None, error=None):
        self.settings = {'LITEAPI_API_KEY': 'sand_synthetic', 'LITEAPI_MODE': 'sandbox', **(settings or {})}
        self.body = content() if body is None else body
        self.error = error
        self.instant = NOW
        self.calls = []
        self.invocations = 0
        self.active = self.max_active = 0
        self.blocked_until = None

    def _now(self):
        return self.instant

    def _key(self, name, provider):
        assert name == 'LITEAPI_API_KEY' and provider == 'liteapi'
        key = self.settings.get(name)
        if not key:
            raise ProviderError('CONFIG_MISSING', 'Synthetic missing key', provider=provider)
        return key

    async def _request(self, provider, method, url, **kwargs):
        self.invocations += 1
        assert provider == 'liteapi' and method == 'GET'
        assert url == 'https://api.liteapi.travel/v3.0/data/hotel'
        assert kwargs['headers'] == {'X-API-Key': self.settings['LITEAPI_API_KEY']}
        assert set(kwargs['params']) == {'hotelId', 'timeout'} and kwargs['params']['timeout'] == 4
        if self.blocked_until is not None and self.instant < self.blocked_until:
            raise ProviderError('SOURCE_COOLDOWN', 'Synthetic provider cooldown', True, 'liteapi')
        self.calls.append({'provider': provider, 'method': method, 'url': url, 'hotel_id': kwargs['params']['hotelId']})
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0)
            if self.error is not None:
                error = self.error
                self.error = None
                if error.code == 'RATE_LIMITED':
                    self.blocked_until = self.instant + timedelta(seconds=120)
                raise error
            body = self.body(kwargs['params']['hotelId']) if callable(self.body) else self.body
            return deepcopy(body)
        finally:
            self.active -= 1


@pytest.fixture(autouse=True)
def forbid_own_http_client_dns_or_image_fetch(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Media must reuse its supplied gateway; no DNS/image fetch or own HTTP client')
    monkeypatch.setattr(httpx, 'AsyncClient', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(socket, 'gethostbyname', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)


@pytest.mark.parametrize('value', [f'{CDN}/hotel/photo.jpg', f'{S3}/property/a.jpg',
    'https://snaphotelapi.com:443/hotels/photo.jpg?width=160', 'https://SNAPHOTELAPI.COM/hotels/photo.jpg'])
def test_media_url_only_approved_https_hosts_and_optional443_without_dns(value):
    actual = safe_media_url(value)
    assert isinstance(actual, str)
    assert urlsplit(actual).scheme == 'https'
    assert urlsplit(actual).hostname in {'snaphotelapi.com', 'liteapi-travel-static-data.s3.amazonaws.com'}


def test_authenticated_LiteAPI_static_cupid_CDN_exact_path_is_CSP_allowed_not_a_wildcard():
    value = 'https://static.cupid.travel/hotels/210194244.jpg'
    assert safe_media_url(value) == value
    assert 'https://static.cupid.travel' in MEDIA_CSP_SOURCES
    for unsafe in ('https://static.cupid.travel.evil.test/hotels/210194244.jpg',
                   'https://other.cupid.travel/hotels/210194244.jpg',
                   'https://static.cupid.travel/redirect/210194244.jpg',
                   'https://static.cupid.travel/hotels/210194244.jpg?api_key=secret'):
        assert safe_media_url(unsafe) is None


@pytest.mark.asyncio
async def test_redacted_actual_Dubai_content_projection_gives_five_identity_bound_photos():
    fixture = json.loads((Path(__file__).parents[1] / 'fixtures/regression/liteapi-hotel-media-lp1b30d1-20261026.json').read_text(encoding='utf-8'))
    gateway = Gateway(fixture['content_projection'])
    plan = {'offers': [hotel(fixture['hotel_id'])]}
    before = deepcopy(plan)
    response = await MediaResolver(gateway).resolve_plan(plan)
    assert response['warnings'] == []
    assert response['items'][0]['media'] == fixture['expected_media_for_projection']
    assert len(response['items'][0]['media']['gallery']) == 5
    assert response['items'][0]['media']['hotelId'] == response['items'][0]['media']['sourceHotelId'] == 'lp1b30d1'
    assert len(gateway.calls) == 1 and gateway.calls[0]['hotel_id'] == 'lp1b30d1'
    assert plan == before


@pytest.mark.parametrize('value', [None, {}, [], 1, '', 'http://snaphotelapi.com/hotels/a.jpg',
    'javascript:alert(1)', 'data:image/png;base64,AA==', '//snaphotelapi.com/hotels/a.jpg', '/local.jpg',
    'https://localhost/a.jpg', 'https://127.0.0.1/a.jpg', 'https://169.254.169.254/a.jpg',
    'https://[::1]/a.jpg', 'https://10.0.0.1/a.jpg', 'https://example.com/a.jpg',
    'https://snaphotelapi.com.evil.test/a.jpg', 'https://evil-snaphotelapi.com/hotels/a.jpg',
    'https://user@snaphotelapi.com/hotels/a.jpg', 'https://user:password@snaphotelapi.com/hotels/a.jpg',
    'https://snaphotelapi.com:444/a.jpg', 'https://snaphotelapi.com:80/a.jpg',
    'https://snaphotelapi.com\\@evil.test/a.jpg', 'https://snaphotelapi.com/hotels/a\\b.jpg',
    'https://snaphotelapi.com/hotels/a\nb.jpg', 'https://snaphotelapi.com/hotels/a\rb.jpg', 'https://snaphotelapi.com/\ta.jpg',
    'https://snaphotelapi.com/hotels/a%0ab.jpg', 'https://snaphotelapi.com/hotels/a%5cb.jpg',
    'https://snaphotelapi.com/hotels/a.jpg?api_key=secret', 'https://snaphotelapi.com/hotels/a.jpg?%61pi_key=secret',
    'https://snaphotelapi.com/hotels/a.jpg?ACCESS_TOKEN=secret', 'https://snaphotelapi.com/hotels/a.jpg?token=secret',
    'https://snaphotelapi.com/hotels/a.jpg?password=secret', 'https://snaphotelapi.com/hotels/a.jpg?signature=secret',
    'https://liteapi-travel-static-data.s3.amazonaws.com/images/hotels/a.jpg?X-Amz-Credential=secret'])
def test_unsafe_url_never_reaches_browser_or_triggers_resolution(value):
    assert safe_media_url(value) is None


@pytest.mark.asyncio
async def test_hotel_media_exact_DTO_default_image_then_order_five_unique_urls():
    images = [{'url': f'{CDN}/image-3.jpg', 'order': 3},
        {'url': f'{CDN}/image-1.jpg', 'defaultImage': True, 'order': 50, 'caption': '<b>Preferred</b>',
         'thumbnailUrl': f'{CDN}/thumb-1.jpg'},
        {'url': f'{CDN}/image-2.jpg', 'order': 2}, {'url': f'{CDN}/image-1.jpg', 'order': 1},
        *[{'url': f'{CDN}/image-{i}.jpg', 'order': i} for i in range(4, 8)],
        {'url': 'https://evil.test/fake.jpg', 'defaultImage': True, 'order': -1}]
    gateway = Gateway(content(images=images))
    media = await MediaResolver(gateway).hotel_media('lp-1')
    assert set(media) == DTO_KEYS
    assert media['url'] == f'{CDN}/image-1.jpg' and media['thumbnailUrl'] == f'{CDN}/thumb-1.jpg'
    assert media['kind'] == 'HOTEL' and media['source'] == 'LITEAPI'
    assert media['hotelId'] == media['sourceHotelId'] == 'lp-1' and media['isActualPropertyPhoto'] is True
    assert [row['url'] for row in media['gallery']] == [f'{CDN}/image-{i}.jpg' for i in range(1, 6)]
    assert all(set(row) == {'url', 'alt', 'caption'} for row in media['gallery'])
    assert len(gateway.calls) == 1


@pytest.mark.asyncio
async def test_unsafe_thumbnail_is_null_and_text_is_plain_bounded_not_HTML():
    body = content(images=[{'url': f'{CDN}/actual.jpg', 'thumbnailUrl': 'https://evil.test/a.jpg',
        'caption': '<b>Caption &amp; detail</b> ' + 'x' * 3000}], name='<b>Hotel &amp; Suites</b> ' + 'y' * 3000)
    media = await MediaResolver(Gateway(body)).hotel_media('lp-1')
    assert media['kind'] == 'HOTEL' and media['thumbnailUrl'] is None
    for text in (media['alt'], media['caption'], media['gallery'][0]['alt'], media['gallery'][0]['caption']):
        assert isinstance(text, str) and len(text) <= 500 and '<' not in text and '>' not in text
    assert 'Caption' in media['alt'] or 'Hotel' in media['alt']


@pytest.mark.asyncio
async def test_main_photo_fallback_still_requires_actual_property_identity_and_safe_host():
    gateway = Gateway(content(images=[], main_photo=f'{S3}/main.jpg', thumbnail=f'{S3}/thumb.jpg'))
    media = await MediaResolver(gateway).hotel_media('lp-1')
    assert media['url'] == f'{S3}/main.jpg' and media['kind'] == 'HOTEL'
    assert media['sourceHotelId'] == 'lp-1' and media['isActualPropertyPhoto'] is True


@pytest.mark.asyncio
async def test_public_media_never_contains_the_gateway_key_in_text_or_image_path():
    secret = 'sand_synthetic'
    body = content(name=f'Hotel {secret}', images=[
        {'url': f'{CDN}/{secret}.jpg', 'defaultImage': True, 'caption': secret},
        {'url': f'{CDN}/%73and_synthetic.jpg', 'caption': secret},
        {'url': f'{CDN}/safe.jpg', 'caption': f'Actual photo {secret}'}])
    media = await MediaResolver(Gateway(body)).hotel_media('lp-1')
    assert media['url'] == f'{CDN}/safe.jpg'
    assert secret not in json.dumps(media)


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [content('another-hotel'), {'data': {'id': 1}}, {'data': []}, {},
    content(images=[{'url': 'https://evil.test/a.jpg'}])])
async def test_mismatch_or_missing_safe_photo_is_placeholder_and_never_false_join(body):
    media = await MediaResolver(Gateway(body)).hotel_media('lp-1')
    assert set(media) == DTO_KEYS
    assert media['kind'] == 'PLACEHOLDER' and media['source'] == 'NONE'
    assert media['url'] is None and media['isActualPropertyPhoto'] is False
    assert media['sourceHotelId'] is None and media['gallery'] == []


@pytest.mark.asyncio
async def test_hotel_content_is_response_bound_dict_or_ProviderError():
    resolver = MediaResolver(Gateway(content('lp-1')))
    actual = await resolver.hotel_content('lp-1')
    assert isinstance(actual, dict) and actual.get('id') == 'lp-1'
    with pytest.raises(ProviderError):
        await MediaResolver(Gateway(content('foreign'))).hotel_content('lp-1')


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [{'LITEAPI_API_KEY': ''}, {'LITEAPI_API_KEY': 'prod_forbidden'},
    {'LITEAPI_API_KEY': 'SANDBOX_uppercase'}, {'LITEAPI_MODE': 'live'}, {'LITEAPI_MODE': None}])
async def test_config_unsafe_preflight_never_calls_provider(change):
    gateway = Gateway(settings=change)
    media = await MediaResolver(gateway).hotel_media('lp-1')
    assert media['kind'] == 'PLACEHOLDER' and media['source'] == 'NONE'
    assert gateway.calls == [] and gateway.invocations == 0


@pytest.mark.asyncio
async def test_resolve_plan_uses_only_matching_LiteAPI_property_and_leaves_economics_byte_exact():
    offers = [hotel(), hotel('lp-demo', 'off-demo', provider='demo_inventory', source_type='DEMO'),
        hotel('lp-search', 'off-search', provider='serpapi', source_type='LIVE_INDICATIVE'),
        {'offer_id': 'flight', 'kind': 'flight', 'provider': 'duffel', 'source_type': 'SANDBOX'}]
    plan = {'plan_id': 'plan-fixture', 'snapshot_hash': 'a' * 64, 'offers': offers,
        'total_minor': 1_234_500, 'currency': 'RUB', 'currency_totals': {'EUR': 12345},
        'fx_snapshot_id': 'cbr-fixture', 'components': [{'amount_minor': 12345, 'currency': 'EUR', 'budget_amount_minor': 1_234_500}]}
    before = json.dumps(plan, ensure_ascii=False, sort_keys=True)
    gateway = Gateway()
    result = await MediaResolver(gateway).resolve_plan(plan)
    assert set(result) == {'items', 'warnings'} and len(result['items']) == 3
    assert json.dumps(plan, ensure_ascii=False, sort_keys=True) == before
    indexed = {item['offer_id']: item for item in result['items']}
    assert indexed['off-1']['component_id'] == 'stay-off-1'
    assert indexed['off-1']['media']['kind'] == 'HOTEL'
    assert indexed['off-demo']['media']['kind'] == indexed['off-search']['media']['kind'] == 'PLACEHOLDER'
    assert len(gateway.calls) == 1 and gateway.calls[0]['hotel_id'] == 'lp-1'


@pytest.mark.asyncio
@pytest.mark.parametrize('field_location', ['top', 'details'])
async def test_binding_and_legacy_property_ID_conflict_is_placeholder_without_HTTP(field_location):
    gateway = Gateway()
    offer = hotel()
    (offer if field_location == 'top' else offer['details'])['hotel_id'] = 'another-hotel'
    result = await MediaResolver(gateway).resolve_plan({'offers': [offer]})
    # The declared binding remains lp-1 and must not be joined to another hotel.
    assert result['items'][0]['media']['kind'] == 'PLACEHOLDER'
    assert result['warnings'] and gateway.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize('provider,source', [('liteapi', 'DEMO'), ('serpapi', 'LIVE_INDICATIVE'),
    ('demo_inventory', 'DEMO'), ('liteapi', 'UNKNOWN')])
async def test_unproven_or_demo_property_never_gets_an_actual_photo_by_its_name_or_ID(provider, source):
    gateway = Gateway()
    offer = hotel(provider=provider, source_type=source)
    result = await MediaResolver(gateway).resolve_plan({'offers': [offer]})
    media = result['items'][0]['media']
    assert media['kind'] == 'PLACEHOLDER' and media['source'] == 'NONE'
    assert media['url'] is None and media['sourceHotelId'] is None and media['isActualPropertyPhoto'] is False
    assert not gateway.calls and gateway.invocations == 0


@pytest.mark.asyncio
async def test_missing_property_ID_never_falls_back_to_name_description_or_first_search_hit():
    offer = hotel()
    offer['details'].pop('binding')
    offer['description'] = 'Fixture Hotel, same name as lp-1'
    gateway = Gateway()
    result = await MediaResolver(gateway).resolve_plan({'offers': [offer]})
    assert result['items'][0]['media']['kind'] == 'PLACEHOLDER'
    assert not gateway.calls and result['warnings']


@pytest.mark.asyncio
async def test_legacy_hotel_ID_and_details_kind_are_supported_without_guessing_another_property():
    offer = hotel()
    offer.pop('kind')
    offer['details'].pop('binding')
    offer['hotel_id'] = 'lp-1'
    gateway = Gateway()
    result = await MediaResolver(gateway).resolve_plan({'offers': [offer]})
    assert result['items'][0]['media']['sourceHotelId'] == 'lp-1'
    assert gateway.calls[0]['hotel_id'] == 'lp-1'


@pytest.mark.asyncio
async def test_plan_maxfive_unique_requests_are_sequential_and_duplicates_share_content():
    offers = [hotel(f'lp-{i}', f'off-{i}') for i in range(1, 8)]
    offers.insert(2, hotel('lp-1', 'off-repeat'))
    gateway = Gateway(body=lambda hotel_id: content(hotel_id))
    result = await MediaResolver(gateway).resolve_plan({'offers': offers})
    assert len(result['items']) == len(offers)
    assert [row['hotel_id'] for row in gateway.calls] == [f'lp-{i}' for i in range(1, 6)]
    assert gateway.max_active == 1
    rows = {item['offer_id']: item['media'] for item in result['items']}
    assert rows['off-repeat'] == rows['off-1'] and rows['off-7']['kind'] == 'PLACEHOLDER'
    assert result['warnings']


@pytest.mark.asyncio
async def test_positive_cache_six_hours_per_resolver_and_concurrent_same_property_deduped():
    gateway = Gateway()
    resolver = MediaResolver(gateway)
    results = await asyncio.gather(*(resolver.hotel_media('lp-1') for _ in range(6)))
    assert all(media['kind'] == 'HOTEL' for media in results)
    assert len(gateway.calls) == 1
    gateway.instant += timedelta(hours=5, minutes=59)
    assert (await resolver.hotel_media('lp-1'))['kind'] == 'HOTEL' and len(gateway.calls) == 1
    gateway.instant += timedelta(minutes=2)
    await resolver.hotel_media('lp-1')
    assert len(gateway.calls) == 2
    await MediaResolver(gateway).hotel_media('lp-1')
    assert len(gateway.calls) == 3  # Content cache belongs to this resolver.


@pytest.mark.asyncio
async def test_provider429_negative_cache60seconds_reuses_gateway_cooldown_without_blind_retry():
    gateway = Gateway(error=ProviderError('RATE_LIMITED', 'Synthetic429', True, 'liteapi', {'retry_after_seconds': 120}))
    resolver = MediaResolver(gateway)
    assert (await resolver.hotel_media('lp-1'))['kind'] == 'PLACEHOLDER'
    assert len(gateway.calls) == gateway.invocations == 1
    gateway.instant += timedelta(seconds=59)
    assert (await resolver.hotel_media('lp-1'))['kind'] == 'PLACEHOLDER'
    assert len(gateway.calls) == gateway.invocations == 1
    gateway.instant += timedelta(seconds=2)
    assert (await resolver.hotel_media('lp-1'))['kind'] == 'PLACEHOLDER'
    assert gateway.invocations == 2 and len(gateway.calls) == 1  # Same provider guard, no second wire request.
    gateway.instant += timedelta(seconds=121)
    assert (await resolver.hotel_media('lp-1'))['kind'] == 'HOTEL'
    assert len(gateway.calls) == 2
