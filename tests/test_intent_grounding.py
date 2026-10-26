"""Controlled geography proposals; no actual NLU, network, DB or shared writes."""
from copy import deepcopy

import pytest

from otravel.domain import Operation, TripState, apply_operations, sample_trip
from otravel.intent_grounding import ground_intent_operations, resolve_country_destination_answer


def draft(city="Белград", origin="Москва"):
    return TripState(origin=origin, travelers=[{"id": "adult", "type": "adult"}],
        stays=[{"id": "existing-stay", "city": city}])


def apply(state, ops):
    return apply_operations(state, ops)[0]


def test_reverse_initial_compound_keeps_every_other_operation_and_fills_only_literal_places():
    before = TripState()
    original = before.model_dump(mode="json")
    proposed = [{"type": "add_traveler", "value": {"id": "adult", "type": "adult"}},
                {"type": "set_preference", "value": {"key": "departure_time", "value": "early_morning"}}]
    ops, meta = ground_intent_operations(before, proposed,
        "Белград в Сербии из Москвы рано утром, детей нет, один взрослый")
    after = apply(before, ops)
    assert after.origin == "Москва" and after.stays[0].city == "Белград"
    assert len(after.travelers) == 1 and after.preferences[0].value == "early_morning"
    assert after.stays[0].rooms == [] and after.stays[0].check_in is None and after.budget is None
    assert before.model_dump(mode="json") == original and proposed[0]["type"] == "add_traveler"
    assert meta["pending_context"]["requested_destination"]["country_code"] == "RS"


def test_explicit_destination_choice_is_not_applied_as_origin_and_clears_old_country_proposal():
    before = draft()
    proposed = [{"type": "set_origin", "value": {"city": "Санкт-Петербург"}}]
    context = {"field": "destination.city", "target_id": "existing-stay", "context": {
        "requested_destination": {"kind": "country", "name": "Сербия", "country_code": "RS"},
        "proposed_destination": {"kind": "city", "name": "Стамбул", "country_code": "TR"}, "confirmation_required": True}}
    ops, meta = ground_intent_operations(before, proposed,
        "Меняем Сербию на Россию: выбираю Санкт-Петербург. Ранний вылет и состав прежние.", context)
    after = apply(before, ops)
    assert after.origin == "Москва" and after.stays[0].id == "existing-stay" and after.stays[0].city == "Санкт-Петербург"
    assert len(after.stays) == 1 and after.travelers == before.travelers
    assert meta["pending_context"]["requested_destination"]["country_code"] == "RU"
    assert "confirmation_required" in meta["pending_context_clear"]
    assert not meta["unknowns"]


@pytest.mark.parametrize("text,destination", [
    ("Мы с супругой хотим на море в Анталью. Пока посчитай нас как двух взрослых, без детей.", "Анталья"),
    ("Нужна командировка в Санкт-Петербург для двух сотрудников. Бюджет уточню отдельно.", "Санкт-Петербург"),
])
def test_unmentioned_origin_is_not_filled_from_model_defaults(text, destination):
    before = TripState()
    proposed = [{"type": "set_origin", "value": {"city": "Казань"}},
                {"type": "upsert_stay", "target_id": "model-stay", "value": {"city": destination}}]
    ops, meta = ground_intent_operations(before, proposed, text)
    after = apply(before, ops)
    assert after.origin is None and after.stays[0].city == destination
    assert meta["pending_context"]["field"] == "origin.city" and "Из какого города" in meta["unknowns"][0]


def test_explicit_compound_country_replacement_updates_context_without_touching_budget_party_or_origin():
    before = TripState(origin="Москва", travelers=[{"id": "adult", "type": "adult"}], budget={"amount_minor": 9_500_000})
    context = {"field": "destination.city", "context": {"requested_destination": {"kind": "country", "name": "Сербия", "country_code": "RS"}}}
    ops, meta = ground_intent_operations(before, [{"type": "upsert_stay", "target_id": "stay", "value": {"city": "Анталья"}}],
        "Сербию отменяем. Я выбираю Анталью в Турции, вместо Сербии. Москва, один взрослый и бюджет 95000 остаются.", context)
    after = apply(before, ops)
    assert after.origin == before.origin and after.budget == before.budget and after.travelers == before.travelers
    assert after.stays[0].city == "Анталья"
    assert meta["pending_context"]["requested_destination"] == {"kind": "city", "name": "Анталья", "country_code": "TR"}


