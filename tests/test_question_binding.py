"""Pure ownership controls for displayed-question vs consumed-slot binding."""
from copy import deepcopy

import pytest
from test_pending_question_rebinding import NATIONALITY_QUESTION, owner_like_state, wrong_owner_pending

from otravel.clarifications import resolve_short_answer
from otravel.question_binding import final_question_binding, repair_question_binding


def test_fresh_search_question_without_old_proposal_cannot_borrow_room_target():
    state = owner_like_state('Стамбул')
    context = {'field': 'stay.rooms', 'target_id': 'new-stay-1', 'stay_id': 'new-stay-1',
        'traveler_id': 'new-traveler-1', 'leg_id': 'old-leg', 'children_known': True,
        'requested_destination': {'kind': 'country', 'name': 'Сербия', 'country_code': 'RS'}}
    original_context = deepcopy(context)
    result = final_question_binding([NATIONALITY_QUESTION], state, context, proposal=None)
    assert result['field'] == 'search.guest_nationality' and result['target_id'] is None
    assert not {'field', 'target_id', 'stay_id', 'traveler_id', 'leg_id'}.intersection(result['context'])
    assert result['context']['children_known'] is True
    assert result['context']['requested_destination']['country_code'] == 'RS'
    assert context == original_context


@pytest.mark.parametrize('question', [NATIONALITY_QUESTION,
    'Укажите дату возвращения и город, где заканчивается поездка.', 'Кто из взрослых входит в отдельную группу?'])
def test_explicit_server_date_proposal_wins_even_if_wording_classifies_as_another_field(question):
    context = {'date_proposal': {'owner': 'date_slots_v1', 'field': 'stay.check_out',
        'candidate_stay_ids': ['new-stay-1'], 'resolved_date': '2026-11-04'}, 'target_selection_required': True}
    proposal = {'field': 'check_out', 'target_id': 'new-stay-1'}
    before_context, before_proposal = deepcopy(context), deepcopy(proposal)
    assert final_question_binding([question], owner_like_state(), context, proposal) == {
        'field': 'stay.check_out', 'target_id': 'new-stay-1'}
    assert context == before_context and proposal == before_proposal


def test_unknown_server_group_count_field_is_preserved_instead_of_reclassified_from_group_words():
    proposal = {'field': 'group.count', 'target_id': 'count-owned-target'}
    assert final_question_binding(['Сколько взрослых и детей входит в отдельные семьи?'], owner_like_state(),
        {'membership_unknown': True}, proposal) == {'field': 'group.count', 'target_id': 'count-owned-target'}


@pytest.mark.parametrize('question', ['Сколько гостиничных номеров нужно?', 'Выберите подходящий вариант.'])
def test_matching_or_unclassified_wording_keeps_explicit_proposal_target(question):
    result = final_question_binding([question], owner_like_state('Стамбул'),
        {'stay_id': 'new-stay-1'}, {'field': 'stay.rooms', 'target_id': 'new-stay-1'})
    assert result == {'field': 'stay.rooms', 'target_id': 'new-stay-1'}


def test_historical_owner_packet_repairs_only_binding_then_existing_resolver_yields_russia():
    state, old = owner_like_state(), wrong_owner_pending()
    original_state, original_pending = deepcopy(state), deepcopy(old)
    old['context'].update(stay_id='new-stay-1', field='stay.rooms', target_id='new-stay-1')
    original_pending = deepcopy(old)
    repaired = repair_question_binding(old, state)
    assert repaired['field'] == 'search.guest_nationality' and repaired['target_id'] is None
    assert not {'field', 'target_id', 'stay_id'}.intersection(repaired['context'])
    for key in ('text', 'questions', 'state_version', 'reason'):
        assert repaired[key] == old[key]
    assert repaired['context']['requested_destination']['country_code'] == 'RS'
    assert resolve_short_answer(state, repaired, 'россии')['operations'] == [
        {'type': 'set_search_policy', 'value': {'guest_nationality': 'RU'}}]
    assert old == original_pending and state == original_state


@pytest.mark.parametrize('wording', ['Уточните параметры поездки.', 'Сколько гостиничных номеров нужно?'])
def test_unknown_or_matching_displayed_slot_does_not_reinterpret_existing_packet(wording):
    old = wrong_owner_pending()
    old.update(text=wording, questions=[wording])
    original = deepcopy(old)
    assert repair_question_binding(old, owner_like_state()) == original


def test_answered_nationality_is_not_reopened_by_an_old_wrong_packet():
    state, old = owner_like_state(), wrong_owner_pending()
    state['search_policy']['guest_nationality'] = 'RU'
    assert repair_question_binding(old, state) == old


def test_non_question_and_no_question_are_not_new_slot_instructions():
    assert repair_question_binding(None, owner_like_state()) is None
    assert final_question_binding([], owner_like_state())['field'] is None


@pytest.mark.parametrize('wording', ['Гражданство какой страны указать для поиска отеля?',
    'Укажите гражданство для гостиницы и дату заезда.', 'Сколько взрослых входит в каждую отдельную группу?'])
def test_similar_or_ambiguous_legacy_wording_does_not_overwrite_old_address(wording):
    old = wrong_owner_pending()
    old.update(text=wording, questions=[wording])
    old['context']['date_proposal'] = {'owner': 'date_slots_v1', 'resolved_date': '2026-11-04'}
    assert repair_question_binding(old, owner_like_state()) == old


def test_exact_canonical_legacy_nationality_question_allows_only_normalized_spacing_case():
    old = wrong_owner_pending()
    old['text'] = '  ГРАЖДАНСТВО   КАКОЙ СТРАНЫ ИСПОЛЬЗОВАТЬ ДЛЯ ПОИСКА ОТЕЛЯ?  '
    result = repair_question_binding(old, owner_like_state())
    assert result['field'] == 'search.guest_nationality' and result['target_id'] is None


def test_historical_extended_server_field_is_not_replaced_even_by_canonical_other_wording():
    old = wrong_owner_pending()
    old['field'] = 'group.count'
    assert repair_question_binding(old, owner_like_state()) == old


@pytest.mark.parametrize('field', ['', 'x'*129, 'group.count\nother', None, 3])
def test_invalid_proposal_field_does_not_become_persisted_question_address(field):
    result = final_question_binding([NATIONALITY_QUESTION], owner_like_state(), proposal={'field': field, 'target_id': 'wrong'})
    assert result['field'] == 'search.guest_nationality' and result['target_id'] is None
