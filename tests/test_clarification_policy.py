"""Narrow optional-question decisions, no model/HTTP/SQL or inferred fields."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from otravel.clarification_policy import classify_model_clarifications, safe_blocking_model_question

SOURCE = json.loads((Path(__file__).parents[1] / 'fixtures/regression/natural-hybrid-optional-unknowns-20261027.json').read_text(encoding='utf-8'))
ROOM, FLIGHTS = SOURCE['raw_source_validated_contract']['unknowns']


def classify(state=None, unknowns=None, text=None, **kwargs):
    return classify_model_clarifications(deepcopy(SOURCE['after_trip']) if state is None else state,
        [ROOM, FLIGHTS] if unknowns is None else unknowns,
        current_text=SOURCE['input_text'] if text is None else text, search_requested=True, **kwargs)


def test_exact_actual_complete_twelve_operation_trip_moves_only_two_optional_questions_without_data_changes():
    state, unknowns = deepcopy(SOURCE['after_trip']), [ROOM, FLIGHTS]
    original = deepcopy(state)
    result = classify(state=state, unknowns=unknowns)
    assert result['blocking_questions'] == []
    assert {item['topic'] for item in result['optional_clarifications']} == {'room_type_preference', 'transport_booking_permission'}
    assert all(item['blocking'] is False for item in result['optional_clarifications'])
    assert all(note['supplier_facts_inferred'] is False and note['booking_consent_granted'] is False for note in result['notes'])
    assert state == original and unknowns == [ROOM, FLIGHTS]
    # This optional-only result may normalize a model wait only when the root
    # independently proves no explicit user pause; no user control is changed here.
    assert result['optional_clarifications'] and not result['blocking_questions']


@pytest.mark.parametrize('text', ['Обязательна двуспальная кровать.', 'Предпочитаю ванну, не душ.',
    'Нужен номер категории люкс.', 'Connecting rooms обязательны.'])
def test_current_user_room_topic_is_never_silently_dismissed(text):
    result = classify(text=text)
    assert ROOM in result['blocking_questions']
    assert not any(item['topic'] == 'room_type_preference' for item in result['optional_clarifications'])


def test_recent_user_bed_need_and_saved_hard_constraint_remain_blocking():
    assert ROOM in classify(recent_user_texts=['Обязательны отдельные кровати.'])['blocking_questions']
    state = deepcopy(SOURCE['after_trip'])
    state['constraints'].append({'key': 'bed_type', 'value': 'double', 'hard': True, 'stay_id': None, 'room_id': None})
    assert classify(state=state)['blocking_questions'] == [ROOM, FLIGHTS]
    state['constraints'] = []
    state['preferences'].append({'key': 'room_type', 'value': 'double', 'weight': 1.0})
    assert ROOM in classify(state=state)['blocking_questions']


@pytest.mark.parametrize('text', ['Не летим самолётом.', 'Перелёты не нужны, только отели.',
    'Без транспорта, нужен только отель.', 'Пока не решил, лететь самолётом или ехать поездом.',
    'Возможно, поедем поездом.', 'С перелётами пока не решил.'])
def test_transport_veto_uncertainty_or_only_hotels_cannot_be_dropped(text):
    result = classify(text=text)
    assert FLIGHTS in result['blocking_questions']
    assert not any(item['topic'] == 'transport_booking_permission' for item in result['optional_clarifications'])


def test_complete_legs_do_not_grant_search_or_booking_consent():
    result = classify_model_clarifications(SOURCE['after_trip'], [FLIGHTS], current_text='Просто сохраняем черновик.', search_requested=False)
    assert result['blocking_questions'] == [FLIGHTS] and result['optional_clarifications'] == []


@pytest.mark.parametrize('missing', ['child_age', 'dates', 'nationality', 'groups', 'legs', 'allocation'])
def test_missing_required_fact_does_not_allow_optional_only_to_turn_into_user_pause_or_search(missing):
    state = deepcopy(SOURCE['after_trip'])
    if missing == 'child_age':
        next(person for person in state['travelers'] if person['type'] == 'child')['age'] = None
    elif missing == 'dates':
        state['stays'][0]['check_in'] = None
    elif missing == 'nationality':
        state['search_policy']['guest_nationality'] = None
    elif missing == 'groups':
        state['groups'][0]['traveler_ids'] = []
    elif missing == 'legs':
        state['legs'] = []
    else:
        state['stays'][0]['rooms'][0]['traveler_ids'] = []
    result = classify(state=state)
    assert result['blocking_questions'] == [ROOM, FLIGHTS] and result['optional_clarifications'] == []


@pytest.mark.parametrize('unknown', ['Сколько лет ребёнку?', 'Гражданство пассажиров', 'Дата заезда',
    'Требования к типу номера и гражданству пассажиров', 'Неизвестный участник второй семьи',
    'Подтверждение доступности пандуса для коляски'])
def test_known_slots_and_ambiguous_or_unsupported_requirements_stay_blocking(unknown):
    result = classify(unknowns=[unknown])
    assert result['blocking_questions'] == [unknown] and result['optional_clarifications'] == []


def test_unsupported_user_need_and_server_questions_have_priority_over_optional_whitelist():
    assert classify(text='Обязательно доступный номер с пандусом для инвалидной коляски.')['blocking_questions'] == [ROOM, FLIGHTS]
    assert classify(server_questions=[ROOM, FLIGHTS])['blocking_questions'] == [ROOM, FLIGHTS]
    assert ROOM in classify(server_questions=[ROOM.upper() + '?'])['blocking_questions']


@pytest.mark.parametrize('text', ['Обязателен вид на море.', 'Балкон обязателен.',
    'Строго тихий номер и высокий этаж.', 'Необходима парковка у отеля.'])
def test_broad_room_type_unknown_is_not_dismissed_over_other_unsupported_hard_room_criteria(text):
    assert classify(text=text)['blocking_questions'] == [ROOM, FLIGHTS]


def test_safe_unrecognized_noun_phrase_keeps_meaning_without_inventing_slot():
    raw = 'Неизвестный участник второй семьи'
    result = classify(unknowns=[raw])
    assert result['blocking_renderings'][raw] == 'Уточните: Неизвестный участник второй семьи?'
    assert safe_blocking_model_question('Каковы ограничения по документам?') == 'Каковы ограничения по документам?'
    assert all(key not in result for key in ['field', 'target_id', 'default_age', 'default_bed'])


@pytest.mark.parametrize('raw', ['Уточните <script>bad</script>', 'Данные https://example.invalid/token',
    'Уточните traveler.age', 'Уточните new-room-2', 'Пароль Bearer abcdef',
    'Документ 12345678-1234-1234-1234-1234567890ab', 'Ключ 0123456789abcdef0123456789abcdef', 'Данные\nпаспортов'])
def test_unsafe_technical_or_opaque_question_does_not_get_a_natural_language_rendering(raw):
    assert safe_blocking_model_question(raw) is None


@pytest.mark.parametrize('unknowns', ['not-a-list', [None], [True]])
def test_unvalidated_unknown_structure_is_not_interpreted(unknowns):
    with pytest.raises(ValueError):
        classify_model_clarifications(SOURCE['after_trip'], unknowns)
