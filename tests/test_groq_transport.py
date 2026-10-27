"""Pure lossless wire grammar/decoder tests; no provider, state store or calls."""
import json
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from otravel.answering import READONLY_SCHEMA
from otravel.domain import Operation, TripState, apply_operations, sample_trip
from otravel.groq_transport import (
    MAX_JSON_BYTES,
    GroqTransportError,
    build_transport_schema,
    decode_transport_response,
)
from otravel.llm_contract import build_operation_schema


def envelope(*operations):
    return {'operations': list(operations), 'control': {'action': 'none'}, 'reply': 'ok',
        'unknowns': [], 'conflicts': [], 'speech_act': 'CHANGE', 'information_subject': None}


def op(kind, value, target=None):
    return {'type': kind, 'target_id': target, 'value': value}


def encode(value):
    result = deepcopy(value)
    result['operations'] = [{'type': item['type'], 'target_id': item['target_id'],
        'value_json': json.dumps(item['value'], ensure_ascii=True, allow_nan=False)} for item in value['operations']]
    return json.dumps(result, ensure_ascii=True, allow_nan=False)


def deref(node, source):
    while '$ref' in node:
        name = node['$ref'][8:].replace('~1', '/').replace('~0', '~')
        node = source['$defs'][name]
    return node


def by_kind(schema):
    return {node['properties']['type']['enum'][0]: node for node in schema['properties']['operations']['items']['anyOf']}


@pytest.mark.parametrize('state', [TripState(), sample_trip()])
def test_wire_has_closed_required_objects_and_only_strict_compatible_schema_nodes(state):
    source = build_operation_schema(state)
    wire = build_transport_schema(source)
    Draft202012Validator.check_schema(wire)
    forbidden = {'const', 'oneOf', 'allOf', 'if', 'then', '$ref', '$defs', 'pattern', 'default'}

    def visit(node):
        if isinstance(node, dict):
            assert node and not forbidden.intersection(node)
            if node.get('type') == 'object':
                assert node['additionalProperties'] is False
                assert set(node['required']) == set(node['properties'])
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)
    visit(wire)
    assert set(wire['required']) == set(envelope())


def test_each_operation_target_allowset_matches_original_instead_of_global_nullable_id():
    original = build_operation_schema(sample_trip())
    wire = build_transport_schema(original)
    raw = deref(original['properties']['operations']['items'], original)['oneOf']
    source_targets = {}
    candidates = {None, '', 'missing-owned-id', 'new-stay-999', 'stay-istanbul', 'child-1', 'new-stay-1'}
    for variant in raw:
        properties = deref(variant, original)['properties']
        kind = deref(properties['type'], original)['const']
        target = properties['target_id']
        source_targets.setdefault(kind, []).append(target)
        resolved = deref(target, original)
        candidates.update(resolved.get('enum', []))
    for kind, variant in by_kind(wire).items():
        exact = Draft202012Validator({'$defs': original.get('$defs', {}), 'anyOf': source_targets[kind]})
        projected = Draft202012Validator(variant['properties']['target_id'])
        for candidate in candidates:
            assert projected.is_valid(candidate) == exact.is_valid(candidate), (kind, candidate)
    assert not Draft202012Validator(by_kind(wire)['upsert_stay']).is_valid(
        {'type': 'upsert_stay', 'target_id': None, 'value_json': '{}'})


@pytest.mark.parametrize('value', [{'amount_minor': 7500000}, {'amount_minor': 7500000, 'currency': 'RUB', 'scope': 'whole_trip'}])
def test_sparse_budget_values_roundtrip_without_defaults(value):
    original = build_operation_schema(TripState())
    expected = envelope(op('set_budget', value))
    assert decode_transport_response(encode(expected), original) == expected


@pytest.mark.parametrize('value', [{'city': 'Казань'}, {'city': 'Казань', 'hotel_id': None},
    {'check_in': None, 'check_out': None}, {'hotel_id': 'explicit-hotel'}])
def test_omission_and_explicit_null_are_distinct_and_preserved(value):
    original = build_operation_schema(TripState())
    expected = envelope(op('upsert_stay', value, 'new-stay-1'))
    decoded = decode_transport_response(encode(expected), original)
    assert decoded == expected and set(decoded['operations'][0]['value']) == set(value)


