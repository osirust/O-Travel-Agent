"""Pure lifecycle guards only: no real WSL, Docker, SQL, HTTP or generation."""
import ast
import base64
import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_after80_cf_backend import IdentityFile, PrivateFile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('gigachat_controller_test', ROOT/'scripts/gigachat_backend.py')
manager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manager)


@pytest.fixture
def engine():
    return manager.new_engine()


def profile():
    return {'GIGACHAT_AUTH_KEY': 'synthetic-base64-auth', 'GIGACHAT_FREEMIUM_CONFIRMED': 'true',
        'LITEAPI_API_KEY': 'sand_synthetic', 'DUFFEL_ACCESS_TOKEN': 'duffel_test_synthetic',
        'SERPAPI_API_KEY': 'synthetic-serp', 'SERPAPI_FREE_TIER_CONFIRMED': 'true', 'LITEAPI_MODE': 'sandbox'}


def readiness():
    status = {name: {'configured': False} for name in ('groq', 'gemini', 'cloudflare', 'openrouter', 'zai',
        'ollama', 'hotelbeds', 'ratehawk', 'travelpayouts')}
    status.update({'_runtime': {'llm_provider': 'gigachat', 'model': manager.MODEL},
        'gigachat': {'configured': True, 'model': manager.MODEL, 'freemium_confirmed': True, 'paid_fallback': False},
        'liteapi': {'configured': True, 'mode': 'sandbox'}, 'duffel': {'configured': True, 'mode': 'sandbox'},
        'serpapi': {'configured': True, 'free_tier_confirmed': True, 'source_type': 'LIVE_INDICATIVE'},
        'open_meteo': {'configured': True}})
    return status


def test_fixed_isolated_namespace_and_plan_has_no_runtime_or_key_actions(engine, monkeypatch, capsys):
    assert engine.SOURCE == Path('/home/aegml/projects/otravel-gigachat-20261027')
    assert engine.DATABASE == 'otravel_gigachat_eval' and engine.PORT == 8085
    assert engine.BASE == 'http://127.0.0.1:8085'
    assert manager.CREATOR == 'gigachat_backend.py'
    assert 'gigachat-backend-8085' in str(engine.RUNTIME)
    assert engine.EVIDENCE.name == 'gigachat-backend-20261027.json'
    no_action = Mock(side_effect=AssertionError('Plan must not enter runtime'))
    monkeypatch.setattr(manager, 'new_engine', no_action)
    monkeypatch.setattr(manager, 'windows_dispatch', no_action)
    monkeypatch.setattr(manager.sys, 'platform', 'win32')
    assert manager.main(['--action', 'plan']) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan['model'] == 'GigaChat-3-Ultra' and plan['provider'] == 'gigachat'
    assert plan['gigachat_daily_engineering_bound'] == 1000
    assert plan['credentials_read'] is False and plan['runtime_changed'] is False
    no_action.assert_not_called()


def test_environment_is_sterile_and_uses_only_selected_personal_freemium_keys(engine, monkeypatch):
    for key in (*engine.KEYS, 'GIGACHAT_AUTH_KEY'):
        monkeypatch.setenv(key, 'FORBIDDEN_INHERITED')
    selected = {**profile(), 'GROQ_API_KEY': 'FORBIDDEN', 'GIGACHAT_MODEL': 'paid-model',
        'MAX_GIGACHAT_LLM_CALLS_PER_DAY': '999999', 'GIGACHAT_CA_FILE': '/home/aegml/certs/official.pem'}
    env = manager.environment(engine, SimpleNamespace(ip='172.18.0.2', password='synthetic:@'), selected)
    assert env['LLM_PROVIDER'] == 'gigachat' and env['GIGACHAT_MODEL'] == manager.MODEL
    assert env['GIGACHAT_SCOPE'] == 'GIGACHAT_API_PERS'
    assert env['MAX_GIGACHAT_LLM_CALLS_PER_DAY'] == '1000' and env['MAX_LLM_CALLS_PER_DAY'] == '40'
    assert env['MAX_LLM_CALLS_PER_RUN'] == '1' and env['MAX_LOCAL_LLM_CALLS_PER_DAY'] == '0'
    assert env['GIGACHAT_CA_FILE'] == '/home/aegml/certs/official.pem'
    assert env['DATABASE_URL'].endswith('/otravel_gigachat_eval') and 'synthetic%3A%40' in env['DATABASE_URL']
    assert not {'GROQ_API_KEY', 'OPENROUTER_API_KEY', 'ZAI_API_KEY', 'GEMINI_API_KEY'}.intersection(env)
    assert all(env[name] == 'false' for name in ('GROQ_FREE_TIER_CONFIRMED', 'GEMINI_FREE_TIER_CONFIRMED',
        'ZAI_FREE_TIER_CONFIRMED', 'CLOUDFLARE_FREE_TIER_CONFIRMED', 'COHERE_EVALUATION_CONFIRMED'))
    assert 'FORBIDDEN' not in json.dumps(env)


