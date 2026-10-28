"""No supplier work may precede current, fresh catalogue admission."""
from types import SimpleNamespace

import pytest
from test_dynamic_place_suppliers import dynamic_trip

from otravel.geo_catalog import GeoCatalogError
from otravel.harness import Harness
from otravel.providers import ProviderError


def worker(catalog):
    harness=object.__new__(Harness)
    harness.settings=SimpleNamespace(dynamic_geography_enabled=True)
    harness.geo_catalog=catalog
    harness.travel=object()
    harness.fx=None
    return harness


@pytest.mark.asyncio
@pytest.mark.parametrize('code',['expired_snapshot','candidate_changed','catalog_unavailable'])
async def test_stale_or_changed_geography_stops_before_supplier_search(monkeypatch,code):
    calls=[]
    class Catalog:
        async def verify_places(self,values,**kwargs):
            calls.append((values,kwargs))
            raise GeoCatalogError(code)
    def forbidden(*args,**kwargs):
        pytest.fail('Supplier orchestrator was constructed before geography admission')
    monkeypatch.setattr('otravel.harness.TripSearch',forbidden)
    state=dynamic_trip()
    before=state.model_dump(mode='json')
    with pytest.raises(ProviderError) as caught:
        await worker(Catalog())._search_trip(state)
    assert caught.value.code=='PLACE_REVALIDATION_REQUIRED'
    assert state.model_dump(mode='json')==before
    assert len(calls)==1 and set(calls[0][0])=={state.stays[0].place_id}
    assert calls[0][1]['require_fresh'] is True and calls[0][1]['require_current'] is True


@pytest.mark.asyncio
async def test_missing_catalog_cannot_admit_persisted_dynamic_state(monkeypatch):
    def forbidden(*args,**kwargs):
        pytest.fail('Supplier orchestrator was constructed without a trusted catalogue')
    monkeypatch.setattr('otravel.harness.TripSearch',forbidden)
    with pytest.raises(ProviderError) as caught:
        await worker(None)._search_trip(dynamic_trip())
    assert caught.value.code=='PLACE_REVALIDATION_REQUIRED'


@pytest.mark.asyncio
async def test_validated_snapshot_reaches_search_after_admission(monkeypatch):
    order=[]
    class Catalog:
        async def verify_places(self,values,**kwargs):
            order.append('admission')
            return values
    class Search:
        def __init__(self,*args,**kwargs):
            order.append('construct')
        async def search(self,*args,**kwargs):
            order.append('search')
            return {'plans':[],'tool_calls_used':0}
    monkeypatch.setattr('otravel.harness.TripSearch',Search)
    result=await worker(Catalog())._search_trip(dynamic_trip())
    assert order==['admission','construct','search'] and result['plans']==[]