def test_existing_people_room_stay_and_group_aliases_roundtrip_and_apply_with_full_canonical_guards():
    state = TripState(origin='Москва')
    original = build_operation_schema(state)
    expected = envelope(
        op('add_traveler', {'id': 'new-traveler-1', 'type': 'adult', 'group_id': 'new-group-1'}),
        op('add_traveler', {'id': 'new-traveler-2', 'type': 'child', 'age': 7, 'group_id': 'new-group-1'}),
        op('upsert_group', {'traveler_ids': ['new-traveler-1', 'new-traveler-2'], 'keep_separate': True}, 'new-group-1'),
        op('upsert_stay', {'city': 'Стамбул', 'check_in': '2027-03-01', 'check_out': '2027-03-05',
            'rooms': [{'id': 'new-room-1', 'traveler_ids': ['new-traveler-1', 'new-traveler-2']}]}, 'new-stay-1'))
    decoded = decode_transport_response(encode(expected), original)
    assert decoded == expected
    updated, _ = apply_operations(state, [Operation.model_validate(item) for item in decoded['operations']])
    assert updated.stays[0].rooms[0].traveler_ids == ['new-traveler-1', 'new-traveler-2']
    assert updated.groups[0].traveler_ids == ['new-traveler-1', 'new-traveler-2']


def test_same_room_id_is_local_to_stay_and_cross_product_membership_constraints_survive():
    state = sample_trip().model_dump(mode='json')
    state['stays'][0]['rooms'][0]['id'] = 'shared-room'
    state['stays'][1]['rooms'][0]['id'] = 'shared-room'
    original = build_operation_schema(state)
    for stay in state['stays']:
        expected = envelope(op('set_room_members', {'stay_id': stay['id'], 'traveler_ids': ['adult-1', 'child-1']}, 'shared-room'))
        assert decode_transport_response(encode(expected), original) == expected
    # The target is present in the union, but does not belong to this stay.
    wrong = envelope(op('set_room_members', {'stay_id': 'stay-antalya', 'traveler_ids': ['adult-1']}, 'stay-istanbul-room-2'))
    assert Draft202012Validator(build_transport_schema(original)).is_valid(json.loads(encode(wrong)))
    with pytest.raises(GroqTransportError) as error:
        decode_transport_response(encode(wrong), original)
    assert error.value.details['validation_stage'] == 'canonical_schema'


@pytest.mark.parametrize('members', [['made-up-traveler'], ['adult-1', 'adult-1']])
def test_room_membership_unknown_and_duplicate_ids_are_canonically_rejected(members):
    original = build_operation_schema(sample_trip())
    wire = encode(envelope(op('set_room_members', {'stay_id': 'stay-istanbul', 'traveler_ids': members}, 'stay-istanbul-room-1')))
    with pytest.raises(GroqTransportError):
        decode_transport_response(wire, original)


def test_actual_strict_prototype_null_stay_target_is_rejected_before_value_can_be_applied():
    # Observed final strict transport had upsert_stay target_id=null.
    original = build_operation_schema(TripState())
    wire = encode(envelope(op('upsert_stay', {'city': 'Белград', 'check_in': None}, None)))
    with pytest.raises(GroqTransportError) as error:
        decode_transport_response(wire, original)
    assert error.value.details['validation_stage'] == 'transport_schema'


def test_chained_local_refs_and_finite_nullable_targets_are_mirrored_exactly():
    original = build_operation_schema(sample_trip())
    items = deref(original['properties']['operations']['items'], original)
    variant = next(v for v in items['oneOf'] if v['properties']['type']['const'] == 'replace_hotel')
    original.setdefault('$defs', {})['target-first'] = {'$ref': '#/$defs/target-final'}
    original['$defs']['target-final'] = {'anyOf': [{'type': 'string', 'enum': ['stay-istanbul']}, {'type': 'null'}]}
    variant['properties']['target_id'] = {'$ref': '#/$defs/target-first'}
    wire_target = by_kind(build_transport_schema(original))['replace_hotel']['properties']['target_id']
    validator = Draft202012Validator(wire_target)
    assert validator.is_valid(None) and validator.is_valid('stay-istanbul')
    assert not validator.is_valid('stay-antalya')


