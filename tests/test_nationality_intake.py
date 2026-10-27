"""Explicit citizenship omission fallback, pure declared facts and pinned CLDR."""
from copy import deepcopy

import pytest
from test_place_refs import place
from test_world_actual_compound_cached import RAW_ACTUAL, TEXT

import otravel.nationality_intake as module
from otravel.country_lexicon import CountryLexiconError
from otravel.domain import Operation, Preference, SearchPolicy, Traveler, TripState, apply_operations
from otravel.intent_grounding import ground_intent_operations
from otravel.nationality_intake import ground_explicit_nationality


def adult_state(*, nationality=None, count=1, child=False):
    people = [Traveler(id=f'adult-{i}', type='adult') for i in range(count)]
    if child:
        people.append(Traveler(id='child-1', type='child', age=7))
    return TripState(origin='Москва', travelers=people, search_policy=SearchPolicy(
        mode='HYBRID', hotel_provider='liteapi', flight_provider='duffel', guest_nationality=nationality))


def declared(operation_list):
    return [o['value'].get('guest_nationality') for o in operation_list
        if o['type'] == 'set_search_policy' and 'guest_nationality' in o['value']]


def test_exact_actual_world_contract_keeps_five_operations_and_adds_only_explicit_RU_search_parameter():
    ref = place(3435910, 'Buenos Aires', 'AR')
    before = TripState(search_policy=SearchPolicy(mode='HYBRID', guest_nationality=None, flight_provider='duffel'))
    before, _ = apply_operations(before, [Operation(type='upsert_stay', target_id='new-stay-1',
        value={'city': ref.canonical_name, 'place_id': ref.place_id})], resolved_places={ref.place_id: ref})
    state_dump, raw = before.model_dump(mode='json'), deepcopy(RAW_ACTUAL['operations'])
    # Hook contract: the normal intent stage first attaches the already owned
    # place identity to its name echo. The original actual model value stays
    # byte-equivalent and no new place is guessed by nationality grounding.
    grounded, _ = ground_intent_operations(before, raw, TEXT)
    assert len(grounded) == len(raw) == 5
    result, meta = ground_explicit_nationality(before, TEXT, grounded)
    assert meta['recovered'] and not meta['conflicts'] and not meta['unknowns']
    assert raw == RAW_ACTUAL['operations'] and before.model_dump(mode='json') == state_dump
    assert result[:-1] == grounded and result[-1] == {
        'type': 'set_search_policy', 'target_id': None, 'value': {'guest_nationality': 'RU'}}
    after, _ = apply_operations(before, result)
    assert after.search_policy.guest_nationality == 'RU'
    expected = before.search_policy.model_dump(mode='json')
    expected['guest_nationality'] = 'RU'
    assert after.search_policy.model_dump(mode='json') == expected
    assert after.origin == 'Москва' and len(after.travelers) == 1 and after.travelers[0].type == 'adult'
    assert after.stays[0].place_id == ref.place_id and after.location_refs == before.location_refs
    assert after.stays[0].check_in.isoformat() == '2026-11-10'
    assert after.stays[0].check_out.isoformat() == '2026-11-17'
    assert 'nationality' not in after.travelers[0].model_dump(mode='json')


@pytest.mark.parametrize('literal,expected', [('Россия', 'RU'), ('России', 'RU'), ('RU', 'RU'),
    ('Russia', 'RU'), ('Argentina', 'AR'), ('Аргентина', 'AR'), ('Сербии', 'RS'), ('FR', 'FR')])
def test_complete_country_label_form_or_explicit_ISO_maps_to_one_country(literal, expected):
    result, meta = ground_explicit_nationality(adult_state(), 'Гражданство: ' + literal + '.', [])
    assert declared(result) == [expected] and meta['recovered'] and not meta['conflicts']


@pytest.mark.parametrize('text', [
    'Если поедем, гражданство Россия.', 'Допустим. Гражданство Россия.',
    'Раньше гражданство Россия.', 'Гражданство Россия, наверное.',
    'Гражданство не Россия.', 'Не используй гражданство Россия.',
    'Гражданство Россия. Не применяй его.', 'Гражданство Россия?',
    '«Гражданство Россия».', '"Гражданство Россия".', '> Гражданство Россия.',
    'Гражданство мужа Россия.', 'У моей дочери гражданство Россия.',
    'Иван, гражданство Россия.', 'Гражданство Петра: Россия.',
    'Из Москвы в Стамбул.', 'Общаемся по-русски.', 'Паспорт РФ.',
    'Гражданство Россия или Сербия.', 'Гражданство Марса.', 'Гражданство ZZ.', 'Гражданство ru.',
])
def test_ambiguous_quoted_historical_negated_or_other_person_facts_never_initiate_policy(text):
    before = adult_state()
    frozen = before.model_dump(mode='json')
    result, meta = ground_explicit_nationality(before, text, [])
    assert result == [] and not meta['recovered']
    assert before.model_dump(mode='json') == frozen


