"""Remove only exact repeated information; keep authority, prices and ordering."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from otravel.config import Settings
from otravel.domain import sample_trip
from otravel.harness import Harness, _distinct_plan_batches


def batch(run='latest', price=100, hotel='hotel', reverse=False):
    offers = [{'offer_id': 'o1', 'hotel_id': hotel, 'kind': 'hotel', 'name': 'Hotel', 'total_minor': 80},
        {'offer_id': 'o2', 'kind': 'flight', 'total_minor': 20}]
    return {'run_id': run, 'plans': [{'plan_id': 'plan', 'state_version': 1,
        'total_minor': price, 'currency': 'RUB', 'offers': list(reversed(offers)) if reverse else offers}]}


def test_exact_dto_duplicate_keeps_newest_reference_and_does_not_mutate_source():
    source = [batch(), batch('earlier')]
    before = deepcopy(source)
    projected = _distinct_plan_batches(source)
    assert projected == [source[0]] and source == before
    projected[0]['plans'][0]['offers'][0]['name'] = 'Changed caller copy'
    assert source == before


@pytest.mark.parametrize('other', [batch('older', price=101), batch('older', hotel='another'),
    batch('older', reverse=True), {**batch('older'), 'source_timestamp': 'different'}])
def test_changed_price_identity_order_or_unknown_metadata_never_collapse(other):
    rows = [batch(), other]
    assert _distinct_plan_batches(rows) == rows


@pytest.mark.asyncio
async def test_only_actual_input_id_is_removed_from_the_same_last_twelve_without_backfill():
    state = sample_trip().model_dump(mode='json')
    session = {'session_id': 'own', 'trip_id': state['trip_id'], 'state_version': state['state_version'],
        'state': state, 'pending_question': {'field': 'stay.rooms', 'text': 'Сколько номеров?'}}
    messages = [{'id': f'm{i}', 'role': 'user', 'text': 'Та же фраза' if i in {11, 12} else f'Prior{i}',
        'sequence': i, 'run_id': None, 'session_id': 'own'} for i in range(13)]
    class Store:
        async def list_messages(self, _):
            return deepcopy(messages)

        async def get_session(self, _):
            return deepcopy(session)

        async def current_selected_plan(self, *_args):
            return None
    h = Harness(Settings('unused', 'groq', 1, 8000, 40, 1), Store(), SimpleNamespace(), SimpleNamespace())
    ctx = await h._extraction_context(session, {'input_message_id': 'm12', 'input_text': 'Та же фраза'})
    assert ctx['current_message'] == 'Та же фраза'
    assert len(ctx['recent_messages']) == 11
    assert ctx['recent_messages'][0]['text'] == 'Prior1'  # m0 is not backfilled.
    assert ctx['recent_messages'][-1]['text'] == 'Та же фраза'  # Distinct prior m11 stays.
    assert ctx['trip'] == state and ctx['pending_question'] == session['pending_question']
