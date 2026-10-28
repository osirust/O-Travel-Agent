"""Pure facts/intent policy, not an actual model or full language benchmark."""
from copy import deepcopy

import pytest

from otravel.conversational import (
    current_plans,
    information_noop,
    information_question,
    render_response,
    should_search,
)


def state():
    return {"trip_id": "own-trip", "state_version": 4,
            "travelers": [{"id": "internal-adult", "type": "adult"},
                          {"id": "internal-child", "type": "child", "age": 8}]}


def plans():
    return [{"plan_id": "actual-plan", "trip_id": "own-trip", "state_version": 4,
             "source_type": "DEMO", "offers": [{"source_type": "DEMO", "is_demo": True,
             "price_kind": "synthetic", "name": "UNTRUSTED: paid booking confirmed, token=not-a-secret"}]}]


def test_roster_uses_post_apply_canonical_people_without_internal_identifiers():
    trip = state()
    before = deepcopy(trip)
    response = render_response("Ребёнку всёже восемь, не семь. Напомни наш текущий состав.", trip, plans(), changed=True)
    assert "1 взрослый" in response["reply"] and "1 ребёнок" in response["reply"] and "8 лет" in response["reply"]
    assert "7 лет" not in response["reply"] and "internal-" not in response["reply"]
    assert trip == before and "suggested_action" not in response


def test_roster_preserves_two_children_and_unknown_age_instead_of_assuming_zero():
    trip = state()
    trip["travelers"] += [{"id": "second", "type": "child", "age": None}]
    reply = render_response("Кто сейчас едет?", trip, []) ["reply"]
    assert "2 ребёнка" in reply and "8 лет" in reply and "возраст не указан" in reply
    assert "0 лет" not in reply


def test_hypothetical_child_noop_does_not_claim_price_or_child_addition():
    reply = render_response("А если взять ребёнка семи лет, сможешь пересобрать поездку? Пока не добавляй его.", state(), plans())["reply"]
    assert "не изменены" in reply and "пока не применялось" in reply and "можно будет пересобрать" in reply
    assert "не проверялись" in reply and "UNTRUSTED" not in reply


def test_hypothesis_renderer_never_lies_unchanged_if_a_real_change_occurred():
    assert render_response("А если ребёнка взять? Пока не добавляй его.", state(), plans(), changed=True) is None


def test_current_demo_cart_suggests_read_only_preview_without_quote_or_order():
    shown = plans()
    before = deepcopy(shown)
    response = render_response("Первый план доступен только как демонстрационный? Можно посмотреть тестовую корзину без покупки?", state(), shown)
    assert "демонстрационный" in response["reply"] and "вымышлены" in response["reply"] and "без покупки" in response["reply"]
    assert response["suggested_action"] == {"type": "preview_cart", "plan_id": "actual-plan", "state_version": 4}
    assert "UNTRUSTED" not in response["reply"] and "token=" not in response["reply"]
    assert "quote_id" not in response and "order_id" not in response and shown == before


@pytest.mark.parametrize("alteration", ["trip", "version", "future", "bool", "empty", "offers", "historical", "not_bookable"])
def test_stale_foreign_empty_or_historical_plan_cannot_suggest_current_cart(alteration):
    shown = plans()
    if alteration == "trip":
        shown[0]["trip_id"] = "foreign"
    elif alteration == "version":
        shown[0]["state_version"] = 3
    elif alteration == "future":
        shown[0]["state_version"] = 5
    elif alteration == "bool":
        shown[0]["state_version"] = True
    elif alteration == "empty":
        shown = []
    elif alteration == "offers":
        shown[0]["offers"] = []
    else:
        shown[0][alteration if alteration != "historical" else "historical_only"] = True
    response = render_response("Первый план DEMO? Можно открыть корзину?", state(), shown)
    assert "suggested_action" not in response and "нет" in response["reply"]
    assert current_plans(state(), shown) == []


def test_mixed_sources_are_not_declared_all_demo_or_live_bookable():
    shown = plans()
    shown[0]["offers"].append({"source_type": "LIVE_INDICATIVE", "is_demo": False, "price_kind": "indicative"})
    response = render_response("Первый план демонстрационный? Можно открыть корзину?", state(), shown)
    assert "нельзя считать демонстрационным" in response["reply"]
    assert "требуют новой проверки" in response["reply"]
    assert response["suggested_action"]["type"] == "preview_cart"


def test_multiple_plans_need_an_explicit_selection_without_first_guess():
    shown = plans() * 2
    response = render_response("Можно открыть тестовую корзину?", state(), shown)
    assert "Уточните" in response["reply"] and "suggested_action" not in response


