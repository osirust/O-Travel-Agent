"""Presentation lookup is owner-bound and never writes a priced plan."""
from copy import deepcopy
import hashlib
from types import SimpleNamespace

import httpx
import pytest

from otravel.api import create_app
from otravel.config import Settings


class MediaStore:
    def __init__(self):
        self.session = {'session_id': 'own', 'trip_id': 'trip', 'state_version': 3,
                        'owner_token_hash': hashlib.sha256(b'private-owner').hexdigest()}
        self.plan = {'plan_id': 'priced', 'trip_id': 'trip', 'state_version': 3,
                     'total_minor': 70000, 'currency': 'RUB', 'offers': [{'offer_id': 'h', 'details': {}}]}
        self.partial = False

    async def get_session(self, session_id):
        assert session_id == 'own'
        return deepcopy(self.session)

    async def list_messages(self, session_id):
        return [{'run_id': 'search'}]

    async def get_run(self, run_id):
        return {'result': {'result': {'plans': [] if self.partial else [deepcopy(self.plan)],
                                      'partial_plan': deepcopy(self.plan) if self.partial else None}}}


class Pictures:
    def __init__(self, store):
        self.store, self.calls, self.concurrent_edit = store, 0, False

    async def resolve_plan(self, plan):
        self.calls += 1
        plan['total_minor'] = 1  # Presentation gets its own copy, never canonical pricing.
        if self.concurrent_edit:
            self.store.session['state_version'] += 1
        return {'items': [{'offer_id': 'h', 'component_id': 'stay', 'media': {
            'url': None, 'kind': 'PLACEHOLDER', 'source': 'NONE', 'isActualPropertyPhoto': False}}],
            'warnings': []}


@pytest.mark.asyncio
async def test_media_requires_owner_and_current_saved_plan_without_changing_price():
    store = MediaStore()
    pictures = Pictures(store)
    app = create_app(Settings('unused', 'groq', 1, 8000, 0, 1), store=store,
                     llm=SimpleNamespace(), travel=SimpleNamespace(), media=pictures)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        path = '/api/v1/sessions/own/media?plan_id=priced&state_version=3'
        assert (await client.get(path)).status_code == 401
        assert (await client.get(path, headers={'Authorization': 'Bearer other'})).status_code == 403
        headers = {'Authorization': 'Bearer private-owner'}
        assert (await client.get(path.replace('version=3', 'version=2'), headers=headers)).status_code == 409
        assert (await client.get(path.replace('priced', 'unknown'), headers=headers)).status_code == 404
        assert pictures.calls == 0
        before = deepcopy(store.plan)
        result = await client.get(path, headers=headers)
        assert result.status_code == 200
        assert result.json()['plan_id'] == 'priced' and result.json()['state_version'] == 3
        assert result.json()['items'][0]['media']['kind'] == 'PLACEHOLDER'
        assert store.plan == before and pictures.calls == 1


@pytest.mark.asyncio
async def test_media_await_cannot_return_old_revision_after_concurrent_edit():
    store = MediaStore()
    pictures = Pictures(store)
    pictures.concurrent_edit = True
    app = create_app(Settings('unused', 'groq', 1, 8000, 0, 1), store=store,
                     llm=SimpleNamespace(), travel=SimpleNamespace(), media=pictures)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        result = await client.get('/api/v1/sessions/own/media?plan_id=priced&state_version=3',
                                  headers={'Authorization': 'Bearer private-owner'})
    assert result.status_code == 409 and result.json()['error']['code'] == 'stale_state'
    assert store.plan['total_minor'] == 70000 and pictures.calls == 1


@pytest.mark.asyncio
async def test_partial_prices_can_have_photos_without_becoming_a_checkout_plan():
    store = MediaStore()
    store.partial = True
    pictures = Pictures(store)
    app = create_app(Settings('unused', 'groq', 1, 8000, 0, 1), store=store,
                     llm=SimpleNamespace(), travel=SimpleNamespace(), media=pictures)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        headers = {'Authorization': 'Bearer private-owner'}
        result = await client.get('/api/v1/sessions/own/media?plan_id=priced&state_version=3', headers=headers)
        assert result.status_code == 200 and pictures.calls == 1
        preview = await client.post('/api/v1/sessions/own/checkout/preview', headers=headers,
                                     json={'plan_id': 'priced', 'state_version': 3})
        assert preview.status_code == 404
