"""No model/DB: omission guard never chooses a traveller or replays history."""
from copy import deepcopy

import pytest

from otravel.domain import Operation, TripState, apply_operations, sample_trip, validate_trip
from otravel.group_semantics import (
    guard_group_separation,
    guard_unapplied_traveler_removal,
    protect_group_separation,
)
from otravel.inventory import DemoInventory


@pytest.mark.parametrize("text", [
    "Один взрослый из второго номера не поедет. Там остаётся один взрослый и ребёнок девяти лет. Первый номер и его семью не трогай.",
    "Взрослый больше не едет с нами.", "Мама не поедет в поездку.",
    "Один из взрослых не поедет.", "Второй взрослый не едет.",
    "Удали этого взрослого из самой поездки, не только из номера.",
    "Исключи одного взрослого из состава поездки.",
])
def test_explicit_removal_without_typed_operation_is_precise_ask(text):
    state = sample_trip()
    ops = [{"type": "upsert_stay", "target_id": state.stays[0].id, "value": {"rooms": [{"id": "r1", "traveler_ids": ["adult-1"]}]}}]
    before_state, before_ops = state.model_dump(mode="json"), deepcopy(ops)
    result = guard_unapplied_traveler_removal(state, ops, text)
    assert result["handled"] and result["field"] == "party.removal" and result["target_id"] is None
    assert "Какого взрослого" in result["question"] and "operations" not in result
    assert state.model_dump(mode="json") == before_state and ops == before_ops


@pytest.mark.parametrize("text", [
    "Если один взрослый не поедет, сколько будет стоить?", "Один взрослый не поедет?",
    "Если не поедет взрослый, пока никого не удаляй.",
    "Возможно, один взрослый не поедет.", "Раньше я писал: один взрослый не поедет.",
    "Взрослый не едет утром, только вечером.", "Взрослый не едет поездом.",
    "Убери одного взрослого из второго номера, он будет жить отдельно.",
    "Один взрослый не живёт во втором номере.", "Мама поедет, взрослого из номера перенеси.",
    "Никого не удаляй, мама не поедет — это только пример.",
    "Скажи, взрослый не едет.", "Например, мама не поедет.",
    "Взрослый не поедет в Анталью, поедет в Стамбул.",
    "Бюджет сократи на двадцать тысяч.", "",
])
def test_unresolved_hypothesis_history_services_and_room_only_move_are_not_removal(text):
    assert guard_unapplied_traveler_removal(sample_trip(), [], text) == {"handled": False}


@pytest.mark.parametrize("typed", [False, True])
def test_existing_remove_operation_is_not_duplicated_or_guessed(typed):
    raw = {"type": "remove_traveler", "target_id": "adult-3", "value": {}}
    ops = [Operation.model_validate(raw) if typed else raw]
    assert guard_unapplied_traveler_removal(sample_trip(), ops, "Один взрослый из второго номера не поедет.") == {"handled": False}


def initial_party_ops():
    return [{"type": "set_origin", "value": {"city": "Москва"}},
        *[{"type": "add_traveler", "value": {"id": f"p{i}", "type": "adult"}} for i in range(4)],
        {"type": "set_budget", "value": {"amount_minor": 28_000_000}},
        {"type": "upsert_stay", "target_id": "s1", "value": {"city": "Казань", "check_in": "2026-12-04", "check_out": "2026-12-10",
            "rooms": [{"id": "r1", "traveler_ids": []}, {"id": "r2", "traveler_ids": []}]}}]


@pytest.mark.parametrize("text", ["Нас две семьи, семьи не смешивать.", "Нас 2 семьи, не смешивай семьи.",
    "Две семьи размещай отдельно.", "Мы из двух семей, семьи раздельно."])
def test_no_mix_missing_groups_preserves_intake_and_blocks_search_without_partition_guess(text):
    state, ops = TripState(), initial_party_ops()
    before = deepcopy(ops)
    result = guard_group_separation(state, ops, text)
    assert result["handled"] and result["block_batch"] is False
    assert result["field"] == "group.membership" and result["target_id"] == "new-group-1"
    assert [o.model_dump(mode="json") for o in result["operations"][:len(ops)]] == [Operation.model_validate(o).model_dump(mode="json") for o in ops]
    assert ops == before and state.travelers == []
    saved, _ = apply_operations(state, result["operations"])
    assert saved.origin == "Москва" and saved.budget.amount_minor == 28_000_000 and len(saved.travelers) == 4
    assert len(saved.groups) == 2 and all(g.keep_separate and not g.traveler_ids for g in saved.groups)
    assert all(p.group_id is None for p in saved.travelers)
    assert [i["code"] for i in validate_trip(saved)].count("missing_group_membership") == 2
    assert not DemoInventory().search_trip(saved)["plans"]


