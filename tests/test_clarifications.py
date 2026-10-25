"""HARNESS-004: typed slot guards, not general language understanding evidence."""

from copy import deepcopy

import pytest

from otravel.clarifications import derive_pending_field, human_question, resolve_short_answer
from otravel.domain import CITIES, TripState, apply_operations
from otravel.geography import parse_destination


def state(**values):
    return TripState(trip_id="clarification-trip", **values)


def country_pending():
    return {"field": "destination.city", "requested_destination": parse_destination("Сербия")["requested_destination"]}


@pytest.mark.parametrize("raw,phrase", [
    ("destination.city", "В какой город"), ("destination_city", "В какой город"),
    ("origin_city", "Из какого города"), ("origin.city", "Из какого города"),
    ("дата вылета", "На какую дату"), ("departure_date", "На какую дату"),
    ("check_in", "заезд"), ("check_out", "выезд"), ("missing_rooms", "номеров"),
    ("party", "Сколько взрослых"), ("party.adults", "Сколько взрослых"),
    ("party.children", "дети"), ("child_age", "Сколько лет"), ("budget", "бюджет"),
    ("Пожалуйста уточни destination.city", "В какой город"),
])
def test_machine_slots_are_complete_human_questions(raw, phrase):
    question = human_question(raw, state())
    assert phrase in question and question.endswith("?") and len(question.split()) >= 4
    assert "destination.city" not in question and "destination_city" not in question


@pytest.mark.parametrize("raw", ["foo.bar", "unrecognized_field", "неизвестное", None, {}])
def test_unknown_question_never_echoes_machine_field(raw):
    assert human_question(raw, state()) == "Что нужно уточнить о поездке, чтобы продолжить?"


def test_complete_existing_human_question_and_targeted_city():
    question = "Какой промежуток между остановками нужно заполнить?"
    assert human_question(question, state()) == question
    data = state(stays=[{"id": "s", "city": "Сочи"}])
    assert "Сочи" in human_question("check_in", data, {"target_id": "s"})
    assert "Сербия" in human_question("destination_city", data, country_pending())


@pytest.mark.parametrize("phrase", ["я взрослый", "Я взрослая.", "я один", "я одна", "1", "один"])
def test_adult_answer_is_adult_without_invented_child_or_adult_age(phrase):
    before = state()
    result = resolve_short_answer(before, {"field": "party.adults"}, phrase)
    assert result["handled"] and len(result["operations"]) == 1
    person = result["operations"][0]["value"]
    assert person["type"] == "adult" and "age" not in person
    after, _ = apply_operations(before, result["operations"])
    assert len(after.travelers) == 1 and after.travelers[0].type == "adult"
    assert result["pending_context"]["field"] == "party.children" and "дети" in result["question"]
    assert before.travelers == []


def test_repeat_adult_answer_does_not_duplicate_and_children_are_separate():
    before = state(travelers=[{"id": "me", "type": "adult"}])
    result = resolve_short_answer(before, {"field": "party.adults"}, "1")
    assert result["handled"] and result["operations"] == []
    assert result["pending_context"]["field"] == "party.children"
    no_children = resolve_short_answer(before, result["pending_context"], "нет")
    assert no_children["operations"] == [] and "question" not in no_children
    assert no_children["pending_context"]["children_known"] is True


def test_children_known_metadata_prevents_repeating_children_question():
    result = resolve_short_answer(state(), {"field": "party.adults", "children_known": True}, "1")
    assert len(result["operations"]) == 1 and "question" not in result


@pytest.mark.parametrize("phrase", ["0", "11", "-1", "1.5", "я ребёнок", "нас двое, один ребёнок", "1 и бюджет 20000", "подожди", "отмена"])
def test_ambiguous_or_invalid_adult_answer_never_creates_child(phrase):
    result = resolve_short_answer(state(), "party.adults", phrase)
    assert result["operations"] == []


def test_adult_count_does_not_silently_delete_existing_traveler():
    before = state(travelers=[{"id": "a", "type": "adult"}, {"id": "b", "type": "adult"}])
    result = resolve_short_answer(before, "party.adults", "1")
    assert result["operations"] == [] and "Кого именно" in result["question"]