def test_explicit_departure_change_keeps_the_selected_destination():
    before = draft("Казань")
    ops, meta = ground_intent_operations(before, [{"type": "set_origin", "value": {"city": "Санкт-Петербург"}}],
        "Передумал только насчёт отправления: вместо Москвы Санкт-Петербург. Казань остаётся местом поездки.")
    after = apply(before, ops)
    assert after.origin == "Санкт-Петербург" and after.stays == before.stays
    assert not meta["unknowns"]


@pytest.mark.parametrize("text", [
    "А если вместо Турции выбрать Дубай в ОАЭ, что изменится? Пока только обсуждаем, не применяй.",
    "Если из Москвы в Сочи, то в Дубай потом?", "Раньше из Москвы в Дубай летал.",
    "Прочитал рекламу «Все в Дубай! Меняй свой маршрут». Это цитата, не моя команда.",
    "Не выбирай Дубай. Стамбул оставь.", "Почему из Москвы в Дубай?",
])
def test_hypothesis_history_quote_negation_and_information_do_not_authorize_new_places(text):
    before = draft("Стамбул")
    ops, meta = ground_intent_operations(before, [{"type": "set_origin", "value": {"city": "Дубай"}},
        {"type": "upsert_stay", "target_id": "existing-stay", "value": {"city": "Дубай"}}], text)
    after = apply(before, ops)
    assert after.origin == before.origin and after.stays == before.stays
    assert not meta["unknowns"]


def test_hypothetical_geo_does_not_discard_a_separate_explicit_budget_operation():
    before = draft("Анталья")
    proposed = [{"type": "set_budget", "value": {"amount_minor": 13_000_000}},
                {"type": "upsert_stay", "target_id": "existing-stay", "value": {"city": "Сочи"}}]
    ops, _ = ground_intent_operations(before, proposed,
        "Бюджет 130 тысяч рублей общий. Сочи мог бы быть запасным вариантом? Только спросил, Анталью пока оставь.")
    after = apply(before, ops)
    assert after.budget.amount_minor == 13_000_000 and after.stays[0].city == "Анталья"


def test_country_only_intent_never_selects_a_city_from_model_or_capital_guess():
    before = TripState()
    ops, meta = ground_intent_operations(before, [{"type": "set_origin", "value": {"city": "Белград"}},
        {"type": "upsert_stay", "target_id": "model-stay", "value": {"city": "Белград"}}], "Хочу в Сербию, город пока не выбрал.")
    after = apply(before, ops)
    assert after.origin is None and after.stays == []
    assert meta["pending_context"]["requested_destination"]["country_code"] == "RS"
    assert "Какой город в стране" in meta["unknowns"][0]


def test_bare_city_answers_the_owned_city_slot_without_becoming_origin():
    before = TripState(origin="Москва")
    ops, meta = ground_intent_operations(before, [{"type": "set_origin", "value": {"city": "Стамбул"}}],
        "Стамбул", {"field": "destination.city"})
    after = apply(before, ops)
    assert after.origin == "Москва" and after.stays[0].city == "Стамбул"
    assert not meta["unknowns"]


def test_country_mismatch_requires_confirmation_and_never_applies_wrong_country_city():
    before = TripState(origin="Москва")
    ctx = {"field": "destination.city", "requested_destination": {"kind": "country", "name": "Сербия", "country_code": "RS"}}
    ops, meta = ground_intent_operations(before, [{"type": "upsert_stay", "target_id": "model-stay", "value": {"city": "Стамбул"}}], "Стамбул", ctx)
    assert apply(before, ops).stays == []
    assert meta["pending_context"]["confirmation_required"] is True
    assert "Турция" in meta["unknowns"][0] and meta["pending_context"]["requested_destination"]["country_code"] == "RS"


