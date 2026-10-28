"""Pure code-static guide compression; no Store, model or provider calls."""
import ast
import json
from copy import deepcopy
from pathlib import Path

import pytest

from otravel.groq_context import compact_operation_guide
from otravel.harness import OPERATION_GUIDE

CLAUSE_FIELDS = {
    "instruction", "duration_contract", "leg_mode_contract", "group_contract",
    "room_constraint_contract", "room_membership_contract", "selection_contract",
    "constraint_contract", "route_contract", "coverage_rules", "schema_authoritative",
}
SCHEMA_AUTHORITY = (
    "Точные типы операций, поля value и допустимые ID заданы обязательной JSON Schema в system; не придумывай другие."
)


def source_guide():
    guide = {key: deepcopy(value) for key, value in OPERATION_GUIDE.items()
        if key not in {"supported_operations", "room_shape", "known_geographic_cities"}}
    guide["schema_authoritative"] = SCHEMA_AUTHORITY
    return guide


def rich_context():
    return {
        "current_message": "Второй номер, ребёнок 8 лет, отель дешевле первого; бюджет 2 000 USD.",
        "trip": {"trip_id": "t1", "state_version": 3, "budget": {"amount_minor": 200000, "currency": "USD"},
            "travelers": [{"id": "p2", "type": "child", "age": 8}],
            "stays": [{"id": "s2", "city": "Стамбул", "rooms": [{"id": "r2", "traveler_ids": ["p2"]}]}]},
        "pending_question": {"field": "stay.rooms", "context": {"proposal": ["p2"], "state_version": 3}},
        "recent_messages": [{"role": "user", "text": "Раньше было 1 000 EUR"},
            {"role": "assistant", "text": "Уточните номер"}],
        "shown_plans": [{"run_id": "r-old", "plans": [{"plan_id": "p-plan-2", "state_version": 3,
            "total_minor": 9911, "currency": "EUR", "offers": [{"offer_id": "o1", "hotel_id": "h3",
                "details": {"rooms": [{"id": "r2"}], "fee_minor": 1234, "refundable": False}}]}]}],
        "current_selected_plan": {"plan_id": "p-plan-2", "state_version": 3,
            "hotels": [{"hotel_id": "h3", "stay_id": "s2", "city": "Стамбул"}]},
        "first_shown_plan": {"historical_only": True, "plan_id": "historical", "hotels": [{"hotel_id": "h1"}]},
        "reference_now_utc": "2026-10-27T08:22:21+00:00", "default_timezone": "Europe/Moscow",
        "operation_contract": source_guide(),
        "__response_schema": {"properties": {"target_id": {"enum": ["s2", "r2", None]}}},
        "unknown_top_field": {"operation_contract": {"instruction": OPERATION_GUIDE["instruction"]}},
    }


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def test_all_current_static_clauses_are_recognized_and_smaller():
    original = rich_context()
    changed, meta = compact_operation_guide(original)
    assert set(meta["changed_fields"]) == CLAUSE_FIELDS
    assert meta["unrecognized_fields"] == []
    assert meta["version"] == "groq_operation_guide_en_v1"
    assert len(encoded(changed)) < len(encoded(original))
    assert meta["saved_value_utf8_bytes"] == len(encoded(original)) - len(encoded(changed))
    for key in CLAUSE_FIELDS:
        assert len(encoded(changed["operation_contract"][key])) < len(encoded(original["operation_contract"][key]))


def test_input_and_all_fields_outside_guide_remain_deep_equal_and_independent():
    original = rich_context()
    snapshot = deepcopy(original)
    changed, _ = compact_operation_guide(original)
    assert original == snapshot
    for key in original:
        if key != "operation_contract":
            assert changed[key] == original[key]
            assert encoded(changed[key]) == encoded(original[key])
    changed["trip"]["stays"][0]["rooms"][0]["traveler_ids"].append("not-input")
    changed["pending_question"]["context"]["proposal"].append("not-input")
    changed["shown_plans"][0]["plans"][0]["offers"][0]["details"]["fee_minor"] = 0
    assert original == snapshot


def test_code_static_data_and_custom_guide_fields_stay_exact():
    original = rich_context()
    custom = {"instruction": "User-supplied custom text", "price_minor": 3456,
        "history": [OPERATION_GUIDE["instruction"]]}
    original["operation_contract"]["custom_addition"] = custom
    changed, _ = compact_operation_guide(original)
    for key in ("supported_cities", "known_hard_constraints", "do_not_invent", "custom_addition"):
        assert encoded(changed["operation_contract"][key]) == encoded(original["operation_contract"][key])
    assert changed["operation_contract"]["custom_addition"] == custom


