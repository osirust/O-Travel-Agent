"""Pure CF-controller guards: no native paths, credentials, Docker or live HTTP."""
import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('cf_backend_test', ROOT / 'scripts/after80_cf_backend.py')
manager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manager)


@pytest.fixture
def engine():
    return manager.new_engine()


def profile():
    return {'CLOUDFLARE_API_TOKEN': 'synthetic-cf-token', 'CLOUDFLARE_ACCOUNT_ID': 'a' * 32,
        'CLOUDFLARE_FREE_TIER_CONFIRMED': 'true', 'LITEAPI_API_KEY': 'sand_synthetic',
        'DUFFEL_ACCESS_TOKEN': 'duffel_test_synthetic', 'SERPAPI_API_KEY': 'synthetic-serp',
        'SERPAPI_FREE_TIER_CONFIRMED': 'true', 'LITEAPI_MODE': 'sandbox'}


class IdentityFile:
    def __init__(self, data=None, mode=0o600):
        self.data, self.mode = data, mode

    def is_file(self):
        return self.data is not None

    def stat(self):
        return SimpleNamespace(st_uid=0, st_mode=self.mode)

    def read_text(self, **kwargs):
        return json.dumps(self.data)


def owned(engine, monkeypatch):
    current = {'pid': 12345, 'uid': 1000, 'start_ticks': 99, 'argv': deepcopy(engine.ARGV),
        'cwd': str(manager.SOURCE), 'executable': str(engine.PYTHON.resolve())}
    manifest = {'format': engine.MANIFEST_FORMAT, 'files': [{'path': 'otravel/api.py', 'sha256': 'fixture'}]}
    saved = {**deepcopy(current), 'created_by': manager.CREATOR, 'database': manager.DATABASE,
        'port': manager.PORT, 'source_manifest': manifest}
    engine.IDENTITY = IdentityFile(saved)
    monkeypatch.setattr(engine, 'process_identity', lambda _: deepcopy(current))
    return saved, current


def readiness():
    status = {key: {'configured': False} for key in ('gemini', 'openrouter', 'groq', 'zai', 'ollama', 'hotelbeds', 'ratehawk', 'travelpayouts')}
    status.update({'_runtime': {'llm_provider': 'cloudflare', 'model': manager.MODEL},
        'cloudflare': {'configured': True, 'free_tier_confirmed': True, 'paid_fallback': False},
        'liteapi': {'configured': True, 'mode': 'sandbox'}, 'duffel': {'configured': True, 'mode': 'sandbox'},
        'serpapi': {'configured': True, 'free_tier_confirmed': True, 'source_type': 'LIVE_INDICATIVE'},
        'open_meteo': {'configured': True, 'requires_key': False}})
    return status


def test_fresh_core_is_own_namespace_paths_loopback_creator_and_evidence():
    original = {name: (ROOT / name).read_bytes() for name in ('scripts/local_backend.py', 'scripts/after80_backend.py', 'scripts/zai_eval_backend.py')}
    first, second = manager.new_engine(), manager.new_engine()
    assert first is not second and first.SAFE_CONTEXT is not second.SAFE_CONTEXT
    assert first.SOURCE == Path('/home/aegml/projects/otravel-after80-20261026')
    assert first.DATABASE == 'otravel_zai_eval' and first.PORT == 8084
    assert 'after80-cf-backend-8084' in str(first.IDENTITY)
    assert 'after80-cf-backend-20261026' in str(first.EVIDENCE)
    assert first.ARGV[-5:] == ['--host', '127.0.0.1', '--port', '8084', '--no-access-log']
    assert manager.CREATOR == 'after80_cf_backend.py'
    assert all((ROOT / name).read_bytes() == data for name, data in original.items())