def test_finite_oneof_target_intersection_does_not_widen_overlap_into_union():
    original = build_operation_schema(sample_trip())
    items = deref(original['properties']['operations']['items'], original)
    variant = next(v for v in items['oneOf'] if v['properties']['type']['const'] == 'replace_hotel')
    variant['properties']['target_id'] = {'oneOf': [
        {'type': 'string', 'enum': ['only-a', 'overlap']}, {'type': 'string', 'enum': ['only-b', 'overlap']}]}
    validator = Draft202012Validator(by_kind(build_transport_schema(original))['replace_hotel']['properties']['target_id'])
    assert validator.is_valid('only-a') and validator.is_valid('only-b')
    assert not validator.is_valid('overlap') and not validator.is_valid(None)


@pytest.mark.parametrize('text', [None, {}, [], b'{}', '', '{', 'null', '[]', 'true', 'NaN', 'Infinity'])
def test_outer_json_requires_bounded_object_text_without_type_coercions(text):
    with pytest.raises(GroqTransportError):
        decode_transport_response(text, build_operation_schema(TripState()))


@pytest.mark.parametrize('value_json', ['[]', 'null', 'true', '1', '"text"', '{',
    '{"city":"Москва","city":"Казань"}', '{"city":"Москва","\\u0063ity":"Казань"}',
    '{"age":NaN}', '{"age":Infinity}', '{"age":1e9999}', '[' * 2000 + ']' * 2000])
def test_inner_json_is_bounded_object_only_no_duplicates_or_nonfinite_numbers(value_json):
    wire = json.loads(encode(envelope(op('set_origin', {'city': 'Москва'}))))
    wire['operations'][0]['value_json'] = value_json
    with pytest.raises(GroqTransportError):
        decode_transport_response(json.dumps(wire), build_operation_schema(TripState()))


@pytest.mark.parametrize('field', ['reasoning', 'failed_generation', 'private_context', 'value'])
def test_extra_outer_or_operation_fields_are_never_accepted(field):
    wire = json.loads(encode(envelope(op('set_origin', {'city': 'Москва'}))))
    wire[field] = 'do-not-echo-secrets'
    with pytest.raises(GroqTransportError) as error:
        decode_transport_response(json.dumps(wire), build_operation_schema(TripState()))
    assert 'do-not-echo-secrets' not in str(error.value) + repr(error.value.details)
    wire.pop(field)
    wire['operations'][0][field] = 'do-not-echo-secrets'
    with pytest.raises(GroqTransportError):
        decode_transport_response(json.dumps(wire), build_operation_schema(TripState()))


@pytest.mark.parametrize('character', [chr(0xd800), chr(0xdfff)])
def test_escaped_surrogates_in_values_and_keys_do_not_reach_outbound_serialization(character):
    payload = envelope()
    payload['reply'] = character
    with pytest.raises(GroqTransportError):
        decode_transport_response(json.dumps(payload), build_operation_schema(TripState()))
    payload = envelope()
    payload[character] = 'x'
    with pytest.raises(GroqTransportError):
        decode_transport_response(json.dumps(payload), build_operation_schema(TripState()))


def test_outer_duplicate_keys_size_depth_and_operation_count_are_bounded():
    original = build_operation_schema(TripState())
    valid = encode(envelope())
    for text in [valid.replace('"reply": "ok"', '"reply":"first","reply":"second"'),
        valid + ' ' * MAX_JSON_BYTES, '[' * 2000 + ']' * 2000,
        encode(envelope(*[op('set_origin', {'city': 'Москва'}) for _ in range(31)]))]:
        with pytest.raises(GroqTransportError):
            decode_transport_response(text, original)


@pytest.mark.parametrize('value', [{'amount_minor': '75000'}, {'amount_minor': True}, {'amount_minor': 0},
    {'amount_minor': 7500000, 'api_key': 'fake-secret'}])
def test_original_value_schema_retains_business_shape_guards(value):
    with pytest.raises(GroqTransportError) as error:
        decode_transport_response(encode(envelope(op('set_budget', value))), build_operation_schema(TripState()))
    assert error.value.details['validation_stage'] == 'canonical_schema'
    assert 'fake-secret' not in str(error.value) + repr(error.value.details)


