"""Captured R17 family-count admission through real isolated PG, fake LLM/API."""
import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_api import StubLLM, _plans, _session
from test_duration_grounding_pg import message
from test_family_ordinal_count import corrected_ops
from test_return_preservation_pg import return_api  # noqa: F401

FIXTURE = Path(__file__).parents[1] / 'fixtures/regression/family-ordinal-count-076-20261028.json'


@pytest.mark.asyncio
async def test_actual076_one_group_for_asserted_two_families_blocks_entire_batch_before_commit_and_search(return_api):  # noqa: F811
    fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
    model = StubLLM([fixture['raw_contract']])
    client, store = await return_api(model)
    sid, token, initial = await _session(client, fixture['before_trip'])
    before = deepcopy(initial['trip'])
    run, after = await message(client, sid, token, fixture['input'])
    assert run['status'] == 'completed' and not run.get('error'), run.get('error')
    assert after['trip'] == before
    assert after['state_version'] == initial['state_version']
    assert after['pending_question']['field'] == 'group.membership'
    assert after['pending_question']['target_id'] is None
    assert 'две' in after['pending_question']['text'].casefold()
    assert not _plans(run) and len(model.calls) == 1
    events = await store.list_events(sid)
    assert not any(event['event_type'] == 'TOOL_CALL_STARTED' for event in events)
    assert await store.list_orders(sid) == []


@pytest.mark.asyncio
async def test_actual076_correct_two_families_preserves_model_membership_and_room_allocation(return_api):  # noqa: F811
    fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
    contract = deepcopy(fixture['raw_contract'])
    contract['operations'] = corrected_ops(fixture)
    model = StubLLM([contract])
    client, store = await return_api(model)
    sid, token, initial = await _session(client, fixture['before_trip'])
    run, after = await message(client, sid, token, fixture['input'])
    assert run['status'] == 'completed' and not run.get('error'), run.get('error')
    assert after['state_version'] == initial['state_version'] + 1
    groups = after['trip']['groups']
    assert len(groups) == 2 and all(group['keep_separate'] for group in groups)
    assert [group['traveler_ids'] for group in groups] == [op['value']['traveler_ids'] for op in contract['operations'][3:]]
    assert [room['traveler_ids'] for room in after['trip']['stays'][0]['rooms']] == [op['value']['traveler_ids'] for op in contract['operations'][:3]]
    assert len(model.calls) == 1 and await store.list_orders(sid) == []


@pytest.mark.asyncio
async def test_existing_cardinal_family_count_keeps_safe_empty_membership_intake(return_api):  # noqa: F811
    fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
    model = StubLLM([fixture['raw_contract']])
    client, store = await return_api(model)
    sid, token, initial = await _session(client, fixture['before_trip'])
    run, after = await message(client, sid, token, 'Две семьи, семьи не смешивай.')
    assert run['status'] == 'completed' and not run.get('error'), run.get('error')
    assert after['state_version'] == initial['state_version'] + 1
    assert len(after['trip']['groups']) == 2 and after['trip']['groups'][1]['traveler_ids'] == []
    assert after['pending_question']['field'] == 'group.membership'
    assert not _plans(run) and len(model.calls) == 1
    assert await store.list_orders(sid) == []