def test_environment_whitelist_has_no_zai_key_or_flags_and_no_inherited_credentials(engine, monkeypatch):
    for key in (*engine.KEYS, 'ZAI_MODEL', 'ZAI_FREE_TIER_CONFIRMED', 'DATABASE_URL'):
        monkeypatch.setenv(key, 'MUST-NOT-INHERIT')
    values = {**profile(), 'ZAI_API_KEY': 'FORBIDDEN_EXTRA', 'GUEST_NATIONALITY': 'RU', 'CLOUDFLARE_MODEL': 'paid-model'}
    env = manager.environment(engine, SimpleNamespace(password='synthetic:#@', ip='172.18.0.2'), values)
    assert env['LLM_PROVIDER'] == 'cloudflare' and env['CLOUDFLARE_MODEL'] == manager.MODEL
    assert env['CLOUDFLARE_API_TOKEN'] == profile()['CLOUDFLARE_API_TOKEN']
    assert env['CLOUDFLARE_FREE_TIER_CONFIRMED'] == 'true'
    assert env['MAX_CLOUDFLARE_LLM_CALLS_PER_DAY'] == '2000'
    assert env['MAX_LLM_CALLS_PER_DAY'] == '2000' and env['MAX_LLM_CALLS_PER_RUN'] == '1'
    assert env['MAX_LOCAL_LLM_CALLS_PER_DAY'] == '0' and env['OPENROUTER_BACKUP_MODEL'] == ''
    assert env['DEFAULT_SEARCH_MODE'] == 'HYBRID' and env['DEFAULT_FLIGHT_PROVIDER'] == 'duffel'
    assert env['DATABASE_URL'].endswith('/otravel_zai_eval') and 'synthetic%3A%23%40' in env['DATABASE_URL']
    assert not any(k.startswith('ZAI_') or 'NATIONALITY' in k for k in env)
    assert all(k not in env for k in engine.KEYS if k not in {*manager.CF_KEYS, *manager.TRAVEL_KEYS})
    assert not any(x in json.dumps(env) for x in ('MUST-NOT-INHERIT', 'FORBIDDEN_EXTRA', 'paid-model'))


@pytest.mark.parametrize('field,value', [
    ('CLOUDFLARE_FREE_TIER_CONFIRMED', 'false'), ('SERPAPI_FREE_TIER_CONFIRMED', False),
    ('CLOUDFLARE_ACCOUNT_ID', 'a' * 31), ('CLOUDFLARE_ACCOUNT_ID', 'g' * 32), ('CLOUDFLARE_ACCOUNT_ID', 'a' * 33),
    ('CLOUDFLARE_API_TOKEN', ''), ('CLOUDFLARE_API_TOKEN', 'bad key'),
    ('LITEAPI_MODE', 'production'), ('LITEAPI_MODE', None), ('LITEAPI_API_KEY', 'prod_forbidden'),
    ('DUFFEL_ACCESS_TOKEN', 'duffel_live_forbidden'), ('DUFFEL_ACCESS_TOKEN', 'duffel_test_'), ('SERPAPI_API_KEY', ''),
])
def test_paid_live_unconfirmed_and_malformed_profile_refuses_before_launch(engine, field, value):
    with pytest.raises(engine.Refusal):
        manager.environment(engine, SimpleNamespace(password='fixture', ip='172.18.0.2'), {**profile(), field: value})


class PrivateFile:
    def __init__(self, text, mode=0o600, symlink=False):
        self.text, self.mode, self.symlink = text, mode, symlink

    def is_file(self):
        return True

    def is_symlink(self):
        return self.symlink

    def resolve(self):
        return self

    def stat(self):
        return SimpleNamespace(st_mode=self.mode)

    def read_text(self, **kwargs):
        return self.text


def test_master_whitelist_never_selects_source_zai_or_interpolates(engine):
    fake = PrivateFile('ZAI_API_KEY=not-selected\nCLOUDFLARE_API_TOKEN=${ZAI_API_KEY}\nCLOUDFLARE_ACCOUNT_ID=' + 'a' * 32)
    assert manager.private_values(engine, fake, ('CLOUDFLARE_ACCOUNT_ID',)) == {'CLOUDFLARE_ACCOUNT_ID': 'a' * 32}
    assert manager.private_values(engine, fake, ('CLOUDFLARE_API_TOKEN',))['CLOUDFLARE_API_TOKEN'] == '${ZAI_API_KEY}'


@pytest.mark.parametrize('fake', [PrivateFile('CLOUDFLARE_API_TOKEN=x', 0o644), PrivateFile('CLOUDFLARE_API_TOKEN=x', symlink=True),
    PrivateFile('CLOUDFLARE_API_TOKEN=a\nCLOUDFLARE_API_TOKEN=b')])