@pytest.mark.parametrize('fault', ['external_ref', 'unknown_ref', 'cycle_ref', 'ref_sibling', 'unsupported_keyword',
    'unknown_operation', 'unbounded_target', 'open_value', 'missing_envelope', 'wrong_optional_type', 'readonly',
    'base_target_constraint', 'outer_variant_condition', 'contradictory_type', 'outer_envelope_condition'])
def test_malformed_or_unsupported_original_schema_fails_closed_without_network(fault):
    source = build_operation_schema(sample_trip())
    items = deref(source['properties']['operations']['items'], source)
    if fault == 'external_ref':
        items['oneOf'][0]['properties']['target_id'] = {'$ref': 'https://example.invalid/private.json'}
    elif fault == 'unknown_ref':
        items['oneOf'][0]['properties']['target_id'] = {'$ref': '#/$defs/not-defined'}
    elif fault == 'cycle_ref':
        source.setdefault('$defs', {})['cycle'] = {'$ref': '#/$defs/cycle'}
    elif fault == 'ref_sibling':
        items['oneOf'][0]['properties']['target_id'] = {'$ref': '#/$defs/s0', 'description': 'unsupported sibling'}
    elif fault == 'unsupported_keyword':
        source['patternProperties'] = {}
    elif fault == 'unknown_operation':
        items['oneOf'][0]['properties']['type'] = {'const': 'invented_booking'}
    elif fault == 'unbounded_target':
        items['oneOf'][0]['properties']['target_id'] = {'type': 'string'}
    elif fault == 'open_value':
        items['oneOf'][0]['properties']['value'] = {'type': 'object', 'additionalProperties': True}
    elif fault == 'missing_envelope':
        del source['properties']['reply']
    elif fault == 'wrong_optional_type':
        source['properties']['information_subject'] = {'anyOf': [{'type': ['string', 'null']}]}
    elif fault == 'readonly':
        source = deepcopy(READONLY_SCHEMA)
    elif fault == 'base_target_constraint':
        items['properties']['target_id'] = {'type': 'string'}
    elif fault == 'outer_variant_condition':
        items['oneOf'][0]['not'] = {}
    elif fault == 'contradictory_type':
        items['oneOf'][0]['properties']['type']['type'] = 'null'
    elif fault == 'outer_envelope_condition':
        source['not'] = {}
    with pytest.raises(GroqTransportError):
        build_transport_schema(source)


def test_exponential_local_reference_expansion_and_unsafe_regex_are_rejected_before_validation():
    source = build_operation_schema(sample_trip())
    definitions = source.setdefault('$defs', {})
    definitions['review0'] = {'type': 'string'}
    for index in range(1, 19):
        definitions[f'review{index}'] = {'allOf': [{'$ref': f'#/$defs/review{index-1}'}, {'$ref': f'#/$defs/review{index-1}'}]}
    source['properties']['reply'] = {'type': 'string', 'allOf': [{'$ref': '#/$defs/review18'}]}
    with pytest.raises(GroqTransportError):
        build_transport_schema(source)
    source = build_operation_schema(sample_trip())
    source['properties']['reply'] = {'type': 'string', 'pattern': '(a+)+$'}
    with pytest.raises(GroqTransportError):
        build_transport_schema(source)


def test_original_and_each_compiled_result_are_independent_and_no_local_decode_mutates_state():
    state = sample_trip()
    before = state.model_dump(mode='json')
    source = build_operation_schema(state)
    source_before = deepcopy(source)
    first = build_transport_schema(source)
    by_kind(first)['update_traveler']['properties']['target_id']['enum'].append('mutated-alias')
    second = build_transport_schema(source)
    assert 'mutated-alias' not in by_kind(second)['update_traveler']['properties']['target_id']['enum']
    payload = envelope(op('update_traveler', {'age': 8}, 'child-1'))
    decoded = decode_transport_response(encode(payload), source)
    decoded['operations'][0]['value']['age'] = 99
    assert source == source_before and state.model_dump(mode='json') == before and payload['operations'][0]['value']['age'] == 8