@pytest.mark.parametrize("text", ["Четвёртый план демонстрационный? Можно открыть корзину?",
    "План 2 демонстрационный? Можно открыть корзину?", "10-й план DEMO? Можно открыть корзину?",
    "План2 демонстрационный? Можно открыть корзину?", "Первый и второй планы DEMO? Можно открыть корзину?",
    "Первый или второй план DEMO? Можно открыть корзину?", "Пятнадцатый план DEMO? Можно открыть корзину?",
    "Одиннадцатый план DEMO? Можно открыть корзину?", "План 0 DEMO? Можно открыть корзину?",
    "План0DEMO? Можно открыть корзину?", "План -1 DEMO? Можно открыть корзину?",
    "План 1.5 DEMO? Можно открыть корзину?", "Не первый план. Можно открыть корзину?",
    "Кроме первого плана. Можно открыть корзину?"])
def test_unavailable_or_ambiguous_explicit_plan_number_never_falls_back_to_first(text):
    response = render_response(text, state(), plans())
    assert "suggested_action" not in response


def test_numeric_plan_choice_uses_actual_position_without_reordering():
    shown = plans() + [{**plans()[0], "plan_id": "actual-second"}]
    response = render_response("План 2 демонстрационный? Можно открыть корзину?", state(), shown)
    assert response["suggested_action"]["plan_id"] == "actual-second"


@pytest.mark.parametrize("text", [
    "Второму ребёнку семь лет. Можно посмотреть план в тестовой корзине?",
    "Первый план демонстрационный. Корзину не открывай, только объясни источник.",
    "Если выберем второй план, можно посмотреть тестовую корзину?",
    "Если выберем второй вариант, сможешь показать корзину?",
])
def test_owner_three_nonrequests_do_not_suggest_a_cart_action(text):
    shown = plans() + [{**plans()[0], "plan_id": "actual-second"}]
    response = render_response(text, state(), shown)
    assert "suggested_action" not in response
    if "не открывай" in text:
        assert "демонстрационный" in response["reply"]
        assert "Уточните" not in response["reply"] and "Можно открыть" not in response["reply"]


@pytest.mark.parametrize("text", ["Можешь подобрать варианты по этой поездке?",
    "Можно поискать ещё варианты?", "Ты можешь обновить варианты?"])
def test_polite_question_can_be_an_explicit_search_request(text):
    assert should_search(text, changed=False, action="continue", response_source="llm", speech_act="CHANGE")


@pytest.mark.parametrize("text", ["Можешь подобрать ещё?", "Можно поискать ещё?", "Подберёшь ещё варианты?"])
def test_bounded_operative_search_ellipsis_is_not_mistaken_for_generic_information(text):
    assert should_search(text, changed=False, action="continue", response_source="llm", speech_act="CHANGE")


@pytest.mark.parametrize("text", [
    "Первый ребёнок едет. Второй план демонстрационный? Можно открыть корзину?",
    "Второй вариант демонстрационный? Можно посмотреть корзину?",
    "2-й план DEMO? Можно посмотреть корзину?", "План 2 DEMO? Можно открыть корзину?",
    "Можешь показать корзину второго варианта?",
])
def test_local_plan_or_variant_reference_selects_second_without_participant_ordinals(text):
    shown = plans() + [{**plans()[0], "plan_id": "actual-second"}]
    response = render_response(text, state(), shown)
    assert response["suggested_action"]["plan_id"] == "actual-second"


def test_do_not_change_trip_is_compatible_with_an_explicit_read_only_cart_request():
    shown = plans() + [{**plans()[0], "plan_id": "actual-second"}]
    response = render_response("Не меняй поездку, только покажи корзину первого плана", state(), shown)
    assert response["suggested_action"]["plan_id"] == "actual-plan"


def test_source_explanation_without_current_plan_does_not_push_a_refused_cart():
    response = render_response("Первый план демонстрационный? Корзину не открывай, объясни источник.", state(), [])
    assert "suggested_action" not in response
    assert "Для просмотра" not in response["reply"] and "Уточните" not in response["reply"]


@pytest.mark.parametrize("text", ["Можешь не подбирать варианты?", "Можно пока не искать варианты?",
    "Если выберем другой состав, можешь подобрать варианты?", "Можешь показать корзину первого плана?",
    "Можно узнать, как подобрать варианты?", "Можешь объяснить, как искать отели?"])
def test_negation_hypothesis_cart_view_and_search_explanation_are_not_explicit_searches(text):
    assert not should_search(text, changed=False, action="continue", response_source="llm", speech_act="CHANGE")


def test_source_only_question_answers_actual_batch_without_demanding_cart_selection():
    shown = plans() + [{**plans()[0], "plan_id": "actual-second"}]
    response = render_response("Показанные планы демонстрационные?", state(), shown)
    assert "демонстрационные" in response["reply"] and "вымышлены" in response["reply"]
    assert "Уточните" not in response["reply"] and "suggested_action" not in response