@pytest.mark.parametrize('key,value', [('GIGACHAT_AUTH_KEY', ''), ('GIGACHAT_AUTH_KEY', 'with space'),
    ('GIGACHAT_FREEMIUM_CONFIRMED', False), ('SERPAPI_FREE_TIER_CONFIRMED', False),
    ('LITEAPI_MODE', 'live'), ('LITEAPI_API_KEY', 'prod_key'), ('DUFFEL_ACCESS_TOKEN', 'duffel_live_key'),
    ('GIGACHAT_CA_FILE', 'https://example.com/ca.pem'), ('GIGACHAT_CA_FILE', '/tmp/arbitrary.pem'),
    ('GIGACHAT_CA_FILE', '/home/aegml/../other.pem')])
def test_unsafe_or_unconfirmed_profile_is_refused(engine, key, value):
    with pytest.raises(engine.Refusal):
        manager.validate_profile(engine, {**profile(), key: value})


def test_master_selectors_never_read_source_env_or_other_provider_keys(engine, monkeypatch):
    fake = PrivateFile('GIGACHAT_AUTH_KEY=synthetic\nGROQ_API_KEY=FORBIDDEN\nPOSTGRES_PASSWORD=db-fixture')
    assert manager.private_values(engine, fake, manager.GIGACHAT_KEYS) == {'GIGACHAT_AUTH_KEY': 'synthetic'}
    reader = Mock(side_effect=[profile(), {'POSTGRES_PASSWORD': 'db-fixture'}])
    monkeypatch.setattr(manager, 'private_values', reader)
    manager.read_master_profile(engine)
    manager.read_database_password(engine)
    assert all(call.args[1] == manager.MASTER for call in reader.call_args_list)
    assert 'GIGACHAT_CA_FILE' in manager.PROFILE_FIELDS
    assert not {'GROQ_API_KEY', 'ZAI_API_KEY', 'OPENROUTER_API_KEY', 'GEMINI_API_KEY'}.intersection(manager.PROFILE_FIELDS)


@pytest.mark.parametrize('bad', [None, 'model', 'free', 'paid', 'groq', 'local', 'live', 'missing_false'])
def test_readiness_uses_only_two_status_gets_no_oauth_or_generation(engine, monkeypatch, bad):
    status = readiness()
    if bad == 'model': status['gigachat']['model'] = 'other-model'
    elif bad == 'free': status['gigachat']['freemium_confirmed'] = False
    elif bad == 'paid': status['gigachat']['paid_fallback'] = True
    elif bad == 'groq': status['groq']['configured'] = True
    elif bad == 'local': status['ollama']['configured'] = True
    elif bad == 'live': status['duffel']['mode'] = 'live'
    elif bad == 'missing_false': del status['openrouter']
    paths = []
    def http(path):
        paths.append(path)
        return 200, {'database': 'ready', 'application': 'backend', 'supplier_booking': False} if path == '/health' else status
    monkeypatch.setattr(engine, 'http', http)
    if bad is None:
        assert manager.require_ready(engine)['generation_performed'] is False
    else:
        with pytest.raises(engine.Refusal): manager.require_ready(engine)
    assert paths == ['/health', '/api/v1/providers/status']


def test_foreign_8085_listener_is_never_stopped_or_adopted(engine, monkeypatch):
    engine.IDENTITY = IdentityFile()
    monkeypatch.setattr(engine, 'listeners', lambda: [{'address': '127.0.0.1:8085', 'pids': [555]}])
    forbidden = Mock(side_effect=AssertionError('Foreign listener must stop the controller'))
    monkeypatch.setattr(manager, 'read_master_profile', forbidden)
    monkeypatch.setattr(engine, 'Database', forbidden)
    monkeypatch.setattr(engine, 'verified_pidfd', forbidden)
    with pytest.raises(engine.Refusal, match='unowned'): manager.start(engine, SimpleNamespace(pw_uid=1000))
    with pytest.raises(engine.Refusal, match='unowned'): engine.stop(SimpleNamespace(pw_uid=1000))
    forbidden.assert_not_called()


def test_scoped_hba_append_preserves_all_original_bytes_and_only_exact_own_rules(engine):
    before = '# baseline\r\nhost other_db other_role 10.1.1.1/32 scram-sha-256\r\n'
    after, receipt = manager.scoped_hba_plan(engine, before, '172.18.0.1', '172.18.0.0/16')
    assert after.startswith(before) and receipt['changed'] is True
    assert receipt['rules'] == ['host otravel_gigachat_eval otravel 127.0.0.1/32 scram-sha-256',
        'host otravel_gigachat_eval otravel 172.18.0.1/32 scram-sha-256']
    assert '/16 scram' not in after and '0.0.0.0/0' not in after
    again, second = manager.scoped_hba_plan(engine, after, '172.18.0.1', '172.18.0.0/16')
    assert again == after and second['changed'] is False


