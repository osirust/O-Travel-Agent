"""Pure v2 selector/codebook proof; no providers, keys, SQL or model calls."""
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from test_groq_transport import by_kind, deref, envelope, op

import otravel.groq_transport as transport
from otravel.answering import SECTIONS
from otravel.domain import TripState, sample_trip
from otravel.llm_contract import build_operation_schema


def encode(value, original):
    codebook = transport._compact_compile(original)[3]
    reverse = {pair: selector for selector, pair in codebook.items()}
    result = deepcopy(value)
    result['operations'] = [{'op': reverse[(item['type'], item['target_id'])],
        'value_json': json.dumps(item['value'], ensure_ascii=False, allow_nan=False)} for item in value['operations']]
    return json.dumps(result, ensure_ascii=False, allow_nan=False)


def selectors(wire):
    return wire['properties']['operations']['items']['properties']['op']['enum']


def original_pairs(original):
    previous = transport.build_transport_schema(original)
    result = set()
    for kind, variant in by_kind(previous).items():
        target = variant['properties']['target_id']
        for branch in target.get('anyOf', [target]):
            for value in ([None] if branch.get('type') == 'null' else branch['enum']):
                result.add((kind, value))
    return result


@pytest.mark.parametrize('state', [TripState(), sample_trip()])
def test_compact_wire_is_closed_two_field_items_and_exact_v1_pair_bijection(state):
    original = build_operation_schema(state)
    original_before = deepcopy(original)
    _, compact, _, codebook = transport._compact_compile(original)
    Draft202012Validator.check_schema(compact)
    assert set(compact['required']) == set(envelope())
    assert len(codebook) == len(set(codebook.values()))
    assert set(codebook.values()) == original_pairs(original)
    assert selectors(compact) == list(codebook)
    item = compact['properties']['operations']['items']
    assert set(item['properties']) == set(item['required']) == {'op', 'value_json'}
    assert item['additionalProperties'] is False

    def visit(value):
        if isinstance(value, dict):
            assert not {'anyOf', 'oneOf', '$ref', '$defs', 'const', 'if', 'then', 'default'}.intersection(value)
            if value.get('type') == 'object':
                assert value['additionalProperties'] is False
                assert set(value['required']) == set(value['properties'])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(compact)
    assert original == original_before
    assert transport.build_compact_transport_schema(original) == compact


@pytest.mark.parametrize('value', [
    {'city': 'Казань'}, {'city': 'Казань', 'hotel_id': None}, {'check_in': None, 'check_out': None},
    {'hotel_id': 'explicit-hotel'},
])
def test_value_omission_null_and_unicode_survive_without_defaults(value):
    original = build_operation_schema(TripState())
    expected = envelope(op('upsert_stay', value, 'new-stay-1'))
    assert transport.decode_compact_transport_response(encode(expected, original), original) == expected


def test_base_selector_cannot_introduce_a_null_target_or_parse_a_made_up_target():
    original = build_operation_schema(sample_trip())
    names = selectors(transport.build_compact_transport_schema(original))
    assert 'set_origin' in names and 'upsert_stay' not in names and 'replace_hotel' not in names
    for selector in ['upsert_stay', 'replace_hotel', 'set_origin@stay-istanbul', 'replace_hotel@invented',
                     'replace_hotel@stay-istanbul@guessed']:
        value = envelope()
        value['operations'] = [{'op': selector, 'value_json': '{}'}]
        with pytest.raises(transport.GroqTransportError) as error:
            transport.decode_compact_transport_response(json.dumps(value), original)
        assert error.value.details['validation_stage'] == 'transport_schema'


def test_targets_with_at_slash_colon_and_unicode_are_exact_codebook_keys_not_split():
    state = sample_trip().model_dump(mode='json')
    target = 'stay@owned/часть:one@two'
    state['stays'][0]['id'] = target
    original = build_operation_schema(state)
    expected = envelope(op('replace_hotel', {'hotel_id': 'literal-property'}, target))
    wire = json.loads(encode(expected, original))
    assert wire['operations'][0]['op'] == 'replace_hotel@' + target
    assert transport.decode_compact_transport_response(json.dumps(wire, ensure_ascii=False), original) == expected
    assert ('replace_hotel', target) in set(transport._compact_compile(original)[3].values())


