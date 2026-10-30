"""Known-place questions cannot grant a destination or budget change."""
import pytest

from otravel.answering import canonical_geography_answer


@pytest.mark.parametrize('question,expected', [
    ('Анталья и Стамбул в одной стране? Ничего менять не надо.', ('Анталья', 'Стамбул', 'Турция', 'одной стране')),
    ('Стамбул в Турции? Это вопрос, ничего не меняй.', ('Стамбул', 'Турция')),
    ('Стамбул в Сербии?', ('Стамбул', 'Турция')),
    ('Белгород находится в Сербии?', ('Белгород', 'Россия')),
    ('Белград и Стамбул в одной стране?', ('Сербия', 'Турция', 'разных странах')),
    ('В какой стране Анталья? Это наше действующее направление?', ('Анталья', 'Турция')),
    ('Питер и Санкт-Петербург один город? Ничего не меняй.', ('Санкт-Петербург', 'одного города')),
    ('Москва и Казань один город?', ('Москва', 'Казань', 'разные города')),
])
def test_geography_facts_are_grounded_in_registry_without_any_operations(question, expected):
    reply = canonical_geography_answer(question, {})
    assert reply and all(word in reply for word in expected)


@pytest.mark.parametrize('text', [
    'Стамбул?', 'В Сербию хочу', 'Стамбул в Сербии',
    'Анталья и Гуглбум в одной стране?',
    'Стамбул в Турции? Добавь ребёнка семи лет.',
    'Стамбул в Турции? Бюджет теперь 50000.',
    'Стамбул в Турции? Подожди, я ещё дополню.',
    'Стамбул в Турции? Найди отель.',
    'Стамбул в Турции? На 5 ночей.',
])
def test_non_faq_and_mixed_facts_remain_on_normal_intake_path(text):
    assert canonical_geography_answer(text, {}) is None
