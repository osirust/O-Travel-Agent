"""Real PostgreSQL/API regressions with controlled model outputs, no vendor HTTP."""
from uuid import uuid4

import pytest

from test_api import StubLLM, _headers, _session, _snapshot, _wait, _plans
from test_api import api_factory as original_api_factory
from otravel.domain import sample_trip
from otravel.domain import Constraint

api_factory = original_api_factory


@pytest.mark.asyncio
async def test_reserve_ambiguity_cannot_persist_a_1200_ruble_budget(api_factory):
    model = StubLLM([{'operations': [{'type': 'set_budget', 'value': {
        'scope': 'whole_trip', 'currency': 'RUB', 'amount_minor': 120000}}],
        'control': {'action': 'continue'}, 'reply': 'В какой город вы хотите поехать?'}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client)
        response = await client.post(f'/api/v1/sessions/{sid}/messages', headers=_headers(token), json={
            'client_message_id': str(uuid4()), 'expected_version': 0,
            'text': 'Хочу уложиться в 100 тысяч с запасом на непредвиденные траты 20%.'})
        run = await _wait(client, response.json()['run_id'], token)
        assert run['status'] == 'completed'
        after = await _snapshot(client, sid, token)
        assert after['trip']['budget'] is None
        assert after['pending_question']['field'] == 'budget.amount_minor'
        assert after['state_version'] == 0
        assert not await client._transport.app.state.store.list_orders(sid)


@pytest.mark.asyncio
async def test_included_unpriced_food_and_bags_require_clarification_or_canonical_blocker(api_factory):
    model = StubLLM([{'operations': [{'type': 'set_budget', 'value': {
        'scope': 'whole_trip', 'currency': 'RUB', 'amount_minor': 12000000}}],
        'control': {'action': 'continue'}}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client)
        response = await client.post(f'/api/v1/sessions/{sid}/messages', headers=_headers(token), json={
            'client_message_id': str(uuid4()), 'text':
            '120 тысяч на всех, включая дорогу туда-обратно, отель, трансфер, еду и багаж.'})
        run = await _wait(client, response.json()['run_id'], token)
        assert run['status'] == 'completed'
        after = await _snapshot(client, sid, token)
        hard_costs = {c['key'] for c in after['trip']['constraints'] if c['hard']}
        assert after['trip']['budget'] is None or {'unpriced_food_costs', 'unpriced_baggage_costs'} <= hard_costs
        assert not await client._transport.app.state.store.list_orders(sid)


@pytest.mark.asyncio
async def test_literal_completion_cannot_skip_unpriced_expense_scope(api_factory):
    model = StubLLM([{'operations': [], 'speech_act': 'CHANGE', 'control': {'action': 'continue'}}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, sample_trip().model_dump(mode='json'))
        response = await client.post(f'/api/v1/sessions/{sid}/messages', headers=_headers(token), json={
            'client_message_id': str(uuid4()), 'text':
            'Общий бюджет 120 тысяч рублей на всех, включая еду и багаж.'})
        run = await _wait(client, response.json()['run_id'], token)
        assert run['status'] == 'completed'
        after = await _snapshot(client, sid, token)
        assert {'unpriced_food_costs', 'unpriced_baggage_costs'} <= {
            c['key'] for c in after['trip']['constraints'] if c['hard']}
        assert not _plans(run)
        assert await client._transport.app.state.store.list_orders(sid) == []


@pytest.mark.asyncio
async def test_unchanged_revision_with_budget_ambiguity_blocks_old_quote_confirm(api_factory):
    model = StubLLM([{'operations': [{'type': 'set_budget', 'value': {
        'scope': 'whole_trip', 'currency': 'RUB', 'amount_minor': 120000}}],
        'speech_act': 'CHANGE', 'control': {'action': 'continue'}}])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, sample_trip().model_dump(mode='json'))
        response = await client.post(f'/api/v1/sessions/{sid}/commands', headers=_headers(token), json={
            'client_message_id': str(uuid4()), 'expected_version': 0, 'operations': [], 'search': True})
        searched = await _wait(client, response.json()['run_id'], token)
        plans = _plans(searched)
        assert plans
        preview = await client.post(f'/api/v1/sessions/{sid}/checkout/preview', headers=_headers(token),
            json={'plan_id': plans[0]['plan_id'], 'state_version': 0})
        assert preview.status_code == 200
        quote = preview.json()
        response = await client.post(f'/api/v1/sessions/{sid}/messages', headers=_headers(token), json={
            'client_message_id': str(uuid4()), 'expected_version': 0,
            'text': 'Хочу уложиться в 100 тысяч с запасом на непредвиденные траты 20%.'})
        run = await _wait(client, response.json()['run_id'], token)
        assert run['status'] == 'completed'
        after = await _snapshot(client, sid, token)
        assert after['state_version'] == 0
        assert after['trip']['budget']['amount_minor'] == sample_trip().budget.amount_minor
        confirm = await client.post(f'/api/v1/sessions/{sid}/checkout/confirm', headers=_headers(token), json={
            'quote_id': quote['quote_id'], 'quote_version': quote['quote_version'], 'state_version': 0,
            'confirmed': True, 'idempotency_key': 'ambiguous-budget-cannot-confirm'})
        assert confirm.status_code == 409 and confirm.json()['error']['code'] == 'budget_scope_unresolved'
        repeated_preview = await client.post(f'/api/v1/sessions/{sid}/checkout/preview', headers=_headers(token),
            json={'plan_id': plans[0]['plan_id'], 'state_version': 0})
        assert repeated_preview.status_code == 409
        assert await client._transport.app.state.store.list_orders(sid) == []


@pytest.mark.asyncio
async def test_hypothetical_waiver_plus_pause_keeps_unpriced_hard_requirement(api_factory):
    trip = sample_trip()
    trip.constraints.append(Constraint(key='unpriced_food_costs', value=True, hard=True))
    model = StubLLM([{'speech_act': 'CHANGE', 'control': {'action': 'wait'}, 'operations': [
        {'type': 'clear_constraint', 'value': {'key': 'unpriced_food_costs'}}]}])
    async with api_factory(model) as (client, _):
        sid, token, before = await _session(client, trip.model_dump(mode='json'))
        response = await client.post(f'/api/v1/sessions/{sid}/messages', headers=_headers(token), json={
            'client_message_id': str(uuid4()), 'expected_version': 0,
            'text': 'Если исключим еду, пока подожди.'})
        run = await _wait(client, response.json()['run_id'], token)
        assert run['status'] == 'completed'
        after = await _snapshot(client, sid, token)
        assert after['trip'] == before['trip'] and after['state_version'] == 0
        assert after['waiting_reason'] == 'user_pause'
        assert not _plans(run)
        assert await client._transport.app.state.store.list_orders(sid) == []