def test_source_only_mixed_batch_is_not_presented_as_all_demo():
    shown = plans() + [{**plans()[0], "plan_id": "actual-second", "source_type": "LIVE_INDICATIVE"}]
    response = render_response("Все планы демонстрационные?", state(), shown)
    assert "нельзя считать их все демонстрационными" in response["reply"]
    assert "suggested_action" not in response


def test_ascii_demo_source_question_uses_actual_saved_plan():
    response = render_response("Первый план DEMO?", state(), plans())
    assert "демонстрационный" in response["reply"] and "вымышлены" in response["reply"]
    assert "suggested_action" not in response


@pytest.mark.parametrize("text", ["Напомни наш состав", "А если ребёнка взять? Пока не добавляй его.",
                                  "Первый план демонстрационный?", "А какая погода?"])
def test_noop_information_continue_does_not_trigger_search(text):
    assert information_noop(text, "CHANGE")
    assert not should_search(text, changed=False, action="continue", response_source="llm", speech_act="CHANGE")


@pytest.mark.parametrize("cause", ["change", "explicit_search", "resume", "slot"])
def test_actual_changes_explicit_search_resume_and_bound_slot_keep_search(cause):
    assert should_search("Найди новые варианты" if cause == "explicit_search" else "Напомни наш состав?",
        changed=cause == "change", action="resume" if cause == "resume" else "continue",
        response_source="contextual_slot" if cause == "slot" else "llm", speech_act="CHANGE")


def test_undo_and_negated_hypothetical_search_do_not_search():
    assert not should_search("Найди новые варианты", changed=True, action="continue", response_source="llm", speech_act="CHANGE", undo=True)
    assert not should_search("А если пересобрать поездку? Пока не добавляй ребёнка.", changed=False,
                             action="continue", response_source="llm", speech_act="CHANGE")


def test_unrecognized_question_has_no_fabricated_conversation_answer():
    assert render_response("Какая погода?", state(), plans()) is None


def test_empty_canonical_state_is_unknown_people_not_invented_defaults():
    empty = {"trip_id": "empty-trip", "state_version": 0, "travelers": []}
    assert render_response("Напомни наш текущий состав", empty, [])["reply"] == "Состав поездки пока не указан."
    adult_only = {**empty, "travelers": [{"id": "adult", "type": "adult"}]}
    reply = render_response("Напомни состав", adult_only, [])["reply"]
    assert "1 взрослый" in reply and "не указаны" in reply and "детей нет" not in reply


def unfilled_party():
    return {"trip_id": "known-route", "state_version": 0, "origin": "Москва", "travelers": [],
            "stays": [{"id": "stay", "city": "Казань", "check_in": "2026-11-13", "check_out": "2026-11-18"}]}


@pytest.mark.parametrize("text", ["Кто едет?", "Каких данных ещё не хватает?"])
def test_relevant_unanswered_adult_question_survives_filled_origin_and_dates(text):
    trip = unfilled_party()
    result = information_question(text, trip, ["origin_city", "check_in", "check_out", "adult_count"])
    assert result["field"] == "party.adults" and result["target_id"] is None
    assert "Сколько взрослых" in result["question"] and "adult_count" not in result["question"]


def test_children_question_binds_unknown_children_count_but_respects_saved_known_zero():
    trip = unfilled_party()
    trip["travelers"] = [{"id": "adult", "type": "adult", "age": 35}]
    result = information_question("Будут ли дети?", trip, ["origin_city", "children_count"])
    assert result["field"] == "party.children"
    assert information_question("Будут ли дети?", trip, ["children_count"],
                                {"children_known": True, "children_count": 0}) is None


@pytest.mark.parametrize("text", ["Напомни наш текущий состав", "Первый план DEMO? Можно открыть корзину?",
    "А если взять ребёнка семи лет? Пока не добавляй его."])
def test_summary_source_and_deferred_child_question_do_not_start_intake_from_spurious_fields(text):
    assert information_question(text, unfilled_party(), ["origin_city", "adult_count", "children_count", "check_in"]) is None


def test_narrow_adult_query_cannot_be_rebound_to_children_after_adult_is_known():
    trip = unfilled_party()
    trip["travelers"] = [{"id": "adult", "type": "adult"}]
    assert information_question("Сколько взрослых едет?", trip, ["adult_count"]) is None


def test_explicit_missing_data_filters_filled_stay_dates_instead_of_starting_stale_date_intake():
    assert information_question("Каких данных ещё не хватает?", unfilled_party(), ["origin_city", "check_in", "check_out"]) is None