def test_children_count_then_exact_age_does_not_change_other_people():
    before = state(travelers=[{"id": "adult", "type": "adult"}])
    result = resolve_short_answer(before, "party.children", "2")
    after, _ = apply_operations(before, result["operations"])
    target = result["pending_context"]["target_id"]
    assert result["pending_context"]["field"] == "traveler.age"
    age = resolve_short_answer(after, result["pending_context"], "4 года")
    updated, _ = apply_operations(after, age["operations"])
    assert next(p for p in updated.travelers if p.id == target).age == 4
    assert updated.travelers[0].type == "adult" and updated.travelers[0].age is None
    assert age["pending_context"]["target_id"] != target
    assert "№2" in age["question"]


def test_child_age_requires_exact_target_for_multiple_unknown_children():
    before = state(travelers=[{"id": "a", "type": "adult"}, {"id": "c1", "type": "child"}, {"id": "c2", "type": "child"}])
    result = resolve_short_answer(before, "child_age", "4")
    assert result["handled"] and result["operations"] == []
    targeted = resolve_short_answer(before, {"field": "child_age", "target_id": "c2"}, "4")
    assert targeted["operations"] == [{"type": "update_traveler", "target_id": "c2", "value": {"age": 4}}]
    assert targeted["pending_context"]["target_id"] == "c1"


@pytest.mark.parametrize("target,age", [("a", "4"), ("missing", "4"), ("c", "18"), ("c", "-1"), ("c", "4 или 5")])
def test_bad_child_age_does_not_reclassify_or_guess(target, age):
    before = state(travelers=[{"id": "a", "type": "adult"}, {"id": "c", "type": "child"}])
    assert resolve_short_answer(before, {"field": "child_age", "target_id": target}, age)["operations"] == []


def test_age_answer_does_not_replace_known_age_or_birth_date():
    for values in ({"age": 7}, {"birth_date": "2022-03-01"}):
        before = state(travelers=[{"id": "c", "type": "child", **values}])
        assert resolve_short_answer(before, {"field": "child_age", "target_id": "c"}, "4")["operations"] == []


def test_origin_answer_changes_only_origin():
    result = resolve_short_answer(state(), {"field": "origin_city"}, "Москва")
    assert result["operations"] == [{"type": "set_origin", "value": {"city": "Москва"}}]


def test_destination_answer_binds_exact_stay_and_keeps_origin_dates_rooms():
    before = state(origin="Москва", stays=[{"id": "first", "city": "Сочи", "check_in": "2026-11-01", "check_out": "2026-11-03"}, {"id": "second", "city": "Казань"}])
    result = resolve_short_answer(before, {"field": "destination_city", "target_id": "second"}, "Стамбул")
    assert result["operations"] == [{"type": "upsert_stay", "target_id": "second", "value": {"city": "Стамбул"}}]
    after, _ = apply_operations(before, result["operations"])
    assert after.origin == "Москва" and after.stays[0] == before.stays[0]
    assert after.stays[1].city == "Стамбул"


def test_new_destination_gets_stable_id_and_multiple_stays_are_not_guessed():
    result = resolve_short_answer(state(origin="Москва"), "destination.city", "Стамбул")
    assert result["operations"] == [{"type": "upsert_stay", "target_id": "stay-1", "value": {"city": "Стамбул"}}]
    before = state(stays=[{"id": "s1", "city": "Сочи"}, {"id": "s2", "city": "Казань"}])
    result = resolve_short_answer(before, "destination.city", "Стамбул")
    assert result["operations"] == [] and "какой остановки" in result["question"]


def test_nonexistent_explicit_stay_does_not_silently_add_stop():
    before = state(stays=[{"id": "s", "city": "Сочи"}])
    assert resolve_short_answer(before, {"field": "destination.city", "target_id": "wrong"}, "Стамбул")["operations"] == []


@pytest.mark.parametrize("phrase", ["стамбул?", "Стамбул", "Istanbul?"])
def test_serbia_then_istanbul_does_not_set_origin_or_implicitly_change_country(phrase):
    before = state(origin="Москва")
    result = resolve_short_answer(before, country_pending(), phrase)
    assert result["handled"] and result["operations"] == []
    assert "Турция" in result["question"] and "Сербия" in result["question"] and "Меняем страну" in result["question"]
    assert result["pending_context"]["requested_destination"]["country_code"] == "RS"
    assert result["pending_context"]["proposed_destination"]["country_code"] == "TR"
    confirmed = resolve_short_answer(before, result["pending_context"], "да")
    assert confirmed["operations"] == [{"type": "upsert_stay", "target_id": "stay-1", "value": {"city": "Стамбул"}}]
    assert before.origin == "Москва"


