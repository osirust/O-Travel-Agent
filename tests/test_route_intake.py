"""Bound route grounding with literal oracles, no model/network/DB."""

from copy import deepcopy

from jsonschema import Draft202012Validator
import pytest

from otravel.domain import Operation, TripState, apply_operations
from otravel.llm_contract import build_operation_schema
from otravel.route_intake import ground_initial_route


OWNER_TEXT = "Один взрослый из Москвы в Казань, детей пока не будет. Хочу сначала собрать простой вариант поездки."
ORIGIN = {"type": "set_origin", "value": {"city": "Москва"}}
ADULT = {"type": "add_traveler", "value": {"id": "new-traveler-1", "type": "adult"}}
STAY = {"type": "upsert_stay", "target_id": "new-stay-1", "value": {"city": "Казань"}}


def test_owner_initial_route_keeps_original_operations_and_supplies_only_missing_city():
    state, operations = TripState(), [deepcopy(ORIGIN), deepcopy(ADULT)]
    before_state, before_ops = state.model_dump(mode="json"), deepcopy(operations)
    result, notes = ground_initial_route(state, operations, OWNER_TEXT)
    assert result == [*before_ops, STAY]
    assert notes == [{"field": "destination.city", "code": "initial_route_destination_grounded",
        "source": "explicit_initial_route", "before": None, "after": "Казань", "target_id": "new-stay-1"}]
    assert operations == before_ops and state.model_dump(mode="json") == before_state
    result[0]["value"]["city"] = "Сочи"
    assert operations == before_ops


def test_city_without_dates_or_rooms_is_an_allowed_draft_operation():
    state = TripState()
    result, _ = ground_initial_route(state, [ORIGIN, ADULT], OWNER_TEXT)
    envelope = {"operations": result, "control": {"action": "none"}, "reply": "Уточним даты.",
        "unknowns": [], "conflicts": [], "speech_act": "CHANGE", "information_subject": None}
    Draft202012Validator(build_operation_schema(state)).validate(envelope)
    changed, _ = apply_operations(state, result)
    assert changed.origin == "Москва" and changed.stays[0].city == "Казань"
    assert changed.stays[0].check_in is None and changed.stays[0].check_out is None
    assert changed.stays[0].rooms == [] and changed.legs == []
    assert len(changed.travelers) == 1 and changed.travelers[0].type == "adult"


@pytest.mark.parametrize("text", [
    "Сколько стоит поездка из Москвы в Казань",
    "Как доехать из Москвы в Казань",
    "Можно поехать из Москвы в Казань",
    "Какой маршрут из Москвы в Казань",
    "Из Москвы в Казань — сколько стоит",
])
def test_question_without_question_mark_does_not_initiate_route_grounding(text):
    operations = [deepcopy(ORIGIN), deepcopy(ADULT)]
    assert ground_initial_route(TripState(), operations, text) == (operations, [])


@pytest.mark.parametrize("text", [
    "Я ехал из Москвы в Казань",
    "Мы ехали из Москвы в Казань",
    "Я путешествовал из Москвы в Казань",
    "Только авиабилеты из Москвы в Казань",
    "Только перелёт из Москвы в Казань",
    "Из Москвы в Казань без гостиницы",
    "Из Москвы в Казань без отеля",
])
def test_past_journey_and_explicit_hotel_exclusion_do_not_add_a_stay(text):
    operations = [deepcopy(ORIGIN), deepcopy(ADULT)]
    assert ground_initial_route(TripState(), operations, text) == (operations, [])


def test_explicit_transport_operation_is_not_augmented_with_hotel_intake():
    operations = [deepcopy(ORIGIN), {'type': 'upsert_leg', 'target_id': 'new-leg-1',
                                   'value': {'origin': 'Москва', 'destination': 'Казань'}}]
    assert ground_initial_route(TripState(), operations, 'Из Москвы в Казань') == (operations, [])


def test_grounding_does_not_exceed_the_operation_envelope_limit():
    operations = [deepcopy(ORIGIN)] + [
        {'type': 'set_preference', 'value': {'key': f'preference-{index}', 'value': 'yes'}}
        for index in range(29)]
    assert ground_initial_route(TripState(), operations, 'Из Москвы в Казань') == (operations, [])


@pytest.mark.parametrize("text,origin,destination", [
    ("из Москвы в Казань", "Москва", "Казань"),
    ("из Москвы в Казани", "Москва", "Казань"),
    ("Из Казани в Москву", "Казань", "Москва"),
    ("из Санкт Петербурга в Казань", "Санкт-Петербург", "Казань"),
    ("из Санкт-Петербурга в Казань", "Санкт-Петербург", "Казань"),
    ("из СПб в Казань", "Санкт-Петербург", "Казань"),
    ("из Москвы в Санкт Петербург", "Москва", "Санкт-Петербург"),
    ("из Москвы в Санкт–Петербург", "Москва", "Санкт-Петербург"),
    ("из Москвы в Белград", "Москва", "Белград"),
    ("из Москвы в Нови Сад", "Москва", "Нови-Сад"),
    ("из Moscow в Kazan", "Москва", "Казань"),
])
def test_literal_registered_city_forms_normalize_without_country_inference(text, origin, destination):
    state = TripState(origin=origin)
    result, notes = ground_initial_route(state, [], text)
    assert result == [{**STAY, "value": {"city": destination}}]
    assert notes[0]["after"] == destination


@pytest.mark.parametrize("text", [
    "Из Москвы в Казань, детей пока не будет.",
    "Детей не будет, один взрослый из Москвы в Казань.",
    "Без детей из Москвы в Казань.",
    "Из Москвы в Казань без детей.",
    "Из Москвы в Казань. Детей нет, взрослых двое.",
])
def test_negation_of_children_does_not_negate_the_explicit_route(text):
    result, notes = ground_initial_route(TripState(origin="Москва"), [], text)
    assert result == [STAY] and notes


