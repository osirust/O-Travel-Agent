"""Bounded server-schema diagnostics; never alters or accepts model output."""
from copy import deepcopy

from jsonschema import Draft202012Validator


def _branch_matches(schema, instance):
    if not isinstance(schema, dict):
        return True
    if 'const' in schema:
        return instance == schema['const']
    if 'enum' in schema:
        return instance in schema['enum']
    if 'type' in schema and not Draft202012Validator({'type': schema['type']}).is_valid(instance):
        return False
    if isinstance(instance, dict):
        for field, field_schema in schema.get('properties', {}).items():
            if (field in instance and ('const' in field_schema or 'enum' in field_schema)
                    and not _branch_matches(field_schema, instance[field])):
                return False
    return True


def _narrow(schema, instance):
    if not isinstance(schema, dict):
        return deepcopy(schema)
    result = deepcopy(schema)
    branches = schema.get('oneOf')
    if isinstance(branches, list):
        matching = [branch for branch in branches if _branch_matches(branch, instance)]
        if len(matching) == 1:
            result.pop('oneOf', None)
            result.setdefault('allOf', []).append(_narrow(matching[0], instance))
    if isinstance(instance, dict) and isinstance(result.get('properties'), dict):
        result['properties'] = {field: _narrow(field_schema, instance.get(field))
                                for field, field_schema in result['properties'].items()}
    if isinstance(result.get('allOf'), list):
        result['allOf'] = [_narrow(branch, instance) for branch in result['allOf']]
    return result


def operation_schema_feedback(value: dict, schema: dict, *, limit: int = 3) -> list[dict]:
    """Inspect only matching operation branches to explain a oneOf failure.

    Full original validation remains the caller's decision. Expected values are
    server-owned schema literals; actual user/model values are never copied.
    """
    if not isinstance(value, dict) or not isinstance(schema, dict):
        return []
    item_schema = schema.get('properties', {}).get('operations', {}).get('items', {})
    variants = item_schema.get('oneOf') if isinstance(item_schema, dict) else None
    operations = value.get('operations')
    if not isinstance(variants, list) or not isinstance(operations, list):
        return []
    result = []
    for index, operation in enumerate(operations[:64]):
        if not isinstance(operation, dict):
            continue
        matching = [variant for variant in variants if
                    variant.get('properties', {}).get('type', {}).get('const') == operation.get('type')]
        if len(matching) != 1:
            continue
        diagnostic = _narrow(matching[0], operation)
        if isinstance(schema.get('$defs'), dict):
            diagnostic['$defs'] = deepcopy(schema['$defs'])
        for error in Draft202012Validator(diagnostic).iter_errors(operation):
            if error.validator in {'oneOf', 'anyOf'}:
                continue
            entry = {'path': ['operations', index, *list(error.absolute_path)], 'rule': error.validator}
            expected = error.validator_value
            if error.validator == 'type':
                entry['expected_type'] = expected
            elif error.validator == 'const' and (expected is None or type(expected) in {bool, int}
                    or isinstance(expected, str) and len(expected) <= 80):
                entry['expected_const'] = expected
            elif error.validator == 'enum' and isinstance(expected, list):
                entry['allowed_values'] = [item for item in expected[:8]
                    if item is None or type(item) in {bool, int} or isinstance(item, str) and len(item) <= 80]
            elif error.validator == 'required' and isinstance(expected, list):
                entry['required_fields'] = expected[:8]
            result.append(entry)
            if len(result) >= max(1, min(3, limit)):
                return result
    return result
