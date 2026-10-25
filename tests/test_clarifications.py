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


def test_policy_v2_nonbare_belgorod_retains_country_confirmation_without_catalogue_claim():
    # The former bare-Belgorod v1 oracle was superseded by explicit owner policy.
    before = state(origin="Москва")
    pending = country_pending()
    snapshot = deepcopy((before.model_dump(mode="json"), pending))
    result = resolve_short_answer(before, pending, "в Белгород")
    assert result["handled"] and result["operations"] == []
    assert all(name in result["question"] for name in ("Белгород", "Белград", "Россия", "Сербия"))
    assert "Меняем страну" in result["question"] and "каталог" not in result["question"]
    assert result["pending_context"]["requested_destination"]["country_code"] == "RS"
    assert result["pending_context"]["proposed_destination"] == {"kind": "city", "name": "Белгород", "country_code": "RU"}
    assert (before.model_dump(mode="json"), pending) == snapshot


def test_policy_v2_belgrade_after_nonbare_belgorod_preserves_serbian_selection():
    before = state(origin="Москва")
    first = resolve_short_answer(before, country_pending(), "в Белгород")
    result = resolve_short_answer(before, first["pending_context"], "Белград")
    assert result["handled"] and result["operations"] == [] and "не входит в каталог" in result["question"]
    assert "Белград" in result["question"] and "Сербия" in result["question"]
    assert result["pending_context"]["requested_destination"] == {"kind": "city", "name": "Белград", "country_code": "RS"}
    assert "proposed_destination" not in result["pending_context"] and not result["pending_context"].get("confirmation_required")
    assert "Выберем другой город" not in result["question"] and "доступных:" not in result["question"]
    assert not any(city in result["question"] for city in CITIES)
    assert before.origin == "Москва" and before.stays == []
    assert human_question("destination.city", before, result["pending_context"]) == result["question"]
    assert resolve_short_answer(before, result["pending_context"], "да")["operations"] == []


@pytest.mark.parametrize("text", ["Белгород", "белгород", " БЕЛГОРОД "])
def test_policy_v2_bare_belgorod_in_serbia_is_visible_contextual_belgrade_correction(text):
    before = state(origin="Москва")
    pending = country_pending()
    snapshot = deepcopy((before.model_dump(mode="json"), pending))
    result = resolve_short_answer(before, pending, text)
    assert result["handled"] and result["operations"] == []
    assert result["notice"] == "Понял как «Белград», Сербия."
    assert result["question"].startswith(result["notice"]) and "не входит в каталог" in result["question"]
    assert "Меняем страну" not in result["question"] and "Россия" not in result["question"]
    assert result["pending_context"]["requested_destination"] == {"kind": "city", "name": "Белград", "country_code": "RS"}
    assert result["pending_context"]["contextual_correction"]["interpreted_city"] == "Белград"
    assert human_question("destination.city", before, result["pending_context"]) == result["question"]
    assert (before.model_dump(mode="json"), pending) == snapshot


@pytest.mark.parametrize("text", ["именно Белгород", "Белгород, Россия", "Белгород в России", "Белгород RU", "в Белгород"])
def test_policy_v2_explicit_or_nonbare_belgorod_is_not_contextually_rewritten(text):
    result = resolve_short_answer(state(), country_pending(), text)
    assert "notice" not in result and "contextual_correction" not in result.get("pending_context", {})
    chosen = result.get("pending_context", {}).get("requested_destination", {})
    assert chosen.get("kind") != "city" or chosen.get("name") != "Белград"


@pytest.mark.parametrize("pending", ["destination.city", {"field": "destination.city", "requested_destination": parse_destination("Турция")["requested_destination"]}, {"field": "origin.city", "requested_origin": country_pending()["requested_destination"]}])
def test_policy_v2_belgorod_is_not_a_global_alias_or_an_origin_correction(pending):
    result = resolve_short_answer(state(), pending, "Белгород")
    assert result["handled"] and "notice" not in result and "contextual_correction" not in result.get("pending_context", {})
    context = result["pending_context"]
    chosen = context.get("proposed_destination", context.get("requested_destination", context.get("requested_origin")))
    assert chosen["name"] == "Белгород" and chosen["country_code"] == "RU"


