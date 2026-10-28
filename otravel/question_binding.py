"""Respect code-owned proposal provenance; repair only exact legacy questions."""
import unicodedata
from copy import deepcopy

from .clarifications import derive_pending_field, human_question, question_field

_ADDRESS_KEYS = {'field', 'target_id', 'traveler_id', 'stay_id', 'leg_id', 'group_id'}
_MAX_FIELD_CHARS = 128
_MAX_QUESTION_CHARS = 2048


def _valid_field(value):
    return (isinstance(value, str) and 0 < len(value) <= _MAX_FIELD_CHARS
        and value.strip() == value and not any(ord(char) < 32 or ord(char) == 127 for char in value))


def _normalized_question(value):
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().replace('ё', 'е').split())


def final_question_binding(questions, state, context=None, proposal=None):
    """An explicit server proposal owns its field/target, not its wording.

    The caller must discard a consumed proposal when a later search/validator
    creates a NEW question. Only then may the fresh wording derive a binding.
    Extended internal fields unknown to the generic classifier are preserved.
    """
    context = deepcopy(context) if isinstance(context, dict) else {}
    proposal = proposal if isinstance(proposal, dict) else {}
    raw = proposal.get('field')
    if _valid_field(raw):
        return {'field': question_field({'field': raw}) or raw,
            'target_id': deepcopy(proposal.get('target_id'))}
    questions = questions if isinstance(questions, (list, tuple)) else []
    displayed = question_field(questions[0]) if questions else None
    if displayed:
        # A new global nationality/budget/party question cannot inherit the
        # hotel/flight target of an already consumed date or room answer.
        context = {key:value for key,value in context.items() if key not in _ADDRESS_KEYS}
    return derive_pending_field([{**context, 'text':question} for question in questions], state)


def repair_question_binding(pending, state):
    """Repair a known legacy slot only for exact canonical rendered wording.

    A generic classifier is insufficient evidence to replace a server-owned
    date/group/room proposal. Unknown extended fields remain untouched.
    """
    if not isinstance(pending, dict):
        return pending
    text = pending.get('text')
    if not isinstance(text, str) or not 0 < len(text) <= _MAX_QUESTION_CHARS:
        return pending
    displayed = question_field(text)
    recorded = question_field({'field':pending.get('field')})
    if not displayed or not recorded or displayed == recorded:
        return pending
    original_context = pending.get('context')
    context = {key:deepcopy(value) for key,value in (original_context.items() if isinstance(original_context, dict) else [])
               if key not in _ADDRESS_KEYS}
    binding = derive_pending_field([{**context, 'text':text}], state)
    if not binding.get('field'):
        return pending
    canonical = human_question({'field': binding['field'], 'target_id': binding.get('target_id'),
        'context': context}, state)
    if _normalized_question(text) != _normalized_question(canonical):
        return pending
    return {**deepcopy(pending), 'field':binding['field'], 'target_id':binding.get('target_id'), 'context':context}
