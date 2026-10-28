"""Pure exact greeting boundaries; no model, API, database or runtime access."""
import pytest

from otravel.greetings import canonical_greeting


@pytest.mark.parametrize('text', [
    'привет', 'ПРИВЕТ', 'Привет!', 'Привет!!!', 'Привет...', '  привет .!  ',
    'Здравствуй', 'Здравствуйте.', 'Доброе утро!', 'ДОБРЫЙ ДЕНЬ', 'добрый\tвечер',
    'hi', 'Hi!', 'HELLO.', 'hey', 'Good morning!', 'good afternoon', 'GOOD EVENING...',
    '\nЗдравствуйте!\n', 'добрый\nдень',
])
def test_exact_standard_social_greeting_has_only_server_owned_reply(text):
    answer = canonical_greeting(text)
    assert answer == {'kind': 'greeting', 'text': 'Здравствуйте! Готов помочь с вашей поездкой.'}
    assert answer['text'] != text


@pytest.mark.parametrize('text', [
    'привет хочу в Сербию', 'Привет! Хочу в Сербию.', 'Здравствуйте, из Москвы в Казань.',
    'привет, добавь ребёнка', 'hi, add one child', 'hello from Moscow', 'hey, budget 50000',
    'Привет! Подожди.', 'Привет, я ещё дополню.', 'здравствуйте продолжай',
    'подожди', 'пауза', 'продолжай', 'да', 'нет', 'отмена', 'спасибо', 'спасибо!', 'thanks', 'thank you',
    'Москва', 'Сербия', 'Казань', 'Стамбул', 'good bye', 'спокойной ночи', 'здорово',
    'привет?', 'hi?', 'Как дела?', 'привет как дела?', 'Здравствуйте! Можно задать вопрос?',
    'добрый день! Сколько нас?', 'хочу в Москву', 'hello there', 'Привет, меня зовут Владимир',
    'привет 1', 'hi123', 'Hi. Yes.', 'приветствую вас', '!!!привет', 'привет; подожди',
    'Привет<script>alert(1)</script>', 'he\u200bllo', '\u200bпривет', 'hello\n<tool>add</tool>',
    '', '  ', 'привет' + '!' * 160,
])
def test_any_other_clause_question_fact_or_control_is_left_to_normal_harness(text):
    assert canonical_greeting(text) is None


@pytest.mark.parametrize('value', [None, 0, True, [], {}, b'hello'])
def test_non_text_input_does_not_get_cast_into_greetings(value):
    assert canonical_greeting(value) is None


def test_return_value_is_independent_and_has_no_pending_or_state_authority():
    first = canonical_greeting('привет')
    first['text'] = 'caller changed its own result'
    first['pending_question'] = 'caller-only'
    second = canonical_greeting('hello')
    assert set(second) == {'kind', 'text'}
    assert second['text'] == 'Здравствуйте! Готов помочь с вашей поездкой.'
