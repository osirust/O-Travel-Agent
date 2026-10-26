"""Permission guards, not a claim about arbitrary route NLU."""
from copy import deepcopy

import pytest

from otravel.domain import Operation
from otravel.route_policy_intent import guard_route_policy_operations


def route(kind, city=None):
    return {"type": "set_route_policy", "value": {"kind": kind, "end_city": city}}


@pytest.mark.parametrize("text", [
    "Еду один взрослый в Москву по работе. Думал вылетать из Петербурга, но город отправления сейчас уточню.",
    "Если полетим в одну сторону, будет дешевле?",
    "Вчера решили: только туда.",
    'Он написал «только туда». Добавь взрослого.',
    "Хочу обсудить билет в одну сторону?",
    "Не хочу в одну сторону.",
    "Он сказал: едем только туда.",
])
def test_unrequested_one_way_is_suppressed_other_ops_preserved(text):
    adult = {"type": "add_traveler", "value": {"type": "adult"}}
    original = [adult, route("one_way")]
    saved = deepcopy(original)
    ops, meta = guard_route_policy_operations({}, original, text)
    assert ops == [adult] and original == saved
    assert meta["notes"][0]["finding_id"] == "ROUTER8-ROUTE-ASSUMPTION-001"


@pytest.mark.parametrize("text,kind,city", [
    ("Едем только туда, обратный перелёт не нужен.", "one_way", None),
    ("Мне нужен билет в одну сторону.", "one_way", None),
    ("Планируем туда и обратно.", "round_trip", None),
    ("Вернёмся домой.", "round_trip", None),
    ("Возвращение пока не решили.", "undecided", None),
    ("Вернёмся в Сочи.", "open_jaw", "Сочи"),
    ("Закончим поездку в Москве.", "open_jaw", "Москва"),
    ("Выбери тип маршрута one_way.", "one_way", None),
    ("Обратный перелёт не нужен, остальных участников не меняй.", "one_way", None),
    ("Состав не меняй и едем только туда.", "one_way", None),
])
def test_explicit_current_policy_and_bound_end_city_are_allowed(text, kind, city):
    prior = {"route_policy": {"kind": "undecided" if kind != "undecided" else "one_way"}}
    proposed = [route(kind, city)]
    ops, meta = guard_route_policy_operations(prior, proposed, text)
    assert ops == proposed and meta == {"notes": [], "conflicts": [], "unknowns": []}


@pytest.mark.parametrize("text,proposed", [
    ("Вернёмся в Сербию.", route("open_jaw", "Белград")),
    ("Вернёмся в Сочи или Москву.", route("open_jaw", "Сочи")),
    ("Выбери open_jaw.", route("open_jaw", "Москва")),
])
def test_country_alternatives_and_missing_open_end_city_are_questions_not_guesses(text, proposed):
    ops, meta = guard_route_policy_operations({}, [proposed], text)
    assert ops == [] and meta["unknowns"]


def test_same_existing_policy_is_safe_noop_and_model_objects_remain_models():
    original = [Operation.model_validate(route("one_way"))]
    ops, meta = guard_route_policy_operations({"route_policy": {"kind": "one_way"}}, original, "Ничего не меняй.")
    assert ops == original and ops[0] is not original[0] and isinstance(ops[0], Operation)
    assert not meta["notes"]


def test_explicit_return_to_known_origin_allows_round_trip_without_new_endpoint_guess():
    prior = {"origin": "Москва", "route_policy": {"kind": "one_way", "end_city": None}}
    proposed = [route("round_trip")]
    result, meta = guard_route_policy_operations(prior, proposed, "Вернёмся в Москву.")
    assert result == proposed and not meta["notes"]


def test_negated_return_is_not_permission_for_an_open_endpoint():
    result, meta = guard_route_policy_operations({}, [route("open_jaw", "Сочи")], "Не вернёмся в Сочи.")
    assert result == [] and meta["notes"]


@pytest.mark.parametrize("start", ["Начинаем в Казани", "Из Казани"])
def test_declared_trip_end_authorizes_open_jaw_without_creating_a_terminal_hotel(start):
    proposed = [{"type": "set_origin", "value": {"city": "Казань"}},
        {"type": "upsert_stay", "target_id": "hotel", "value": {"city": "Анталья"}}, route("open_jaw", "Москва")]
    original = deepcopy(proposed)
    result, meta = guard_route_policy_operations({}, proposed,
        f"{start}, отель в Анталье с 6 по 8 ноября 2026. Один взрослый без детей, номер один. "
        "Поездка заканчивается в Москве, отель в Москве не нужен. Бюджет 500000 рублей.")
    assert result == original and proposed == original
    assert meta == {"notes": [], "conflicts": [], "unknowns": []}


@pytest.mark.parametrize("text", [
    "Поездка заканчивается в Москве?", "Если поездка заканчивается в Москве, будет дешевле?",
    "Раньше поездка заканчивалась в Москве.", "Раньше: поездка заканчивается в Москве.",
    'Он написал «Поездка заканчивается в Москве».', "Поездка не заканчивается в Москве.",
    "Поездка заканчивается не в Москве.", "Отель в Москве не нужен.",
])
def test_declared_end_still_requires_current_positive_nonquoted_transport_intent(text):
    result, meta = guard_route_policy_operations({}, [route("open_jaw", "Москва")], text)
    assert result == [] and meta["notes"]


def test_declared_country_end_or_multiple_end_cities_are_not_guessed():
    for text in ("Поездка заканчивается в Сербии.", "Поездка заканчивается в Москве или Казани."):
        result, meta = guard_route_policy_operations({}, [route("open_jaw", "Москва")], text)
        assert result == [] and meta["unknowns"]


def test_declared_end_at_known_origin_is_round_trip_without_inventing_open_jaw():
    state = {"origin": "Москва", "route_policy": {"kind": "one_way"}}
    result, meta = guard_route_policy_operations(state, [route("round_trip")], "Поездка заканчивается в Москве.")
    assert result == [route("round_trip")] and meta["unknowns"] == []
