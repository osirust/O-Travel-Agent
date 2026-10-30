"""Current mode literals and two exact CF r2 cached failures; zero HTTP."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from otravel.domain import Operation, TripState, apply_operations
from otravel.search_policy_intent import explicit_search_mode_request, guard_search_policy_operations

PRIOR = {"search_policy": {"mode": "HYBRID", "hotel_provider": "serpapi", "flight_provider": "duffel",
    "demo_fallback": False, "guest_nationality": "TR"}}
ADULT = {"type": "add_traveler", "value": {"type": "adult"}}
CHILD = {"type": "add_traveler", "value": {"type": "child", "age": 7}}


def policy(**fields):
    return {"type": "set_search_policy", "value": fields}


def literal_note(meta):
    return [note for note in meta["notes"] if note["code"] == "search_policy_literal_grounded"]


@pytest.mark.parametrize("text,mode", [
    ("Включи деморежим.", "DEMO"),
    ("Включи демо-режим.", "DEMO"),
    ("Включи демо режим.", "DEMO"),
    ("Пожалуйста, включи режим DEMO.", "DEMO"),
    ("Переключи поиск в DEMO.", "DEMO"),
    ("Хочу режим DEMO.", "DEMO"),
    ("Переключи режим на HYBRID.", "HYBRID"),
    ("Включи гибридный режим.", "HYBRID"),
    ("Включи гибридныйрежим.", "HYBRID"),
    ("Используй гибрид.", "HYBRID"),
    ("Switch search mode to HYBRID.", "HYBRID"),
    ("Enable DEMO.", "DEMO"),
    ('Он написал «включи HYBRID», а сейчас включи деморежим.', "DEMO"),
    ("Вчера использовали HYBRID, но теперь включи DEMO.", "DEMO"),
])
def test_missing_model_mode_is_grounded_only_from_one_current_literal(text, mode):
    prior = deepcopy(PRIOR)
    prior["search_policy"]["mode"] = "HYBRID" if mode == "DEMO" else "DEMO"
    original = [ADULT]
    saved = deepcopy(original)
    state_saved = deepcopy(prior)
    actual, meta = guard_search_policy_operations(prior, original, text)
    assert actual == [ADULT, policy(mode=mode)]
    assert original == saved and prior == state_saved
    assert explicit_search_mode_request(text) == mode
    assert literal_note(meta)[0]["mode"] == mode
    assert literal_note(meta)[0]["operation_added"] is True
    assert not meta["unknowns"] and not meta["conflicts"]
    assert set(actual[-1]["value"]) == {"mode"}


@pytest.mark.parametrize("text", [
    "Как включить DEMO?", "Включи DEMO?", "Это деморежим?", "Это тестовые данные?",
    "Если включи деморежим, что изменится?", "Если включи DEMO, будет дешевле.",
    "Предположим включи деморежим.", "Например включи DEMO.", "Вчера просил включи деморежим.",
    "Ранее говорил включи DEMO.", 'Он сказал «включи деморежим».', '"Включи DEMO".',
    "> Включи деморежим\nПокажи состав.", "Не включай деморежим.", "Не включи DEMO.",
    "Не используй DEMO.", "Не хочу режим DEMO.", "If enable DEMO, prices would change.",
    "Yesterday enable DEMO.", "Do not enable DEMO.", "Don't switch to DEMO.",
    "Включи DEMO fallback.", "Включи демо-фолбэк.", "Включи fallback DEMO.",
    "Включи деморежим не сейчас.", "Включи DEMO пока не нужно.",
    "Напиши инструкцию: включи DEMO.", "Я спрашивал включи DEMO.",
    "Можно включи деморежим", "Как включи DEMO", "Фраза: включи DEMO.",
    "Можно только тестовую корзину без покупки.", "Сравни DEMO и HYBRID.",
    "Деморежим.", "DEMO", "Покажи демопример.",
])
def test_noncommand_or_fallback_does_not_create_mode_or_permission(text):
    assert explicit_search_mode_request(text) is None
    actual, meta = guard_search_policy_operations(PRIOR, [ADULT, policy(mode="DEMO")], text)
    assert actual == [ADULT]
    assert not literal_note(meta)


@pytest.mark.parametrize("text", [
    "Включи DEMO и HYBRID.", "Включи DEMO или HYBRID.", "Включи DEMO/HYBRID.",
    "Включи деморежим либо гибридный режим.", "Включи DEMO. Переключи на HYBRID.",
    "Включи DEMO, переключи на HYBRID.", "Enable DEMO or HYBRID.",
])
def test_conflicting_mode_literals_remove_even_identical_model_mode_and_ask(text):
    assert explicit_search_mode_request(text) is None
    for mode in ("DEMO", "HYBRID"):
        actual, meta = guard_search_policy_operations(PRIOR, [ADULT, policy(mode=mode)], text)
        assert actual == [ADULT]
        assert len(meta["unknowns"]) == 1 and "DEMO" in meta["unknowns"][0] and "HYBRID" in meta["unknowns"][0]
        assert not literal_note(meta)
    actual, meta = guard_search_policy_operations(PRIOR, [CHILD], text)
    assert actual == [CHILD] and meta["unknowns"]


@pytest.mark.parametrize("text,batch_conflict", [
    ("Включи DEMO или HYBRID. Добавь ребёнка семи лет.", False),
    ("Включи DEMO. Включи HYBRID. Добавь ребёнка семи лет.", True),
    ("Включи DEMO, затем переключи на HYBRID. Добавь ребёнка семи лет.", False),
])
def test_choices_keep_independent_facts_but_simultaneous_opposing_commands_block_batch(text, batch_conflict):
    actual, meta = guard_search_policy_operations(PRIOR, [CHILD, policy(mode="DEMO")], text)
    assert actual == [CHILD] and meta["unknowns"]
    assert bool(meta["conflicts"]) is batch_conflict
    if batch_conflict:
        assert meta["conflicts"] == meta["unknowns"]


def test_wrong_or_duplicate_model_mode_cannot_override_current_literal_or_generate_defaults():
    original = [policy(mode="HYBRID", hotel_provider="liteapi", demo_fallback=True), CHILD,
        policy(mode="DEMO", flight_provider="serpapi")]
    saved = deepcopy(original)
    actual, meta = guard_search_policy_operations(PRIOR, original, "Включи деморежим. Добавь ребёнка 7 лет.")
    assert actual == [policy(mode="DEMO"), CHILD]
    assert original == saved
    assert literal_note(meta)[0]["model_mode_corrected"] is True
    assert not literal_note(meta)[0]["operation_added"]


def test_model_objects_remain_objects_and_identical_requested_mode_stays_noop_with_literal_note():
    command = Operation.model_validate(policy(mode="HYBRID"))
    actual, meta = guard_search_policy_operations(PRIOR, [command], "Включи DEMO.")
    assert isinstance(actual[0], Operation) and actual[0].value == {"mode": "DEMO"}
    assert command.value == {"mode": "HYBRID"}
    actual, meta = guard_search_policy_operations(PRIOR, [command], "Включи HYBRID.")
    assert actual == [] and literal_note(meta)[0]["mode"] == "HYBRID"


def test_compound_mode_child_and_wait_preserve_nonpolicy_operations_and_control():
    extraction = {"operations": [CHILD], "control": {"action": "wait"}}
    before = deepcopy(extraction)
    actual, meta = guard_search_policy_operations(PRIOR, extraction["operations"],
        "Включи деморежим, добавь ребёнка 7 лет и подожди, я ещё дополню.")
    assert actual == [CHILD, policy(mode="DEMO")]
    assert extraction == before and extraction["control"]["action"] == "wait"
    assert literal_note(meta)


def test_mode_change_chain_keeps_existing_suppliers_fallback_nationality_and_other_facts():
    state = TripState(trip_id="source-chain", search_policy=PRIOR["search_policy"])
    texts = ["Включи деморежим.", "Это тестовые данные?", "Включи HYBRID.",
        'Он сказал «включи DEMO».', "Включи DEMO fallback.", "Включи DEMO."]
    expected_modes = ["DEMO", "DEMO", "HYBRID", "HYBRID", "HYBRID", "DEMO"]
    for text, expected in zip(texts, expected_modes, strict=True):
        actual, _ = guard_search_policy_operations(state, [], text)
        state, _ = apply_operations(state, [Operation.model_validate(op) for op in actual])
        assert state.search_policy.model_dump() == {**PRIOR["search_policy"], "mode": expected}
        assert state.trip_id == "source-chain" and state.travelers == [] and state.stays == []


CACHED = json.loads((Path(__file__).parent / "fixtures/demo-mode-cf-r2-20261027.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CACHED, ids=[case["id"] for case in CACHED])
@pytest.mark.parametrize("model_omitted_mode", [False, True], ids=["exact_actual_contract", "omitted_mode_boundary"])
def test_exact_cached_cf_r2_failures_and_missing_mode_variant(case, model_omitted_mode):
    assert case["historical_mode_after"] == "HYBRID"
    raw = deepcopy(case["raw_contract"])
    # Evidence matters: in both actual failures the model emitted DEMO, then
    # the historical guard suppressed it. Omission is a separate boundary.
    assert raw["operations"][0]["type"] == "set_search_policy"
    assert raw["operations"][0]["value"]["mode"] == "DEMO"
    if model_omitted_mode:
        raw["operations"] = raw["operations"][1:]
    original = deepcopy(raw)
    guarded, meta = guard_search_policy_operations(case["before_trip"], raw["operations"], case["text"])
    assert raw == original
    assert [op["value"]["mode"] for op in guarded if op["type"] == "set_search_policy" and "mode" in op["value"]] == ["DEMO"]
    assert [op for op in guarded if op["type"] != "set_search_policy"] == [op for op in raw["operations"] if op["type"] != "set_search_policy"]
    state = TripState.model_validate(case["before_trip"])
    updated, _ = apply_operations(state, [Operation.model_validate(op) for op in guarded])
    assert updated.search_policy.model_dump() == {**state.search_policy.model_dump(), "mode": "DEMO"}
    assert literal_note(meta)[0]["finding_id"] == "HARNESS-004-DEMO-COMMAND-IGNORED-001"
    assert raw["control"] == original["control"] and not meta["unknowns"]