def test_confirmed_supported_city_replaces_old_sidebar_metadata_without_stale_slot():
    before = state(origin="Москва")
    old_selection = resolve_short_answer(before, country_pending(), "Белград")["pending_context"]
    origin = parse_destination("Россия")["requested_destination"]
    pending = {**old_selection, "requested_origin": origin, "children_known": True}
    proposed = resolve_short_answer(before, pending, "Санкт-Петербург")
    assert proposed["operations"] == [] and proposed["pending_context"]["confirmation_required"]
    selected = resolve_short_answer(before, proposed["pending_context"], "да")
    after, _ = apply_operations(before, selected["operations"])
    assert after.stays[0].city == "Санкт-Петербург" and after.origin == "Москва"
    metadata = selected["pending_context"]
    assert metadata["requested_destination"] == {"kind": "city", "name": "Санкт-Петербург", "country_code": "RU"}
    assert metadata["requested_origin"] == origin and metadata["children_known"] is True
    assert not any(key in metadata for key in ("field", "target_id", "stay_id", "proposed_destination", "confirmation_required"))
    next_slot = derive_pending_field(["check_in"], after)
    assert next_slot["field"] == "stay.check_in" and next_slot["target_id"] == after.stays[0].id


def test_direct_supported_city_commit_returns_current_metadata_without_binding():
    selected = resolve_short_answer(state(), {"field": "destination.city", "target_id": "new-stay"}, "Сочи")
    assert selected["pending_context"] == {"requested_destination": {"kind": "city", "name": "Сочи", "country_code": "RU"}}
    assert selected["operations"][0]["target_id"] == "new-stay"


def test_unselected_country_metadata_survives_declining_city_change():
    original = country_pending()
    proposed = resolve_short_answer(state(), original, "Стамбул")
    declined = resolve_short_answer(state(), proposed["pending_context"], "нет")
    assert declined["operations"] == [] and declined["pending_context"]["requested_destination"] == original["requested_destination"]
    assert declined["pending_context"]["field"] == "destination.city"


@pytest.mark.parametrize("city,country", [("Белград", "Сербия"), ("Белгород", "Россия")])
def test_known_unsupported_city_keeps_selection_and_offers_no_search_or_forced_replacement(city, country):
    result = resolve_short_answer(state(), "destination.city", city)
    assert result["handled"] and result["operations"] == []
    assert city in result["question"] and country in result["question"] and "не входит в каталог" in result["question"]
    assert result["pending_context"]["requested_destination"]["name"] == city
    assert "Выберем другой город" not in result["question"] and "доступных:" not in result["question"]


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


def test_actual_age_continuation_updates_unknown_child_not_already_known_sibling():
    before = state(origin="Казань", travelers=[
        {"id": "adult", "type": "adult", "age": 35},
        {"id": "child-1", "type": "child", "age": None},
        {"id": "child-2", "type": "child", "age": 7},
    ])
    pending = {"field": "traveler.age", "target_id": "child-1"}
    result = resolve_short_answer(before, pending, "Ему четыре года на дату выезда. Это единственное уточнение.")
    assert result["handled"]
    assert result["operations"] == [{"type": "update_traveler", "target_id": "child-1", "value": {"age": 4}}]
    after, _ = apply_operations(before, result["operations"])
    assert [(p.id, p.type, p.age) for p in after.travelers] == [
        ("adult", "adult", 35), ("child-1", "child", 4), ("child-2", "child", 7)]


_AGE_WORD_CASES = [
    ("ноль", 0), ("нуль", 0), ("один", 1), ("два", 2), ("три", 3),
    ("четыре", 4), ("пять", 5), ("шесть", 6), ("семь", 7), ("восемь", 8),
    ("девять", 9), ("десять", 10), ("одиннадцать", 11), ("двенадцать", 12),
    ("тринадцать", 13), ("четырнадцать", 14), ("пятнадцать", 15),
    ("шестнадцать", 16), ("семнадцать", 17),
]


