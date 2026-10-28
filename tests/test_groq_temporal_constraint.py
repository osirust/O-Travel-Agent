"""Saved-only temporal key: strict Groq guidance, no model/source calls."""
from copy import deepcopy
import json

from jsonschema import Draft202012Validator
import pytest

from otravel.domain import Constraint,sample_trip
from otravel.groq_prompt import build_compact_operation_hints
from otravel.groq_transport import GroqTransportError
from otravel.llm_contract import build_operation_schema
from test_groq_prompt import unintern,variants

KEY='free_cancel_until_checkin'

def proposal(key=KEY,value=True,stay_id=None,room_id=None):
    fields={'key':key,'value':value}
    if stay_id is not None:fields['stay_id']=stay_id
    if room_id is not None:fields['room_id']=room_id
    return {'operations':[{'type':'set_constraint','value':fields}],
        'control':{'action':'continue'},'reply':'','unknowns':[],'conflicts':[]}


def test_fresh_temporal_requirement_is_server_only_and_old_hints_unchanged():
    schema=build_operation_schema(sample_trip());text,meta=build_compact_operation_hints(schema)
    assert not Draft202012Validator(schema).is_valid(proposal())
    assert KEY not in text and KEY not in json.dumps(schema)
    assert meta['server_original_schema_required'] is True


@pytest.mark.parametrize('legacy',[False,True])
def test_saved_temporal_strict_boolean_is_explicit_in_reviewed_groq_hints(legacy):
    state=sample_trip();before=build_operation_schema(state)
    state.constraints.append(Constraint(key=KEY,value=True))
    if legacy:state.constraints.append(Constraint(key='legacy-known',value=None))
    schema=build_operation_schema(state);text,meta=build_compact_operation_hints(schema)
    assert KEY+'|pets_allowed' in text and 'not supplier refund evidence' in text
    validator=Draft202012Validator(schema)
    assert validator.is_valid(proposal()) and not validator.is_valid(proposal(value='true'))
    assert not validator.is_valid(proposal(key='invented-new-constraint'))
    assert meta['server_original_schema_required'] and meta['wire_exact_type_targets_required']
    if not legacy:
        encode=lambda s:len(json.dumps(s,ensure_ascii=False,separators=(',',':')).encode())
        assert 0<encode(schema)-encode(before)<=150


def test_saved_temporal_room_is_typed_but_foreign_ids_and_extra_keys_still_fail():
    state=sample_trip();stay=state.stays[0];room=stay.rooms[0]
    state.constraints.append(Constraint(key=KEY,value=True,stay_id=stay.id,room_id=room.id))
    schema=build_operation_schema(state);build_compact_operation_hints(schema);validator=Draft202012Validator(schema)
    assert validator.is_valid(proposal(stay_id=stay.id,room_id=room.id))
    assert not validator.is_valid(proposal(stay_id='foreign-stay',room_id=room.id))
    assert not validator.is_valid(proposal(stay_id=stay.id,room_id='foreign-room'))
    raw=proposal();raw['operations'][0]['value']['ignore_validation']=True
    assert not validator.is_valid(raw)


@pytest.mark.parametrize('mutation',['extra-property','arbitrary-bool-key','string-value','relax-flight-scope'])
def test_saved_temporal_shape_review_does_not_allow_nearby_grammar_widening(mutation):
    state=sample_trip();state.constraints.append(Constraint(key=KEY,value=True))
    schema=unintern(build_operation_schema(state));value=variants(schema,'set_constraint')[0]['properties']['value']
    if mutation=='extra-property':value['properties']['invented']={'type':'boolean'}
    elif mutation=='arbitrary-bool-key':value['oneOf'][0]['properties']['key']['enum'].append('invented')
    elif mutation=='string-value':value['oneOf'][0]['properties']['value']['type']=['boolean','string']
    else:value['allOf'][1]['then']['properties']['stay_id']={'type':['null','string']}
    with pytest.raises(GroqTransportError):build_compact_operation_hints(schema)


def test_temporal_key_cannot_hide_in_legacy_scalar_branch():
    state=sample_trip();state.constraints.append(Constraint(key='legacy-known',value=None))
    schema=unintern(build_operation_schema(state));value=variants(schema,'set_constraint')[0]['properties']['value']
    value['oneOf'][-1]['properties']['key']['enum']=[KEY]
    with pytest.raises(GroqTransportError):build_compact_operation_hints(schema)
