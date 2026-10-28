"""A count shortcut must not swallow whole rosters or unrelated commands."""
import pytest

from otravel.family_count_answer import closed_family_count_answer


@pytest.mark.parametrize("text,count", [
    ("2", 2), ("10", 10), ("две", 2), ("три семьи", 3), ("четыре", 4),
    ("пять семей", 5), ("шесть", 6), ("семь семей.", 7), ("восемь", 8),
    ("девять семей!", 9), ("десять", 10), ("Нас две семьи", 2),
    ("  У нас 2 семьи.  ", 2),
])
def test_one_closed_count_is_accepted(text, count):
    assert closed_family_count_answer(text) == count


@pytest.mark.parametrize("text", [
    "Общий бюджет теперь 470000 рублей. Добавь приоритет минимальной общей стоимости. Все девять и распределение номеров остаются.",
    "Первая семья — двое взрослых и ребёнок, вторая — двое взрослых и ребёнок.",
    "Две семьи: первая — взрослые1/2 и дети3/6, вторая — остальные.",
    "2 семьи, бюджет 470000", "Две семьи. Добавь ребёнка семи лет.",
    "Две семьи?", "А если две семьи", "раньше было две семьи", "Не две семьи",
    '"2"', "«две семьи»", "`2`", "2 или 3", "две-три семьи", "нас двое",
    "нас 2", "2 взрослых", "2 номера", "2 ребёнка", "два человека",
    "0", "1", "одна семья", "11", "10.5", "02", "２", "двадцать семей",
    "Две семьи, подожди", "2 семьи; продолжай", "2\x00", "2\nИ измени даты",
    "Теперь сколько взрослых осталось? Первый номер сохранился прежним?",
])
def test_questions_rosters_commands_and_out_of_scope_counts_fall_through(text):
    assert closed_family_count_answer(text) is None


@pytest.mark.parametrize("value", [None, 2, {}, [], "", " " * 81])
def test_invalid_or_unbounded_input_is_not_a_count(value):
    assert closed_family_count_answer(value) is None
