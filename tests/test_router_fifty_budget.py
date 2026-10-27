"""Explicit free-request engineering bound; real isolated ledger, no inference."""
import asyncio
from unittest.mock import patch

import httpx
import pytest
from test_api import _ModelPolicyStore
from test_local_model_budget import ledger as budget_ledger
from test_local_model_budget import new_run, reserve, seed

ledger = budget_ledger


@pytest.mark.asyncio
@pytest.mark.parametrize('requested,expected', [(0, 0), (39, 39), (40, 40), (45, 40), (50, 50), (2000, 40), (100000, 40)])
async def test_real_gateway_harness_passes_explicit_router_budget_to_durable_guard(requested, expected):
    from otravel.config import Settings
    from otravel.domain import sample_trip
    from otravel.harness import Harness
    from otravel.providers import LLMGateway

    class ControlledRouter(LLMGateway):
        def __init__(self):
            super().__init__({'LLM_PROVIDER': 'openrouter', 'OPENROUTER_MODEL': 'test/model:free',
                'OPENROUTER_API_KEY': 'synthetic-unused-router-key', 'OPENROUTER_BACKUP_MODEL': '',
                'http_transport': httpx.MockTransport(lambda request: pytest.fail('No inference HTTP permitted'))})

        async def extract_operations(self, context):
            return {'operations': [], 'control': {'action': 'none'}, 'reply': 'Текущие условия сохранены.',
                'unknowns': [], 'conflicts': [], 'speech_act': 'ASK'}

    gateway, store = ControlledRouter(), _ModelPolicyStore()

    async def reserve(sid, runid, limit, provider):
        assert (sid, runid, provider) == ('policy-session', 'policy-run', 'openrouter')
        store.reservations.append(limit)
        return limit > 0

    store.reserve_model_call = reserve
    harness = Harness(Settings('unused', 'openrouter', 1, 8000, requested, 1), store, gateway, None)
    session = {'session_id': 'policy-session', 'state_version': 0,
        'state': sample_trip().model_dump(mode='json'), 'pending_question': None}
    run = {'run_id': 'policy-run', 'input_message_id': 'policy-message', 'input_text': 'Уточни поездку'}
    try:
        if expected == 0:
            from otravel.providers import ProviderError
            with pytest.raises(ProviderError, match='лимит'):
                await harness._extract(session, run)
        else:
            await harness._extract(session, run)
        assert store.reservations == [expected]
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_explicit_fifty_retains_prior_forty_reservations_and_blocks_fifty_one(ledger):
    await seed(ledger, ['openrouter'] * 39)
    assert await reserve(ledger, await new_run(ledger), provider='openrouter', limit=40)
    assert not await reserve(ledger, await new_run(ledger), provider='openrouter', limit=40)
    for _ in range(10):
        assert await reserve(ledger, await new_run(ledger), provider='openrouter', limit=50)
    assert not await reserve(ledger, await new_run(ledger), provider='openrouter', limit=100000)
    assert not await reserve(ledger, await new_run(ledger), provider='gemini', limit=100000)


@pytest.mark.asyncio
async def test_last_explicit_free_slot_is_atomic_across_callers(ledger):
    await seed(ledger, ['openrouter'] * 49)
    runs = [await new_run(ledger) for _ in range(3)]
    repository, sid, day = ledger
    with patch('otravel.store._now', return_value=day):
        admitted = await asyncio.gather(*(repository.reserve_model_call(sid, run, 50, 'openrouter') for run in runs))
    assert admitted.count(True) == 1 and admitted.count(False) == 2
    assert not await reserve(ledger, await new_run(ledger), provider='openrouter', limit=50)