@pytest.mark.parametrize("text,age", _AGE_WORD_CASES + [
    ("0", 0), ("17 лет", 17), ("Ему четыре года", 4), ("Ей 4 года на дату поездки", 4),
    ("Ребёнку будет семь лет на дату выезда", 7),
    ("Ей семнадцать лет на дату поездки. Это единственное уточнение.", 17),
    ("4 года, это единственное уточнение", 4),
])
def test_bounded_age_words_and_qualifiers_change_only_exact_unknown_child(text, age):
    from otravel.domain import sample_trip

    before = sample_trip()
    before.travelers[6].age = None  # child-1; preserve groups, allocations and route.
    snapshot = before.model_dump(mode="json")
    pending = {"field": "traveler.age", "target_id": "child-1", "question_id": "known-question"}
    prior_pending = deepcopy(pending)
    result = resolve_short_answer(before, pending, text)
    assert result["handled"] and result["operations"] == [
        {"type": "update_traveler", "target_id": "child-1", "value": {"age": age}}]
    after, _ = apply_operations(before, result["operations"])
    expected = deepcopy(snapshot)
    expected["travelers"][6]["age"] = age
    assert after.model_dump(mode="json") == expected
    assert before.model_dump(mode="json") == snapshot and pending == prior_pending


@pytest.mark.parametrize("text", [
    "Ему четыре года и бюджет 200000 рублей", "Ему четыре года, добавь взрослого",
    "Ему четыре года. Перенеси выезд на два дня.", "Ему четыре года на дату 2026-12-01",
    "Ему четыре года; это единственное уточнение", "Ему четыре года\nЭто единственное уточнение",
    "Ребёнку №2 четыре года", "child-2 четыре года", "Другому ребёнку четыре года",
    "Младшему четыре года", "Ему не четыре года", "Не ему четыре года",
    "Если ему четыре года", "А если ему четыре года?", "Ему четыре года?",
    "Наверное, ему четыре года", "Ему примерно четыре года", "Четыре или пять лет",
    "От четырёх до пяти лет", "4-5 лет", "Ему восемнадцать лет", "Ему 18 лет",
    "Ему -1 год", "Ему 4.5 года", "Ему четыре года. Это единственное уточнение, но убери отель",
    "Ему четыре года, но это не единственное уточнение", "Это единственное уточнение",
])
def test_age_parser_does_not_intercept_compound_other_child_or_nonasserted_age(text):
    before = state(travelers=[{"id": "child-1", "type": "child"}, {"id": "child-2", "type": "child", "age": 7}])
    snapshot = before.model_dump(mode="json")
    result = resolve_short_answer(before, {"field": "traveler.age", "target_id": "child-1"}, text)
    assert result == {"handled": False, "operations": []}
    assert before.model_dump(mode="json") == snapshot


@pytest.mark.parametrize("person", [{"id": "target", "type": "adult"},
    {"id": "target", "type": "child", "age": 7},
    {"id": "target", "type": "child", "birth_date": "2022-03-01"}])
def test_word_age_never_overwrites_adult_known_age_or_birth_date(person):
    before = state(travelers=[person])
    result = resolve_short_answer(before, {"field": "traveler.age", "target_id": "target"}, "Ему четыре года")
    assert result["operations"] == []
    assert before.travelers[0].model_dump(mode="json") == {"age": None, "birth_date": None, "group_id": None, **person}


@pytest.mark.parametrize("pending", [None, {}, {"field": "stay.check_in", "target_id": "child-1"},
    {"field": "party.children"}, {"field": "traveler.age"},
    {"field": "traveler.age", "target_id": "missing"}])
def test_word_age_requires_existing_question_and_exact_existing_child_target(pending):
    before = state(travelers=[{"id": "child-1", "type": "child"}])
    result = resolve_short_answer(before, pending, "Ему четыре года")
    assert result["operations"] == []


def test_age_target_metadata_in_nested_pending_context_is_respected():
    before = state(travelers=[{"id": "child-1", "type": "child"}, {"id": "child-2", "type": "child"}])
    pending = {"field": "traveler.age", "context": {"traveler_id": "child-2"}}
    result = resolve_short_answer(before.model_dump(mode="json"), pending, "Ей четыре года")
    assert result["operations"] == [{"type": "update_traveler", "target_id": "child-2", "value": {"age": 4}}]
    assert result["pending_context"]["target_id"] == "child-1"