def test_master_private_file_and_duplicate_guards(engine, fake):
    with pytest.raises(engine.Refusal):
        manager.private_values(engine, fake, ('CLOUDFLARE_API_TOKEN',))


def test_profile_and_database_read_only_master_with_separate_whitelists(engine, monkeypatch):
    reader = Mock(side_effect=[profile(), {'POSTGRES_USER': 'otravel', 'POSTGRES_DB': 'otravel', 'POSTGRES_PASSWORD': 'fixture'}])
    monkeypatch.setattr(manager, 'private_values', reader)
    assert manager.read_master_profile(engine) == profile()
    assert engine.read_database_password() == 'fixture'
    assert [call.args for call in reader.call_args_list] == [(engine, manager.MASTER, manager.PROFILE_FIELDS), (engine, manager.MASTER, manager.DB_FIELDS)]
    assert manager.SOURCE / '.env' not in [call.args[1] for call in reader.call_args_list]


@pytest.mark.parametrize('field,value', [('created_by', 'after80_backend.py'), ('database', 'otravel'), ('port', 8083), ('uid', 0), ('cwd', '/foreign')])
def test_old_zai_or_foreign_identity_never_authorizes_signalling(engine, monkeypatch, field, value):
    saved, _ = owned(engine, monkeypatch)
    saved[field] = value
    signal = Mock(side_effect=AssertionError('No foreign process signal'))
    monkeypatch.setattr(engine, 'verified_pidfd', signal)
    with pytest.raises(engine.Refusal):
        engine.stop(SimpleNamespace(pw_uid=1000))
    signal.assert_not_called()


@pytest.mark.parametrize('field,value', [('pid', 99), ('start_ticks', 100), ('argv', ['foreign']), ('cwd', '/foreign')])
def test_pid_reuse_or_identity_change_refuses_before_credentials(engine, monkeypatch, field, value):
    _, current = owned(engine, monkeypatch)
    current[field] = value
    reader = Mock(side_effect=AssertionError('No key reads'))
    monkeypatch.setattr(manager, 'read_master_profile', reader)
    with pytest.raises(engine.Refusal, match='identity_changed'):
        manager.start(engine, SimpleNamespace(pw_uid=1000))
    reader.assert_not_called()


def test_foreign_zai_listener_refuses_start_and_stop_without_key_or_db_reads(engine, monkeypatch):
    engine.IDENTITY = IdentityFile()
    monkeypatch.setattr(engine, 'listeners', lambda: [{'address': '127.0.0.1:8084', 'pids': [999]}])
    reader = Mock(side_effect=AssertionError('No credentials read'))
    database = Mock(side_effect=AssertionError('No DB operations'))
    monkeypatch.setattr(manager, 'read_master_profile', reader)
    monkeypatch.setattr(engine, 'Database', database)
    signal = Mock(side_effect=AssertionError('No process signals'))
    monkeypatch.setattr(engine, 'verified_pidfd', signal)
    with pytest.raises(engine.Refusal, match='unowned'):
        manager.start(engine, SimpleNamespace(pw_uid=1000))
    with pytest.raises(engine.Refusal, match='unowned'):
        engine.stop(SimpleNamespace(pw_uid=1000))
    reader.assert_not_called(); database.assert_not_called(); signal.assert_not_called()


@pytest.mark.parametrize('bad', [None, 'model', 'cf_free', 'serp_free', 'live', 'zai', 'zai_flag', 'missing_false', 'paid'])
def test_readiness_metadata_only_exact_cf_travel_profile(engine, monkeypatch, bad):
    status = readiness()
    if bad == 'model': status['_runtime']['model'] = 'other-model'
    elif bad == 'cf_free': status['cloudflare']['free_tier_confirmed'] = False
    elif bad == 'serp_free': status['serpapi']['free_tier_confirmed'] = False
    elif bad == 'live': status['duffel']['mode'] = 'live'
    elif bad == 'zai': status['zai']['configured'] = True
    elif bad == 'zai_flag': status['zai']['free_tier_confirmed'] = True
    elif bad == 'missing_false': del status['zai']
    elif bad == 'paid': status['cloudflare']['paid_fallback'] = True
    paths = []
    def metadata_get(path):
        paths.append(path)
        assert path in {'/health', '/api/v1/providers/status'}
        return 200, {'database': 'ready', 'application': 'backend', 'supplier_booking': False} if path == '/health' else status
    monkeypatch.setattr(engine, 'http', metadata_get)
    if bad is None:
        assert manager.require_ready(engine)['generation_performed'] is False
    else:
        with pytest.raises(engine.Refusal): manager.require_ready(engine)
    assert paths == ['/health', '/api/v1/providers/status']


