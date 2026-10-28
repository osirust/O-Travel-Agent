"""Cached invalid-calendar model response through public API/real test PG."""
from copy import deepcopy

import pytest
from test_api import StubLLM, _preview, _session
from test_api import api_factory as calendar_api_factory
from test_current_calendar_guard import ROW
from test_duration_persistent import search
from test_slot_continuation import _message, _reply

api_factory = calendar_api_factory


async def no_source_or_order(client, sid):
    store = client._transport.app.state.store
    assert not any(e['event_type'] == 'TOOL_CALL_STARTED' for e in await store.list_events(sid))
    assert await store.list_orders(sid) == []


@pytest.mark.asyncio
async def test_exact_current_calendar_error_blocks_rolled_date_and_keeps_human_explanation(api_factory):
    model = StubLLM([ROW['raw']])
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client, ROW['before'])
        run, after = await _message(client, sid, token, ROW['input'])
        assert after['trip'] == before['trip'] and after['state_version'] == before['state_version']
        assert after['trip']['stays'][0]['check_out'] == '2026-11-30'
        reply = await _reply(client, sid, token, run)
        assert '31 ноября 2026' in reply and 'не существует' in reply
        assert 'перенес' not in reply.casefold() and '1 декабря' not in reply
        assert len(model.calls) == 1
        await no_source_or_order(client, sid)


@pytest.mark.asyncio
async def test_calendar_conflict_blocks_independent_budget_without_reprice_or_touching_old_quote(api_factory):
    initial = deepcopy(ROW['before'])
    initial['search_policy']['mode'] = 'DEMO'
    initial['budget']['amount_minor'] = 40000000  # Explicit quote-readiness setup; not original model inference.
    raw = deepcopy(ROW['raw'])
    raw['operations'].append({'type': 'set_budget', 'value': {'amount_minor': 7500000, 'currency': 'RUB'}})
    model = StubLLM([raw])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, initial)
        plan = await search(client, sid, token)
        store = client._transport.app.state.store
        current = await store.get_session(sid)
        quote = await _preview(client, sid, token, plan, current['state_version'])
        old_quote = deepcopy(await store.get_quote(quote['quote_id']))
        text = ROW['input'] + ' И установи общий бюджет75000 рублей.'
        run, after = await _message(client, sid, token, text)
        assert after['trip'] == current['state'] and after['state_version'] == current['state_version']
        assert await store.get_quote(quote['quote_id']) == old_quote
        assert after['trip']['budget']['amount_minor'] == 40000000
        assert 'не существует' in await _reply(client, sid, token, run)
        await no_source_or_order(client, sid)


@pytest.mark.asyncio
async def test_new_invalid_edit_withdraws_old_year_candidate_before_a_late_yes(api_factory):
    initial = {'origin': 'Москва', 'travelers': [{'id': 'adult-a', 'type': 'adult'}],
        'stays': [{'id': 'new-stay-1', 'city': 'Стамбул', 'check_in': '2026-12-28'}]}
    raw = {'speech_act': 'CHANGE', 'control': {'action': 'continue'}, 'reply': 'Неверный выезд1декабря.',
        'operations': [{'type': 'upsert_stay', 'target_id': 'new-stay-1', 'value': {'check_out': '2026-12-01'}}]}
    model = StubLLM([{'unknowns': ['stay.check_out']}, raw, {}])
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client, initial)
        await _message(client, sid, token, 'Уточни дату выезда.')
        _, candidate = await _message(client, sid, token, '5 января')
        assert candidate['pending_question']['context']['date_proposal']['owner'] == 'date_slots.year.v1'
        _, rejected = await _message(client, sid, token, ROW['input'])
        assert rejected['trip'] == before['trip']
        assert 'date_proposal' not in rejected['pending_question']['context']
        _, late = await _message(client, sid, token, 'да')
        assert late['trip'] == before['trip'] and late['trip']['stays'][0]['check_out'] is None
        await no_source_or_order(client, sid)
