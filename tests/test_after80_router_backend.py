"""Router-specific pure profile guards; shared lifecycle mechanics remain fake."""
import importlib.util
import json
from copy import deepcopy
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


def test_explicit50_env_changes_only_daily_bound_and_keeps_free_profile():
    default, expanded = manager.new_engine(), manager.new_engine(50)
    db = SimpleNamespace(password='synthetic', ip='172.18.0.2')
    before, after = manager.environment(default, db, profile()), manager.environment(expanded, db, profile())
    assert {key for key in before if before[key] != after[key]} == {'MAX_LLM_CALLS_PER_DAY'}
    assert before['MAX_LLM_CALLS_PER_DAY'] == '40' and after['MAX_LLM_CALLS_PER_DAY'] == '50'
    assert after['OPENROUTER_MODEL'].endswith(':free') and after['OPENROUTER_BACKUP_MODEL'] == ''
    assert after['MAX_LLM_CALLS_PER_RUN'] == '1' and after['MAX_LOCAL_LLM_CALLS_PER_DAY'] == '0'


@pytest.mark.parametrize('bound', [None, True, False, 40.0, 50.0, '40', '50', 0, 41, 51, 2000])
def test_noncanonical_or_unbounded_engine_configuration_is_refused(bound):
    with pytest.raises(ValueError, match='daily_bound'):
        manager.new_engine(bound)


@pytest.mark.parametrize('bound', [None, True, 50.0, '50', 2000])
def test_manually_corrupted_requested_or_recorded_bound_is_refused(engine, bound):
    engine.ROUTER_DAILY_BOUND = bound
    with pytest.raises(engine.Refusal, match='daily_bound'):
        manager.environment(engine, SimpleNamespace(password='fake', ip='172.18.0.2'), profile())
    with pytest.raises(engine.Refusal, match='daily_bound'):
        manager.recorded_daily_bound(engine, {'daily_engineering_bound': bound})


def router_owned(engine, monkeypatch, bound=None):
    current = {'pid': 12345, 'uid': 1000, 'start_ticks': 99, 'argv': deepcopy(engine.ARGV),
        'cwd': str(manager.SOURCE), 'executable': str(engine.PYTHON.resolve())}
    manifest = {'format': engine.MANIFEST_FORMAT, 'files': [{'path': 'otravel/api.py', 'sha256': 'fixture'}]}
    saved = {**deepcopy(current), 'created_by': manager.CREATOR, 'database': manager.DATABASE,
        'port': manager.PORT, 'source_manifest': manifest}
    if bound is not None:
        saved['daily_engineering_bound'] = bound
    engine.IDENTITY = IdentityFile(saved)
    monkeypatch.setattr(engine, 'process_identity', lambda _: deepcopy(current))
    monkeypatch.setattr(engine, 'require_owned_listener', lambda _: None)
    monkeypatch.setattr(engine, 'source_manifest', lambda: deepcopy(manifest))
    return saved, current


@pytest.mark.parametrize('requested,saved', [(50, 40), (40, 50), (50, None)])
def test_reusing_different_or_legacy_bound_requires_explicit_stop_start(monkeypatch, requested, saved):
    engine = manager.new_engine(requested)
    router_owned(engine, monkeypatch, saved)
    forbidden = Mock(side_effect=AssertionError('No key, DB, metadata probe or launch for mismatched bound'))
    monkeypatch.setattr(manager, 'read_master_profile', forbidden)
    monkeypatch.setattr(manager, 'require_ready', forbidden)
    monkeypatch.setattr(engine, 'Database', forbidden)
    monkeypatch.setattr(engine, 'save_evidence', forbidden)
    with pytest.raises(engine.Refusal, match='explicit_stop_start'):
        manager.start(engine, SimpleNamespace(pw_uid=1000))
    forbidden.assert_not_called()


