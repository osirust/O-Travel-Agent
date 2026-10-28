import asyncio

import httpx

from otravel.destination_photos import DestinationPhotos, _safe_url


def test_city_photos_are_attributed_and_cached():
    calls = []
    def handler(request):
        calls.append(request.url.host)
        if request.url.host == 'en.wikipedia.org':
            return httpx.Response(200, json={'query': {'pages': [{'title': 'Istanbul', 'pageimage': 'Istanbul_view.jpg'}]}})
        return httpx.Response(200, json={'query': {'pages': [{'title': 'File:Istanbul view.jpg', 'imageinfo': [{
            'thumburl': 'https://upload.wikimedia.org/wikipedia/commons/thumb/a/a1/Istanbul.jpg/320px-Istanbul.jpg',
            'descriptionurl': 'https://commons.wikimedia.org/wiki/File:Istanbul.jpg',
            'extmetadata': {'Artist': {'value': '<a href="https://example.test">Example author</a>'}, 'LicenseShortName': {'value': 'CC BY 4.0'}},
        }]}]}})
    async def run():
        resolver = DestinationPhotos(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            first = await resolver.resolve(['Стамбул'])
            second = await resolver.resolve(['Стамбул'])
            assert first == second
            assert first['Стамбул']['author'] == 'Example author'
            assert first['Стамбул']['license'] == 'CC BY 4.0'
            assert len(calls) == 2
        finally:
            await resolver.close()
    asyncio.run(run())


def test_photo_urls_reject_untrusted_or_credentialed_hosts():
    assert _safe_url('https://upload.wikimedia.org/wikipedia/commons/a/example.jpg', 'upload.wikimedia.org', '/wikipedia/commons/')
    for value in ['https://upload.wikimedia.org.evil.test/wikipedia/commons/a/x.jpg', 'https://user:pass@upload.wikimedia.org/wikipedia/commons/a/x.jpg', 'https://upload.wikimedia.org/wikipedia/commons/a/x.jpg?token=x', 'http://upload.wikimedia.org/wikipedia/commons/a/x.jpg']:
        assert _safe_url(value, 'upload.wikimedia.org', '/wikipedia/commons/') is None
