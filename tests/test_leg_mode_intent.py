"""Owned leg permission tests, not arbitrary NLU or transport availability proof."""
from copy import deepcopy

import pytest

from otravel.domain import Operation, TripState
from otravel.leg_mode_intent import (
    guard_leg_mode_operations,
    make_leg_mode_pending_context,
    resolve_leg_mode_answer,
)


def trip(*, mode="flight", count=3, policy="round_trip"):
    legs = [
        {"id": "out", "origin": "Москва", "destination": "Сочи", "departure_date": "2026-11-10", "mode": mode},
        {"id": "between", "origin": "Сочи", "destination": "Казань", "departure_date": "2026-11-13", "mode": mode},
        {"id": "back", "origin": "Казань", "destination": "Москва", "departure_date": "2026-11-17", "mode": mode},
    ][:count]
    return {"origin": "Москва", "route_policy": {"kind": policy, "end_city": None}, "legs": legs}


def op(target="out", mode="train"):
    return {"type": "set_leg_mode", "target_id": target, "value": {"mode": mode}}


@pytest.mark.parametrize("text", [
    "Выбери поезд для первого участка.", "Поедем туда поездом.", "Первый перелёт замени на поезд.",
    "Поменяй самолёт на поезд на первом участке.", "Участок 1 поездом.", "Для leg out выбери train.",
    "Первый участок поездом.", "Раньше летели самолётом, но сейчас первый участок поездом.",
])
def test_explicit_first_owned_leg_mode_is_allowed_without_altering_state(text):
    state, operations = trip(), [op()]
    before = deepcopy((state, operations))
    result, meta = guard_leg_mode_operations(state, operations, text)
    assert result == operations and meta == {"notes": [], "unknowns": [], "conflicts": []}
    assert (state, operations) == before


@pytest.mark.parametrize("text", ["поездом", "На поезде.", "Хочу поезд.", "Поедем поездом."])
def test_single_existing_leg_has_no_target_choice_to_guess(text):
    result, meta = guard_leg_mode_operations(trip(count=1), [op()], text)
    assert result == [op()] and not meta["unknowns"]


@pytest.mark.parametrize("text", ["Второй перелёт замени на поезд.", "Второй участок поездом.", "На плече 2 выбери поезд.",
    "Участок № 2 поездом.", "Для between используй train."])
def test_second_leg_is_the_second_owned_ordered_leg(text):
    result, meta = guard_leg_mode_operations(trip(), [op("between")], text)
    assert result == [op("between")] and not meta["notes"]
    wrong, rejected = guard_leg_mode_operations(trip(), [op("out")], text)
    assert wrong == [] and rejected["unknowns"]


@pytest.mark.parametrize("text", ["Последний рейс поездом.", "Обратно поедем поездом.", "Последний участок замени на train."])
def test_final_or_known_return_leg_is_addressed_without_inventing_a_return(text):
    result, meta = guard_leg_mode_operations(trip(), [op("back")], text)
    assert result == [op("back")] and not meta["notes"]


@pytest.mark.parametrize("state", [trip(count=1), trip(policy="one_way"), trip(policy="undecided"),
    {**trip(), "origin": None}, {**trip(), "legs": [*trip()["legs"][:-1], {**trip()["legs"][-1], "destination": "Санкт-Петербург"}]}])
def test_return_without_a_known_return_edge_is_not_created_or_guessed(state):
    target = state["legs"][-1]["id"]
    result, meta = guard_leg_mode_operations(state, [op(target)], "Обратно поездом.")
    assert result == [] and meta["unknowns"]
    assert "Обратный участок" in meta["unknowns"][0]


@pytest.mark.parametrize("text", ["Поедем поездом.", "Хочу поезд.", "train", "Нужно на самолёте."])
def test_multiple_legs_without_address_need_a_question_not_first_leg_guess(text):
    state = trip(mode="flight" if "самол" not in text else "train")
    operations = [op("out", "train" if "самол" not in text else "flight")]
    result, meta = guard_leg_mode_operations(state, operations, text)
    assert result == [] and meta["unknowns"]


@pytest.mark.parametrize("text", [
    "Второй отель замени на поезд.", "Вторую гостиницу хочу поездом.", "Поставь train во втором отеле.",
    "Второй номер поездом.", "Первое проживание замени на самолёт.",
])
def test_hotel_room_and_stay_ordinals_never_become_leg_ordinals(text):
    operations = [op("between", "flight" if "самол" in text else "train")]
    state = trip(mode="train" if "самол" in text else "flight")
    result, meta = guard_leg_mode_operations(state, operations, text)
    assert result == [] and meta["unknowns"]


