"""Router-specific pure profile guards; shared lifecycle mechanics remain fake."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_after80_cf_backend import IdentityFile, PrivateFile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('router_controller_test', ROOT / 'scripts/after80_router_backend.py')
manager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manager)


@pytest.fixture
def engine():
    return manager.new_engine()


def profile():
    return {'OPENROUTER_API_KEY': 'synthetic-router-key', 'LITEAPI_API_KEY': 'sand_synthetic',
        'DUFFEL_ACCESS_TOKEN': 'duffel_test_synthetic', 'SERPAPI_API_KEY': 'synthetic-serp',
        'SERPAPI_FREE_TIER_CONFIRMED': 'true', 'LITEAPI_MODE': 'sandbox'}


def readiness():
    value = {name: {'configured': False} for name in ('gemini', 'cloudflare', 'groq', 'zai', 'ollama', 'hotelbeds', 'ratehawk', 'travelpayouts')}
    value.update({'_runtime': {'llm_provider': 'openrouter', 'model': manager.MODEL},
        'openrouter': {'configured': True, 'model': manager.MODEL, 'backup_model': '', 'paid_fallback': False},
        'liteapi': {'configured': True, 'mode': 'sandbox'}, 'duffel': {'configured': True, 'mode': 'sandbox'},
        'serpapi': {'configured': True, 'free_tier_confirmed': True, 'source_type': 'LIVE_INDICATIVE'},
        'open_meteo': {'configured': True, 'requires_key': False}})
    return value


def test_fixed_router_env_no_inheritance_or_other_keys_and_shared40_counter(engine, monkeypatch):
    for key in (*engine.KEYS, 'ZAI_FREE_TIER_CONFIRMED', 'CLOUDFLARE_FREE_TIER_CONFIRMED'):
        monkeypatch.setenv(key, 'DO-NOT-INHERIT')
    env = manager.environment(engine, SimpleNamespace(password='synthetic:#@', ip='172.18.0.2'), {**profile(),
        'ZAI_API_KEY': 'FORBIDDEN', 'OPENROUTER_MODEL': 'paid-model'})
    assert env['LLM_PROVIDER'] == 'openrouter' and env['OPENROUTER_MODEL'] == 'dots-studio/dots-3-note-preview:free'
    assert env['OPENROUTER_BACKUP_MODEL'] == '' and env['MAX_LLM_CALLS_PER_DAY'] == '40'
    assert env['MAX_LLM_CALLS_PER_RUN'] == '1' and env['MAX_LOCAL_LLM_CALLS_PER_DAY'] == '0'
    assert env['DEFAULT_SEARCH_MODE'] == 'HYBRID' and env['DEFAULT_FLIGHT_PROVIDER'] == 'duffel'
    assert env['DATABASE_URL'].endswith('/otravel_zai_eval') and 'synthetic%3A%23%40' in env['DATABASE_URL']
    assert all(k not in env for k in engine.KEYS if k not in {*manager.ROUTER_KEYS, *manager.TRAVEL_KEYS})
    assert not any(k.startswith(('ZAI_', 'CLOUDFLARE_')) for k in env)
    assert env['GROQ_FREE_TIER_CONFIRMED'] == 'false'
    assert 'DO-NOT-INHERIT' not in json.dumps(env) and 'FORBIDDEN' not in json.dumps(env)


def test_own_namespace_creator_evidence_and_no_mutation_of_old_controllers():
    paths = ['scripts/local_backend.py', 'scripts/after80_backend.py', 'scripts/after80_cf_backend.py']
    before = {p: (ROOT / p).read_bytes() for p in paths}
    first, second = manager.new_engine(), manager.new_engine()
    assert first is not second and first.SAFE_CONTEXT is not second.SAFE_CONTEXT
    assert first.SOURCE == Path('/home/aegml/projects/otravel-after80-20261026') and first.DATABASE == 'otravel_zai_eval'
    assert 'after80-router-backend-8084' in str(first.IDENTITY)
    assert 'after80-router-backend-20261026' in str(first.EVIDENCE)
    assert first.ARGV[-5:] == ['--host', '127.0.0.1', '--port', '8084', '--no-access-log']
    assert manager.CREATOR == 'after80_router_backend.py'
    assert all((ROOT / p).read_bytes() == content for p, content in before.items())


@pytest.mark.parametrize('key,value', [('SERPAPI_FREE_TIER_CONFIRMED', False), ('OPENROUTER_API_KEY', ''),
    ('OPENROUTER_API_KEY', 'bad key'), ('LITEAPI_API_KEY', 'prod_forbidden'), ('LITEAPI_MODE', 'production'),
    ('DUFFEL_ACCESS_TOKEN', 'duffel_live_forbidden')])
def test_invalid_or_paid_travel_profile_refuses(engine, key, value):
    with pytest.raises(engine.Refusal): manager.validate_profile(engine, {**profile(), key: value})


@pytest.mark.parametrize('model', ['paid/model', 'other/model:free'])
def test_only_fixed_dots_free_not_arbitrary_free_model(engine, monkeypatch, model):
    monkeypatch.setattr(manager, 'MODEL', model)
    with pytest.raises(engine.Refusal, match='fixed_free'): manager.validate_profile(engine, profile())


def test_master_reader_uses_only_router_travel_and_database_fields_not_source_env(engine, monkeypatch):
    fake = PrivateFile('ZAI_API_KEY=forbidden\nCLOUDFLARE_API_TOKEN=forbidden\nOPENROUTER_API_KEY=synthetic\nPOSTGRES_PASSWORD=dbfixture')
    assert manager.private_values(engine, fake, manager.ROUTER_KEYS) == {'OPENROUTER_API_KEY': 'synthetic'}
    reader = Mock(side_effect=[profile(), {'POSTGRES_PASSWORD': 'dbfixture'}])
    monkeypatch.setattr(manager, 'private_values', reader)
    manager.read_master_profile(engine); engine.read_database_password()
    assert all(c.args[1] == manager.MASTER for c in reader.call_args_list)
    assert 'ZAI_API_KEY' not in manager.PROFILE_FIELDS and 'CLOUDFLARE_API_TOKEN' not in manager.PROFILE_FIELDS


def test_unowned_listener_refuses_start_stop_before_keys_db_or_signal(engine, monkeypatch):
    engine.IDENTITY = IdentityFile()
    monkeypatch.setattr(engine, 'listeners', lambda: [{'address': '127.0.0.1:8084', 'pids': [999]}])
    forbidden = Mock(side_effect=AssertionError('No foreign action'))
    monkeypatch.setattr(manager, 'read_master_profile', forbidden)
    monkeypatch.setattr(engine, 'Database', forbidden)
    monkeypatch.setattr(engine, 'verified_pidfd', forbidden)
    with pytest.raises(engine.Refusal, match='unowned'): manager.start(engine, SimpleNamespace(pw_uid=1000))
    with pytest.raises(engine.Refusal, match='unowned'): engine.stop(SimpleNamespace(pw_uid=1000))
    forbidden.assert_not_called()


@pytest.mark.parametrize('bad', [None, 'model', 'backup', 'paid', 'other', 'local', 'live', 'missing_false'])
def test_readiness_zero_generation_only_fixed_free_router_and_travel(engine, monkeypatch, bad):
    status = readiness()
    if bad == 'model': status['openrouter']['model'] = 'paid-model'
    elif bad == 'backup': status['openrouter']['backup_model'] = 'another:free'
    elif bad == 'paid': status['openrouter']['paid_fallback'] = True
    elif bad == 'other': status['zai']['configured'] = True
    elif bad == 'local': status['ollama']['configured'] = True
    elif bad == 'live': status['duffel']['mode'] = 'live'
    elif bad == 'missing_false': del status['cloudflare']
    paths = []
    def get(path):
        paths.append(path)
        assert path in {'/health', '/api/v1/providers/status'}
        return 200, {'database': 'ready', 'application': 'backend', 'supplier_booking': False} if path == '/health' else status
    monkeypatch.setattr(engine, 'http', get)
    if bad is None:
        assert manager.require_ready(engine)['pay_zero_policy']['sdk_max_price'] == 0
    else:
        with pytest.raises(engine.Refusal): manager.require_ready(engine)
    assert paths == ['/health', '/api/v1/providers/status']


def test_existing_database_only_no_create_or_hba(engine):
    db = SimpleNamespace(verify_same=Mock(), sql=Mock(return_value='otravel|false|true'), receipt={}, create=Mock(), allow_local_database=Mock())
    manager.require_existing_database(engine, db)
    assert db.receipt['database_created'] is False and db.receipt['hba_modified_by_manager'] is False
    db.create.assert_not_called(); db.allow_local_database.assert_not_called()