def test_multiple_stays_need_an_explicit_existing_target_no_first_guess():
    before = sample_trip()
    proposed = [{"type": "upsert_stay", "target_id": before.stays[0].id, "value": {"city": "Сочи"}}]
    ops, meta = ground_intent_operations(before, proposed, "Теперь выбираю Сочи вместо прежнего города")
    assert apply(before, ops).stays == before.stays and meta["unknowns"]
    ops, meta = ground_intent_operations(before, proposed, "Теперь выбираю Сочи вместо прежнего города",
        {"field": "destination.city", "target_id": before.stays[1].id})
    after = apply(before, ops)
    assert after.stays[0] == before.stays[0] and after.stays[1].city == "Сочи"


def test_explicit_multicity_proposals_are_not_collapsed_into_the_first_city():
    before = TripState()
    proposed = [{"type": "set_origin", "value": {"city": "Казань"}},
        {"type": "upsert_stay", "target_id": "first", "value": {"city": "Стамбул"}},
        {"type": "upsert_stay", "target_id": "second", "value": {"city": "Анталья"}}]
    ops, _ = ground_intent_operations(before, proposed, "Из Казани в Стамбул, затем в Анталью")
    after = apply(before, ops)
    assert after.origin == "Казань" and [s.city for s in after.stays] == ["Стамбул", "Анталья"]


def test_transliteration_has_closed_known_forms_not_a_default_city():
    before = TripState()
    proposed = [{"type": "set_origin", "value": {"city": "Казань"}},
        {"type": "upsert_stay", "target_id": "stay", "value": {"city": "Стамбул"}}]
    ops, _ = ground_intent_operations(before, proposed, "Privet! Ya iz Kazani, hochu v Stambul, odin vzroslyy bez detey.")
    after = apply(before, ops)
    assert after.origin == "Казань" and after.stays[0].city == "Стамбул"


def test_same_city_copied_in_a_date_edit_is_not_treated_as_an_unrequested_change():
    before = draft("Анталья")
    proposed = [{"type": "upsert_stay", "target_id": "existing-stay", "value": {"city": "Анталья", "check_in": "2026-11-20"}}]
    ops, _ = ground_intent_operations(before, proposed, "Заезд 20 ноября 2026")
    after = apply(before, ops)
    assert after.stays[0].city == "Анталья" and after.stays[0].check_in.isoformat() == "2026-11-20"


def test_model_objects_and_all_inputs_remain_immutable():
    state = draft(); operations = [Operation(type="set_origin", value={"city": "Санкт-Петербург"})]
    context = {"field": "destination.city", "target_id": "existing-stay"}
    before = state.model_dump(mode="json"), deepcopy(operations), deepcopy(context)
    ground_intent_operations(state, operations, "Выбираю Санкт-Петербург вместо Белграда", context)
    assert state.model_dump(mode="json") == before[0] and operations == before[1] and context == before[2]


def test_invalid_stable_pending_target_is_not_redirected_to_the_only_other_stay():
    before = draft()
    ops, meta = ground_intent_operations(before,
        [{"type": "upsert_stay", "target_id": "gone", "value": {"city": "Сочи"}}],
        "Выбираю Сочи вместо прежнего города", {"field": "destination.city", "target_id": "gone"})
    assert apply(before, ops).stays == before.stays and meta["unknowns"]


def test_explicit_user_stay_id_selects_one_multi_stay_without_using_model_guess():
    before = sample_trip()
    ops, meta = ground_intent_operations(before,
        [{"type": "upsert_stay", "target_id": "wrong-model-target", "value": {"city": "Сочи"}}],
        "Остановку stay-antalya замени на Сочи")
    after = apply(before, ops)
    assert after.stays[0] == before.stays[0] and after.stays[1].city == "Сочи"
    assert not meta["unknowns"]