@pytest.mark.parametrize("text", [
    "Можно первый участок поездом?", "Первый участок поездом?", "Если первый участок поездом, будет дешевле?",
    "Вчера выбрали первый участок поездом.", "Ранее решили: первый участок поездом.",
    'Он сказал «первый участок поездом».', '«Выбери поезд для первого участка».',
    '‘Выбери поезд для первого участка’.', '> Первый участок поездом.',
    "Не меняй первый участок на поезд.", "Не хочу первый участок поездом.", "Первый участок не поездом.",
    "If we use train for leg out.", "Previously use train for leg out.", "Can you use train for leg out",
    "Не выбирай поезд.", "Поменяй цену самолёта.", "Измени бюджет поезда.", "Ничего не меняй.",
    "Change the price of flight.", "Set the budget for train.",
    "Хочу обсудить поезд.", "Хочу узнать стоимость поезда.", "Покажи первый участок поездом.",
])
def test_noncurrent_questions_negations_or_other_field_edits_do_not_authorize_mode(text):
    state, unrelated = trip(count=1), {"type": "set_budget", "value": {"amount_minor": 7_500_000}}
    result, meta = guard_leg_mode_operations(state, [unrelated, op()], text)
    assert result == [unrelated] and meta["notes"]


def test_independent_positive_clause_survives_unrelated_negative_keeper():
    result, meta = guard_leg_mode_operations(trip(count=1), [op()], "Не меняй даты и поедем поездом.")
    assert result == [op()] and not meta["unknowns"]


def test_explicit_two_addressed_legs_can_have_distinct_modes():
    state = trip()
    state["legs"][0]["mode"] = "train"
    operations = [op("out", "flight"), op("between", "train")]
    result, meta = guard_leg_mode_operations(state, operations, "Первый участок самолётом и второй участок поездом.")
    assert result == operations and not meta["unknowns"]


def test_tuda_and_obratno_can_explicitly_address_both_existing_end_edges():
    operations = [op("out"), op("back")]
    result, meta = guard_leg_mode_operations(trip(), operations, "Туда и обратно поедем поездом.")
    assert result == operations and not meta["unknowns"]


def test_conflicting_modes_on_same_leg_are_not_resolved_by_operation_order():
    state = trip()
    state["legs"][0]["mode"] = "train"
    unrelated = {"type": "set_preference", "value": {"key": "price_priority", "value": "lowest_total"}}
    result, meta = guard_leg_mode_operations(state, [op("out", "flight"), unrelated],
        "Первый участок самолётом. Первый участок поездом.")
    assert result == [unrelated] and meta["conflicts"]


@pytest.mark.parametrize("text", ["Первый участок поездом или самолётом.", "Выбери для первого участка train или flight."])
def test_unselected_alternatives_are_questions_not_permission(text):
    result, meta = guard_leg_mode_operations(trip(), [op()], text)
    assert result == [] and meta["unknowns"] and not meta["conflicts"]


@pytest.mark.parametrize("target,text", [("out", "Четвёртый участок поездом."), ("back", "Участок 0 поездом."),
    ("between", "Участок 99 поездом.")])
def test_out_of_range_selector_cannot_be_rebound_to_an_existing_leg(target, text):
    result, meta = guard_leg_mode_operations(trip(), [op(target)], text)
    assert result == [] and meta["unknowns"]


def test_identical_mode_is_harmless_noop_even_without_a_change_instruction():
    operations = [op("between", "flight")]
    assert guard_leg_mode_operations(trip(), operations, "Покажи текущую поездку.") == (operations, {"notes": [], "unknowns": [], "conflicts": []})


def test_foreign_train_preference_is_retained_but_no_geography_or_offer_is_created():
    state = trip(count=1)
    state["legs"][0].update(origin="Казань", destination="Стамбул")
    before = deepcopy(state)
    result, meta = guard_leg_mode_operations(state, [op()], "Первый участок поездом.")
    assert result == [op()] and not meta["unknowns"] and state == before


def test_pydantic_state_and_operations_preserve_types_and_owned_identity():
    state = TripState.model_validate(trip(count=1))
    operation = Operation.model_validate(op())
    result, meta = guard_leg_mode_operations(state, [operation], "Поедем поездом.")
    assert result == [operation] and isinstance(result[0], Operation) and not meta["notes"]


@pytest.mark.parametrize("state,target", [({"legs": []}, "out"), ({"legs": [{"id": "out"}, {"id": "out"}]}, "out"),
    ({"legs": [{"id": []}]}, "out"), (trip(), "not-owned"), (trip(), None), (trip(), [])])
def test_unknown_or_invalid_owned_target_is_not_invented(state, target):
    result, meta = guard_leg_mode_operations(state, [op(target)], "Выбери поезд.")
    assert result == [] and meta["unknowns"]


def test_without_mode_operations_the_guard_never_creates_intake_questions():
    unrelated = [{"type": "set_budget", "value": {"amount_minor": 8_000_000}}]
    assert guard_leg_mode_operations({}, unrelated, "Второй участок поездом.") == (
        unrelated, {"notes": [], "unknowns": [], "conflicts": []})