def test_status_has_only_owned_metadata_gets_and_no_credential_or_database_reads(engine, monkeypatch, tmp_path):
    saved, _ = owned(engine, monkeypatch)
    monkeypatch.setattr(engine, 'source_manifest', lambda: deepcopy(saved['source_manifest']))
    monkeypatch.setattr(engine, 'listeners', lambda: [{'address': '127.0.0.1:8084', 'pids': [12345]}])
    monkeypatch.setattr(engine, 'require_owned_listener', lambda _: None)
    forbidden = Mock(side_effect=AssertionError('No secret or database access'))
    monkeypatch.setattr(manager, 'read_master_profile', forbidden)
    monkeypatch.setattr(engine, 'Database', forbidden)
    paths = []
    def metadata(path):
        paths.append(path)
        if path == '/health': return 200, {'database': 'ready', 'application': 'backend', 'supplier_booking': False}
        if path == '/api/v1/providers/status': return 200, readiness()
        assert path in {'/debug', '/docs'}
        return 200, None
    monkeypatch.setattr(engine, 'http', metadata)
    engine.EVIDENCE = tmp_path / 'receipt.json'
    record = manager.save_evidence(engine, 'status', SimpleNamespace(pw_uid=1000))
    assert record['status'] == 'RUNNING' and record['loaded_manifest']['matches_current']
    assert record['source_env_read'] is False and record['limits']['cloudflare_daily_engineering_bound'] == 2000
    assert record['limits']['vendor_quota'] == 'UNKNOWN' and record['credentials_recorded'] is False
    assert paths == ['/health', '/api/v1/providers/status', '/docs', '/debug']
    forbidden.assert_not_called()
    with pytest.raises(engine.Refusal, match='not_allowlisted'):
        manager.save_evidence(engine, 'status', SimpleNamespace(pw_uid=1000), {'ZAI_API_KEY': 'fixture'})


def test_status_foreign_listener_is_metadata_only_and_does_not_probe_zai_http(engine, monkeypatch, tmp_path):
    engine.IDENTITY = IdentityFile()
    engine.EVIDENCE = tmp_path / 'unowned-status.json'
    monkeypatch.setattr(engine, 'listeners', lambda: [{'address': '127.0.0.1:8084', 'pids': [999]}])
    monkeypatch.setattr(engine, 'source_manifest', lambda: {'format': engine.MANIFEST_FORMAT, 'files': []})
    forbidden = Mock(side_effect=AssertionError('No calls to foreign stand or credentials'))
    monkeypatch.setattr(engine, 'http', forbidden)
    monkeypatch.setattr(manager, 'read_master_profile', forbidden)
    monkeypatch.setattr(engine, 'Database', forbidden)
    result = manager.save_evidence(engine, 'status', SimpleNamespace(pw_uid=1000))
    assert result['status'] == 'UNOWNED_LISTENER' and result['process'] is None
    assert result['loaded_manifest']['status'] == 'NOT_RUNNING' and 'readiness' not in result
    forbidden.assert_not_called()


@pytest.mark.parametrize('reply', ['', 'wrong|false|true', 'otravel|true|true', 'otravel|false|false'])
def test_shared_database_missing_wrong_or_template_is_never_created(engine, reply):
    db = SimpleNamespace(verify_same=Mock(), sql=Mock(return_value=reply), receipt={}, create=Mock(), allow_local_database=Mock())
    with pytest.raises(engine.Refusal): manager.require_existing_database(engine, db)
    db.create.assert_not_called(); db.allow_local_database.assert_not_called()