@pytest.mark.parametrize('bound', [40, 50])
def test_same_bound_process_reuse_never_reads_keys_or_resets_ledger(monkeypatch, bound):
    engine = manager.new_engine(bound)
    router_owned(engine, monkeypatch, bound)
    forbidden = Mock(side_effect=AssertionError('Reuse must not create DB, read keys or reset counters'))
    monkeypatch.setattr(manager, 'read_master_profile', forbidden)
    monkeypatch.setattr(engine, 'Database', forbidden)
    monkeypatch.setattr(manager, 'require_ready', lambda _: {})
    saved = Mock(return_value={'status': 'reused'})
    monkeypatch.setattr(engine, 'save_evidence', saved)
    assert manager.start(engine, SimpleNamespace(pw_uid=1000)) == {'status': 'reused'}
    saved.assert_called_once_with('start_reused_owned_process', SimpleNamespace(pw_uid=1000))
    forbidden.assert_not_called()


def test_status_reports_recorded50_with_default40_cli_engine_without_mutation(engine, monkeypatch, tmp_path):
    router_owned(engine, monkeypatch, 50)
    monkeypatch.setattr(engine, 'listeners', lambda: [{'address': '127.0.0.1:8084', 'pids': [12345]}])
    engine.EVIDENCE = tmp_path / 'status.json'
    forbidden = Mock(side_effect=AssertionError('Status cannot read keys or DB'))
    monkeypatch.setattr(manager, 'read_master_profile', forbidden)
    monkeypatch.setattr(engine, 'Database', forbidden)

    def metadata(path):
        if path == '/health':
            return 200, {'database': 'ready', 'application': 'backend', 'supplier_booking': False}
        return (200, readiness()) if path == '/api/v1/providers/status' else (200, None)

    monkeypatch.setattr(engine, 'http', metadata)
    record = manager.save_evidence(engine, 'status', SimpleNamespace(pw_uid=1000))
    assert record['limits']['daily_engineering_bound'] == 50 and engine.ROUTER_DAILY_BOUND == 40
    assert record['readiness']['pay_zero_policy']['sdk_max_price'] == 0
    assert record['credentials_recorded'] is False and record['inference_requested_by_manager'] is False
    forbidden.assert_not_called()


def test_stopped_owned50_identity_keeps_its_recorded_bound_in_evidence(engine, monkeypatch, tmp_path):
    saved, _ = router_owned(engine, monkeypatch, 50)
    saved['stopped_at_utc'] = '2026-10-27T04:00:00+00:00'
    engine.EVIDENCE = tmp_path / 'stopped.json'
    monkeypatch.setattr(engine, 'listeners', list)
    forbidden = Mock(side_effect=AssertionError('Stopped status cannot contact runtime, keys or DB'))
    monkeypatch.setattr(engine, 'http', forbidden)
    monkeypatch.setattr(engine, 'Database', forbidden)
    record = manager.save_evidence(engine, 'status', SimpleNamespace(pw_uid=1000))
    assert record['status'] == 'STOPPED' and record['limits']['daily_engineering_bound'] == 50
    forbidden.assert_not_called()


@pytest.mark.parametrize('argv,bound', [(['--action', 'status'], 40), (['--action', 'start', '--daily-bound', '50'], 50)])
def test_windows_cli_dispatch_preserves_default_or_explicit_bound_without_runtime(monkeypatch, argv, bound):
    monkeypatch.setattr(manager.sys, 'platform', 'win32')
    child = Mock(return_value=0)
    monkeypatch.setattr(manager, 'windows_dispatch', child)
    assert manager.main(argv) == 0
    child.assert_called_once_with('status' if bound == 40 else 'start', bound)


@pytest.mark.parametrize('bound', ['41', '51', '2000', '50.0', 'True', '-1'])
def test_invalid_cli_bound_exits_before_any_engine_or_runtime(monkeypatch, bound):
    forbidden = Mock(side_effect=AssertionError('Invalid bound cannot reach runtime'))
    monkeypatch.setattr(manager, 'new_engine', forbidden)
    monkeypatch.setattr(manager, 'windows_dispatch', forbidden)
    with pytest.raises(SystemExit) as error:
        manager.main(['--action', 'start', '--daily-bound', bound])
    assert error.value.code == 2
    forbidden.assert_not_called()


