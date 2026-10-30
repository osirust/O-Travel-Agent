"""Retry-After is a provider hint, not a guarantee or an automatic retry."""
import httpx
import pytest
from test_groq_transport_provider import gateway

from otravel.domain import sample_trip
from otravel.llm_contract import build_operation_schema
from otravel.providers import ProviderError


@pytest.mark.asyncio
@pytest.mark.parametrize('header,expected', [('16','16 сек.'), ('1.2','2 сек.'), ('malicious-secret',None), ('',None)])
async def test_429_reports_only_bounded_numeric_wait_and_attempts_once(header, expected):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={'retry-after':header}, json={'error':{'message':'synthetic vendor text'}})
    model = gateway(handler)
    try:
        with pytest.raises(ProviderError) as error:
            await model.extract_operations({'current_message':'хочу в Сербию', '__response_schema':build_operation_schema(sample_trip())})
        assert error.value.code == 'RATE_LIMITED' and len(calls) == 1
        assert 'malicious-secret' not in error.value.message
        assert (expected in error.value.message) if expected else 'ожидание:' not in error.value.message
    finally:
        await model.close()