def test_local_room_stay_correlation_and_shared_room_ids_remain_canonical():
    state = sample_trip().model_dump(mode='json')
    shared = 'room@same/:id'
    for stay in state['stays']:
        stay['rooms'][0]['id'] = shared
    original = build_operation_schema(state)
    for stay in state['stays']:
        expected = envelope(op('set_room_members', {'stay_id': stay['id'], 'traveler_ids': ['adult-1', 'child-1']}, shared))
        assert transport.decode_compact_transport_response(encode(expected, original), original) == expected
    wrong = envelope(op('set_room_members', {'stay_id': 'stay-antalya', 'traveler_ids': ['adult-1']}, 'stay-istanbul-room-2'))
    wire = encode(wrong, original)
    assert Draft202012Validator(transport.build_compact_transport_schema(original)).is_valid(json.loads(wire))
    with pytest.raises(transport.GroqTransportError) as error:
        transport.decode_compact_transport_response(wire, original)
    assert error.value.details['validation_stage'] == 'canonical_schema'


def test_chained_local_refs_and_finite_oneof_intersections_have_no_new_pairs():
    original = build_operation_schema(sample_trip())
    item = deref(original['properties']['operations']['items'], original)
    variant = next(v for v in item['oneOf'] if v['properties']['type']['const'] == 'replace_hotel')
    original.setdefault('$defs', {})['first'] = {'$ref': '#/$defs/final'}
    original['$defs']['final'] = {'oneOf': [
        {'type': 'string', 'enum': ['only@a/:', 'overlap']},
        {'type': 'string', 'enum': ['only@b/:', 'overlap']}]}
    variant['properties']['target_id'] = {'$ref': '#/$defs/first'}
    pairs = set(transport._compact_compile(original)[3].values())
    assert pairs == original_pairs(original)
    assert {target for kind, target in pairs if kind == 'replace_hotel'} == {'only@a/:', 'only@b/:'}
    original['$defs']['final'] = {'anyOf': [{'type': 'string', 'enum': ['only@a/:']}, {'type': 'null'}]}
    pairs = set(transport._compact_compile(original)[3].values())
    assert pairs == original_pairs(original)
    assert {target for kind, target in pairs if kind == 'replace_hotel'} == {None, 'only@a/:'}


@pytest.mark.parametrize('field', ['type', 'target_id', 'value', 'reasoning', 'failed_generation', 'private_context'])
def test_extra_operation_and_outer_fields_do_not_leak_private_values(field):
    original = build_operation_schema(TripState())
    value = json.loads(encode(envelope(op('set_origin', {'city': 'Москва'})), original))
    for location in [value, value['operations'][0]]:
        location[field] = 'synthetic-private-value-do-not-echo'
        with pytest.raises(transport.GroqTransportError) as error:
            transport.decode_compact_transport_response(json.dumps(value), original)
        assert 'synthetic-private-value-do-not-echo' not in str(error.value) + repr(error.value.details)
        location.pop(field)


@pytest.mark.parametrize('operation', [
    {'value_json': '{}'}, {'op': 'set_origin'}, {'op': None, 'value_json': '{}'},
    {'op': True, 'value_json': '{}'}, {'op': 'set_origin', 'value_json': None},
    {'op': 'set_origin', 'value_json': {}},
])
def test_missing_or_null_selector_and_value_text_never_get_defaults(operation):
    original = build_operation_schema(TripState())
    value = envelope()
    value['operations'] = [operation]
    with pytest.raises(transport.GroqTransportError) as error:
        transport.decode_compact_transport_response(json.dumps(value), original)
    assert error.value.details['validation_stage'] == 'transport_schema'