@pytest.mark.parametrize('client,subnet', [('8.8.8.8','172.18.0.0/16'), ('172.19.0.1','172.18.0.0/16'),
    ('::1','172.18.0.0/16'), ('172.18.0.1','0.0.0.0/0')])
def test_hba_plan_refuses_non_observed_private_bridge_client(engine, client, subnet):
    with pytest.raises(engine.Refusal): manager.scoped_hba_plan(engine, '', client, subnet)


@pytest.mark.parametrize('rule', ['host otravel_gigachat_eval all 0.0.0.0/0 trust',
    'host otravel_gigachat_eval otravel 172.18.0.0/16 scram-sha-256',
    'hostssl otravel_gigachat_eval otravel 172.18.0.1/32 scram-sha-256'])
def test_unexpected_existing_own_hba_rules_are_not_overwritten(engine, rule):
    with pytest.raises(engine.Refusal): manager.scoped_hba_plan(engine, rule+'\n', '172.18.0.1', '172.18.0.0/16')


def test_database_create_is_fixed_and_untracked_existing_database_refuses(engine, monkeypatch, tmp_path):
    engine.RUNTIME = tmp_path
    db = SimpleNamespace(identifier='owned-container', receipt={}, verify_same=Mock(),
        sql=Mock(side_effect=['', '', 'otravel|false|true', '4321']))
    monkeypatch.setattr(engine, 'atomic_json', Mock())
    manager.ensure_owned_database(engine, db)
    calls = [call.args[0] for call in db.sql.call_args_list]
    assert calls[1] == 'CREATE DATABASE otravel_gigachat_eval OWNER otravel;\n'
    assert not any(word in ''.join(calls).upper() for word in ('DROP ', 'TRUNCATE ', 'DELETE '))
    assert db.receipt['created_by'] == manager.CREATOR and db.receipt['database_created'] is True
    wrong = SimpleNamespace(identifier='owned-container', receipt={}, verify_same=Mock(), sql=Mock(return_value='otravel|false|true'))
    with pytest.raises(engine.Refusal, match='unowned'): manager.ensure_owned_database(engine, wrong)


def test_hba_setup_has_exact_byte_snapshot_and_only_reload_no_restart(engine, monkeypatch, tmp_path):
    engine.RUNTIME = tmp_path
    original = '# existing\r\nhost another_db another_role 10.0.0.1/32 scram-sha-256\r\n'
    after, _ = manager.scoped_hba_plan(engine, original, '172.18.0.1', '172.18.0.0/16')
    results = [SimpleNamespace(returncode=0, stdout='[{"prefsrc":"172.18.0.1"}]'),
        SimpleNamespace(returncode=0, stdout=base64.b64encode(original.encode()).decode()),
        SimpleNamespace(returncode=0, stdout=base64.b64encode(after.encode()).decode())]
    capture = Mock(side_effect=results)
    monkeypatch.setattr(engine, 'capture', capture)
    write = Mock()
    monkeypatch.setattr(engine, 'docker', write)
    monkeypatch.setattr(engine, 'atomic_json', Mock())
    db = SimpleNamespace(identifier='verified-owned', ip='172.18.0.2', subnet=manager.ipaddress.ip_network('172.18.0.0/16'),
        receipt={}, verify_same=Mock(), sql=Mock(side_effect=['t', '0', '2']))
    manager.ensure_scoped_hba(engine, db)
    assert write.call_count == 1 and write.call_args.kwargs['stdin'] == after
    assert write.call_args.args[:5] == ('exec', '-i', '-u', '0', 'verified-owned')
    assert original.encode() == next(tmp_path.glob('pg_hba-before-*.conf')).read_bytes()
    assert db.receipt['hba']['postgres_restarted'] is False
    assert db.receipt['hba']['unrelated_bytes_preserved'] is True
    queries = [call.args[0] for call in db.sql.call_args_list]
    assert queries[0] == 'SELECT pg_reload_conf();\n'
    assert "database=ARRAY['otravel_gigachat_eval']" in queries[-1]
    assert "netmask='255.255.255.255'" in queries[-1]