def test_no_mix_known_memberships_set_true_without_duplicate_false_setter_or_side_effects():
    state = sample_trip()
    for group in state.groups:
        group.keep_separate = False
    ops = [{"type": "upsert_group", "target_id": "family-1", "value": {"traveler_ids": state.groups[0].traveler_ids, "keep_separate": False}}]
    before = state.model_dump(mode="json")
    result = guard_group_separation(state, ops, "Семьи не смешивай.")
    assert result["handled"] and "question" not in result
    assert len(result["operations"]) == 2
    saved, _ = apply_operations(state, result["operations"])
    assert all(g.keep_separate for g in saved.groups)
    assert [g.traveler_ids for g in saved.groups] == [g.traveler_ids for g in state.groups]
    assert state.model_dump(mode="json") == before and ops[0]["value"]["keep_separate"] is False
    again = guard_group_separation(saved, [], "Семьи не смешивай.")
    repeated, changes = apply_operations(saved, again["operations"])
    assert repeated == saved and changes == []


@pytest.mark.parametrize("answer", ["2", "две", "две семьи"])
def test_unknown_count_pending_survives_and_only_bound_answer_materializes_groups(answer):
    first = guard_group_separation(TripState(), initial_party_ops(), "Семьи не смешивай.")
    assert first["handled"] and first["block_batch"] is False and first["context"]["membership_unknown"]
    assert all(op.type != "upsert_group" for op in first["operations"])
    saved, _ = apply_operations(TripState(), first["operations"])
    second = guard_group_separation(saved, [], answer, first["context"])
    assert second["handled"] and second["target_id"] == "new-group-1"
    final, _ = apply_operations(saved, second["operations"])
    assert len(final.groups) == 2 and not DemoInventory().search_trip(final)["plans"]
    assert not guard_group_separation(saved, [], answer)["handled"]


@pytest.mark.parametrize("text", ["Если поедут две семьи, семьи не смешивай.", "Две семьи, семьи не смешивать?",
    "Раньше я просил: две семьи, семьи не смешивать.", "Семьи можно смешивать.",
    "Две семьи, семьи не смешивай, но можно смешивать.", "Две семьи отдельно не надо.",
    "Две семьи, номера не обязательно раздельно.", "Четыре человека, два номера.",
    "Не смешивай чемоданы.", "Две или три семьи, семьи не смешивать."])
def test_no_mix_negatives_hypotheses_conflicts_and_unrelated_text_leave_ops_unchanged(text):
    ops = initial_party_ops()
    result = guard_group_separation(TripState(), ops, text)
    assert result["handled"] is False
    assert result["operations"] == [Operation.model_validate(o) for o in ops]


def test_no_mix_count_conflict_does_not_remove_old_group_or_members():
    state = sample_trip()
    result = guard_group_separation(state, [], "Одна семья, семьи не смешивай.")
    assert not result["handled"] and not result["operations"]
    third, _ = apply_operations(state, [{"type": "upsert_group", "target_id": "third", "value": {"traveler_ids": [], "keep_separate": False}}])
    result = guard_group_separation(third, [], "Две семьи, семьи не смешивай.")
    assert result["handled"] and result["block_batch"] and not result["operations"]


def test_missing_one_group_never_fills_unknown_members_from_the_roster():
    state = sample_trip()
    state.groups = state.groups[:1]
    for person in state.travelers:
        if person.group_id == "family-2":
            person.group_id = None
    result = guard_group_separation(state, [], "Нас две семьи, не смешивай семьи.")
    final, _ = apply_operations(state, result["operations"])
    assert len(final.groups) == 2 and not final.groups[1].traveler_ids
    assert [p.group_id for p in final.travelers] == [p.group_id for p in state.travelers]


def test_pending_unknown_count_keeps_unrelated_intake_edit_and_question():
    first = guard_group_separation(TripState(), initial_party_ops(), "Семьи не смешивай, никого не удаляй.")
    saved, _ = apply_operations(TripState(), first["operations"])
    change = [{"type": "set_budget", "value": {"amount_minor": 20_000_000}}]
    second = guard_group_separation(saved, change, "Бюджет двадцать миллионов копеек.", first["context"])
    assert second["handled"] and second["context"]["family_separation_required"]
    assert second["operations"] == [Operation.model_validate(o) for o in change]
    assert "Сколько" in second["question"] and "upsert_group" not in [o.type for o in second["operations"]]


@pytest.mark.parametrize("ops", [
    [{"type": "upsert_group", "target_id": "g1", "value": {"traveler_ids": ["adult-1"], "keep_separate": False}},
     {"type": "upsert_group", "target_id": "g1", "value": {"traveler_ids": ["adult-2"], "keep_separate": True}}],
    [{"type": "upsert_group", "target_id": "g1", "value": {"traveler_ids": ["adult-1"], "keep_separate": False}},
     {"type": "upsert_group", "target_id": "g2", "value": {"traveler_ids": ["adult-1"], "keep_separate": True}}],
])
def test_no_mix_grounding_does_not_erase_conflicting_model_group_writes(ops):
    result = guard_group_separation(sample_trip(), ops, "Семьи не смешивай.")
    assert result["handled"] is False and result["operations"] == [Operation.model_validate(o) for o in ops]