def test_existing_database_check_does_not_create_db_or_modify_hba(engine):
    db = SimpleNamespace(verify_same=Mock(), sql=Mock(return_value='otravel|false|true'), receipt={}, create=Mock(), allow_local_database=Mock())
    manager.require_existing_database(engine, db)
    assert db.receipt == {'database_created': False, 'hba_modified_by_manager': False}
    db.create.assert_not_called(); db.allow_local_database.assert_not_called()


class RegistryKey:
    def __enter__(self): return self
    def __exit__(self, *args): return False


@pytest.mark.parametrize('base', [r'C:\WSL\AegML-Hackathon', r'\\?\C:\WSL\AegML-Hackathon', r'D:\WSL\foreign'])
def test_windows_dispatch_uses_only_fixed_distro_and_own_script(monkeypatch, base):
    registry = SimpleNamespace(HKEY_CURRENT_USER=1, OpenKey=lambda *args: RegistryKey(), QueryInfoKey=lambda _: (1, 0, 0), EnumKey=lambda *args: 'unit',
        QueryValueEx=lambda _, name: ('AegML-Hackathon' if name == 'DistributionName' else base, 0))
    monkeypatch.setitem(manager.sys.modules, 'winreg', registry)
    monkeypatch.setattr(manager, 'Path', lambda _: SimpleNamespace(resolve=lambda: r'E:\work\aegml\scripts\after80_cf_backend.py'))
    call = Mock(return_value=SimpleNamespace(returncode=0, stdout='fixture-status\n', stderr=''))
    monkeypatch.setattr(manager.subprocess, 'run', call)
    if base.startswith('D:'):
        with pytest.raises(ValueError): manager.windows_dispatch('status')
        call.assert_not_called()
    else:
        assert manager.windows_dispatch('status') == 0
        argv = call.call_args.args[0]
        assert argv[:5] == ['wsl.exe', '-d', 'AegML-Hackathon', '-u', 'root']
        assert argv[-3:] == ['/mnt/e/work/aegml/scripts/after80_cf_backend.py', '--action', 'status']


def test_cli_cannot_override_source_database_or_port(monkeypatch):
    factory = Mock(side_effect=AssertionError('No lifecycle effects'))
    monkeypatch.setattr(manager, 'new_engine', factory)
    with pytest.raises(SystemExit): manager.main(['--action', 'start', '--port', '8083'])
    factory.assert_not_called()


def fake_start(engine, monkeypatch, tmp_path, *, pending=0, cf_limit=2000, model_after=5):
    engine.IDENTITY = IdentityFile()
    engine.LOG, engine.EVIDENCE = tmp_path / 'private.log', tmp_path / 'receipt.json'
    manifest = {'format': engine.MANIFEST_FORMAT, 'files': [{'path': 'otravel/api.py', 'sha256': 'synthetic'}]}
    current = {'pid': 12345, 'uid': 1000, 'start_ticks': 99, 'argv': deepcopy(engine.ARGV),
        'cwd': str(manager.SOURCE), 'executable': str(engine.PYTHON.resolve())}
    monkeypatch.setattr(engine, 'listeners', list)
    monkeypatch.setattr(engine, 'source_manifest', lambda: deepcopy(manifest))
    monkeypatch.setattr(engine, 'process_identity', lambda _: deepcopy(current))
    monkeypatch.setattr(engine, 'require_owned_listener', lambda _: None)
    monkeypatch.setattr(manager, 'read_master_profile', lambda _: profile())
    before = {'sessions': 10, 'agent_runs': 20, 'agent_events': 100, 'pending_or_running': pending, 'model_call_started': 5}
    after = {**before, 'model_call_started': model_after}
    database = SimpleNamespace(password='synthetic-db-password', ip='172.18.0.2', receipt={},
        verify_same=Mock(), sql=Mock(return_value='otravel|false|true'), counts=Mock(side_effect=[before, after]),
        create=Mock(side_effect=AssertionError('No database creation')), allow_local_database=Mock(side_effect=AssertionError('No HBA writes')))
    monkeypatch.setattr(engine, 'Database', lambda: database)
    imported = {'api_file': str(manager.SOURCE / 'otravel/api.py'), 'provider': 'cloudflare', 'model': manager.MODEL,
        'cf_daily_limit': cf_limit, 'run_limit': 1, 'local_limit': 0, 'search_mode': 'HYBRID', 'zai_environment_present': False}
    capture = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps(imported)))
    monkeypatch.setattr(engine, 'capture', capture)
    monkeypatch.setattr(engine, 'http', lambda path: (200, {'database': 'ready', 'application': 'backend', 'supplier_booking': False})
        if path == '/health' else (200, readiness()) if path == '/api/v1/providers/status' else (200, None))
    stored = []
    def atomic(path, data, mode=0o600):
        stored.append((path, deepcopy(data), mode))
        if path is engine.IDENTITY:
            engine.IDENTITY.data = deepcopy(data)
    monkeypatch.setattr(engine, 'atomic_json', atomic)
    launch = Mock(return_value=SimpleNamespace(pid=12345, poll=lambda: None))
    monkeypatch.setattr(manager.subprocess, 'Popen', launch)
    return database, capture, launch, stored


