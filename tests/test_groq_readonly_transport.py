"""Pure readonly transport contracts; no gateway, HTTP, database or Native calls."""

import json
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from otravel.answering import READONLY_SCHEMA, SECTIONS
from otravel.groq_transport import (
    MAX_JSON_BYTES,
    GroqTransportError,
    build_readonly_transport_schema,
    decode_readonly_transport_response,
)

SECTION_CODES = (
    "party", "budget", "dates", "route", "rooms", "preferences", "source", "options",
    "rates", "orders", "changes", "missing", "hypothesis", "geography", "unknown",
)


def _wire(sections):
    return json.dumps({"sections": sections}, ensure_ascii=False)


def _canonical(sections):
    return {
        "operations": [],
        "control": {"action": "none"},
        "reply": ",".join(sections),
        "unknowns": [],
        "conflicts": [],
        "speech_act": "ASK",
        "information_subject": None,
    }


def test_readonly_transport_schema_exposes_only_required_section_codes():
    schema = build_readonly_transport_schema()
    assert tuple(SECTIONS) == SECTION_CODES
    assert schema == {
        "type": "object",
        "additionalProperties": False,
        "required": ["sections"],
        "properties": {
            "sections": {
                "type": "array",
                "items": {"type": "string", "enum": list(SECTION_CODES)},
            },
        },
    }
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("sections", [[], ["party", "party"], list(SECTION_CODES[:9])],
                         ids=["empty", "duplicates", "nine-sections"])
def test_readonly_transport_bounds_and_uniqueness_are_local_checks(sections):
    # The provider sees the small enum schema. These constraints belong to decoding.
    Draft202012Validator(build_readonly_transport_schema()).validate({"sections": sections})
    with pytest.raises(GroqTransportError):
        decode_readonly_transport_response(_wire(sections))


@pytest.mark.parametrize("section", SECTION_CODES)
def test_readonly_transport_accepts_each_section_and_validates_canonical_contract(section):
    result = decode_readonly_transport_response(_wire([section]))
    assert result == _canonical([section])
    Draft202012Validator(READONLY_SCHEMA).validate(result)


@pytest.mark.parametrize("sections", [
    ["budget", "party"],
    ["unknown", "geography", "hypothesis", "missing", "changes", "orders", "rates", "options"],
    list(SECTION_CODES[:8]),
], ids=["preserved-order", "eight-reverse", "eight-forward"])
def test_readonly_transport_preserves_requested_order_through_eight_sections(sections):
    source = {"sections": deepcopy(sections)}
    before = deepcopy(source)
    content = json.dumps(source)
    result = decode_readonly_transport_response(content)
    assert source == before
    assert content == json.dumps(before)
    assert result == _canonical(sections)
    Draft202012Validator(READONLY_SCHEMA).validate(result)


def test_readonly_transport_accepts_json_whitespace_and_escaped_strings_without_normalizing_codes():
    content = ' \n\t { "sections" : ["\\u0062udget", "party"] } \r\n '
    assert decode_readonly_transport_response(content) == _canonical(["budget", "party"])


@pytest.mark.parametrize("sections", [
    None, "party", {"0": "party"}, 0, 1, 1.0, False, True,
], ids=["null", "string", "object", "zero", "one", "float", "false", "true"])
def test_readonly_transport_requires_an_array_without_coercion(sections):
    with pytest.raises(GroqTransportError):
        decode_readonly_transport_response(_wire(sections))


@pytest.mark.parametrize("section", [
    "", "Party", "PARTY", " party", "party ", "party\n", "party,budget", "party, budget",
    "travellers", "состав", "not-a-section", "__proto__",
], ids=["empty", "capitalized", "uppercase", "leading-space", "trailing-space", "newline",
        "comma-list", "spaced-comma-list", "synonym", "translated", "unknown", "object-key"])
