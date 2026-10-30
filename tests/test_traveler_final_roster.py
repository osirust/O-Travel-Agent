import json
from copy import deepcopy
from pathlib import Path

import pytest

from otravel.traveler_removal_grounding import guard_traveler_removal_operations


def cached025():
    return json.loads((Path(__file__).parents[1] / 'fixtures/regression/traveler-final-census025-20261028.json')
        .read_text(encoding='utf-8'))


def test_exact_cached025_complete_roster_authorizes_only_equivalent_anonymous_adults():
    fixture = cached025()
    before, operations = deepcopy(fixture['before_trip']), deepcopy(fixture['validated_raw_contract']['value']['operations'])
    frozen = deepcopy((before, operations))
    kept, meta = guard_traveler_removal_operations(before, operations, fixture['input'])
    assert kept == operations
    assert meta['notes'][0]['code'] == 'complete_current_roster_removal_authorized'
    assert not meta['conflicts'] and not meta['unknowns']
    assert (before, operations) == frozen


@pytest.mark.parametrize('text', [
    'Какой окончательный состав: двое взрослых и двое детей, четырёх и двенадцати лет?',
    'Если окончательный состав: двое взрослых и двое детей, четырёх и двенадцати лет.',
    'Раньше писал: окончательный состав: двое взрослых и двое детей, четырёх и двенадцати лет.',
    '«Окончательный состав: двое взрослых и двое детей, четырёх и двенадцати лет.»',
    '> Окончательный состав: двое взрослых и двое детей, четырёх и двенадцати лет.',
    'Окончательный состав номера: двое взрослых и двое детей, четырёх и двенадцати лет.',
    'Окончательный состав группы: двое взрослых и двое детей, четырёх и двенадцати лет.',
    'В первом номере только двое взрослых и двое детей, четырёх и двенадцати лет.',
    'Состав: двое взрослых и двое детей, четырёх и двенадцати лет.',
    'Окончательный состав: двое взрослых и двое детей.',
])
def test_no_complete_current_whole_trip_authority_from_question_history_quote_or_subset(text):
    fixture = cached025()
    kept, meta = guard_traveler_removal_operations(fixture['before_trip'],
        fixture['validated_raw_contract']['value']['operations'], text)
    assert not any(operation['type'] == 'remove_traveler' for operation in kept)
    assert not any(note['code'] == 'complete_current_roster_removal_authorized' for note in meta['notes'])


@pytest.mark.parametrize('kind', ['age', 'birth_date', 'name', 'group', 'room', 'person_preference'])
def test_distinguishing_adult_facts_require_human_identity_clarification(kind):
    fixture = cached025()
    before = deepcopy(fixture['before_trip'])
    if kind == 'age':
        before['travelers'][2]['age'] = 45
    elif kind == 'birth_date':
        before['travelers'][2]['birth_date'] = '1981-04-03'
    elif kind == 'name':
        before['travelers'][2]['name'] = 'Ольга'
    elif kind == 'group':
        before['travelers'][2]['group_id'] = 'family-a'
        before['groups'] = [{'id': 'family-a', 'traveler_ids': ['new-traveler-3'], 'keep_separate': True}]
    elif kind == 'room':
        before['stays'][0]['rooms'] = [{'id': 'existing-room', 'traveler_ids': ['new-traveler-3']}]
    else:
        before['preferences'] = [{'key': 'personal', 'value': {'traveler_id': 'new-traveler-3'}, 'weight': 1.0}]
    kept, meta = guard_traveler_removal_operations(before,
        fixture['validated_raw_contract']['value']['operations'], fixture['input'])
    assert not any(operation['type'] == 'remove_traveler' for operation in kept)
    assert meta['conflicts'] and 'участник' in meta['conflicts'][0]


def test_projected_census_must_match_exact_adult_count_and_all_child_ages():
    fixture = cached025()
    operations = deepcopy(fixture['validated_raw_contract']['value']['operations'])
    operations[-1]['value']['age'] = 11
    kept, meta = guard_traveler_removal_operations(fixture['before_trip'], operations, fixture['input'])
    assert not any(operation['type'] == 'remove_traveler' for operation in kept)
    assert meta['conflicts']


def test_final_census_does_not_authorize_child_identity_removal_by_age_alone():
    before = {'trip_id': 'children-identity', 'travelers': [
        {'id': 'a1', 'type': 'adult'}, {'id': 'a2', 'type': 'adult'},
        {'id': 'c1', 'type': 'child', 'age': 4}, {'id': 'c2', 'type': 'child', 'age': 12},
        {'id': 'c3', 'type': 'child', 'age': 16}]}
    kept, meta = guard_traveler_removal_operations(before,
        [{'type': 'remove_traveler', 'target_id': 'c3', 'value': {}}],
        'Окончательный состав: двое взрослых и двое детей, четырёх и двенадцати лет.')
    assert kept == [] and meta['conflicts']


def test_other_closed_only_whole_trip_census_not_a_hardcoded025_exception():
    before = {'trip_id': 'other-census', 'travelers': [{'id': f'a{i}', 'type': 'adult'} for i in range(1, 4)]}
    operations = [{'type': 'remove_traveler', 'target_id': f'a{i}', 'value': {}} for i in (2, 3)]
    operations.append({'type': 'add_traveler', 'value': {'id': 'child', 'type': 'child', 'age': 8}})
    kept, meta = guard_traveler_removal_operations(before, operations,
        'В поездке остаются только один взрослый и один ребёнок 8 лет.')
    assert kept == operations and not meta['conflicts']


def test_conflicting_final_declarations_or_nonapplication_do_not_authorize_roster_writes():
    fixture = cached025()
    for suffix in (' Никого не удаляй.', ' Это гипотеза.',
            ' Окончательный состав: три взрослых и двое детей, четырёх и двенадцати лет.'):
        kept, meta = guard_traveler_removal_operations(fixture['before_trip'],
            fixture['validated_raw_contract']['value']['operations'], fixture['input'] + suffix)
        assert not any(operation['type'] == 'remove_traveler' for operation in kept)
        assert meta['conflicts']