@pytest.mark.parametrize("text", [
    "Из Москвы в Казань?", "А если из Москвы в Казань", "If из Москвы в Казань",
    "Я бы поехал из Москвы в Казань", "Представь из Москвы в Казань",
    "Например, из Москвы в Казань", "Допустим, из Москвы в Казань", "Возможно из Москвы в Казань",
    "Я хотел бы из Москвы в Казань", "Раньше из Москвы в Казань", "Вчера из Москвы в Казань",
    "Была поездка из Москвы в Казань", "Прошлый маршрут из Москвы в Казань", "Обсуждали из Москвы в Казань",
    "Старый маршрут из Москвы в Казань", "Он сказал: «из Москвы в Казань»",
    "Из Москвы в Казань или Сочи", "Из Москвы в Казань либо Дубай", "Из Москвы в Казань через Сочи",
    "Из Москвы в Казань, затем Стамбул", "Из Москвы в Казань и обратно", "Из Москвы в Казань → Сочи",
    "Из Москвы в Казань, из Сочи в Стамбул", "Из Москвы в Казань и в Стамбул",
    "Не еду из Москвы в Казань", "Не хочу из Москвы в Казань", "Не надо из Москвы в Казань",
    "Не добавляй маршрут из Москвы в Казань", "Не из Москвы в Казань", "Из Москвы в Казань не еду",
    "Без поездки из Москвы в Казань", "Маршрут из Москвы в Казань отменяется",
    "Из Москвы в Казань не планируется", "Не строй маршрут из Москвы в Казань", "Из Москвы в Казань — нет",
    "Из Москвы не в Казань", "Из Москвы в Казань, а из Марса в Венеру",
    "Из России в Казань", "Из Москвы в Сербию", "Из Москвы в Georgia", "Из Марса в Казань",
    "Из Москвы в Казан", "Из Москвы в Казаньскую область", "Из Москвы в Казань-на-Волге", "В Казань из Москвы",
])
def test_history_questions_alternatives_negated_unknown_or_unbounded_routes_are_untouched(text):
    operations = [deepcopy(ORIGIN), deepcopy(ADULT)]
    result, notes = ground_initial_route(TripState(), operations, text)
    assert result == operations and notes == []


@pytest.mark.parametrize("state,operations", [
    (TripState(), []),
    (TripState(origin="Сочи"), []),
    (TripState(origin="Сочи"), [ORIGIN]),
    (TripState(origin="Москва"), [{"type": "set_origin", "value": {"city": "Сочи"}}]),
    (TripState(), [ORIGIN, {"type": "set_origin", "value": {"city": "Сочи"}}]),
    (TripState(), [{"type": "set_origin", "value": {"city": "Россия"}}]),
    (TripState(), [{"type": "set_origin", "value": {"city": "Москва", "origin": "Сочи"}}]),
])
def test_origin_must_already_agree_without_overwriting_model_or_saved_fact(state, operations):
    result, notes = ground_initial_route(state, operations, OWNER_TEXT)
    assert result == operations and notes == []


def test_existing_origin_and_matching_model_operation_are_both_preserved():
    operations = [ORIGIN, {"type": "set_budget", "value": {"amount_minor": 7_500_000}}]
    result, notes = ground_initial_route(TripState(origin="Москва", state_version=4), operations, OWNER_TEXT)
    assert result[:-1] == operations and result[-1] == STAY and notes


@pytest.mark.parametrize("operation", [
    {"type": "upsert_stay", "target_id": "new-stay-2", "value": {"city": "Сочи"}},
    {"type": "upsert_stay", "target_id": "new-stay-1", "value": {}},
    {"type": "remove_stay", "target_id": "old-stay", "value": {}},
    {"type": "replace_hotel", "target_id": "old-stay", "value": {"hotel_id": "a"}},
    {"type": "shift_return", "value": {"days": 2}},
    {"type": "undo", "value": {}},
])
def test_existing_route_operation_is_not_replaced_or_completed(operation):
    operations = [ORIGIN, operation]
    result, notes = ground_initial_route(TripState(), operations, OWNER_TEXT)
    assert result == operations and notes == []


@pytest.mark.parametrize("state", [
    TripState(origin="Москва", stays=[{"id": "s1", "city": "Казань"}]),
    TripState(origin="Москва", stays=[{"id": "s1"}]),
    TripState(origin="Москва", legs=[{"id": "l1", "origin": "Москва", "destination": "Сочи"}]),
    TripState(origin="Москва", travelers=[{"id": "new-stay-1", "type": "adult"}]),
])
def test_existing_stays_transport_or_colliding_identity_block_initial_grounding(state):
    result, notes = ground_initial_route(state, [ORIGIN], OWNER_TEXT)
    assert result == [ORIGIN] and notes == []


def test_typed_operations_and_mapping_state_are_supported_without_mutation():
    state = TripState(origin="Москва").model_dump(mode="json")
    operations = [Operation.model_validate(ADULT)]
    before = deepcopy(state)
    result, notes = ground_initial_route(state, operations, OWNER_TEXT)
    assert result[0] == operations[0] and result[0] is not operations[0]
    assert result[-1] == STAY and notes
    assert state == before


@pytest.mark.parametrize("text", [None, 4, "", "из Москвы в Казань" + "x" * 20_001])
def test_nontext_and_oversize_inputs_are_not_grounded(text):
    assert ground_initial_route(TripState(origin="Москва"), [], text) == ([], [])