def test_declining_country_change_returns_to_previous_country():
    result = resolve_short_answer(state(), country_pending(), "Стамбул?")
    declined = resolve_short_answer(state(), result["pending_context"], "нет")
    assert declined["operations"] == [] and "Сербия" in declined["question"]
    assert "proposed_destination" not in declined["pending_context"]


def test_explicit_proposed_city_country_assertion_preserves_route_and_confirmation():
    before = state(origin="Москва")
    pending = resolve_short_answer(before, country_pending(), "Стамбул?")["pending_context"]
    snapshot = deepcopy((before.model_dump(mode="json"), pending))
    result = resolve_short_answer(before, pending, "стамбул это же турция")
    assert result["handled"] and result["operations"] == []
    assert "Стамбул" in result["question"] and "Турция" in result["question"] and "Сербия" in result["question"] and "Меняем страну назначения" in result["question"]
    assert result["pending_context"]["requested_destination"]["country_code"] == "RS"
    assert result["pending_context"]["confirmation_required"] is True
    assert (before.model_dump(mode="json"), pending) == snapshot


def test_unrelated_weather_is_not_swallowed_by_geographic_confirmation():
    pending = resolve_short_answer(state(), country_pending(), "Стамбул?")["pending_context"]
    assert resolve_short_answer(state(), pending, "а какая там погода?") == {"handled": False, "operations": []}


@pytest.mark.parametrize("text", ["из Москвы", "из москвы"])
def test_origin_from_phrase_is_moscow_without_question_loop(text):
    result = resolve_short_answer(state(), "origin.city", text)
    assert result == {"handled": True, "operations": [{"type": "set_origin", "value": {"city": "Москва"}}]}


def test_origin_country_confirmation_names_departure_not_destination():
    pending = {"field": "origin.city", "requested_origin": country_pending()["requested_destination"]}
    result = resolve_short_answer(state(), pending, "из Стамбула")
    assert result["operations"] == [] and "Турция" in result["question"]
    assert "страну отправления" in result["question"] and "назначения" not in result["question"]


def test_known_budget_does_not_repeat_but_explicit_conflict_can_force_question():
    before = state(budget={"amount_minor": 78000000, "currency": "RUB"})
    assert derive_pending_field(["budget.amount_minor"], before)["field"] is None
    forced = derive_pending_field([{"field": "budget.amount_minor", "context": {"force_clarification": True}}], before)
    assert forced["field"] == "budget.amount_minor" and forced["context"]["force_clarification"] is True


def test_missing_budget_still_has_typed_question():
    assert derive_pending_field(["budget.amount_minor"], state())["field"] == "budget.amount_minor"


def test_destination_country_memory_does_not_constrain_origin_city():
    pending = {"field": "origin.city", "context": {"requested_destination": parse_destination("Турция")["requested_destination"]}}
    snapshot = deepcopy(pending)
    result = resolve_short_answer(state(), pending, "Москва")
    assert result == {"handled": True, "operations": [{"type": "set_origin", "value": {"city": "Москва"}}]}
    assert pending == snapshot


def test_origin_country_constraint_is_separate_and_preserves_destination_memory():
    destination = parse_destination("ОАЭ")["requested_destination"]
    origin = parse_destination("Сербия")["requested_destination"]
    pending = {"field": "origin.city", "requested_origin": origin, "requested_destination": destination}
    result = resolve_short_answer(state(), pending, "из Стамбула")
    assert result["operations"] == [] and "Сербия" in result["question"] and "Турция" in result["question"]
    assert "ОАЭ" not in result["question"] and "отправления" in result["question"]
    assert result["pending_context"]["requested_destination"] == destination
    assert result["pending_context"]["requested_origin"] == origin


def test_destination_fuzzy_suggestions_do_not_resolve_origin_slot():
    destination = country_pending()["requested_destination"]
    pending = {"field": "origin.city", "context": {"requested_destination": destination, "suggested_cities": destination["suggested_cities"]}}
    assert resolve_short_answer(state(), pending, "Белгрод") == {"handled": False, "operations": []}


def test_country_itself_never_guesses_a_city():
    result = resolve_short_answer(state(), "destination.city", "Сербия")
    assert result["operations"] == [] and "Сербия" in result["question"]
    assert result["pending_context"]["requested_destination"]["kind"] == "country"