def test_quote_of_unrelated_hotel_name_does_not_hide_a_separate_unquoted_citizenship_clause():
    result, meta = ground_explicit_nationality(adult_state(), 'Отель «Белый дом». Гражданство России.', [])
    assert declared(result) == ['RU'] and meta['recovered']


@pytest.mark.parametrize('count,child', [(0, False), (2, False), (1, True), (0, True)])
def test_personal_clause_never_assigns_one_passport_to_an_unknown_or_multi_person_party(count, child):
    result, meta = ground_explicit_nationality(adult_state(count=count, child=child), 'Гражданство Россия.', [])
    assert result == [] and not meta['recovered']
    assert meta['notes'] == [{'code': 'nationality_person_scope_ambiguous'}]


@pytest.mark.parametrize('text', ['Для поиска отеля используй гражданство Россия.',
    'Для поиска гостиницы используйте гражданство: RU.'])
def test_explicit_hotel_search_policy_for_group_is_not_individual_passport_inheritance(text):
    before = adult_state(count=2, child=True)
    people = deepcopy(before.model_dump(mode='json')['travelers'])
    result, meta = ground_explicit_nationality(before, text, [])
    assert declared(result) == ['RU'] and meta['recovered']
    assert meta['notes'][0]['scope'] == 'explicit_hotel_search_policy'
    after, _ = apply_operations(before, result)
    assert after.search_policy.guest_nationality == 'RU'
    assert after.model_dump(mode='json')['travelers'] == people


@pytest.mark.parametrize('model_value', ['RS', None])
def test_contradictory_model_nationality_is_a_conflict_without_overwriting_original_operations(model_value):
    ops = [{'type': 'set_search_policy', 'value': {'guest_nationality': model_value, 'hotel_provider': 'serpapi'}}]
    result, meta = ground_explicit_nationality(adult_state(), 'Гражданство Россия.', ops)
    assert result == ops and meta['conflicts'] and not meta['recovered']
    assert 'guest_nationality' not in meta['conflicts'][0]


def test_multiple_current_literal_citizenship_values_require_one_hotel_search_choice():
    result, meta = ground_explicit_nationality(adult_state(), 'Гражданство Россия. Гражданство Сербия.', [])
    assert result == [] and meta['conflicts'] and not meta['recovered']


def test_matching_saved_or_matching_model_value_is_noop_and_other_source_policy_fields_are_untouched():
    assert ground_explicit_nationality(adult_state(nationality='RU'), 'Гражданство Россия.', [])[0] == []
    ops = [{'type': 'set_search_policy', 'value': {'guest_nationality': 'RU', 'hotel_provider': 'serpapi'}}]
    result, meta = ground_explicit_nationality(adult_state(), 'Гражданство Россия.', ops)
    assert result == ops and not meta['recovered'] and not meta['conflicts']
    unrelated = [{'type': 'set_search_policy', 'value': {'demo_fallback': False}}]
    result, meta = ground_explicit_nationality(adult_state(), 'Гражданство Россия.', unrelated)
    assert result[0] == unrelated[0] and result[1]['value'] == {'guest_nationality': 'RU'}
    assert meta['recovered']


def test_current_literal_declaration_can_change_saved_search_parameter_without_rewriting_other_policy():
    before = adult_state(nationality='RS')
    result, meta = ground_explicit_nationality(before, 'Гражданство Россия.', [])
    assert declared(result) == ['RU'] and meta['recovered']
    after, _ = apply_operations(before, result)
    expected = before.search_policy.model_dump(mode='json')
    expected['guest_nationality'] = 'RU'
    assert after.search_policy.model_dump(mode='json') == expected


def test_existing_city_or_saved_language_is_not_a_nationality_source():
    before = adult_state()
    # Declared old persisted preference; the current public setter does not
    # create this legacy key, and this control must not silently trust it.
    before.preferences = [Preference(key='language', value='ru')]
    result, meta = ground_explicit_nationality(before, 'Давай продолжим.', [])
    assert result == [] and not meta['recovered'] and before.search_policy.guest_nationality is None


def test_invalid_operation_context_and_unavailable_reference_fail_closed_without_repairing_raw_ops(monkeypatch):
    ops = [{'type': 'unsupported', 'value': {}}]
    result, meta = ground_explicit_nationality(adult_state(), 'Гражданство Россия.', ops)
    assert result == ops and not meta['recovered'] and meta['notes'][0]['code'] == 'nationality_context_not_validated'
    def unavailable():
        raise CountryLexiconError('controlled_pinned_data_unavailable')
    monkeypatch.setattr(module, '_lexicon', unavailable)
    result, meta = ground_explicit_nationality(adult_state(), 'Гражданство Россия.', [])
    assert result == [] and not meta['recovered'] and meta['notes'] == [{'code': 'nationality_reference_unavailable'}]
