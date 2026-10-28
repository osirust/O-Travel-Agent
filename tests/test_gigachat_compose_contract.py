"""Check the shipped GigaChat env/CA contract without starting Compose or HTTP."""
import re
from pathlib import Path

import pytest

from otravel.config import Settings

ROOT = Path(__file__).resolve().parents[1]
FIELDS = {'GIGACHAT_AUTH_KEY', 'GIGACHAT_MODEL', 'GIGACHAT_FREEMIUM_CONFIRMED',
    'GIGACHAT_CA_FILE', 'MAX_GIGACHAT_LLM_CALLS_PER_DAY'}


def compose_fields():
    content = (ROOT/'docker-compose.yml').read_text(encoding='utf-8')
    app = content.split('\n  app:\n', 1)[1].split('\n    ports:', 1)[0]
    rows = re.findall(r'^      ([A-Z][A-Z0-9_]*): (.*)$', app, re.MULTILINE)
    assert len(rows) == len(dict(rows)), 'Duplicate Compose environment keys are ambiguous.'
    return dict(rows)


def example_fields():
    return dict(line.split('=', 1) for line in (ROOT/'.env.example').read_text(encoding='utf-8').splitlines()
        if line and not line.startswith('#'))


def test_compose_forwards_selected_gigachat_fields_and_defaults_to_verified_bundled_ca():
    env = compose_fields()
    assert FIELDS <= set(env)
    for name in FIELDS:
        assert env[name].startswith('${'+name+':-') and env[name].endswith('}')
    assert env['GIGACHAT_AUTH_KEY'] == '${GIGACHAT_AUTH_KEY:-}'
    assert env['GIGACHAT_MODEL'] == '${GIGACHAT_MODEL:-GigaChat-3-Ultra}'
    assert env['GIGACHAT_FREEMIUM_CONFIRMED'] == '${GIGACHAT_FREEMIUM_CONFIRMED:-false}'
    assert env['MAX_GIGACHAT_LLM_CALLS_PER_DAY'] == '${MAX_GIGACHAT_LLM_CALLS_PER_DAY:-40}'
    ca = '/app/certs/gigachat-root-ca.pem'
    assert env['GIGACHAT_CA_FILE'] == '${GIGACHAT_CA_FILE:-'+ca+'}'
    dockerfile = (ROOT/'Dockerfile').read_text(encoding='utf-8')
    assert 'COPY --chmod=0444 certs/gigachat-root-ca.pem '+ca in dockerfile
    assert (ROOT/'certs/gigachat-root-ca.pem').is_file()


def test_example_does_not_select_or_authorize_gigachat_or_embed_a_native_path():
    env = example_fields()
    assert FIELDS <= set(env)
    assert env and all(value == '' for value in env.values()), 'Organisers require every example value to be empty.'
    assert env['LLM_PROVIDER'] == ''
    assert env['GIGACHAT_AUTH_KEY'] == '' and env['GIGACHAT_FREEMIUM_CONFIRMED'] == ''
    assert env['GIGACHAT_CA_FILE'] == ''
    assert env['GIGACHAT_MODEL'] == ''
    assert env['MAX_GIGACHAT_LLM_CALLS_PER_DAY'] == ''
    # Forwarding GigaChat must retain the existing opt-in Groq v2 contract.
    shipped = compose_fields()
    assert shipped['LLM_PROVIDER'] == '${LLM_PROVIDER:-openrouter}'
    assert shipped['GIGACHAT_FREEMIUM_CONFIRMED'] == '${GIGACHAT_FREEMIUM_CONFIRMED:-false}'
    assert shipped['GIGACHAT_MODEL'] == '${GIGACHAT_MODEL:-GigaChat-3-Ultra}'
    assert shipped['MAX_GIGACHAT_LLM_CALLS_PER_DAY'] == '${MAX_GIGACHAT_LLM_CALLS_PER_DAY:-40}'
    assert shipped['GIGACHAT_CA_FILE'] == '${GIGACHAT_CA_FILE:-/app/certs/gigachat-root-ca.pem}'
    assert '/home/' not in shipped['GIGACHAT_CA_FILE'] and not re.search(r'[A-Z]:[\\/]', shipped['GIGACHAT_CA_FILE'])
    assert shipped['GROQ_TRANSPORT_VERSION'] == '${GROQ_TRANSPORT_VERSION:-1}'
    assert shipped['GROQ_STRUCTURED_TRANSPORT_ENABLED'] == '${GROQ_STRUCTURED_TRANSPORT_ENABLED:-false}'


@pytest.mark.parametrize('bound', ['0', '40', '1000'])
def test_forwarded_giga_budget_is_consumed_by_real_settings(monkeypatch, bound):
    monkeypatch.setenv('LLM_PROVIDER', 'gigachat')
    monkeypatch.setenv('MAX_GIGACHAT_LLM_CALLS_PER_DAY', bound)
    monkeypatch.setenv('GIGACHAT_MODEL', 'GigaChat-3-Ultra')
    monkeypatch.setenv('GIGACHAT_AUTH_KEY', 'synthetic-only')
    monkeypatch.setenv('GIGACHAT_FREEMIUM_CONFIRMED', 'true')
    monkeypatch.setenv('GIGACHAT_CA_FILE', '/app/certs/gigachat-root-ca.pem')
    settings = Settings.from_env()
    assert settings.max_gigachat_llm_calls_per_day == int(bound)
    provider = settings.provider_settings()
    assert provider['GIGACHAT_AUTH_KEY'] == 'synthetic-only'
    assert provider['GIGACHAT_CA_FILE'] == '/app/certs/gigachat-root-ca.pem'
    assert provider['GIGACHAT_FREEMIUM_CONFIRMED'] is True


@pytest.mark.parametrize('bound', ['-1', '1001', 'not-an-integer'])
def test_forwarded_invalid_budget_is_rejected_before_runtime(monkeypatch, bound):
    monkeypatch.setenv('MAX_GIGACHAT_LLM_CALLS_PER_DAY', bound)
    with pytest.raises(ValueError):
        Settings.from_env()
