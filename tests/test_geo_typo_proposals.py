"""Curated one-edit/transposition suggestions require owned confirmation."""
from copy import deepcopy

import pytest

from otravel.domain import TripState
from otravel.intent_grounding import ground_intent_operations


@pytest.mark.parametrize("text,field,candidate,country,model_ops", [
    ("Из Казани в Санкт-Петербрг, двое взрослых без детей.", "destination.city", "Санкт-Петербург", "RU",
     [{"type": "upsert_stay", "target_id": "new-stay-1", "value": {"city": "Санкт-Петербург"}}]),
    ("Из Мосвкы в Анталью хочу, двое взрослых без детей.", "origin.city", "Москва", "RU",
     [{"type": "set_origin", "value": {"city": "Москва"}},
      {"type": "upsert_stay", "target_id": "new-stay-1", "value": {"city": "Анталья"}}]),
    ("Из Москвы в Стамбулл, бюджет 90000 рублей.", "destination.city", "Стамбул", "TR",
     [{"type": "upsert_stay", "target_id": "new-stay-1", "value": {"city": "Стамбул"}}]),
])
def test_explicit_typo_proposes_unique_curated_city_without_applying_it(text, field, candidate, country, model_ops):
    state = TripState()
    keep = {"type": "set_budget", "value": {"amount_minor": 9_000_000, "currency": "RUB"}}
    before = deepcopy(state.model_dump(mode="json"))
    operations, meta = ground_intent_operations(state, [keep, *model_ops], text)
    assert keep in operations
    assert not any(op.get("type") == ("set_origin" if field == "origin.city" else "upsert_stay")
        and op.get("value", {}).get("city") == candidate for op in operations)
    question = meta["pending_context"]
    assert question["field"] == field and question["proposal_field"] == field
    assert question["proposed_destination"] == {"kind": "city", "name": candidate, "country_code": country}
    assert question["confirmation_required"] is True
    assert any(candidate in item and "имеете в виду" in item for item in meta["unknowns"])
    assert state.model_dump(mode="json") == before


@pytest.mark.parametrize("text", [
    "Если из Мосвкы в Анталью поехать?", "Раньше из Мосвкы летал в Анталью.",
    "Не хочу в Стамбулл.", "«Из Мосвкы в Стамбулл» — это рекламная цитата.",
    "Живу в Мосвке, еду в Анталью.", "С пересадкой в Дубае без отеля.",
    "Планирую пересадку в Дубй, без проживания там.", "Из Казани в Турцию, город пока не выбрал.",
])
def test_quote_history_residence_transit_negation_and_country_do_not_become_typo_proposals(text):
    _, meta = ground_intent_operations(TripState(), [], text)
    assert not (meta.get("pending_context") or {}).get("confirmation_required")


def test_correct_registered_city_is_not_reported_as_a_typo():
    operations, meta = ground_intent_operations(TripState(), [], "Из Москвы в Анталью")
    assert any(op["type"] == "set_origin" and op["value"]["city"] == "Москва" for op in operations)
    assert any(op["type"] == "upsert_stay" and op["value"]["city"] == "Анталья" for op in operations)
    assert not any(item.get("code") == "geographic_typo_confirmation_required" for item in meta["notes"])
