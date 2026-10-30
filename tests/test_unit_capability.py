"""A capability question must not turn a nightly cap into a whole-trip budget."""
from copy import deepcopy

import pytest

from otravel.answering import canonical_unit_capability_answer
from otravel.domain import sample_trip

SOURCE = "За гостинницу хочу не больше пяти тысяч за ночь. Это точно не замена общего бюджета; если такой лимит не умеешь, скажи прямо."


@pytest.mark.parametrize('text', [SOURCE,
    'Умеешь ли ограничивать цену отеля за ночь?',
    'Поддерживается ли лимит стоимости номера в сутки?'])
def test_supported_capability_answer_is_precise_and_preserves_saved_budget(text):
    state = sample_trip().model_dump(mode='json')
    before = deepcopy(state)
    reply = canonical_unit_capability_answer(text, state)
    assert reply and 'не поддерживается' in reply and 'не применён' in reply
    assert 'общий бюджет' in reply and state == before


@pytest.mark.parametrize('text', [
    'Номер до 5000 за ночь, общий бюджет 75000.',
    'За отель хочу максимум 5000 за ночь; если не умеешь, скажи. Добавь ребёнка 7 лет.',
    'Умеешь ли ограничивать цену отеля за ночь? Общий бюджет становится 100000.',
    'Умеешь ли ограничивать цену отеля за ночь? Перенеси выезд на два дня.',
    'Умеешь ли ограничивать цену отеля за ночь? Подожди, я ещё дополню.',
    'За отель 5000 за ночь, найди варианты; если не умеешь, скажи.',
    'Сохрани общий бюджет 75000 на 5 ночей.',
    'За перелёт не больше 5000; если такой лимит не умеешь, скажи прямо.',
])
def test_edits_dates_and_ordinary_price_requests_are_not_consumed(text):
    assert canonical_unit_capability_answer(text, sample_trip().model_dump(mode='json')) is None