def test_yes_only_consumes_an_active_owned_destination_proposal_not_an_old_rejected_one():
    before = TripState(origin="Москва")
    proposed = [{"type": "upsert_stay", "target_id": "stay", "value": {"city": "Стамбул"}}]
    context = {"field": "destination.city", "state_version": before.state_version,
        "proposal_field": "destination.city", "confirmation_required": True,
        "proposed_destination": {"kind": "city", "name": "Стамбул", "country_code": "TR"},
        "requested_destination": {"kind": "country", "name": "Сербия", "country_code": "RS"}}
    ops, _ = ground_intent_operations(before, proposed, "да", context)
    assert apply(before, ops).stays[0].city == "Стамбул"
    for changed in [{**context, "confirmation_required": False}, {**context, "state_version": before.state_version + 1}]:
        ops, meta = ground_intent_operations(before, proposed, "да", changed)
        assert apply(before, ops).stays == [] and meta["unknowns"]


def test_stale_pending_does_not_authorize_a_bare_city_but_new_explicit_text_can():
    before = draft()
    pending = {"field": "destination.city", "target_id": "existing-stay", "state_version": before.state_version + 1}
    ops, _ = ground_intent_operations(before,
        [{"type": "set_origin", "value": {"city": "Сочи"}}], "Сочи", pending)
    assert apply(before, ops).origin == "Москва" and apply(before, ops).stays == before.stays
    ops, _ = ground_intent_operations(before, [], "Меняем направление: выбираю Сочи вместо Белграда", pending)
    assert apply(before, ops).stays[0].city == "Сочи"


def test_explicit_replacement_of_unknown_old_city_still_authorizes_known_new_city():
    before = TripState(origin="Москва")
    ops, meta = ground_intent_operations(before,
        [{"type": "upsert_stay", "target_id": "stay", "value": {"city": "Дубай"}}],
        "Согласен вместо Тбилиси на Дубай в ОАЭ. Это явная замена, один взрослый из Москвы остаётся.",
        {"field": "destination.city"})
    assert apply(before, ops).stays[0].city == "Дубай"
    assert meta["pending_context"]["requested_destination"]["country_code"] == "AE"


def test_bare_city_in_a_bound_compound_is_secondary_to_an_explicit_destination_choice():
    before = TripState(origin="Москва")
    ops, meta = ground_intent_operations(before, [], "Бюджет 100 тысяч, Дубай", {"field": "destination.city"})
    assert apply(before, ops).stays[0].city == "Дубай"
    ops, meta = ground_intent_operations(before, [], "Выбираю Анталью в Турции. Москва, один взрослый и бюджет прежние.",
        {"field": "destination.city"})
    assert apply(before, ops).stays[0].city == "Анталья" and not meta["unknowns"]


def test_replace_a_unique_departure_value_does_not_also_replace_destination():
    before = draft("Стамбул")
    ops, _ = ground_intent_operations(before, [], "Санкт-Петербург вместо Москвы")
    after = apply(before, ops)
    assert after.origin == "Санкт-Петербург" and after.stays == before.stays


def test_explicit_direction_choice_is_not_inferred_as_departure_from_an_old_name():
    before = draft("Стамбул")
    ops, _ = ground_intent_operations(before, [], "Выбираю Дубай вместо Москвы")
    after = apply(before, ops)
    assert after.origin == "Москва" and after.stays[0].city == "Дубай"


def test_city_choice_in_an_owned_origin_slot_is_origin_not_destination():
    before = draft("Стамбул")
    ops, _ = ground_intent_operations(before, [], "Выбираю Санкт-Петербург вместо Москвы", {"field": "origin.city"})
    after = apply(before, ops)
    assert after.origin == "Санкт-Петербург" and after.stays == before.stays


def test_an_explicit_departure_field_overrides_an_unrelated_destination_pending_slot():
    before = draft("Стамбул")
    ops, _ = ground_intent_operations(before, [], "Город отправления выбираю Санкт-Петербург", {"field": "destination.city"})
    after = apply(before, ops)
    assert after.origin == "Санкт-Петербург" and after.stays == before.stays


