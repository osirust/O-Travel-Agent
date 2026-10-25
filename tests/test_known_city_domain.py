"""Owner policy v2: known city identity is separate from DEMO inventory scope."""
import pytest

from otravel.domain import CITIES, DomainError, Operation, TripState, apply_operations, canonical_city
from otravel.inventory import DemoInventory


@pytest.mark.parametrize("value,expected", [
    ("Белград", "Белград"), ("Belgrade", "Белград"), ("Beograd", "Белград"),
    ("Нови Сад", "Нови-Сад"), ("Novi Sad", "Нови-Сад"), ("Belgorod", "Белгород"),
])
def test_only_curated_known_city_aliases_are_canonical(value, expected):
    assert canonical_city(value) == expected
    assert expected not in CITIES


@pytest.mark.parametrize("value", ["Сербия", "RS", "Турция", "Georgia", "Корея", "Лондон", "Белгрод"])
def test_countries_ambiguity_and_unregistered_names_are_not_city_values(value):
    with pytest.raises(DomainError) as caught:
        canonical_city(value)
    assert caught.value.code == "unsupported_city"


def test_explicit_belgrade_route_is_retained_but_demo_does_not_fabricate_coverage():
    original = TripState()
    state, _ = apply_operations(original, [
        Operation(type="set_origin", value={"origin": "Москва"}),
        Operation(type="add_traveler", value={"id": "one-adult", "type": "adult"}),
        Operation(type="upsert_stay", target_id="serbia-stay", value={"city": "Белград", "check_in": "2026-10-26", "check_out": "2026-10-29", "rooms": [{"id": "one-room", "traveler_ids": ["one-adult"]}]}),
        Operation(type="set_budget", value={"amount_minor": 10000000}),
    ])
    result = DemoInventory().search_trip(state)
    assert state.stays[0].city == "Белград"
    assert [(leg.origin, leg.destination) for leg in state.legs] == [("Москва", "Белград"), ("Белград", "Москва")]
    assert result["plans"] == []
    assert any(item["code"] == "unsupported_city" for item in result["violations"])
    assert CITIES == ("Москва", "Санкт-Петербург", "Сочи", "Казань", "Стамбул", "Анталья", "Дубай")