def test_readonly_transport_rejects_unknown_or_nonliteral_section_codes(section):
    with pytest.raises(GroqTransportError):
        decode_readonly_transport_response(_wire([section]))


@pytest.mark.parametrize("section", [None, 0, 1, 1.0, False, True, [], ["party"], {}, {"code": "party"}],
                         ids=["null", "zero", "one", "float", "false", "true", "empty-array",
                              "nested-array", "empty-object", "nested-object"])
def test_readonly_transport_rejects_nonstring_array_members(section):
    with pytest.raises(GroqTransportError):
        decode_readonly_transport_response(_wire(["budget", section]))


@pytest.mark.parametrize("extra", [
    {"operations": []},
    {"operations": [{"type": "set_budget", "value": {"amount": 1, "currency": "RUB"}}]},
    {"control": {"action": "none"}},
    {"control": {"action": "search"}},
    {"reply": "party"},
    {"unknowns": []},
    {"conflicts": []},
    {"speech_act": "ASK"},
    {"information_subject": None},
    {"section": "party"},
    {"__proto__": {"operations": []}},
], ids=["empty-operations", "mutation", "none-control", "search-control", "reply", "unknowns",
        "conflicts", "speech-act", "information-subject", "section-alias", "prototype-key"])
def test_readonly_transport_rejects_extra_fields_including_mutation_envelopes(extra):
    content = json.dumps({"sections": ["party"], **extra})
    with pytest.raises(GroqTransportError):
        decode_readonly_transport_response(content)


@pytest.mark.parametrize("payload", [{}, {"section": ["party"]}, {"operations": []}, _canonical(["party"])],
                         ids=["empty-object", "section-alias", "missing-sections", "canonical-envelope"])
def test_readonly_transport_does_not_default_missing_sections_or_accept_canonical_input(payload):
    with pytest.raises(GroqTransportError):
        decode_readonly_transport_response(json.dumps(payload))


@pytest.mark.parametrize("content", [
    '{"sections":["party"],"sections":["party"]}',
    '{"sections":["party"],"sections":["budget"]}',
    '{"sections":["party"],"\\u0073ections":["budget"]}',
    '{"sections":["party"],"sections":null}',
], ids=["same-value", "different-value", "escaped-key", "overwriting-null"])
def test_readonly_transport_rejects_duplicate_json_object_keys(content):
    with pytest.raises(GroqTransportError):
        decode_readonly_transport_response(content)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize("template", ["{}", '{{"sections":[{}]}}', '{{"sections":["party"],"extra":{}}}'],
                         ids=["root", "array-member", "extra-value"])
def test_readonly_transport_rejects_nonfinite_json_constants(constant, template):
    with pytest.raises(GroqTransportError):
        decode_readonly_transport_response(template.format(constant))


@pytest.mark.parametrize("content", [
    None, b'{"sections":["party"]}', bytearray(b'{"sections":["party"]}'),
    {"sections": ["party"]}, ["party"], 0, 1.5, False, True,
], ids=["none", "bytes", "bytearray", "object", "array", "int", "float", "false", "true"])
def test_readonly_transport_rejects_nonstring_content(content):
    with pytest.raises(GroqTransportError):
        decode_readonly_transport_response(content)


@pytest.mark.parametrize("content", [
    "", " \n\t ", "party", '"party"', "null", "false", "1", "[]", '["party"]',
    '{"sections":', '{"sections":["party"]', '{"sections":["party"],}',
    "{'sections':['party']}", '```json\n{"sections":["party"]}\n```',
    '{"sections":["party"]}{"sections":["budget"]}',
    '{"sections":["party"]} explanation', '/* comment */ {"sections":["party"]}',
    '\ufeff{"sections":["party"]}',
], ids=["empty", "whitespace", "plain-text", "json-string", "null", "bool", "number", "array",
        "array-of-codes", "truncated-value", "truncated-object", "trailing-comma", "single-quotes",
        "markdown", "two-objects", "trailing-text", "comment", "bom"])