def test_group_grounding_respects_existing_operation_engineering_cap():
    ops = [{"type": "set_budget", "value": {"amount_minor": 100_000}} for _ in range(30)]
    result = guard_group_separation(TripState(), ops, "Две семьи, семьи не смешивай.")
    assert result["handled"] and result["block_batch"] and len(result["operations"]) == 30
    assert result["notes"][0]["code"] == "group_grounding_operation_cap"


@pytest.mark.parametrize("text,other", [
    ("Ребёнку теперь восемь лет.", {"type": "update_traveler", "target_id": "child-2", "value": {"age": 8}}),
    ("Общий бюджет 700000 рублей.", {"type": "set_budget", "value": {"amount_minor": 70_000_000}}),
    ("Уточняю состав первой семьи.", None),
    ("Какой сейчас бюджет?", None),
])
def test_saved_no_mix_survives_unsolicited_false_setter_during_other_edits(text, other):
    state = sample_trip()
    ids = list(state.groups[0].traveler_ids)
    ops = [{"type": "upsert_group", "target_id": "family-1", "value": {"traveler_ids": ids, "keep_separate": False}}]
    if other:
        ops.append(other)
    before = deepcopy(ops)
    result = guard_group_separation(state, ops, text)
    assert result["handled"] and "question" not in result
    saved, _ = apply_operations(state, result["operations"])
    assert saved.groups[0].keep_separate is True and saved.groups[0].traveler_ids == ids
    assert len(saved.groups) == len(state.groups) and ops == before
    if other and other["type"] == "set_budget":
        assert saved.budget.amount_minor == 70_000_000


@pytest.mark.parametrize("text", ["Семьи можно смешивать.", "Разрешаю смешивать семьи.",
    "Семьи раздельно не надо."])
def test_explicit_asserted_allow_mix_allows_false_but_faq_or_hypothesis_does_not(text):
    state = sample_trip()
    op = {"type": "upsert_group", "target_id": "family-1", "value": {"traveler_ids": state.groups[0].traveler_ids, "keep_separate": False}}
    result = guard_group_separation(state, [op], text)
    saved, _ = apply_operations(state, result["operations"])
    assert saved.groups[0].keep_separate is False
    for phrase in ("Можно ли смешивать семьи?", "Если семьи можно смешивать."):
        protected = guard_group_separation(state, [op], phrase)
        unchanged, _ = apply_operations(state, protected["operations"])
        assert unchanged.groups[0].keep_separate is True


@pytest.mark.parametrize("text", [
    "Нас две семьи, семьи не смешивай. Какие даты нужны?",
    "Нас две семьи, семьи не смешивай; Какой бюджет нужен?",
])
def test_asserted_separation_before_information_question_is_not_swallowed(text):
    result = guard_group_separation(TripState(), initial_party_ops(), text)
    assert result["handled"] and result["target_id"] == "new-group-1"
    saved, _ = apply_operations(TripState(), result["operations"])
    assert len(saved.groups) == 2 and all(g.keep_separate and not g.traveler_ids for g in saved.groups)


@pytest.mark.parametrize("text", ["Можно ли не смешивать семьи?", "Какие даты нужны? Семьи не смешивать?",
    "Если нас две семьи, семьи не смешивай. Какие даты нужны?"])
def test_pure_or_hypothetical_no_mix_question_remains_noop(text):
    result = guard_group_separation(TripState(), [], text)
    assert not result["handled"] and not result["operations"]


def test_explicit_adult_removal_before_information_question_still_asks_identity():
    result = guard_unapplied_traveler_removal(sample_trip(), [], "Один взрослый из второго номера не поедет. Какие даты у поездки?")
    assert result["handled"] and result["field"] == "party.removal"


def test_flag_protection_preserves_proposed_membership_and_only_exact_previous_true_group():
    state = sample_trip()
    state.groups[1].keep_separate = False
    first_members = [p for p in state.groups[0].traveler_ids if p != "adult-3"]
    ops = [{"type": "upsert_group", "target_id": "family-1", "value": {"traveler_ids": first_members, "keep_separate": False}},
           {"type": "upsert_group", "target_id": "family-2", "value": {"traveler_ids": state.groups[1].traveler_ids, "keep_separate": False}}]
    result = protect_group_separation(state, ops, "В первой семье остаются эти участники.")
    saved, _ = apply_operations(state, result["operations"])
    assert saved.groups[0].traveler_ids == first_members and saved.groups[0].keep_separate is True
    assert saved.groups[1].keep_separate is False and len(saved.groups) == 2
    assert next(p for p in saved.travelers if p.id == "adult-3").group_id is None
    assert result["notes"][0]["group_ids"] == ["family-1"]


def test_contradictory_mix_permission_does_not_drop_existing_policy():
    state = sample_trip()
    ops = [{"type": "upsert_group", "target_id": "family-1", "value": {"traveler_ids": state.groups[0].traveler_ids, "keep_separate": False}}]
    result = protect_group_separation(state, ops, "Семьи не смешивай. Но можно смешивать.")
    saved, _ = apply_operations(state, result["operations"])
    assert saved.groups[0].keep_separate is True
