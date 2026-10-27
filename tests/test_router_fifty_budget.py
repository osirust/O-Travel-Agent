"""Explicit free-request engineering bound; real isolated ledger, no inference."""
import asyncio
from unittest.mock import patch

import pytest
from test_local_model_budget import ledger as budget_ledger
from test_local_model_budget import new_run, reserve, seed

ledger = budget_ledger


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
