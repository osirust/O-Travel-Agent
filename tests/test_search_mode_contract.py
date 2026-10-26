"""Search mode is a policy assertion, never an ignored or hidden mode switch."""
import pytest
from test_api import StubLLM, _headers, _session, _snapshot
from test_api import api_factory as search_mode_api_factory
from test_harness_conversational import known_trip

api_factory = search_mode_api_factory


@pytest.mark.asyncio
@pytest.mark.parametrize('saved_mode,requested_mode', [('DEMO', 'HYBRID'), ('HYBRID', 'DEMO')])
async def test_explicit_mode_mismatch_has_no_run_or_external_actions(api_factory, saved_mode, requested_mode):
    model = StubLLM()
    async with api_factory(model) as (client, _):
        initial = known_trip()
        initial['search_policy'] = {'mode': saved_mode, 'guest_nationality': 'RU'}
        sid, token, before = await _session(client, initial)
        response = await client.post(f'/api/v1/sessions/{sid}/search', headers=_headers(token), json={
            'mode': requested_mode, 'expected_version': before['state_version']})
        assert response.status_code == 409
        assert response.json()['error']['code'] == 'search_policy_mismatch'
        assert (await _snapshot(client, sid, token))['trip'] == before['trip']
        assert (await client.get(f'/api/v1/sessions/{sid}/messages', headers=_headers(token))).json()['messages'] == []
        assert model.calls == []
