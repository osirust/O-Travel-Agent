"""Public readiness reports the transport actually selected by the gateway."""
import pytest

from otravel.config import provider_status


@pytest.mark.parametrize('value,expected',[('1',1),('2',2),('3',3),('invalid',1),('',1)])
def test_groq_status_reports_opted_in_transport(monkeypatch,value,expected):
    monkeypatch.setenv('GROQ_STRUCTURED_TRANSPORT_ENABLED','true')
    monkeypatch.setenv('GROQ_TRANSPORT_VERSION',value)
    monkeypatch.setenv('DYNAMIC_GEOGRAPHY_ENABLED','true')
    assert provider_status()['groq']['transport_version']==expected
