"""Reference FX planning/quote economics; no HTTP, account or database access."""
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from test_planning import provider_offers

from otravel.domain import sample_trip
from otravel.fx import convert_minor, parse_cbr_xml
from otravel.planning import assemble_plan, compare_plans, validate_quote

NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)


def snapshot(eur="100", usd="80", now=NOW):
    raw = (f'<ValCurs Date="26.10.2026"><Valute><CharCode>EUR</CharCode><Nominal>1</Nominal><Value>{eur}</Value></Valute>'
           f'<Valute><CharCode>USD</CharCode><Nominal>1</Nominal><Value>{usd}</Value></Valute></ValCurs>').encode()
    return parse_cbr_xml(raw, now)


def basket(target="RUB"):
    state = sample_trip()
    offers = provider_offers(state)
    for offer in offers:
        offer["amount_minor"] = 1000
        offer["retrieved_at"] = NOW.isoformat()
        offer["expires_at"] = (NOW + timedelta(hours=12)).isoformat()
    foreign = next(offer for offer in offers if offer["details"]["kind"] == "flight")
    foreign.update(amount_minor=12345, currency="EUR", provider="duffel", source_type="SANDBOX")
    state.budget.currency = target
    return state, offers, foreign


def codes(plan):
    return {issue["code"] for issue in plan["blockers"]}


def quote_codes(plan, state, **kwargs):
    return {issue["code"] for issue in validate_quote(plan, state, **kwargs)}


def test_EUR12345_native_is_byte_exact_and_projects_RUB1234500_at_rate100():
    state, offers, native = basket()
    original = json.dumps(offers, ensure_ascii=False, sort_keys=True)
    fx = snapshot()
    plan = assemble_plan(state, offers, fx_snapshot=fx, now=NOW)
    assert not plan["blockers"] and plan["ready_for_simulated_checkout"]
    assert json.dumps(offers, ensure_ascii=False, sort_keys=True) == original
    assert json.dumps(plan["offers"], ensure_ascii=False, sort_keys=True) == original
    component = next(c for c in plan["components"] if c["component_id"] == native["offer_id"])
    assert component["amount_minor"] == 12345 and component["currency"] == "EUR"
    assert component["budget_amount_minor"] == 1_234_500 and component["budget_currency"] == "RUB"
    assert plan["currency_totals"] == {"EUR": 12345, "RUB": 6000}
    assert plan["total_minor"] == plan["known_subtotal_minor"] == 1_240_500
    assert sum(c["budget_amount_minor"] for c in plan["components"]) == plan["total_minor"]
    assert plan["fx_snapshot"] == fx and plan["fx_snapshot_id"] == fx["snapshot_id"]
    assert plan["fx_reference_only"] is True and plan["fx_fees_unknown"] is True
    assert plan["source_type"] == "MIXED" and component["source_type"] == "SANDBOX"
    assert not plan["ready_for_real_booking"] and not plan["booking_created"]
    assert any("банковские комиссии" in warning and "26" in warning for warning in plan["warnings"])


def test_pinned_snapshot_rebuild_sameFX_valid_different_rate_requires_new_quote_and_consent():
    state, offers, _ = basket()
    first_fx, second_fx = snapshot(), snapshot(eur="101")
    before = assemble_plan(state, offers, now=NOW, fx_snapshot=first_fx)
    assert validate_quote(before, state, now=NOW, current_offers=offers) == []
    assert validate_quote(before, state, now=NOW, fx_snapshot=first_fx) == []
    after = assemble_plan(state, offers, now=NOW, fx_snapshot=second_fx, previous_plan=before)
    assert after["offers"] == before["offers"]
    assert after["currency_totals"] == before["currency_totals"]
    assert after["plan_id"] != before["plan_id"] and after["snapshot_hash"] != before["snapshot_hash"]
    assert "fx_snapshot_changed" in quote_codes(before, state, now=NOW, fx_snapshot=second_fx)
    delta = compare_plans(before, after)
    assert delta == after["comparison"] and delta["requires_new_confirmation"]
    assert delta["delta_minor"] == 12345 and delta["changes"]
    changed = delta["changes"][0]
    assert changed["before"]["amount_minor"] == changed["after"]["amount_minor"]
    assert changed["before"]["budget_amount_minor"] != changed["after"]["budget_amount_minor"]
    assert validate_quote(after, state, now=NOW, fx_snapshot=second_fx) == []


@pytest.mark.parametrize("kind,expected", [("absent", "fx_required"), ("invalid", "fx_invalid_snapshot"),
    ("expired", "fx_snapshot_expired"), ("future", "fx_future_snapshot"), ("no_clock", "fx_invalid_clock")])