def test_start_mock_proves_exact_import_limits_private_launch_and_zero_startup_generation(engine, monkeypatch, tmp_path):
    database, capture, launch, stored = fake_start(engine, monkeypatch, tmp_path)
    result = manager.start(engine, SimpleNamespace(pw_uid=1000, pw_gid=1000))
    assert result['status'] == 'RUNNING' and result['import_check']['cf_daily_limit'] == 2000
    assert result['database_counts_before']['model_call_started'] == result['database_counts_after']['model_call_started']
    assert result['inference_requested_by_manager'] is False and result['source_env_read'] is False
    assert launch.call_args.args[0] == engine.ARGV
    env = launch.call_args.kwargs['env']
    assert env['LLM_PROVIDER'] == 'cloudflare' and env['MAX_CLOUDFLARE_LLM_CALLS_PER_DAY'] == '2000'
    assert not any(k.startswith('ZAI_') for k in env)
    assert launch.call_args.kwargs['user'] == 1000 and launch.call_args.kwargs['umask'] == 0o077
    assert capture.call_args.kwargs['env'] == env
    identity = next(data for path, data, _ in stored if path is engine.IDENTITY)
    assert identity['created_by'] == manager.CREATOR and identity['source_manifest']
    public = next(data for path, data, _ in stored if path == engine.EVIDENCE)
    assert not any(secret in json.dumps(public) for secret in (*profile().values(), 'synthetic-db-password') if len(secret) > 8)
    database.create.assert_not_called(); database.allow_local_database.assert_not_called()


def test_pending_shared_runs_prevent_start_and_any_automatic_replay(engine, monkeypatch, tmp_path):
    database, _, launch, _ = fake_start(engine, monkeypatch, tmp_path, pending=1)
    with pytest.raises(engine.Refusal, match='pending_runs'):
        manager.start(engine, SimpleNamespace(pw_uid=1000, pw_gid=1000))
    launch.assert_not_called()
    database.create.assert_not_called(); database.allow_local_database.assert_not_called()


def test_old_source_without_explicit_cf2000_support_refuses_before_database_launch(engine, monkeypatch, tmp_path):
    database, _, launch, _ = fake_start(engine, monkeypatch, tmp_path, cf_limit=40)
    with pytest.raises(engine.Refusal, match='explicit_limits'):
        manager.start(engine, SimpleNamespace(pw_uid=1000, pw_gid=1000))
    database.verify_same.assert_not_called(); launch.assert_not_called()


def test_start_rejects_unexpected_generation_without_force_stopping_process(engine, monkeypatch, tmp_path):
    _, _, launch, _ = fake_start(engine, monkeypatch, tmp_path, model_after=6)
    stop = Mock(side_effect=AssertionError('No automatic stop after failed startup assertion'))
    monkeypatch.setattr(engine, 'stop', stop)
    with pytest.raises(engine.Refusal, match='unexpected_inference'):
        manager.start(engine, SimpleNamespace(pw_uid=1000, pw_gid=1000))
    launch.assert_called_once(); stop.assert_not_called()
