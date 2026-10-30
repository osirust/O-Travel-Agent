"""Opt-in Groq wire encoding; canonical operation schemas retain authority.

No state, provider, network or database access. A wire value_json is decoded as
an object without adding/removing its keys. Target IDs are never guessed.
"""
from __future__ import annotations

import json
import math
import re
from collections import OrderedDict
from copy import deepcopy
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .llm_contract import CONTROL_ACTIONS, OPERATION_TYPES

MAX_SCHEMA_BYTES = 262_144
MAX_JSON_BYTES = 65_536
MAX_VALUE_BYTES = 32_768
MAX_JSON_DEPTH = 32
_MAX_SCHEMA_DEPTH = 64
_MAX_NODES = 20_000
_MAX_EXPANDED_SCHEMA_NODES = 20_000
_FIELDS = ('operations', 'control', 'reply', 'unknowns', 'conflicts', 'speech_act', 'information_subject')
_VOCABULARY = frozenset({'type', 'properties', 'required', 'additionalProperties', '$defs', '$ref', '$schema',
    'enum', 'const', 'items', 'maxItems', 'minItems', 'uniqueItems', 'minProperties', 'maxProperties',
    'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum', 'multipleOf', 'minLength', 'maxLength',
    'pattern', 'format', 'oneOf', 'anyOf', 'allOf', 'if', 'then', 'else', 'not', 'title', 'description', '$comment'})


class GroqTransportError(ValueError):
    """Bounded diagnostic codes/paths only, never model or schema values."""
    def __init__(self, code: str, stage: str, errors=()):
        self.code = code
        self.details: dict[str, Any] = {'validation_stage': stage}
        if errors:
            self.details['schema_errors'] = [
                {'path': list(error.absolute_path)[:12], 'rule': error.validator} for error in errors[:3]]
        super().__init__(code)


def _fail(code, stage='transport_schema'):
    raise GroqTransportError(code, stage)


def _bounded_tree(value, *, depth_limit, stage):
    count = 0
    active = set()
    string_chars = 0

    def text(value):
        nonlocal string_chars
        string_chars += len(value)
        if stage == 'original_schema' and string_chars > MAX_SCHEMA_BYTES:
            _fail('schema_byte_limit', stage)
        try:
            value.encode('utf-8')
        except UnicodeError:
            _fail('invalid_json_unicode', stage)

    def visit(node, depth):
        nonlocal count
        count += 1
        if count > _MAX_NODES or depth > depth_limit:
            _fail('json_structure_limit', stage)
        if isinstance(node, (dict, list)):
            if id(node) in active:
                _fail('cyclic_json_structure', stage)
            active.add(id(node))
            if isinstance(node, dict):
                if any(type(key) is not str for key in node):
                    _fail('non_string_json_key', stage)
                for key in node:
                    text(key)
                children = node.values()
            else:
                children = node
            for child in children:
                visit(child, depth + 1)
            active.remove(id(node))
        elif type(node) is str:
            text(node)
        elif type(node) is float and not math.isfinite(node):
            _fail('non_finite_json_number', stage)
        elif type(node) is int and node.bit_length() > 4096:
            _fail('json_number_limit', stage)
        elif node is not None and type(node) not in {str, bool, int, float}:
            _fail('non_json_value', stage)
    visit(value, 0)


def _json(content, *, limit, stage):
    if type(content) is not str:
        _fail('json_text_required', stage)
    try:
        if len(content) > limit or len(content.encode('utf-8')) > limit:
            _fail('json_byte_limit', stage)
    except UnicodeError:
        _fail('invalid_json_unicode', stage)
    depth, quoted, escaped = 0, False, False
    for char in content:
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in '[{':
            depth += 1
            if depth > MAX_JSON_DEPTH:
                _fail('json_depth_limit', stage)
        elif char in ']}':
            depth -= 1

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                _fail('duplicate_json_key', stage)
            result[key] = value
        return result

    def constant(_):
        _fail('non_finite_json_number', stage)
    try:
        value = json.loads(content, object_pairs_hook=pairs, parse_constant=constant)
    except GroqTransportError:
        raise
    except (ValueError, TypeError, RecursionError, OverflowError):
        _fail('invalid_json', stage)
    _bounded_tree(value, depth_limit=MAX_JSON_DEPTH, stage=stage)
    return value