def test_readonly_transport_rejects_malformed_json_or_nonobject_roots(content):
    with pytest.raises(GroqTransportError):
        decode_readonly_transport_response(content)


@pytest.mark.parametrize("content", [
    "[" * 2000 + '"party"' + "]" * 2000,
    '{"sections":' + "[" * 2000 + '"party"' + "]" * 2000 + "}",
], ids=["root-depth", "sections-depth"])
def test_readonly_transport_wraps_excessive_json_depth_in_transport_error(content):
    with pytest.raises(GroqTransportError):
        decode_readonly_transport_response(content)


@pytest.mark.parametrize("padding", ["before", "after"])
def test_readonly_transport_rejects_oversized_otherwise_valid_response(padding):
    valid = _wire(["party"])
    content = " " * (1024 * 1024) + valid if padding == "before" else valid + " " * (1024 * 1024)
    with pytest.raises(GroqTransportError):
        decode_readonly_transport_response(content)


@pytest.mark.parametrize("padding", ["before", "after"])
def test_readonly_transport_enforces_the_json_byte_limit_boundary(padding):
    valid = _wire(["party"])
    spaces = " " * (MAX_JSON_BYTES - len(valid.encode("utf-8")))
    at_limit = spaces + valid if padding == "before" else valid + spaces
    assert len(at_limit.encode("utf-8")) == MAX_JSON_BYTES
    assert decode_readonly_transport_response(at_limit) == _canonical(["party"])
    with pytest.raises(GroqTransportError):
        decode_readonly_transport_response(at_limit + " ")


@pytest.mark.parametrize("content", [
    '{"sections":["party"]}' + chr(0xD800),
    '{"sections":["\\ud800"]}',
    '{"sections":["\\udfff"]}',
], ids=["raw-surrogate", "escaped-high-surrogate", "escaped-low-surrogate"])
def test_readonly_transport_rejects_unpaired_unicode_surrogates(content):
    with pytest.raises(GroqTransportError):
        decode_readonly_transport_response(content)


def test_readonly_transport_error_diagnostics_do_not_echo_rejected_content():
    marker = "synthetic-private-section-do-not-echo"
    with pytest.raises(GroqTransportError) as error:
        decode_readonly_transport_response(_wire([marker]))
    diagnostic = json.dumps({"message": str(error.value), "details": error.value.details})
    assert marker not in diagnostic


def test_readonly_transport_error_is_a_value_error():
    assert issubclass(GroqTransportError, ValueError)


def test_readonly_transport_schema_builds_independent_mutable_trees_without_source_mutation():
    source_schema = deepcopy(READONLY_SCHEMA)
    source_sections = tuple(SECTIONS)
    first = build_readonly_transport_schema()
    expected = deepcopy(first)
    second = build_readonly_transport_schema()

    first["required"].append("operations")
    first["properties"]["sections"]["items"]["enum"].clear()
    first["properties"]["sections"]["items"]["type"] = "number"
    first["properties"]["operations"] = {"type": "array"}

    assert second == expected
    assert build_readonly_transport_schema() == expected
    assert READONLY_SCHEMA == source_schema
    assert tuple(SECTIONS) == source_sections


def test_readonly_transport_decodes_independent_envelopes_without_source_mutation():
    source_schema = deepcopy(READONLY_SCHEMA)
    source_sections = tuple(SECTIONS)
    content = _wire(["party", "budget"])
    unchanged_content = content
    first = decode_readonly_transport_response(content)
    second = decode_readonly_transport_response(content)

    first["operations"].append({"type": "set_budget"})
    first["control"]["action"] = "search"
    first["unknowns"].append("injected")
    first["conflicts"].append("injected")

    assert second == _canonical(["party", "budget"])
    assert decode_readonly_transport_response(content) == second
    assert content == unchanged_content
    assert READONLY_SCHEMA == source_schema
    assert tuple(SECTIONS) == source_sections
