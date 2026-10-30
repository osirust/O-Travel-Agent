"""Pinned country identity is independent of supplier availability. No API/DB."""
from copy import deepcopy
from datetime import timedelta

import pytest
from test_place_resolution import NOW, System, request, state

from otravel.country_lexicon import DATA_SHA256
from otravel.place_resolution import PlaceResolutionCoordinator


def offline(system):
    system.coordinator=PlaceResolutionCoordinator(system.catalog,system.directory,system.travel,
        clock=lambda:system.clock,offline_countries=True)
    return system


@pytest.mark.asyncio
@pytest.mark.parametrize("query,code", [("AR","AR"),("Argentina","AR"),("Аргентина","AR"),("Аргентину","AR"),
    ("DE","DE"),("Germany","DE"),("Германию","DE"),("Brazil","BR"),("Бразилия","BR"),
    ("Singapore","SG"),("Australia","AU"),("Казахстан","KZ")])
async def test_known_country_uses_pinned_identity_without_touching_provider_or_geocoder(query,code):
    system,original=offline(System()),state()
    before=deepcopy(original)
    try:
        packet=await system.coordinator.resolve(request(kind="country",query=query),original,before_call=system.before)
        assert packet["action"]=="ask" and not packet["operations"] and not packet["resolved_places"]
        pending=packet["pending_context"]["place_resolution"]
        assert pending["stage"]=="city_query" and not pending["candidates"]
        country=pending["country_ref"]
        assert country["country_iso2"]==code and country["source_provider"]=="unicode-cldr"
        assert country["source_sha256"]==DATA_SHA256
        assert original==before and not system.travel.calls and not system.wires and not system.budgets
    finally:
        await system.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("query,hint", [("Atlantis","AR"),("Georgia","GE"),("AR","GB")])
async def test_hint_or_ambiguous_label_requires_explicit_country_confirmation(query,hint):
    system,original=offline(System()),state()
    try:
        packet=await system.coordinator.resolve(request(kind="country",query=query,country_iso2=hint),original,before_call=system.before)
        assert packet["action"]=="ask" and packet["pending_context"]["place_resolution"]["stage"]=="country_choice"
        assert not packet["operations"] and not packet["resolved_places"]
        if query=="Atlantis":
            confirmed=await system.coordinator.select("да",packet["pending_context"],original,before_call=system.before)
            ref=confirmed["pending_context"]["place_resolution"]["country_ref"]
            assert ref["source_provider"]=="unicode-cldr" and ref["country_iso2"]=="AR"
            assert ref["source_sha256"]==DATA_SHA256
        assert not system.travel.calls and not system.wires and not system.budgets
    finally:
        await system.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["Atlantis","XX","in","us","ar","Аргентина или Сербия"])
async def test_unknown_compound_or_lowercase_two_letter_word_is_not_a_country_fact(query):
    system=offline(System())
    try:
        packet=await system.coordinator.resolve(request(kind="country",query=query),state(),before_call=system.before)
        assert packet["action"]=="no_match" and not packet["operations"] and not packet["resolved_places"]
        assert not system.travel.calls and not system.wires and not system.budgets
    finally:
        await system.close()


@pytest.mark.asyncio
async def test_offline_country_ttl_does_not_refresh_on_city_query_and_never_proves_inventory():
    system,original=offline(System(rows=[])),state()
    try:
        country=await system.coordinator.resolve(request(kind="country",query="AR"),original,before_call=system.before)
        ref=country["pending_context"]["place_resolution"]["country_ref"]
        system.clock+=timedelta(minutes=1)
        city=await system.coordinator.resolve(request(query="Atlantis",country_ref=ref),original,before_call=system.before)
        packet=city["pending_context"]["place_resolution"]
        assert city["action"]=="no_match" and packet["country_ref"]==ref
        assert not city["operations"] and not city["resolved_places"] and not system.travel.calls
        assert len(system.wires)==1 and len(system.budgets)==1
        assert ref["retrieved_at"]==NOW.isoformat()
    finally:
        await system.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("name","Invented"),("source_sha256","0"*64),("country_iso2","XX")])
async def test_persisted_cldr_country_ref_cannot_change_source_or_identity(field,value):
    from otravel.place_resolution import _sha
    system,original=offline(System()),state()
    try:
        country=await system.coordinator.resolve(request(kind="country",query="AR"),original,before_call=system.before)
        ref=deepcopy(country["pending_context"]["place_resolution"]["country_ref"])
        ref[field]=value
        ref["fact_sha256"]=_sha({"country_iso2":ref["country_iso2"],"name":ref["name"]})
        result=await system.coordinator.resolve(request(query="Buenos Aires",country_ref=ref),original,before_call=system.before)
        assert result["action"]=="ask" and not result["operations"] and not result["resolved_places"]
        assert not system.wires and not system.travel.calls
    finally:
        await system.close()
