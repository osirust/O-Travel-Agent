"""Representation-only room alias repair; guest membership is never inferred.

Only complete bijections onto an existing stay's room IDs qualify. These are
traveller-allocation identities, not evidence of supplier room types or stock.
"""
from collections import Counter
from copy import deepcopy

from .domain import Operation, TripState


def _rooms(prior: list[dict], proposed: list[dict], existing_people: set[str]) -> tuple[list[dict], list[dict]] | None:
    if not prior or not isinstance(proposed, list) or len(prior) != len(proposed):
        return None
    if any(not isinstance(room, dict) or not isinstance(room.get('id'), str)
           or not isinstance(room.get('traveler_ids'), list) for room in proposed):
        return None
    old_ids = [room['id'] for room in prior]
    new_ids = [room['id'] for room in proposed]
    if len(set(old_ids)) != len(old_ids) or len(set(new_ids)) != len(new_ids):
        return None
    old_members = [person for room in prior for person in room['traveler_ids']]
    all_members = [person for room in proposed for person in room['traveler_ids']]
    if any(not isinstance(person, str) for person in all_members):
        return None
    # No dropped, duplicated or moved-in existing traveller can be disguised as
    # an ID repair. Added travellers remain exactly where the model placed them.
    if (len(set(old_members)) != len(old_members) or len(set(all_members)) != len(all_members)
            or Counter(person for person in all_members if person in existing_people) != Counter(old_members)):
        return None
    retained = set(new_ids).intersection(old_ids)
    if retained == set(old_ids):
        # A fully valid setter may express a deliberate ordinal/guest change.
        # Without an unknown alias there is no representation repair to prove.
        return None
    used = set(retained)
    replacements = []
    rewritten = deepcopy(proposed)
    for room in rewritten:
        if room['id'] in retained:
            continue  # Valid existing IDs never receive inferred memberships.
        anchors = set(room['traveler_ids']).intersection(existing_people)
        candidates = [old for old in prior if old['id'] not in used
                      and anchors and set(old['traveler_ids']) == anchors]
        if len(candidates) != 1:
            return None  # Keep the entire original setter on a partial match.
        old_id = candidates[0]['id']
        replacements.append({'proposed_id': room['id'], 'canonical_id': old_id,
                             'existing_traveler_ids': sorted(anchors)})
        room['id'] = old_id
        used.add(old_id)
    if used != set(old_ids):
        return None
    indexed = {room['id']: room for room in rewritten}
    return [indexed[identifier] for identifier in old_ids], replacements


def normalize_room_identities(state: TripState | dict, operations: list) -> tuple[list, list[dict]]:
    """Copy-only LLM alias repair before conflict/domain preflight.

    It never creates rooms, places people, changes a group, clears a constraint,
    or interprets an ordinal. Existing guest sets uniquely identify a room only
    when the complete original partition survives in the proposed same-count
    setter. Explicit valid-ID swaps retain their proposed guest assignments.
    """
    original = deepcopy(operations)
    state = TripState.model_validate(state)
    prior = {stay.id: stay.model_dump(mode='json') for stay in state.stays}
    people = {person.id for person in state.travelers}
    setters: dict[str, list[int]] = {}
    for index, operation in enumerate(original):
        kind = operation.type if isinstance(operation, Operation) else operation.get('type')
        value = operation.value if isinstance(operation, Operation) else operation.get('value')
        target = operation.target_id if isinstance(operation, Operation) else operation.get('target_id')
        if kind == 'upsert_stay' and isinstance(value, dict) and 'rooms' in value and target in prior:
            setters.setdefault(target, []).append(index)
    result, notes = deepcopy(original), []
    for target, indices in setters.items():
        if len(indices) != 1:
            continue  # Conflicting setters belong to the unchanged conflict gate.
        index = indices[0]
        operation = result[index]
        value = operation.value if isinstance(operation, Operation) else operation['value']
        saved = prior[target]
        if 'city' in value and value['city'] != saved['city']:
            continue
        mapped = _rooms(saved['rooms'], value['rooms'], people)
        if mapped is None:
            continue
        rooms, aliases = mapped
        if rooms == value['rooms']:
            continue
        value['rooms'] = rooms
        notes.append({'code': 'existing_room_aliases_normalized', 'target_id': target,
                      'source': 'canonical_saved_room_membership', 'aliases': aliases,
                      'guest_membership_inferred': False, 'canonical_room_order_restored': True})
    return result, notes