def test_unusable_FX_keeps_native_offers_but_blocks_final_total(kind, expected):
    state, offers, _ = basket()
    fx, now = snapshot(), NOW
    if kind == "absent": fx = None
    elif kind == "invalid": fx = {"rates_rub_per_unit": {"EUR": "100"}}
    elif kind == "expired": now = NOW + timedelta(hours=6)
    elif kind == "future": fx = snapshot(now=NOW + timedelta(minutes=1))
    elif kind == "no_clock": now = None
    before = deepcopy(offers)
    plan = assemble_plan(state, offers, fx_snapshot=fx, now=now)
    assert expected in codes(plan)
    assert plan["offers"] == offers == before
    assert plan["total_minor"] is None and not plan["ready_for_simulated_checkout"]
    assert plan["currency_totals"] == {"EUR": 12345, "RUB": 6000}
    assert "fx_snapshot" not in plan and "fx_snapshot_id" not in plan
    foreign = next(c for c in plan["components"] if c["currency"] == "EUR")
    assert foreign["budget_amount_minor"] is None


def test_pinned_FX_expiry_blocks_quote_without_restoring_old_economics():
    state, offers, _ = basket()
    plan = assemble_plan(state, offers, now=NOW, fx_snapshot=snapshot())
    frozen = deepcopy(plan)
    rejected = quote_codes(plan, state, now=NOW + timedelta(hours=6))
    assert {"fx_snapshot_expired", "quote_snapshot_changed"}.issubset(rejected)
    assert plan == frozen


@pytest.mark.parametrize("field", ["projection_amount", "projection_currency", "native_amount", "native_currency",
    "fx_id", "fx_rate", "fx_source_hash", "fx_removed", "known_subtotal", "currency_totals", "reference_flag", "fee_flag"])
def test_fx_quote_economic_metadata_tampering_is_rejected(field):
    state, offers, _ = basket()
    plan = json.loads(json.dumps(assemble_plan(state, offers, now=NOW, fx_snapshot=snapshot())))
    if field == "projection_amount": plan["components"][0]["budget_amount_minor"] += 1
    elif field == "projection_currency": plan["components"][0]["budget_currency"] = "USD"
    elif field == "native_amount": plan["offers"][0]["amount_minor"] += 1
    elif field == "native_currency": plan["offers"][0]["currency"] = "USD"
    elif field == "fx_id": plan["fx_snapshot_id"] = "cbr-" + "0" * 64
    elif field == "fx_rate": plan["fx_snapshot"]["source_quotes"]["EUR"]["value_rub"] = "99"
    elif field == "fx_source_hash": plan["fx_snapshot"]["raw_source_sha256"] = "0" * 64
    elif field == "fx_removed": plan.pop("fx_snapshot")
    elif field == "known_subtotal": plan["known_subtotal_minor"] += 1
    elif field == "currency_totals": plan["currency_totals"]["EUR"] += 1
    elif field == "reference_flag": plan["fx_reference_only"] = False
    elif field == "fee_flag": plan["fx_fees_unknown"] = False
    assert "quote_snapshot_changed" in quote_codes(plan, state, now=NOW)


def test_FX_does_not_hide_provider_fee_or_inventory_unknowns():
    state, offers, _ = basket()
    offers[0]["details"].pop("fees_included")
    plan = assemble_plan(state, offers, now=NOW, fx_snapshot=snapshot())
    assert "mandatory_fees_unknown" in codes(plan)
    assert plan["total_minor"] is None and not plan["ready_for_simulated_checkout"]
    assert plan["fx_snapshot"]["fees_unknown"] is True


def test_budget_validation_uses_converted_projection_and_keeps_native_amount():
    state, offers, foreign = basket()
    state.budget.amount_minor = 1_000_000
    plan = assemble_plan(state, offers, now=NOW, fx_snapshot=snapshot())
    assert "budget_exceeded" in codes(plan) and not plan["ready_for_simulated_checkout"]
    assert plan["total_minor"] == 1_240_500
    assert next(o for o in plan["offers"] if o["offer_id"] == foreign["offer_id"])["amount_minor"] == 12345


def test_cross_target_USD_is_supported_without_relabeling_EUR_and_RUB_components():
    state, offers, foreign = basket(target="USD")
    fx = snapshot()
    plan = assemble_plan(state, offers, now=NOW, fx_snapshot=fx)
    assert not plan["blockers"] and plan["ready_for_simulated_checkout"]
    assert plan["currency"] == "USD"
    assert plan["total_minor"] == 15_431 + 6 * 13
    assert plan["total_minor"] == sum(convert_minor(o["amount_minor"], o["currency"], "USD", fx, NOW) for o in offers)
    assert plan["currency_totals"] == {"EUR": 12345, "RUB": 6000}
    assert {c["currency"] for c in plan["components"]} == {"EUR", "RUB"}
    assert {c["budget_currency"] for c in plan["components"]} == {"USD"}
    assert next(o for o in plan["offers"] if o["offer_id"] == foreign["offer_id"])["currency"] == "EUR"
    assert validate_quote(plan, state, now=NOW) == []