def _ref_name(ref, definitions):
    if (type(ref) is not str or not ref.startswith('#/$defs/') or '/' in ref[8:]
            or re.search(r'~(?![01])', ref[8:])):
        _fail('unsupported_schema_reference')
    name = ref[8:].replace('~1', '/').replace('~0', '~')
    if not name or name not in definitions:
        _fail('unknown_schema_reference')
    return name


def _safe_pattern(value):
    """Only fixed-width, anchored ASCII code/date patterns from this contract.

    Arbitrary server-schema regexes are intentionally unsupported: a byte bound
    does not make nested quantifiers safe for canonical regex validation.
    """
    if type(value) is not str or not re.fullmatch(r'\^(?:(?:\[(?:A-Z|0-9)\])\{[0-9]{1,2}\}|-)+\$', value):
        _fail('unsupported_schema_pattern')
    lengths = [int(number) for number in re.findall(r'\{([0-9]{1,2})\}', value)]
    if not lengths or any(not 1 <= number <= 32 for number in lengths) or sum(lengths) > 64:
        _fail('unsupported_schema_pattern')


def _prepare(original):
    if type(original) is not dict:
        _fail('dynamic_operation_schema_required')
    _bounded_tree(original, depth_limit=_MAX_SCHEMA_DEPTH, stage='original_schema')
    try:
        text = json.dumps(original, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        if len(text.encode('utf-8')) > MAX_SCHEMA_BYTES:
            _fail('schema_byte_limit')
        schema = json.loads(text)
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        _fail('invalid_original_schema')
    definitions = schema.get('$defs', {})
    if type(definitions) is not dict or len(definitions) > 256:
        _fail('unsupported_schema_definitions')
    graph = {name: set() for name in definitions}

    def scan(node, owner=None, root=False):
        if type(node) is bool:
            return
        if type(node) is not dict or not set(node) <= _VOCABULARY:
            _fail('unsupported_original_schema_keyword')
        if '$ref' in node:
            if set(node) != {'$ref'}:
                _fail('schema_reference_siblings_unsupported')
            name = _ref_name(node['$ref'], definitions)
            if owner is not None:
                graph[owner].add(name)
            return
        if '$defs' in node and not root:
            _fail('nested_schema_definitions_unsupported')
        if 'pattern' in node:
            _safe_pattern(node['pattern'])
        if '$schema' in node and node['$schema'] not in {
                'https://json-schema.org/draft/2020-12/schema', 'https://json-schema.org/draft/2020-12/schema#'}:
            _fail('unsupported_schema_dialect')
        if node.get('type') == 'object' and node.get('additionalProperties') is not False:
            _fail('open_original_object_unsupported')
        properties = node.get('properties', {})
        if type(properties) is not dict:
            _fail('invalid_original_properties')
        for child in properties.values():
            scan(child, owner)
        if 'items' in node:
            scan(node['items'], owner)
        if isinstance(node.get('additionalProperties'), dict):
            scan(node['additionalProperties'], owner)
        for key in ('oneOf', 'anyOf', 'allOf'):
            if key in node:
                if type(node[key]) is not list:
                    _fail('invalid_schema_union')
                for child in node[key]:
                    scan(child, owner)
        for key in ('if', 'then', 'else', 'not'):
            if key in node:
                scan(node[key], owner)
    scan(schema, root=True)
    for name, node in definitions.items():
        scan(node, name)

    heights = {}

    def acyclic(name, active):
        if name in active:
            _fail('cyclic_or_deep_schema_reference')
        if name not in heights:
            height = 1 + max((acyclic(child, active | {name}) for child in graph[name]), default=0)
            if height > 32:
                _fail('cyclic_or_deep_schema_reference')
            heights[name] = height
        return heights[name]
    for name in graph:
        acyclic(name, set())

    # A short acyclic DAG may expand exponentially (e.g. allOf has two refs to
    # the preceding definition). Count repeated edges, not just distinct refs.
    costs = {}

    def expanded(node):
        if type(node) is bool:
            return 1
        if '$ref' in node:
            name = _ref_name(node['$ref'], definitions)
            if name not in costs:
                costs[name] = expanded(definitions[name])
            return costs[name]
        children = list(node.get('properties', {}).values())
        if 'items' in node:
            children.append(node['items'])
        if isinstance(node.get('additionalProperties'), dict):
            children.append(node['additionalProperties'])
        for key in ('oneOf', 'anyOf', 'allOf'):
            children.extend(node.get(key, []))
        children.extend(node[key] for key in ('if', 'then', 'else', 'not') if key in node)
        total = 1
        for child in children:
            total += expanded(child)
            if total > _MAX_EXPANDED_SCHEMA_NODES:
                _fail('expanded_schema_complexity_limit')
        return total
    expanded(schema)
    for node in definitions.values():
        expanded(node)
    try:
        Draft202012Validator.check_schema(schema)
    except (SchemaError, ValueError, TypeError, RecursionError):
        _fail('invalid_original_schema')

    def resolve(node):
        seen = set()
        while isinstance(node, dict) and '$ref' in node:
            name = _ref_name(node['$ref'], definitions)
            if name in seen or len(seen) >= 32:
                _fail('cyclic_or_deep_schema_reference')
            seen.add(name)
            node = definitions[name]
        if type(node) is not dict:
            _fail('unsupported_schema_node')
        return node
    return schema, definitions, resolve


def _object(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def _string_enum(node, resolve):
    node = resolve(node)
    values = node.get('enum') if 'enum' in node else [node['const']] if 'const' in node else None
    if (type(values) is not list or not 1 <= len(values) <= 256
            or any(type(value) is not str or not 0 < len(value) <= 200 for value in values)):
        _fail('bounded_string_enum_required')
    return list(dict.fromkeys(values))


def _wire(schema, definitions, resolve):
    if (schema.get('type') != 'object' or schema.get('additionalProperties') is not False
            or not set(schema) <= {'type', 'properties', 'required', 'additionalProperties', '$defs', '$schema', 'title', 'description', '$comment'}):
        _fail('dynamic_operation_schema_required')
    props = schema.get('properties', {})
    if set(props) != set(_FIELDS) or not set(_FIELDS[:5]) <= set(schema.get('required', [])):
        _fail('dynamic_operation_envelope_required')
    operations = resolve(props['operations'])
    maximum = operations.get('maxItems')
    if operations.get('type') != 'array' or type(maximum) is not int or not 1 <= maximum <= 30:
        _fail('dynamic_operations_required')
    item = resolve(operations.get('items'))
    variants = item.get('oneOf')
    if (item.get('type') != 'object' or item.get('additionalProperties') is not False
            or set(item.get('properties', {})) != {'type', 'target_id', 'value'}
            or type(variants) is not list or not 1 <= len(variants) <= 128
            or not set(item) <= {'type', 'properties', 'required', 'additionalProperties', 'oneOf', 'description', 'title'}
            or any(resolve(node) != {} for node in item['properties'].values())):
        _fail('dynamic_operation_variants_required')
    targets = OrderedDict()
    for variant in variants:
        variant = resolve(variant)
        properties = variant.get('properties', {})
        if (set(properties) != {'type', 'target_id', 'value'}
                or not set(variant) <= {'properties', 'required', 'description', 'title'}
                or not set(variant.get('required', [])) <= {'target_id'}):
            _fail('unsupported_operation_variant')
        kinds = _string_enum(properties['type'], resolve)
        if len(kinds) != 1 or kinds[0] not in OPERATION_TYPES:
            _fail('known_operation_type_required')
        if not Draft202012Validator({'$defs': definitions, 'allOf': [properties['type']]}).is_valid(kinds[0]):
            _fail('invalid_operation_type_constraint')
        value_schema = resolve(properties['value'])
        if value_schema.get('type') != 'object' or value_schema.get('additionalProperties') is not False:
            _fail('closed_operation_value_required')
        target = properties['target_id']

        def candidates(node):
            node = resolve(node)
            allowed = {'type', 'enum', 'const', 'anyOf', 'oneOf', 'description', 'title', 'pattern', 'minLength', 'maxLength'}
            if not set(node) <= allowed:
                _fail('unsupported_target_schema')
            if 'enum' in node or 'const' in node:
                values = node.get('enum') if 'enum' in node else [node['const']]
                if type(values) is not list:
                    _fail('invalid_target_enum')
            elif 'anyOf' in node or 'oneOf' in node:
                branches = node.get('anyOf', node.get('oneOf'))
                values = [value for branch in branches for value in candidates(branch)]
            elif node.get('type') == 'null':
                values = [None]
            else:
                _fail('finite_target_schema_required')
            if not 1 <= len(values) <= 256 or any(value is not None and (type(value) is not str or not 0 < len(value) <= 200) for value in values):
                _fail('bounded_target_enum_required')
            return list(dict.fromkeys(values))
        validator = Draft202012Validator({'$defs': definitions, 'allOf': [target]})
        exact = [value for value in candidates(target) if validator.is_valid(value)]
        if not exact:
            _fail('empty_target_allowset')
        saved = targets.setdefault(kinds[0], [])
        saved.extend(value for value in exact if value not in saved)
        if len(saved) > 256:
            _fail('bounded_target_enum_required')
    wire_variants = []
    for kind, values in targets.items():
        strings = [value for value in values if value is not None]
        target = {'type': 'string', 'enum': strings} if strings else {'type': 'null'}
        if strings and None in values:
            target = {'anyOf': [target, {'type': 'null'}]}
        wire_variants.append(_object({'type': {'type': 'string', 'enum': [kind]},
            'target_id': target, 'value_json': {'type': 'string'}}))
    control = resolve(props['control'])
    if control.get('type') != 'object' or set(control.get('properties', {})) != {'action'}:
        _fail('unsupported_control_schema')
    actions = _string_enum(control['properties']['action'], resolve)
    if not set(actions) <= set(CONTROL_ACTIONS):
        _fail('unknown_control_action')
    for field in ('reply',):
        if resolve(props[field]).get('type') != 'string':
            _fail('unsupported_envelope_string')
    for field in ('unknowns', 'conflicts'):
        node = resolve(props[field])
        if node.get('type') != 'array' or resolve(node.get('items')).get('type') != 'string':
            _fail('unsupported_envelope_array')
    acts = _string_enum(props['speech_act'], resolve)
    if not set(acts) <= {'CHANGE', 'ASK', 'PROPOSE'}:
        _fail('unknown_speech_act')
    subject = resolve(props['information_subject'])
    if 'anyOf' not in subject or {resolve(node).get('type') for node in subject['anyOf']} != {'string', 'null'}:
        _fail('unsupported_information_subject')
    return _object({'operations': {'type': 'array', 'items': {'anyOf': wire_variants}},
        'control': _object({'action': {'type': 'string', 'enum': actions}}), 'reply': {'type': 'string'},
        'unknowns': {'type': 'array', 'items': {'type': 'string'}},
        'conflicts': {'type': 'array', 'items': {'type': 'string'}},
        'speech_act': {'type': 'string', 'enum': acts}, 'information_subject': {'type': ['string', 'null']}})


def _compile(original_dynamic_schema):
    try:
        original, definitions, resolve = _prepare(original_dynamic_schema)
        return original, _wire(original, definitions, resolve), resolve
    except GroqTransportError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError, OverflowError):
        _fail('unsupported_original_schema')


def build_transport_schema(original_dynamic_schema: dict) -> dict:
    """Strict grammar for targets/envelope; opaque values stay canonically checked."""
    return _compile(original_dynamic_schema)[1]


def _validate(value, schema, stage):
    errors = []
    for error in Draft202012Validator(schema).iter_errors(value):
        errors.append(error)
        if len(errors) >= 3:
            break
    if errors:
        raise GroqTransportError('schema_validation_failed', stage, errors)


def decode_transport_response(content: str, original_dynamic_schema: dict) -> dict:
    """Decode final-answer wire text; apply no defaults, casts or trip operations."""
    # Rebuild from the same original rather than accepting a caller-supplied
    # widened grammar. No general/reference resolver can perform remote I/O.
    original, wire, resolve = _compile(original_dynamic_schema)
    parsed = _json(content, limit=MAX_JSON_BYTES, stage='transport_json')
    _validate(parsed, wire, 'transport_schema')
    if len(parsed['operations']) > resolve(original['properties']['operations'])['maxItems']:
        _fail('operation_count_limit', 'canonical_schema')
    result = {key: deepcopy(value) for key, value in parsed.items() if key != 'operations'}
    result['operations'] = []
    for operation in parsed['operations']:
        value = _json(operation['value_json'], limit=MAX_VALUE_BYTES, stage='operation_value_json')
        if type(value) is not dict:
            _fail('operation_value_object_required', 'operation_value_json')
        result['operations'].append({'type': operation['type'], 'target_id': operation['target_id'], 'value': value})
    _validate(result, original, 'canonical_schema')
    return result


def build_readonly_transport_schema() -> dict:
    """Explicit read-only server role; no operation transport or free-form reply."""
    from .answering import SECTIONS
    return _object({'sections': {'type': 'array', 'items': {'type': 'string', 'enum': list(SECTIONS)}}})


def decode_readonly_transport_response(content: str) -> dict:
    """Server-owned no-op envelope from1..8 unique, known answer section codes."""
    from .answering import READONLY_SCHEMA
    value = _json(content, limit=MAX_JSON_BYTES, stage='readonly_transport_json')
    _validate(value, build_readonly_transport_schema(), 'readonly_transport_schema')
    sections = value['sections']
    if not 1 <= len(sections) <= 8 or len(set(sections)) != len(sections):
        _fail('readonly_section_count_or_duplicates', 'readonly_transport_schema')
    result = {'operations': [], 'control': {'action': 'none'}, 'reply': ','.join(sections),
        'unknowns': [], 'conflicts': [], 'speech_act': 'ASK', 'information_subject': None}
    _validate(result, READONLY_SCHEMA, 'readonly_canonical_schema')
    return result


# Engineering bounds for the separate v2 grammar, not provider quota claims.
# Refuse an oversized codebook as a whole; never truncate its exact allowset.
MAX_COMPACT_SELECTORS = 4096


def _compact_compile(original_dynamic_schema):
    original, previous, resolve = _compile(original_dynamic_schema)
    codebook = OrderedDict()
    for variant in previous['properties']['operations']['items']['anyOf']:
        props = variant['properties']
        kind = props['type']['enum'][0]
        target = props['target_id']
        branches = target.get('anyOf', [target])
        for branch in branches:
            targets = [None] if branch.get('type') == 'null' else branch['enum']
            for target_id in targets:
                selector = kind if target_id is None else kind + '@' + target_id
                if selector in codebook:
                    _fail('compact_selector_collision')
                codebook[selector] = (kind, target_id)
                if len(codebook) > MAX_COMPACT_SELECTORS:
                    _fail('compact_selector_count_limit')
    wire = deepcopy(previous)
    wire['properties']['operations']['items'] = _object({
        'op': {'type': 'string', 'enum': list(codebook)}, 'value_json': {'type': 'string'}})
    _bounded_tree(wire, depth_limit=_MAX_SCHEMA_DEPTH, stage='transport_schema')
    if len(json.dumps(wire, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')) > MAX_SCHEMA_BYTES:
        _fail('compact_schema_byte_limit')
    return original, wire, resolve, codebook


def build_compact_transport_schema(original_dynamic_schema: dict) -> dict:
    """Separate v2 grammar: exact finite operation selectors plus opaque JSON.

    A selector is a codebook key, not a delimiter-parsed type/target expression.
    All7 envelope fields and original canonical value validation are retained.
    """
    return _compact_compile(original_dynamic_schema)[1]


def decode_compact_transport_response(content: str, original_dynamic_schema: dict) -> dict:
    """Decode only known selectors, preserving values/omission/null verbatim."""
    original, wire, resolve, codebook = _compact_compile(original_dynamic_schema)
    parsed = _json(content, limit=MAX_JSON_BYTES, stage='transport_json')
    _validate(parsed, wire, 'transport_schema')
    if len(parsed['operations']) > resolve(original['properties']['operations'])['maxItems']:
        _fail('operation_count_limit', 'canonical_schema')
    result = {key: deepcopy(value) for key, value in parsed.items() if key != 'operations'}
    result['operations'] = []
    for operation in parsed['operations']:
        pair = codebook.get(operation['op'])
        if pair is None:
            _fail('unknown_operation_selector', 'transport_schema')
        kind, target_id = pair
        value = _json(operation['value_json'], limit=MAX_VALUE_BYTES, stage='operation_value_json')
        if type(value) is not dict:
            _fail('operation_value_object_required', 'operation_value_json')
        result['operations'].append({'type': kind, 'target_id': target_id, 'value': value})
    _validate(result, original, 'canonical_schema')
    return result


# V3 is an additive opt-in extension. The complete preexisting v1/v2 source
# above stays byte-identical; default calls retain those original functions.
_LEGACY_COMPILE = _compile
_LEGACY_BUILD_TRANSPORT_SCHEMA = build_transport_schema
_LEGACY_DECODE_TRANSPORT_RESPONSE = decode_transport_response
_LEGACY_COMPACT_COMPILE = _compact_compile
_LEGACY_BUILD_COMPACT_TRANSPORT_SCHEMA = build_compact_transport_schema
_LEGACY_DECODE_COMPACT_TRANSPORT_RESPONSE = decode_compact_transport_response


def _compile_dynamic_places(original_dynamic_schema):
    original, _, resolve = _prepare(original_dynamic_schema)

    def expanded(node):
        if isinstance(node, dict):
            node = resolve(node) if '$ref' in node else node
            return {key: expanded(value) for key, value in node.items() if key != '$defs'}
        if isinstance(node, list):
            return [expanded(value) for value in node]
        return deepcopy(node)

    projected = expanded(original)
    try:
        variants = projected['properties']['operations']['items']['oneOf']
        special = [variant for variant in variants
            if variant.get('properties', {}).get('type') == {'const': 'resolve_place'}]
        if len(special) != 1:
            _fail('single_resolve_place_variant_required')
        variant = special[0]
        props = variant.get('properties', {})
        if (set(variant) != {'properties', 'required'} or variant['required'] != ['target_id']
                or set(props) != {'type', 'target_id', 'value'}
                or props['target_id'] != {'type': 'null'}
                or props['value'].get('type') != 'object'
                or props['value'].get('additionalProperties') is not False):
            _fail('bounded_resolve_place_variant_required')
        variants.remove(variant)
        # The legacy compiler checks the full unchanged envelope and every
        # other exact type/target allowset. It never learns a global new kind.
        _, wire, _ = _LEGACY_COMPILE(projected)
        wire['properties']['operations']['items']['anyOf'].append(_object({
            'type': {'type': 'string', 'enum': ['resolve_place']},
            'target_id': {'type': 'null'}, 'value_json': {'type': 'string'}}))
        return original, wire, resolve
    except GroqTransportError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError, OverflowError):
        _fail('unsupported_dynamic_place_schema')


def _compile(original_dynamic_schema, *, allow_dynamic_places=False):
    if type(allow_dynamic_places) is not bool:
        _fail('explicit_dynamic_place_flag_required')
    if not allow_dynamic_places:
        return _LEGACY_COMPILE(original_dynamic_schema)
    try:
        return _compile_dynamic_places(original_dynamic_schema)
    except GroqTransportError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError, OverflowError):
        _fail('unsupported_dynamic_place_schema')


def build_transport_schema(original_dynamic_schema: dict, *, allow_dynamic_places: bool = False) -> dict:
    if allow_dynamic_places is False:
        return _LEGACY_BUILD_TRANSPORT_SCHEMA(original_dynamic_schema)
    return _compile(original_dynamic_schema, allow_dynamic_places=allow_dynamic_places)[1]


def decode_transport_response(content: str, original_dynamic_schema: dict, *,
                              allow_dynamic_places: bool = False) -> dict:
    if allow_dynamic_places is False:
        return _LEGACY_DECODE_TRANSPORT_RESPONSE(content, original_dynamic_schema)
    original, wire, resolve = _compile(original_dynamic_schema, allow_dynamic_places=allow_dynamic_places)
    parsed = _json(content, limit=MAX_JSON_BYTES, stage='transport_json')
    _validate(parsed, wire, 'transport_schema')
    if len(parsed['operations']) > resolve(original['properties']['operations'])['maxItems']:
        _fail('operation_count_limit', 'canonical_schema')
    result = {key: deepcopy(value) for key, value in parsed.items() if key != 'operations'}
    result['operations'] = []
    for operation in parsed['operations']:
        value = _json(operation['value_json'], limit=MAX_VALUE_BYTES, stage='operation_value_json')
        if type(value) is not dict:
            _fail('operation_value_object_required', 'operation_value_json')
        result['operations'].append({'type': operation['type'], 'target_id': operation['target_id'], 'value': value})
    _validate(result, original, 'canonical_schema')
    return result


def _compact_compile(original_dynamic_schema, *, allow_dynamic_places=False):
    if allow_dynamic_places is False:
        return _LEGACY_COMPACT_COMPILE(original_dynamic_schema)
    original, previous, resolve = _compile(original_dynamic_schema, allow_dynamic_places=allow_dynamic_places)
    codebook = OrderedDict()
    for variant in previous['properties']['operations']['items']['anyOf']:
        props = variant['properties']
        kind = props['type']['enum'][0]
        target = props['target_id']
        for branch in target.get('anyOf', [target]):
            for target_id in [None] if branch.get('type') == 'null' else branch['enum']:
                selector = kind if target_id is None else kind + '@' + target_id
                if selector in codebook:
                    _fail('compact_selector_collision')
                codebook[selector] = (kind, target_id)
                if len(codebook) > MAX_COMPACT_SELECTORS:
                    _fail('compact_selector_count_limit')
    wire = deepcopy(previous)
    wire['properties']['operations']['items'] = _object({
        'op': {'type': 'string', 'enum': list(codebook)}, 'value_json': {'type': 'string'}})
    _bounded_tree(wire, depth_limit=_MAX_SCHEMA_DEPTH, stage='transport_schema')
    if len(json.dumps(wire, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()) > MAX_SCHEMA_BYTES:
        _fail('compact_schema_byte_limit')
    return original, wire, resolve, codebook


def build_compact_transport_schema(original_dynamic_schema: dict, *,
                                   allow_dynamic_places: bool = False) -> dict:
    if allow_dynamic_places is False:
        return _LEGACY_BUILD_COMPACT_TRANSPORT_SCHEMA(original_dynamic_schema)
    return _compact_compile(original_dynamic_schema, allow_dynamic_places=allow_dynamic_places)[1]


def decode_compact_transport_response(content: str, original_dynamic_schema: dict, *,
                                      allow_dynamic_places: bool = False) -> dict:
    if allow_dynamic_places is False:
        return _LEGACY_DECODE_COMPACT_TRANSPORT_RESPONSE(content, original_dynamic_schema)
    original, wire, resolve, codebook = _compact_compile(original_dynamic_schema,
        allow_dynamic_places=allow_dynamic_places)
    parsed = _json(content, limit=MAX_JSON_BYTES, stage='transport_json')
    _validate(parsed, wire, 'transport_schema')
    if len(parsed['operations']) > resolve(original['properties']['operations'])['maxItems']:
        _fail('operation_count_limit', 'canonical_schema')
    result = {key: deepcopy(value) for key, value in parsed.items() if key != 'operations'}
    result['operations'] = []
    for operation in parsed['operations']:
        pair = codebook.get(operation['op'])
        if pair is None:
            _fail('unknown_operation_selector', 'transport_schema')
        kind, target_id = pair
        value = _json(operation['value_json'], limit=MAX_VALUE_BYTES, stage='operation_value_json')
        if type(value) is not dict:
            _fail('operation_value_object_required', 'operation_value_json')
        result['operations'].append({'type': kind, 'target_id': target_id, 'value': value})
    _validate(result, original, 'canonical_schema')
    return result