@pytest.mark.parametrize('imported_bound', [40, 50])
def test_fresh50_start_attests_bound_records_identity_and_preserves_39_counter(monkeypatch, tmp_path, imported_bound):
    engine = manager.new_engine(50)
    engine.IDENTITY = IdentityFile()
    engine.LOG = tmp_path / 'synthetic.log'
    manifest = {'format': engine.MANIFEST_FORMAT, 'files': [{'path': 'otravel/api.py', 'sha256': 'fixture'}]}
    current = {'pid': 12345, 'uid': 1000, 'start_ticks': 99, 'argv': deepcopy(engine.ARGV),
        'cwd': str(manager.SOURCE), 'executable': str(engine.PYTHON.resolve())}
    counts = {'sessions': 2, 'agent_runs': 3, 'agent_events': 10, 'pending_or_running': 0, 'model_call_started': 39}
    db = SimpleNamespace(password='synthetic', ip='172.18.0.2', receipt={}, verify_same=Mock(),
        sql=Mock(return_value='otravel|false|true'), counts=Mock(side_effect=lambda: deepcopy(counts)))
    monkeypatch.setattr(engine, 'Database', lambda: db)
    monkeypatch.setattr(engine, 'source_manifest', lambda: deepcopy(manifest))
    monkeypatch.setattr(engine, 'listeners', list)
    monkeypatch.setattr(engine, 'require_owned_listener', lambda _: None)
    monkeypatch.setattr(engine, 'process_identity', lambda _: deepcopy(current))
    monkeypatch.setattr(manager, 'read_master_profile', lambda _: profile())
    monkeypatch.setattr(manager, 'require_ready', lambda _: {})
    facts = {'api_file': str(manager.SOURCE / 'otravel/api.py'), 'provider': 'openrouter', 'model': manager.MODEL,
        'daily_limit': imported_bound, 'run_limit': 1, 'local_limit': 0, 'search_mode': 'HYBRID', 'zai_environment_present': False}

    def capture(*args, **kwargs):
        assert kwargs['env']['MAX_LLM_CALLS_PER_DAY'] == '50'
        return SimpleNamespace(returncode=0, stdout=json.dumps(facts))

    monkeypatch.setattr(engine, 'capture', capture)
    launched = Mock(return_value=SimpleNamespace(pid=12345, poll=lambda: None))
    monkeypatch.setattr(manager.subprocess, 'Popen', launched)
    identities = []

    def save_identity(path, data, mode=0o600):
        assert path is engine.IDENTITY and mode == 0o600
        engine.IDENTITY.data = deepcopy(data)
        identities.append(deepcopy(data))

    monkeypatch.setattr(engine, 'atomic_json', save_identity)
    monkeypatch.setattr(engine, 'save_evidence', lambda action, account, extra=None: extra)
    account = SimpleNamespace(pw_uid=1000, pw_gid=1000)
    if imported_bound != 50:
        with pytest.raises(engine.Refusal, match='explicit_limits_mismatch'):
            manager.start(engine, account)
        launched.assert_not_called()
        db.sql.assert_not_called()
        assert identities == []
    else:
        record = manager.start(engine, account)
        assert identities[0]['daily_engineering_bound'] == 50 and len(identities) == 1
        assert record['import_check']['daily_limit'] == 50
        assert record['database_counts_before']['model_call_started'] == record['database_counts_after']['model_call_started'] == 39
        assert launched.call_args.kwargs['env']['MAX_LLM_CALLS_PER_DAY'] == '50'
        assert launched.call_args.kwargs['env']['OPENROUTER_MODEL'].endswith(':free')
        assert db.sql.call_count == 1 and db.sql.call_args.args[0].startswith('SELECT')