def test_round_each_component_projection_once_then_sum_not_aggregate_native_currency():
    state, offers, _ = basket()
    for offer in offers: offer.update(amount_minor=0, currency="RUB")
    for offer in offers[:2]: offer.update(amount_minor=1, currency="EUR")
    plan = assemble_plan(state, offers, now=NOW, fx_snapshot=snapshot(eur="1.5"))
    assert plan["currency_totals"] == {"EUR": 2, "RUB": 0}
    assert [c["budget_amount_minor"] for c in plan["components"][:2]] == [2, 2]
    assert plan["total_minor"] == 4  # Rounding2 separate1.5minor components, not aggregate3.
    assert validate_quote(plan, state, now=NOW) == []


def test_single_common_currency_noFX_retains_identity_totals_and_ignores_unneeded_snapshot():
    state, offers, _ = basket()
    for offer in offers: offer["currency"] = "RUB"
    baseline = assemble_plan(state, offers, now=NOW)
    assert not baseline["blockers"]
    assert baseline["total_minor"] == sum(o["amount_minor"] for o in offers)
    assert "fx_snapshot_id" not in baseline and "fx_snapshot" not in baseline
    assert baseline == assemble_plan(state, offers, now=NOW, fx_snapshot={"invalid_unused": True})
    assert baseline == assemble_plan(state, offers, now=NOW, fx_snapshot=snapshot())
    assert all(c["budget_amount_minor"] == c["amount_minor"] and c["budget_currency"] == c["currency"] for c in baseline["components"])
    assert validate_quote(baseline, state, now=NOW) == []


def test_single_common_foreign_currency_needs_no_FX_and_keeps_legacy_scope():
    state, offers, _ = basket(target="EUR")
    for offer in offers: offer["currency"] = "EUR"
    plan = assemble_plan(state, offers, now=NOW)
    assert not plan["blockers"] and plan["total_minor"] == sum(o["amount_minor"] for o in offers)
    assert plan["currency"] == "EUR" and "fx_snapshot" not in plan
    assert validate_quote(plan, state, now=NOW) == []


def test_unknown_currency_is_not_invented_by_reference_conversion():
    state, offers, foreign = basket()
    foreign["currency"] = "GBP"
    plan = assemble_plan(state, offers, now=NOW, fx_snapshot=snapshot())
    assert "fx_unsupported_currency" in codes(plan)
    assert plan["total_minor"] is None and plan["offers"] == offers


def test_source_reprice_native_change_is_still_independently_rejected_with_same_FX():
    state, offers, _ = basket()
    fx = snapshot()
    plan = assemble_plan(state, offers, now=NOW, fx_snapshot=fx)
    repriced = deepcopy(offers)
    repriced[0]["amount_minor"] += 1
    assert "source_snapshot_changed" in quote_codes(plan, state, now=NOW, current_offers=repriced, fx_snapshot=fx)


def test_reference_FX_mixed_fallback_keeps_actual_SANDBOX_and_synthetic_DEMO_distinct():
    state, _, foreign = basket()
    original = deepcopy(foreign)
    plan = assemble_plan(state, [foreign], demo_fallback=True, now=NOW, fx_snapshot=snapshot())
    assert not plan["blockers"] and plan["ready_for_simulated_checkout"]
    assert plan["source_type"] == "MIXED"
    assert {c["source_type"] for c in plan["components"]} == {"SANDBOX", "DEMO"}
    assert next(o for o in plan["offers"] if o["offer_id"] == foreign["offer_id"]) == original == foreign
    assert plan["currency_totals"]["EUR"] == 12345
    assert plan["total_minor"] == 1_234_500 + plan["currency_totals"]["RUB"]
    assert all(c["amount_minor"] == c["budget_amount_minor"] for c in plan["components"] if c["currency"] == "RUB")
    assert not plan["ready_for_real_booking"] and not plan["booking_created"]
    assert validate_quote(plan, state, now=NOW) == []


@pytest.mark.parametrize("value", [True, 1.0, float("nan")])
def test_quote_projection_is_strict_minor_integer_not_python_numeric_equality(value):
    state, offers, foreign = basket()
    for offer in offers: offer["amount_minor"] = 0
    foreign["amount_minor"] = 1
    plan = json.loads(json.dumps(assemble_plan(state, offers, now=NOW, fx_snapshot=snapshot(eur="1"))))
    component = next(c for c in plan["components"] if c["component_id"] == foreign["offer_id"])
    assert component["budget_amount_minor"] == plan["total_minor"] == 1
    component["budget_amount_minor"] = value
    assert "quote_snapshot_changed" in quote_codes(plan, state, now=NOW)
