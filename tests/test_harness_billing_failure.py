"""Documented account denial reaches the client without exposing vendor prose."""
import pytest
from test_api import StubLLM, _session, _wait, _headers
from test_api import api_factory as billing_api_factory

from otravel.domain import sample_trip
from otravel.providers import ProviderError

api_factory = billing_api_factory


class AccountDenied(StubLLM):
    async def extract_operations(self, context):
        self.calls.append(context)
        raise ProviderError('RATE_LIMITED', 'Нет доступного API-ресурса у провайдера.',
            retryable=False, provider='zai', details={'http_status': 429,
                'failure_kind': 'billing', 'business_code': 1113,
                'raw_message': 'SYNTHETIC_PRIVATE_ACCOUNT_DATA',
                'authorization': 'Bearer SYNTHETIC_PRIVATE_ACCOUNT_DATA'})


@pytest.mark.asyncio
async def test_billing_denial_is_nonretryable_keeps_trip_and_exposes_only_documented_category(api_factory):
    model = AccountDenied()
    async with api_factory(model) as (client, _):
        sid, token, created = await _session(client, sample_trip().model_dump(mode='json'))
        base = f'/api/v1/sessions/{sid}'
        accepted = await client.post(base + '/messages', headers=_headers(token), json={
            'client_message_id': 'billing-denial-regression', 'text': 'Сократи бюджет на 10 тысяч рублей.'})
        assert accepted.status_code == 202
        failed = await _wait(client, accepted.json()['run_id'], token)
        assert failed['status'] == 'failed' and len(model.calls) == 1
        problem = failed['error']
        assert problem['code'] == 'RATE_LIMITED' and problem['retryable'] is False
        assert problem['details'] == {'http_status': 429, 'business_code': 1113, 'failure_kind': 'billing'}
        assert 'API-ресурса' in problem['message']
        snapshot = await client.get(base, headers=_headers(token))
        assert snapshot.json()['trip'] == created['trip']
        assert (await client.get(base + '/orders', headers=_headers(token))).json()['orders'] == []
        assert 'SYNTHETIC_PRIVATE_ACCOUNT_DATA' not in str(failed)