@pytest.mark.parametrize("field", sorted(CLAUSE_FIELDS))
def test_changed_or_custom_known_clause_is_not_compressed(field):
    original = rich_context()
    value = original["operation_contract"][field]
    if isinstance(value, str):
        value += " Preserve this newly added requirement."
    elif isinstance(value, list):
        value.append("Preserve a new rule.")
    else:
        value["new_key"] = "Preserve this custom semantic clause."
    original["operation_contract"][field] = value
    changed, meta = compact_operation_guide(original)
    assert field in meta["unrecognized_fields"]
    assert field not in meta["changed_fields"]
    assert encoded(changed["operation_contract"][field]) == encoded(value)
    assert set(meta["changed_fields"]) == CLAUSE_FIELDS - {field}


@pytest.mark.parametrize("value", [None, [], "not a guide", 2, False])
def test_missing_or_non_object_guide_is_copied_without_change(value):
    original = {"operation_contract": value, "trip": {"id": "unchanged"}}
    changed, meta = compact_operation_guide(original)
    assert changed == original and changed is not original
    assert meta["changed_fields"] == [] and meta["saved_value_utf8_bytes"] == 0
    assert compact_operation_guide({})[0] == {}


def test_tuple_coverage_is_custom_not_equivalent_json_array():
    original = rich_context()
    original["operation_contract"]["coverage_rules"] = tuple(OPERATION_GUIDE["coverage_rules"])
    changed, meta = compact_operation_guide(original)
    assert type(changed["operation_contract"]["coverage_rules"]) is tuple
    assert "coverage_rules" in meta["unrecognized_fields"]


def test_dictionary_order_is_not_part_of_clause_identity():
    original = rich_context()
    for key in ("route_contract", "constraint_contract"):
        original["operation_contract"][key] = dict(reversed(list(original["operation_contract"][key].items())))
    changed, meta = compact_operation_guide(original)
    assert {"route_contract", "constraint_contract"} <= set(meta["changed_fields"])
    assert changed["operation_contract"]["route_contract"]["round_trip"] == "Return to origin."


def test_compression_is_idempotent_and_does_not_translate_arbitrary_same_text():
    original = rich_context()
    first, _ = compact_operation_guide(original)
    second, meta = compact_operation_guide(first)
    assert second == first
    assert meta["changed_fields"] == [] and meta["saved_value_utf8_bytes"] == 0
    assert first["unknown_top_field"] == original["unknown_top_field"]


def test_translation_retains_critical_semantics_and_currency_is_neutral():
    guide, _ = compact_operation_guide({"operation_contract": source_guide()})
    guide = guide["operation_contract"]
    instruction = guide["instruction"]
    for term in ("current_message", "server review", "not already applied", "entire batch", "Omission is not false",
        "traveler.id", "minor units", "sole operation", "cancel", "neither rolls back the trip nor cancels orders"):
        assert term in instruction
    assert "RUB" not in instruction and "копей" not in instruction
    for term in ("ONLY that room", "other rooms remain", "add_traveler first", "replaces ALL rooms", "its stay ID", "new IDs"):
        assert term in guide["room_membership_contract"]
    assert "empty keep_separate=true" in guide["group_contract"]
    assert "atomically" in guide["group_contract"]
    assert "room_id requires stay_id" in guide["room_constraint_contract"]
    assert "Days are not nights" in guide["duration_contract"]
    assert "technical stops" in guide["constraint_contract"]["nonstop"]
    assert "ALL flight legs" in guide["constraint_contract"]["nonstop"]
    assert "trip-wide, no stay_id/room_id" in guide["constraint_contract"]["nonstop"]
    assert set(guide["route_contract"]) == {"round_trip", "one_way", "open_jaw", "undecided", "transit"}
    for term in ("current preview selection", "no prices or purchase consent", "destination/sources", "replace_hotel",
        "first-display history", "not current selection", "cannot confirm an old price or booking"):
        assert term in guide["selection_contract"]
    assert "On an explicit request to keep" in guide["selection_contract"]
    assert "explicitly stated citizenship, ISO-2" in guide["coverage_rules"][-1]
    assert "language or destination" in guide["coverage_rules"][-1]
    assert "unknowns" in guide["coverage_rules"][-2]


def test_helper_has_no_harness_provider_store_import_cycle():
    path = Path(__file__).parents[1] / "otravel" / "groq_context.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not any(name and any(part in name for part in ("harness", "providers", "store")) for name in imports)