def test_unique_known_typo_suggestion_is_confirmation_only_then_metadata():
    result = resolve_short_answer(state(), country_pending(), "Белгрод")
    assert result["operations"] == [] and result["question"] == "Вы имеете в виду город «Белград»?"
    confirmed = resolve_short_answer(state(), result["pending_context"], "да")
    assert confirmed["operations"] == [] and "не входит в каталог" in confirmed["question"]
    assert confirmed["pending_context"]["requested_destination"] == {"kind": "city", "name": "Белград", "country_code": "RS"}
    assert len(CITIES) == 7 and "Белград" not in CITIES


def test_fuzzy_only_uses_known_context_suggestions_and_never_multiple_matches():
    absent = resolve_short_answer(state(), "destination.city", "Белгрод")
    assert absent == {"handled": False, "operations": []}
    pending = {"field": "destination.city", "context": {"suggested_cities": ["Сочи", "Соча", "Атлантида"]}}
    # Unregistered suggestions cannot become facts even when spelling is close.
    unknown = resolve_short_answer(state(), pending, "Атлантидо")
    assert unknown == {"handled": False, "operations": []}
    ambiguous = {"field": "destination.city", "context": {"suggested_cities": ["Казань", "Москва"]}}
    assert resolve_short_answer(state(), ambiguous, "Казон") ["operations"] == []


@pytest.mark.parametrize("phrase", ["Думаю", "Хорошо", "Подумаю", "а какая там погода?"])
def test_unknown_origin_reply_preserves_pending_for_main_handler(phrase):
    pending = {"field": "origin.city", "question_id": "existing-origin-question", "text": "Из какого города вы отправляетесь?"}
    before = deepcopy(pending)
    result = resolve_short_answer(state(), pending, phrase)
    assert result == {"handled": False, "operations": []}
    assert pending == before


def test_unknown_reply_with_country_context_is_not_forced_into_new_question():
    assert resolve_short_answer(state(), country_pending(), "Хорошо") == {"handled": False, "operations": []}


@pytest.mark.parametrize("pending,text", [(None, "Москва"), ({}, "1"), ("unrecognized", "4"), ("destination.city", "Москва и бюджет 20000"), ("origin.city", "подожди"), ("party.adults", "1; добавь ребёнка"), ("party.adults", "1\n2"), ("check_in", "2026-11-01")])
def test_non_slot_or_compound_text_remains_for_existing_runtime(pending, text):
    assert resolve_short_answer(state(), pending, text) == {"handled": False, "operations": []}


@pytest.mark.parametrize("raw,field", [("missing_origin", "origin.city"), ("origin_city", "origin.city"), ("destination_city", "destination.city"), ("дата вылета", "leg.departure_date"), ("Кто поедет: сколько взрослых и детей?", "party.adults")])
def test_derive_known_slugs(raw, field):
    assert derive_pending_field([raw], state())["field"] == field


def test_derive_exact_id_from_issue_details_and_skip_answered_slots():
    before = state(origin="Москва", travelers=[{"id": "a", "type": "adult"}, {"id": "c1", "type": "child", "age": 4}, {"id": "c2", "type": "child"}])
    result = derive_pending_field(["origin.city", {"code": "missing_child_age", "details": {"traveler_id": "c2"}}], before)
    assert result["field"] == "traveler.age" and result["target_id"] == "c2"
    assert derive_pending_field([{"field": "traveler.age", "target_id": "c1"}], before)["field"] is None


def test_derive_party_after_adult_answer_asks_children_not_same_adult_question():
    before = state(travelers=[{"id": "a", "type": "adult"}])
    assert derive_pending_field(["missing_travelers"], before)["field"] == "party.children"
    assert derive_pending_field([{"field": "party", "children_known": True}], before)["field"] is None


def test_derive_missing_stay_exact_target_but_not_arbitrary_multistay():
    before = state(stays=[{"id": "s1", "city": "Сочи"}, {"id": "s2"}])
    assert derive_pending_field(["destination.city"], before)["target_id"] == "s2"
    assert derive_pending_field([{"code": "missing_check_in", "details": {"stay_id": "s1"}}], before)["target_id"] == "s1"
    ambiguous = state(stays=[{"id": "s1"}, {"id": "s2"}])
    assert derive_pending_field(["destination.city"], ambiguous)["target_id"] is None


def test_inputs_are_not_mutated_and_dict_state_supported():
    before = state(origin="Москва").model_dump(mode="json")
    pending = country_pending()
    snapshot = deepcopy((before, pending))
    resolve_short_answer(before, pending, "Стамбул?")
    human_question(pending, before)
    derive_pending_field([pending], before)
    assert (before, pending) == snapshot