@pytest.mark.parametrize("start", ["Начинаем в Казани", "Поездка начинается в Казани", "Starting in Kazan", "Из Казани"])
def test_general_start_role_is_origin_and_terminal_city_is_not_a_hotel_stay(start):
    before = TripState(travelers=[{"id": "adult", "type": "adult"}])
    proposed = [{"type": "set_origin", "value": {"city": "Казань"}},
        {"type": "upsert_stay", "target_id": "ant-stay", "value": {"city": "Анталья", "check_in": "2026-11-06", "check_out": "2026-11-08"}},
        {"type": "set_route_policy", "value": {"kind": "open_jaw", "end_city": "Москва"}}]
    ops, meta = ground_intent_operations(before, proposed,
        start + ", отель в Анталье с 6 по 8 ноября 2026. Поездка заканчивается в Москве, отель в Москве не нужен.")
    after = apply(before, ops)
    assert after.origin == "Казань" and [s.city for s in after.stays] == ["Анталья"]
    assert after.route_policy.kind == "open_jaw" and after.route_policy.end_city == "Москва"
    assert [(leg.origin, leg.destination) for leg in after.legs] == [("Казань", "Анталья"), ("Анталья", "Москва")]
    assert not meta["unknowns"]


@pytest.mark.parametrize("text", ["Если начинаем в Казани, то потом в Анталью?", "Не начинаем в Казани.",
    "Раньше поездка начиналась в Казани.", "If starting in Kazan, what would change?", "Not starting in Kazan."])
def test_start_role_does_not_authorize_hypothetical_historical_or_negated_origins(text):
    before = TripState()
    ops, _ = ground_intent_operations(before, [{"type": "set_origin", "value": {"city": "Казань"}}], text)
    assert apply(before, ops).origin is None


def test_model_terminal_hotel_proposal_is_suppressed_without_blocking_authorised_destination():
    before = TripState()
    ops, meta = ground_intent_operations(before, [{"type": "set_origin", "value": {"city": "Казань"}},
        {"type": "upsert_stay", "target_id": "ant-stay", "value": {"city": "Анталья"}},
        {"type": "upsert_stay", "target_id": "bad-terminal-hotel", "value": {"city": "Москва"}}],
        "Начинаем в Казани, отель в Анталье. Поездка заканчивается в Москве, отель в Москве не нужен.")
    after = apply(before, ops)
    assert after.origin == "Казань" and [s.city for s in after.stays] == ["Анталья"] and not meta["unknowns"]


@pytest.mark.parametrize("city,text", [
    ("Виндхук", "Добавь Виндхук, если он поддерживается"),
    ("Лима", "Пожалуйста, добавь город Лима, если этот город поддерживается."),
    ("Виндхук", "Добавь Виндхук"),
])
def test_declared_unknown_destination_asks_without_changing_trip(city, text):
    before = sample_trip()
    original = before.model_dump(mode="json")
    proposed = [{"type": "upsert_stay", "target_id": "extra", "value": {"city": city}}]
    ops, meta = ground_intent_operations(before, proposed, text)
    assert apply(before, ops).model_dump(mode="json") == original
    assert meta["unknowns"] and city in meta["unknowns"][0]
    assert "распозна" in meta["unknowns"][0] or "каталог" in meta["unknowns"][0]
    assert meta["pending_context"]["field"] == "destination.city"
    assert meta["pending_context"]["requested_destination"] == {
        "kind": "unknown", "name": city, "country_code": None}
    assert before.model_dump(mode="json") == original and proposed[0]["value"]["city"] == city


@pytest.mark.parametrize("text", [
    "А если добавить Виндхук, что изменится?", "Добавь Виндхук?",
    "Не добавляй Виндхук, даже если он поддерживается", "Если Виндхук поддерживается, можно было бы его добавить.",
    "Раньше я просил: добавь Виндхук, если он поддерживается.",
    "Прочитал цитату «Добавь Виндхук, если он поддерживается».",
    "Добавь Виндхук, если будет недорого", "Добавь Лима, если он поддерживается",
])
def test_unknown_destination_hypothesis_quotes_negation_or_unmentioned_model_city_do_not_ask(text):
    before = sample_trip()
    ops, meta = ground_intent_operations(before,
        [{"type": "upsert_stay", "target_id": "extra", "value": {"city": "Виндхук"}}], text)
    assert apply(before, ops) == before and not meta["unknowns"] and meta["pending_context"] is None


