"""Giga Personal authorization keys are private even without an API_KEY suffix."""
import json
from urllib.parse import quote

from otravel import providers
from otravel.api import _clean
from otravel.observability import model_snapshot
from otravel.providers import safe_model_context
from scripts.run_tests import redact


def test_authorization_key_is_removed_from_public_model_context_and_trace(monkeypatch):
    secret = 'synthetic-personal-auth-key-with-colon:and-plus+'
    monkeypatch.setenv('GIGACHAT_AUTH_KEY', secret)
    value = {'reply':'echo '+secret, 'operations':[{'value':{'GIGACHAT-AUTH-KEY':secret, 'safe':'kept'}}]}
    assert secret not in json.dumps(_clean(value))
    assert 'GIGACHAT-AUTH-KEY' not in json.dumps(safe_model_context(value))
    trace = model_snapshot(value, {'GIGACHAT_AUTH_KEY':secret})
    assert secret not in json.dumps(trace) and 'GIGACHAT-AUTH-KEY' not in json.dumps(trace)


def test_test_wrapper_redacts_raw_and_urlencoded_auth_key():
    secret = 'synthetic-personal-auth-key-with-colon:and-plus+'
    for encoded in (secret, quote(secret, safe='')):
        assert encoded not in redact('diagnostic value='+encoded, {'GIGACHAT_AUTH_KEY':secret})


def test_public_text_is_not_corrupted_by_other_provider_log_registration(monkeypatch):
    monkeypatch.setattr(providers, '_SECRETS', {'x', 'test'})
    monkeypatch.setattr(providers, '_EPHEMERAL_PUBLIC_SECRETS', set())
    text = 'test-owned-selector-with-x'
    assert _clean({'reply':text, 'target_id':text}) == {'reply':text, 'target_id':text}
