"""Controlled ASGI/real isolated PG; no supplier or model HTTP is permitted."""
from copy import deepcopy
from uuid import uuid4

import pytest
from test_api import StubLLM, _confirmation, _demo, _preview, _session, _snapshot
from test_api import api_factory as controlled_api_fixture
from test_final40_geo_date_booking import F
from test_slot_continuation import _message, _reply

api_factory = controlled_api_fixture


class NoModel(StubLLM):
    async def extract_operations(self, context):
        raise AssertionError('A complete owned date/order question must not call the model')


async def bind_checkout(repository, sid, snapshot):
    run, _ = await repository.intake_message(sid, str(uuid4()), 'controlled owned checkout setup', snapshot['state_version'])
    assert await repository.claim_run(run['run_id'])
    target = snapshot['trip']['stays'][0]['id']
    pending = {'question_id': 'controlled-fresh-checkout', 'field': 'stay.check_out', 'target_id': target,
        'state_version': snapshot['state_version'], 'reason': 'clarification',
        'text': 'На какую дату планируете выезд?', 'questions': ['На какую дату планируете выезд?'],
        'context': {'trip_id': snapshot['trip']['trip_id']}}
    await repository.finish_run(run['run_id'], snapshot['state_version'], None, 'WAITING_USER',
        pending['text'], pending_question=pending, waiting_reason='clarification')


@pytest.mark.asyncio
async def test_owned_checkout_suffix_commits_only_date_without_model_or_unrelated_mutation(api_factory):
    row = F['date_cases'][0]
    async with api_factory(NoModel()) as (client, _):
        sid, token, created = await _session(client, deepcopy(row['before']['trip']))
        repository = client._transport.app.state.store
        await bind_checkout(repository, sid, created)
        before = await _snapshot(client, sid, token)

        run, after = await _message(client, sid, token, row['user'])
        assert after['state_version'] == before['state_version'] + 1
        assert after['trip']['stays'][0]['check_in'] == before['trip']['stays'][0]['check_in']
        assert after['trip']['stays'][0]['check_out'] == '2026-11-19'
        for field in ('origin', 'travelers', 'groups', 'budget', 'constraints', 'preferences', 'search_policy', 'route_policy'):
            assert after['trip'][field] == before['trip'][field]
        assert after['trip']['stays'][0]['rooms'] == before['trip']['stays'][0]['rooms']
        assert not any(event['event_type'] in {'MODEL_CALL_STARTED', 'TOOL_CALL_STARTED'}
            for event in await repository.list_events(sid))
        assert await repository.list_orders(sid) == []
        assert run['result']['result']['response_source'] == 'contextual_slot'


@pytest.mark.asyncio
@pytest.mark.parametrize('with_order', [False, True])
async def test_booking_restore_question_preserves_all_trip_fields_pending_and_orders(api_factory, monkeypatch, with_order):
    async with api_factory(NoModel()) as (client, _):
        row = F['booking_cases'][0]
        sid, token, _ = await _session(client, deepcopy(row['before']['trip']))
        if with_order:
            before_demo, plans = await _demo(client, sid, token)
            quote = await _preview(client, sid, token, plans[0], before_demo['state_version'])
            response = await client.post(f'/api/v1/sessions/{sid}/checkout/confirm',
                headers={'Authorization': 'Bearer ' + token}, json=_confirmation(quote, before_demo['state_version']))
            assert response.status_code in {200, 201}
        repository = client._transport.app.state.store
        before = await _snapshot(client, sid, token)
        orders = await repository.list_orders(sid)
        assert len(orders) == int(with_order)

        async def no_search(*args, **kwargs):
            raise AssertionError('Order restoration question must not search suppliers')

        monkeypatch.setattr(client._transport.app.state.harness, '_search_trip', no_search)
        run, after = await _message(client, sid, token, row['user'])
        for field in ('trip', 'state_version', 'status', 'waiting_reason', 'pending_question'):
            assert after[field] == before[field]
        assert await repository.list_orders(sid) == orders
        assert not any(event['event_type'] == 'MODEL_CALL_STARTED' for event in await repository.list_events(sid))
        reply = await _reply(client, sid, token, run)
        assert 'не изменены' in reply and 'Уточните' in reply
        assert ('BOOKED_DEMO' in reply) if with_order else ('не создавалась' in reply)