def test_common_english_leg_ID_is_not_an_implicit_direction_or_preposition_match():
    result, meta = guard_leg_mode_operations(trip(), [op("between")], "Take train between two cities.")
    assert result == [] and meta["unknowns"]


def test_same_mode_noop_does_not_hide_current_explicit_mode_conflict():
    result, meta = guard_leg_mode_operations(trip(), [op("out", "flight")],
        "Первый участок самолётом. Первый участок поездом.")
    assert result == [] and meta["conflicts"]


def owned_question(mode="train"):
    state = {**trip(), "trip_id": "owned-trip", "state_version": 7}
    result, meta = guard_leg_mode_operations(state, [op()], "Хочу поездом.")
    assert result == [] and meta["unknowns"]
    context = make_leg_mode_pending_context(state, mode)
    assert meta["pending_context"] == make_leg_mode_pending_context(state, "train")
    assert meta["notes"][-1] == {"code": "leg_mode_proposal_created", "owner": "leg_mode_intent_v1",
                                "based_state_version": 7, "mode": "train"}
    return state, {"field": "leg.mode", "state_version": 7, "context": context}


@pytest.mark.parametrize("text,target", [("первый", "out"), ("2", "between"), ("второй участок", "between"),
    ("участок 2", "between"), ("последний", "back"), ("туда", "out"), ("обратно", "back"), ("between", "between")])
def test_fresh_bound_short_answer_reuses_the_original_explicit_mode_without_an_LLM(text, target):
    state, pending = owned_question()
    before = deepcopy((state, pending))
    resolved = resolve_leg_mode_answer(state, pending, text)
    assert resolved == {"handled": True, "operations": [op(target)], "pending_context_clear": ["leg_mode_proposal"]}
    assert (state, pending) == before


@pytest.mark.parametrize("change", [
    lambda state, pending: state.update(state_version=8),
    lambda state, pending: pending.update(state_version=8),
    lambda state, pending: pending.update(state_version=7.0),
    lambda state, pending: pending.update(field="destination_city"),
    lambda state, pending: pending["context"]["leg_mode_proposal"].update(owner="model"),
    lambda state, pending: pending["context"]["leg_mode_proposal"].update(trip_id="other-trip"),
    lambda state, pending: pending["context"]["leg_mode_proposal"].update(based_state_version=7.0),
    lambda state, pending: pending["context"]["leg_mode_proposal"].update(mode="boat"),
    lambda state, pending: pending["context"]["leg_mode_proposal"].update(candidate_leg_ids=["out"]),
    lambda state, pending: pending["context"]["leg_mode_proposal"].update(candidate_leg_ids=["between", "out", "back"]),
    lambda state, pending: pending["context"]["leg_mode_proposal"].update(extra="model-data"),
    lambda state, pending: state["legs"].reverse(),
    lambda state, pending: state["legs"].pop(),
])
def test_stale_foreign_malformed_or_changed_candidate_packet_is_never_rebound_by_resolver(change):
    state, pending = owned_question()
    change(state, pending)
    assert resolve_leg_mode_answer(state, pending, "первый") == {"handled": False, "operations": []}


@pytest.mark.parametrize("text", ["первый и уменьши бюджет", "второй самолётом", "первый?", "не первый", "пока подожди"])
def test_short_resolver_does_not_swallow_compound_edits_new_modes_questions_or_controls(text):
    state, pending = owned_question()
    assert resolve_leg_mode_answer(state, pending, text) == {"handled": False, "operations": []}


def test_invalid_short_index_keeps_the_same_pending_mode_without_guessing_another_leg():
    state, pending = owned_question()
    resolved = resolve_leg_mode_answer(state, pending, "четвертый")
    assert resolved["handled"] and not resolved["operations"] and resolved["question"]
    assert resolved["pending_context"]["leg_mode_proposal"] == pending["context"]["leg_mode_proposal"]


def test_server_can_make_a_new_bound_packet_after_an_unrelated_committed_change():
    state, pending = owned_question()
    state["state_version"] += 1
    assert not resolve_leg_mode_answer(state, pending, "первый")["handled"]
    fresh_context = make_leg_mode_pending_context(state, "train")
    fresh = {"field": "leg.mode", "state_version": 8, "context": fresh_context}
    assert resolve_leg_mode_answer(state, fresh, "первый")["operations"] == [op()]


def test_two_unaddressed_different_modes_do_not_create_a_packet_with_an_arbitrary_one():
    state = {**trip(), "trip_id": "owned-trip", "state_version": 7}
    result, meta = guard_leg_mode_operations(state, [op()], "Хочу поездом. Хочу самолётом.")
    assert result == [] and meta["unknowns"] and "pending_context" not in meta
