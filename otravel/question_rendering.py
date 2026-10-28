"""Render an untrusted question using its already derived canonical address."""
from copy import deepcopy

from .clarifications import derive_pending_field, human_question, question_field


def render_untrusted_question(raw, state, context=None, *, binding=None):
    """Keep wording and bound field aligned without inferring any answer.

    Server-owned/trusted questions remain outside this helper. A known canonical
    binding may change during derivation (adult count -> missing child count,
    flight date -> the only unresolved hotel date); context alone cannot change
    ``human_question``'s field parsed from the original wording.
    """
    extra = deepcopy(context) if isinstance(context, dict) else {}
    packet = {**extra, **(deepcopy(raw) if isinstance(raw, dict) else {"text": raw})}
    bound = deepcopy(binding) if isinstance(binding, dict) else derive_pending_field([packet], state)
    field = bound.get("field")
    if isinstance(field, str) and question_field({"field": field}) == field:
        packet["field"] = field
        packet["target_id"] = bound.get("target_id")
    return human_question(packet, state, {**extra, **bound})
