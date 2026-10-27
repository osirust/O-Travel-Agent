"""Cached exact BULK inputs through controlled gateway/real Store and API.

Preferences reply is deliberately replaced with a wrong-date canary to require
canonical facts, never trusted model prose. No new remote inference is scored.
"""
from copy import deepcopy

import pytest
from test_api import StubLLM, _session
from test_api import api_factory as bulk32_api_factory
from test_bulk32_literal_repairs import RECORDS
from test_slot_continuation import _message, _reply

api_factory = bulk32_api_factory


async def no_orders_or_tools(client, sid, *, changed_preference=False):
    store = client._transport.app.state.store
    assert await store.list_orders(sid) == []
    tool_events = [event for event in await store.list_events(sid) if event['event_type'] == 'TOOL_CALL_STARTED']
    if changed_preference:
        # A changed flight preference may trigger a bounded re-search against
        # the controlled NoTravelHTTP adapter. This is not source availability.
        assert len(tool_events) <= 16
    else:
        assert not tool_events


@pytest.mark.asyncio
async def test_exact_typo_budget_delta_preserves_children_and_recalls_actual_ages(api_factory):
    row = RECORDS['money']
    model = StubLLM([row['raw']])
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client, row['before'])
        run, after = await _message(client, sid, token, row['source_user_text'])
        assert after['trip']['budget']['amount_minor'] == 13000000
        assert after['trip']['travelers'] == before['trip']['travelers']
        assert after['trip']['stays'] == before['trip']['stays']
        assert after['state_version'] == before['state_version'] + 1
        reply = await _reply(client, sid, token, run)
        assert '5 лет' in reply and '9 лет' in reply
        assert len(model.calls) == 1
        await no_orders_or_tools(client, sid)


@pytest.mark.asyncio
async def test_exact_suffix_answers_checkout_without_a_later_message_or_model_repair(api_factory):
    row = RECORDS['date']
    model = StubLLM([{'unknowns': ['stay.check_out']}])
    async with api_factory(model) as (client, _):
        sid, token, initial = await _session(client, row['before'])
        _, asked = await _message(client, sid, token, 'Уточни дату выезда.')
        assert asked['pending_question']['field'] == 'stay.check_out'
        assert asked['pending_question']['target_id'] == 'new-stay-1'
        run, after = await _message(client, sid, token, row['source_user_text'])
        assert after['trip']['stays'][0]['check_in'] == '2026-11-06'
        assert after['trip']['stays'][0]['check_out'] == '2026-11-10'
        assert after['trip']['legs'][-1]['departure_date'] == '2026-11-10'
        assert after['state_version'] == initial['state_version'] + 1
        assert after['trip']['constraints'] == initial['trip']['constraints']
        assert (after.get('pending_question') or {}).get('field') != 'stay.check_out'
        assert len(model.calls) == 1, 'Only the labelled question setup used the controlled model'
        assert run['result']['result']['response_source'] == 'contextual_slot'
        await no_orders_or_tools(client, sid)


@pytest.mark.asyncio
@pytest.mark.parametrize('saved_preference', [False, True])
async def test_morning_typo_and_checkout_question_use_canonical_date_on_change_and_noop(api_factory, saved_preference):
    row = RECORDS['preference']
    initial = deepcopy(row['before'])
    if saved_preference:
        initial['preferences'] = [{'key': 'departure_time', 'value': 'early_morning', 'weight': 1}]
    raw = deepcopy(row['raw'])
    raw['reply'] = 'Модельная ловушка: выезд01.01.2099. Не использовать как факт.'
    model = StubLLM([raw])
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client, initial)
        run, after = await _message(client, sid, token, row['source_user_text'])
        assert after['trip']['preferences'] == [{'key': 'departure_time', 'value': 'early_morning', 'weight': 1}]
        assert after['trip']['stays'] == before['trip']['stays']
        assert after['trip']['travelers'] == before['trip']['travelers']
        assert after['state_version'] == before['state_version'] + (not saved_preference)
        reply = await _reply(client, sid, token, run)
        assert '15.11.2026' in reply and '2099' not in reply
        await no_orders_or_tools(client, sid, changed_preference=not saved_preference)
