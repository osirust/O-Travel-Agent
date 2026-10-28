"""Exact cached extractions + saved domain facts from owner-supplied009 only.

New session/trip IDs and version zero are assigned by the isolated test API.
No actual LLM calls and no read of the ongoing100 sessions during tests.
"""
import json
from pathlib import Path
from uuid import uuid4

import pytest
from test_api import StubLLM, _headers, _plans, _session, _snapshot, _wait
from test_api import api_factory as cached_api_factory
from test_slot_continuation import _message, _reply

api_factory = cached_api_factory
FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures/evaluation/owner-zai009-extractions-20261026.json'


@pytest.mark.asyncio
async def test_exact_cached_last_four_turns_preserve_edits_and_answer_questions(api_factory, monkeypatch):
    fixture = json.loads(FIXTURE.read_text())
    cases = fixture['turns'][5:]
    assert len(cases) == 4 and all(case['cached_extraction'] is not None for case in cases)
    model = StubLLM([case['cached_extraction'] for case in cases])
    async with api_factory(model) as (client, _):
        sid, token, _ = await _session(client, fixture['initial_for_turn6'])
        response = await client.post(f'/api/v1/sessions/{sid}/search', headers=_headers(token), json={
            'client_message_id': str(uuid4()), 'mode': 'DEMO'})
        assert response.status_code == 202
        initial = await _wait(client, response.json()['run_id'], token)
        assert initial['status'] == 'completed' and _plans(initial)
        before = await _snapshot(client, sid, token)
        harness = client._transport.app.state.harness
        search, searches = harness._search_trip, []
        async def observed_search(*args, **kwargs):
            searches.append(True)
            return await search(*args, **kwargs)
        monkeypatch.setattr(harness, '_search_trip', observed_search)

        hypo, unchanged = await _message(client, sid, token, cases[0]['input_text'])
        assert unchanged['trip'] == before['trip'] and not searches
        assert 'не измен' in (await _reply(client, sid, token, hypo)).casefold()

        added, child7 = await _message(client, sid, token, cases[1]['input_text'])
        assert len(searches) == 1 and len(child7['trip']['travelers']) == 2
        assert child7['trip']['travelers'][0] == before['trip']['travelers'][0]
        assert child7['trip']['travelers'][1]['age'] == 7
        assert '7 лет' in await _reply(client, sid, token, added)

        corrected, child8 = await _message(client, sid, token, cases[2]['input_text'])
        assert len(searches) == 2 and child8['trip']['travelers'][1]['age'] == 8
        roster = await _reply(client, sid, token, corrected)
        assert '1 взрослый' in roster and '1 ребёнок' in roster and '8 лет' in roster
        assert all(person['id'] not in roster for person in child8['trip']['travelers'])

        cart, after = await _message(client, sid, token, cases[3]['input_text'])
        assert after['trip'] == child8['trip'] and len(searches) == 2
        assert _plans(cart) == _plans(corrected)
        text = await _reply(client, sid, token, cart)
        assert 'демонстрационный' in text and 'без покупки' in text
        action = cart['result']['result']['suggested_action']
        assert action == {'type': 'preview_cart', 'plan_id': _plans(corrected)[0]['plan_id'],
                          'state_version': after['state_version']}
        repository = client._transport.app.state.store
        assert await repository.list_orders(sid) == []
        assert not any(event['event_type'] == 'QUOTE_CREATED' for event in await repository.list_events(sid))
        assert len(model.calls) == 4