def test_new_unknown_destination_question_retires_old_geo_confirmation():
    before = sample_trip()
    ops, meta = ground_intent_operations(before,
        [{"type": "upsert_stay", "target_id": "extra", "value": {"city": "Виндхук"}}],
        "Добавь Виндхук, если он поддерживается", {"field": "destination.city",
        "confirmation_required": True, "proposal_field": "destination.city",
        "proposed_destination": {"kind": "city", "name": "Стамбул", "country_code": "TR"}})
    assert apply(before, ops) == before
    assert {"proposed_destination", "confirmation_required", "proposal_field"} <= set(meta["pending_context_clear"])
    assert meta["pending_context"]["requested_destination"]["name"] == "Виндхук"


def test_explicit_country_slot_answer_does_not_become_a_city_or_only_a_geography_fact():
    before = TripState(origin="Москва", travelers=[{"id": "adult", "type": "adult"}])
    ops, meta = ground_intent_operations(before, [], "страна Грузия",
        {"field": "destination.city", "state_version": before.state_version})
    assert apply(before, ops) == before
    assert meta["pending_context"] is not None
    assert meta["pending_context"]["requested_destination"]["country_code"] == "GE"
    assert "город" in meta["unknowns"][0].lower()


@pytest.mark.parametrize("text,code", [
    ("страна Грузия", "GE"), ("Это страна Грузия.", "GE"), ("Страна назначения: Грузия", "GE"),
    ("Я имел в виду страну Грузию, не американский штат. Если её нет в поиске, так и скажи, город за меня не выбирай.", "GE"),
    ("Я имею в виду страну Georgia, не американский штат.", "GE"),
    ("Я имел в виду страну Сербию", "RS"), ("Страна Турция", "TR"),
])
def test_contextual_country_answer_preserves_trip_and_asks_for_city_without_catalogue_claim(text, code):
    before = TripState(origin="Москва", travelers=[{"id": "adult", "type": "adult"}])
    snapshot = before.model_dump(mode="json")
    result = resolve_country_destination_answer(before, {"field": "destination.city", "state_version": before.state_version,
        "context": {"requested_destination": {"kind": "ambiguous", "name": "Georgia", "country_code": None},
        "proposed_destination": {"kind": "city", "name": "Стамбул", "country_code": "TR"}, "confirmation_required": True}}, text)
    assert result["handled"] and result["operations"] == []
    assert result["pending_context"]["requested_destination"]["kind"] == "country"
    assert result["pending_context"]["requested_destination"]["country_code"] == code
    assert result["pending_context"]["field"] == "destination.city" and "город" in result["question"].lower()
    assert "proposed_destination" in result["pending_context_clear"]
    assert all(word not in result["question"].lower() for word in ("доступен", "доступны", "отсутствует", "тбилиси"))
    assert before.model_dump(mode="json") == snapshot


@pytest.mark.parametrize("text", [
    "А если страна Грузия?", "Страна Грузия?", "Не страна Грузия", "Раньше я имел в виду страну Грузию",
    "Цитата «Я имел в виду страну Грузию»", "У меня гражданство Грузии", "Страна моего гражданства Грузия",
    "Я имел в виду страну Грузию, добавь ребёнка 8 лет", "Я имел в виду страну Грузию. Бюджет 100 тысяч",
    "Я имел в виду страну Грузию, но выбираю Дубай", "Я имел в виду штат Джорджия", "Страна Неизвестния",
])
def test_country_slot_hypothesis_faq_citizenship_and_other_edits_do_not_get_swallowed(text):
    assert resolve_country_destination_answer(TripState(), {"field": "destination.city"}, text) == {
        "handled": False, "operations": []}


@pytest.mark.parametrize("pending", [None, {"field": "origin.city"}, {"field": "party.adults"},
    {"field": "destination.city", "state_version": 100},
    {"field": "destination.city", "state_version": True},
    {"field": "destination.city", "target_id": "not-owned"}])
def test_country_slot_requires_fresh_owned_destination_binding(pending):
    assert not resolve_country_destination_answer(TripState(), pending, "страна Грузия")["handled"]