@pytest.mark.parametrize('change', ['creator', 'database', 'port', 'start_ticks', 'argv', 'cwd'])
def test_recorded_process_cannot_be_borrowed_from_8084_or_reused_pid(engine, monkeypatch, change):
    current = {'pid': 12345, 'uid': 1000, 'start_ticks': 99, 'argv': deepcopy(engine.ARGV),
        'cwd': str(manager.SOURCE), 'executable': str(engine.PYTHON.resolve())}
    saved = {**deepcopy(current), 'created_by': manager.CREATOR, 'database': manager.DATABASE, 'port': manager.PORT}
    if change == 'creator': saved['created_by'] = 'after80_gptoss_backend.py'
    elif change == 'database': saved['database'] = 'otravel_zai_eval'
    elif change == 'port': saved['port'] = 8084
    elif change == 'start_ticks': current['start_ticks'] += 1
    elif change == 'argv': current['argv'] = ['foreign']
    elif change == 'cwd': current['cwd'] = '/home/aegml/projects/otravel-after80-20261026'
    engine.IDENTITY = IdentityFile(saved)
    monkeypatch.setattr(engine, 'process_identity', lambda _: deepcopy(current))
    with pytest.raises(engine.Refusal): manager.recorded_process(engine, SimpleNamespace(pw_uid=1000))


@pytest.mark.parametrize('bad', [None, 'import_model_count', 'startup_model_count', 'pending'])
def test_start_import_and_launch_are_counter_guarded_without_real_process(engine, monkeypatch, tmp_path, bad):
    engine.IDENTITY = IdentityFile()
    engine.LOG = tmp_path/'private.log'
    account = SimpleNamespace(pw_uid=1000, pw_gid=1000)
    counts = {'sessions': 0, 'agent_runs': 0, 'agent_events': 0, 'pending_or_running': int(bad == 'pending'), 'model_call_started': 0}
    after_import = {**counts, 'model_call_started': int(bad == 'import_model_count')}
    after_start = {**counts, 'model_call_started': int(bad == 'startup_model_count')}
    db = SimpleNamespace(ip='172.18.0.2', password='synthetic', receipt={}, counts=Mock(side_effect=[after_import, after_start]))
    monkeypatch.setattr(manager, 'read_master_profile', lambda _: profile())
    monkeypatch.setattr(engine, 'Database', lambda: db)
    monkeypatch.setattr(manager, 'ensure_owned_database', Mock())
    monkeypatch.setattr(manager, 'ensure_scoped_hba', Mock())
    def initialize(*_):
        engine.require(counts['pending_or_running'] == 0, 'pending runs')
        return counts
    monkeypatch.setattr(manager, 'initialize_owned_schema', initialize)
    manifest = {'sha256': 'a'*64}
    monkeypatch.setattr(engine, 'source_manifest', lambda: deepcopy(manifest))
    monkeypatch.setattr(engine, 'listeners', list)
    facts = {'api_file': str(manager.SOURCE/'otravel/api.py'), 'provider': 'gigachat', 'model': manager.MODEL,
        'daily_limit': 1000, 'generic_daily_limit': 40, 'run_limit': 1, 'local_limit': 0, 'search_mode': 'HYBRID',
        'freemium_confirmed': True, 'other_model_credentials_present': False}
    def capture(argv, **kwargs):
        ast.parse(argv[2])
        assert kwargs['env']['GIGACHAT_AUTH_KEY'] == profile()['GIGACHAT_AUTH_KEY']
        assert 'GROQ_API_KEY' not in kwargs['env']
        return SimpleNamespace(returncode=0, stdout=json.dumps(facts))
    monkeypatch.setattr(engine, 'capture', capture)
    current = {'pid': 12345, 'uid': 1000, 'start_ticks': 99, 'argv': deepcopy(engine.ARGV),
        'cwd': str(manager.SOURCE), 'executable': str(engine.PYTHON.resolve())}
    monkeypatch.setattr(engine, 'process_identity', lambda _: deepcopy(current))
    monkeypatch.setattr(engine, 'atomic_json', lambda path, value, *args: setattr(engine.IDENTITY, 'data', deepcopy(value)))
    monkeypatch.setattr(engine, 'require_owned_listener', Mock())
    monkeypatch.setattr(manager, 'require_ready', lambda _: {'generation_performed': False})
    launch = Mock(return_value=SimpleNamespace(pid=12345, poll=lambda: None))
    monkeypatch.setattr(manager.subprocess, 'Popen', launch)
    save = Mock(return_value={'status': 'RUNNING'})
    monkeypatch.setattr(engine, 'save_evidence', save)
    if bad is None:
        assert manager.start(engine, account) == {'status': 'RUNNING'}
        extra = save.call_args.args[2]
        assert extra['database_counts_before'] == extra['database_counts_after_import'] == extra['database_counts_after']
    else:
        with pytest.raises(engine.Refusal): manager.start(engine, account)
        save.assert_not_called()
        if bad != 'startup_model_count': launch.assert_not_called()