def test_explicit_v1_and_v2_decoders_do_not_silently_accept_the_other_wire_format():
    original = build_operation_schema(TripState())
    expected = envelope(op('set_origin', {'city': 'Москва'}))
    with pytest.raises(transport.GroqTransportError):
        transport.decode_transport_response(encode(expected, original), original)
    previous = deepcopy(expected)
    previous['operations'] = [{'type': 'set_origin', 'target_id': None, 'value_json': '{"city":"Москва"}'}]
    with pytest.raises(transport.GroqTransportError):
        transport.decode_compact_transport_response(json.dumps(previous), original)


@pytest.mark.parametrize('content', [None, {}, [], b'{}', '', '{', 'null', '[]', 'true', 'NaN', 'Infinity'])
def test_outer_json_remains_bounded_object_text(content):
    with pytest.raises(transport.GroqTransportError):
        transport.decode_compact_transport_response(content, build_operation_schema(TripState()))


@pytest.mark.parametrize('value_json', ['[]', 'null', 'true', '1', '"text"', '{',
    '{"city":"Москва","city":"Казань"}', '{"city":"Москва","\\u0063ity":"Казань"}',
    '{"age":NaN}', '{"age":Infinity}', '{"age":1e9999}', '[' * 2000 + ']' * 2000])
def test_inner_values_retain_duplicate_finite_depth_and_object_guards(value_json):
    original = build_operation_schema(TripState())
    value = envelope()
    value['operations'] = [{'op': 'set_origin', 'value_json': value_json}]
    with pytest.raises(transport.GroqTransportError):
        transport.decode_compact_transport_response(json.dumps(value), original)


def test_outer_duplicates_operation_count_and_byte_bounds_are_not_relaxed():
    original = build_operation_schema(TripState())
    valid = encode(envelope(), original)
    operation = encode(envelope(op('set_origin', {'city': 'Москва'})), original)
    for content in [valid.replace('"reply": "ok"', '"reply":"one","reply":"two"'),
        operation.replace('"op": "set_origin"', '"op":"set_origin","op":"set_origin"'),
        valid + ' ' * transport.MAX_JSON_BYTES, '[' * 2000 + ']' * 2000,
        encode(envelope(*[op('set_origin', {'city': 'Москва'}) for _ in range(31)]), original)]:
        with pytest.raises(transport.GroqTransportError):
            transport.decode_compact_transport_response(content, original)
    value = envelope()
    value['operations'] = [{'op': 'set_origin', 'value_json': '{"city":"' + 'x' * transport.MAX_VALUE_BYTES + '"}'}]
    with pytest.raises(transport.GroqTransportError) as error:
        transport.decode_compact_transport_response(json.dumps(value), original)
    assert error.value.details['validation_stage'] == 'operation_value_json'


@pytest.mark.parametrize('value', [{'amount_minor': '75000'}, {'amount_minor': True}, {'amount_minor': 0},
    {'amount_minor': 7500000, 'api_key': 'synthetic-private-value'}])
def test_canonical_value_schema_is_not_replaced_by_the_compact_grammar(value):
    original = build_operation_schema(TripState())
    with pytest.raises(transport.GroqTransportError) as error:
        transport.decode_compact_transport_response(encode(envelope(op('set_budget', value)), original), original)
    assert error.value.details['validation_stage'] == 'canonical_schema'
    assert 'synthetic-private-value' not in str(error.value) + repr(error.value.details)


@pytest.mark.parametrize('character', [chr(0xd800), chr(0xdfff)])
def test_unicode_surrogate_keys_values_and_selectors_are_rejected(character):
    original = build_operation_schema(TripState())
    for value in [{**envelope(), 'reply': character}, {**envelope(), character: 'x'},
        {**envelope(), 'operations': [{'op': character, 'value_json': '{}'}]}]:
        with pytest.raises(transport.GroqTransportError):
            transport.decode_compact_transport_response(json.dumps(value), original)


