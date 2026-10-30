"""Control-only revisions preserve fresh questions, never revive stale packets."""
from copy import deepcopy

import pytest

from otravel.store import _pending_after_control


def row(owner='date_slots.v1', trip='trip', based=3, question_version=3):
    return {'trip_id': 'trip', 'state_version': 3, 'pending_question': {
        'field': 'stay.check_out', 'state_version': question_version, 'text': 'Уточните остановку.',
        'context': {'date_proposal': {'owner': owner, 'trip_id': trip, 'based_state_version': based},
                    'requested_destination': {'kind': 'country', 'name': 'Сербия', 'country_code': 'RS'}}}}


def test_control_rebind_is_copy_only_and_fresh_exact_version():
    original = row()
    before = deepcopy(original)
    updated = _pending_after_control(original, 4)
    assert updated['state_version'] == updated['context']['date_proposal']['based_state_version'] == 4
    assert updated['context']['requested_destination']['country_code'] == 'RS' and original == before


@pytest.mark.parametrize('question_version', [None, True, 2, 4, '3'])
def test_stale_or_unbound_question_is_not_resurrected_by_a_control(question_version):
    assert _pending_after_control(row(question_version=question_version), 4) is None


@pytest.mark.parametrize('change', [{'owner': 'untrusted'}, {'trip': 'another-trip'}, {'based': 2}, {'based': True}])
def test_stale_unknown_or_other_trip_packet_is_not_rebased(change):
    original = row(**change)
    updated = _pending_after_control(original, 4)
    assert updated['context']['date_proposal']['based_state_version'] == original['pending_question']['context']['date_proposal']['based_state_version']