@pytest.mark.parametrize('fault', ['external', 'unknown', 'cycle', 'unsafe_regex', 'nonfinite', 'cyclic_tree'])
def test_original_schema_guards_are_inherited_without_reference_IO(fault):
    original = build_operation_schema(sample_trip())
    if fault in {'external', 'unknown'}:
        original['properties']['reply'] = {'$ref': 'https://example.invalid/private' if fault == 'external' else '#/$defs/missing'}
    elif fault == 'cycle':
        original.setdefault('$defs', {})['cycle'] = {'$ref': '#/$defs/cycle'}
    elif fault == 'unsafe_regex':
        original['properties']['reply'] = {'type': 'string', 'pattern': '(a+)+$'}
    elif fault == 'nonfinite':
        original['properties']['reply']['maxLength'] = float('nan')
    else:
        original['properties']['reply'] = original
    with pytest.raises(transport.GroqTransportError):
        transport.build_compact_transport_schema(original)


def test_selector_count_limit_refuses_without_truncation(monkeypatch):
    original = build_operation_schema(sample_trip())
    count = len(selectors(transport.build_compact_transport_schema(original)))
    monkeypatch.setattr(transport, 'MAX_COMPACT_SELECTORS', count)
    assert len(selectors(transport.build_compact_transport_schema(original))) == count
    monkeypatch.setattr(transport, 'MAX_COMPACT_SELECTORS', count - 1)
    with pytest.raises(transport.GroqTransportError) as error:
        transport.build_compact_transport_schema(original)
    assert error.value.code == 'compact_selector_count_limit'


def test_compact_wire_byte_bound_refuses_shared_large_enum_without_partial_schema():
    original = build_operation_schema(sample_trip())
    original.setdefault('$defs', {})['many-targets'] = {'type': 'string',
        'enum': ['x' * 180 + str(n).zfill(20) for n in range(100)]}
    for variant in deref(original['properties']['operations']['items'], original)['oneOf']:
        variant['properties']['target_id'] = {'$ref': '#/$defs/many-targets'}
    transport.build_transport_schema(original)
    with pytest.raises(transport.GroqTransportError) as error:
        transport.build_compact_transport_schema(original)
    assert error.value.code == 'compact_schema_byte_limit'


def test_v1_prefix_and_separate_readonly_role_stay_byte_unchanged():
    # Source checkouts may use CRLF; the preexisting LF source prefix remains
    # exact and the v1 wire-byte behavior is separately exercised in its suite.
    raw = Path(transport.__file__).read_bytes().replace(b'\r\n', b'\n')
    assert hashlib.sha256(raw[:20374]).hexdigest() == 'c3d5c4083fdc814ccecd5255e0bb74ba2532ba96985996c748636b136b851aa7'
    assert transport.build_readonly_transport_schema() == {'type': 'object', 'properties': {'sections': {
        'type': 'array', 'items': {'type': 'string', 'enum': list(SECTIONS)}}},
        'required': ['sections'], 'additionalProperties': False}
    assert transport.decode_readonly_transport_response('{"sections":["party","budget"]}') == {
        'operations': [], 'control': {'action': 'none'}, 'reply': 'party,budget',
        'unknowns': [], 'conflicts': [], 'speech_act': 'ASK', 'information_subject': None}


def test_build_decode_are_independent_and_deterministic_without_state_mutation():
    original = build_operation_schema(sample_trip())
    before = deepcopy(original)
    one = transport.build_compact_transport_schema(original)
    two = transport.build_compact_transport_schema(original)
    assert json.dumps(one, ensure_ascii=False) == json.dumps(two, ensure_ascii=False)
    selectors(one).append('invented')
    assert 'invented' not in selectors(transport.build_compact_transport_schema(original))
    expected = envelope(op('update_traveler', {'age': 8}, 'child-1'))
    decoded = transport.decode_compact_transport_response(encode(expected, original), original)
    decoded['operations'][0]['value']['age'] = 99
    assert expected['operations'][0]['value']['age'] == 8 and original == before
