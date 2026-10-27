"""Pure GPT-OSS120B lifecycle guards. No native config, HTTP, Docker or model calls."""
import ast
import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_after80_cf_backend import IdentityFile, PrivateFile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('gptoss_controller_test', ROOT / 'scripts/after80_gptoss_backend.py')
manager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manager)


@pytest.fixture
def engine():
    return manager.new_engine()


def profile():
    return {'GROQ_API_KEY': 'gsk_synthetic', 'GROQ_FREE_TIER_CONFIRMED': 'true',
        'LITEAPI_API_KEY': 'sand_synthetic', 'DUFFEL_ACCESS_TOKEN': 'duffel_test_synthetic',
        'SERPAPI_API_KEY': 'synthetic-serp', 'SERPAPI_FREE_TIER_CONFIRMED': 'true', 'LITEAPI_MODE': 'sandbox'}


def readiness():
    value = {name: {'configured': False} for name in ('gemini', 'cloudflare', 'openrouter', 'zai', 'ollama', 'hotelbeds', 'ratehawk', 'travelpayouts')}
    value.update({'_runtime': {'llm_provider': 'groq', 'model': manager.MODEL},
        'groq': {'configured': True, 'model': manager.MODEL, 'free_tier_confirmed': True, 'paid_fallback': False,
            'structured_transport': True, 'reasoning_effort': 'low', 'include_reasoning': False},
        'liteapi': {'configured': True, 'mode': 'sandbox'}, 'duffel': {'configured': True, 'mode': 'sandbox'},
        'serpapi': {'configured': True, 'free_tier_confirmed': True, 'source_type': 'LIVE_INDICATIVE'},
        'open_meteo': {'configured': True, 'requires_key': False}})
    return value


def test_fixed_gptoss_env_uses_existing_groq1000_pool_generic40_and_no_inherited_secrets(engine, monkeypatch):
    for key in (*engine.KEYS, 'ZAI_FREE_TIER_CONFIRMED', 'CLOUDFLARE_FREE_TIER_CONFIRMED', 'MAX_GROQ_LLM_CALLS_PER_DAY',
            'GROQ_STRUCTURED_TRANSPORT_ENABLED', 'GROQ_REASONING_EFFORT'):
        monkeypatch.setenv(key, 'DO-NOT-INHERIT')
    env = manager.environment(engine, SimpleNamespace(password='synthetic:#@', ip='172.18.0.2'), {**profile(),
        'ZAI_API_KEY': 'FORBIDDEN', 'OPENROUTER_API_KEY': 'FORBIDDEN', 'GROQ_MODEL': 'paid-model',
        'MAX_GROQ_LLM_CALLS_PER_DAY': '999999'})
    assert env['LLM_PROVIDER'] == 'groq' and env['GROQ_MODEL'] == 'openai/gpt-oss-120b'
    assert env['GROQ_FREE_TIER_CONFIRMED'] == 'true'
    assert env['MAX_GROQ_LLM_CALLS_PER_DAY'] == '1000' and env['MAX_LLM_CALLS_PER_DAY'] == '40'
    assert env['OPENROUTER_BACKUP_MODEL'] == '' and env['MAX_LLM_CALLS_PER_RUN'] == '1'
    assert env['MAX_LOCAL_LLM_CALLS_PER_DAY'] == '0' and env['LLM_MAX_OUTPUT_TOKENS'] == '2048'
    assert env['GROQ_STRUCTURED_TRANSPORT_ENABLED'] == 'true' and env['GROQ_REASONING_EFFORT'] == 'low'
    assert env['REFERENCE_FX_ENABLED'] == 'true'
    assert env['DEFAULT_SEARCH_MODE'] == 'HYBRID' and env['DEFAULT_FLIGHT_PROVIDER'] == 'duffel'
    assert env['DATABASE_URL'].endswith('/otravel_zai_eval') and 'synthetic%3A%23%40' in env['DATABASE_URL']
    assert all(k not in env for k in engine.KEYS if k not in {*manager.GROQ_KEYS, *manager.TRAVEL_KEYS})
    assert all(env[k] == 'false' for k in ('GEMINI_FREE_TIER_CONFIRMED', 'ZAI_FREE_TIER_CONFIRMED',
        'CLOUDFLARE_FREE_TIER_CONFIRMED', 'COHERE_EVALUATION_CONFIRMED', 'NVIDIA_PROTOTYPING_CONFIRMED'))
    assert 'DO-NOT-INHERIT' not in json.dumps(env) and 'FORBIDDEN' not in json.dumps(env)


def test_own_namespace_creator_manifest_paths_and_no_old_controller_mutation():
    paths = ['scripts/local_backend.py', 'scripts/after80_backend.py', 'scripts/after80_cf_backend.py', 'scripts/after80_router_backend.py', 'scripts/after80_groq_backend.py']
    before = {p: (ROOT / p).read_bytes() for p in paths}
    first, second = manager.new_engine(), manager.new_engine()
    assert first is not second and first.SAFE_CONTEXT is not second.SAFE_CONTEXT
    assert first.SOURCE == Path('/home/aegml/projects/otravel-after80-20261026') and first.DATABASE == 'otravel_zai_eval'
    assert 'after80-gptoss-backend-8084' in str(first.IDENTITY)
    assert 'after80-gptoss-backend-20261027' in str(first.EVIDENCE)
    assert first.ARGV[-5:] == ['--host', '127.0.0.1', '--port', '8084', '--no-access-log']
    assert manager.CREATOR == 'after80_gptoss_backend.py'
    assert all((ROOT / p).read_bytes() == content for p, content in before.items())


@pytest.mark.parametrize('key,value', [('GROQ_FREE_TIER_CONFIRMED', False), ('SERPAPI_FREE_TIER_CONFIRMED', False),
    ('GROQ_API_KEY', ''), ('GROQ_API_KEY', 'bad key'), ('LITEAPI_API_KEY', 'prod_forbidden'), ('LITEAPI_MODE', 'production'),
    ('DUFFEL_ACCESS_TOKEN', 'duffel_live_forbidden'), ('DUFFEL_ACCESS_TOKEN', 'duffel_test_')])
def test_unconfirmed_or_unsafe_profile_refuses(engine, key, value):
    with pytest.raises(engine.Refusal): manager.validate_profile(engine, {**profile(), key: value})


@pytest.mark.parametrize('model', ['paid/model', 'other/model:free', 'qwen/qwen3.8-27b', 'openai/gpt-oss-20b'])
def test_only_fixed_gptoss_model_not_arbitrary_model(engine, monkeypatch, model):
    monkeypatch.setattr(manager, 'MODEL', model)
    with pytest.raises(engine.Refusal, match='fixed_model'): manager.validate_profile(engine, profile())


def test_master_reader_selects_only_groq_free_flag_travel_and_database_fields(engine, monkeypatch):
    fake = PrivateFile('ZAI_API_KEY=forbidden\nCLOUDFLARE_API_TOKEN=forbidden\nOPENROUTER_API_KEY=forbidden\nGROQ_API_KEY=gsk_synthetic\nPOSTGRES_PASSWORD=dbfixture')
    assert manager.private_values(engine, fake, manager.GROQ_KEYS) == {'GROQ_API_KEY': 'gsk_synthetic'}
    reader = Mock(side_effect=[profile(), {'POSTGRES_PASSWORD': 'dbfixture'}])
    monkeypatch.setattr(manager, 'private_values', reader)
    manager.read_master_profile(engine); engine.read_database_password()
    assert all(c.args[1] == manager.MASTER for c in reader.call_args_list)
    assert {'GROQ_API_KEY', 'GROQ_FREE_TIER_CONFIRMED'} <= set(manager.PROFILE_FIELDS)
    assert not {'OPENROUTER_API_KEY', 'ZAI_API_KEY', 'CLOUDFLARE_API_TOKEN', 'GEMINI_API_KEY'}.intersection(manager.PROFILE_FIELDS)


def test_unowned_listener_refuses_start_stop_before_reading_keys_db_or_signaling(engine, monkeypatch):
    engine.IDENTITY = IdentityFile()
    monkeypatch.setattr(engine, 'listeners', lambda: [{'address': '127.0.0.1:8084', 'pids': [999]}])
    forbidden = Mock(side_effect=AssertionError('No foreign action'))
    monkeypatch.setattr(manager, 'read_master_profile', forbidden)
    monkeypatch.setattr(engine, 'Database', forbidden)
    monkeypatch.setattr(engine, 'verified_pidfd', forbidden)
    with pytest.raises(engine.Refusal, match='unowned'): manager.start(engine, SimpleNamespace(pw_uid=1000))
    with pytest.raises(engine.Refusal, match='unowned'): engine.stop(SimpleNamespace(pw_uid=1000))
    forbidden.assert_not_called()


@pytest.mark.parametrize('bad', [None, 'model', 'provider', 'free_flag', 'paid', 'other', 'local', 'live', 'missing_false',
    'legacy_transport', 'reasoning', 'qwen_model', 'visible_reasoning', 'missing_include', 'string_false_include'])
def test_readiness_performs_only_status_gets_and_requires_exact_free_groq(engine, monkeypatch, bad):
    status = readiness()
    if bad == 'model': status['groq']['model'] = 'paid-model'
    elif bad == 'provider': status['_runtime']['llm_provider'] = 'openrouter'
    elif bad == 'free_flag': status['groq']['free_tier_confirmed'] = False
    elif bad == 'paid': status['groq']['paid_fallback'] = True
    elif bad == 'legacy_transport': status['groq'].pop('structured_transport')
    elif bad == 'reasoning': status['groq']['reasoning_effort'] = 'none'
    elif bad == 'qwen_model': status['groq']['model'] = 'qwen/qwen3.8-27b'
    elif bad == 'visible_reasoning': status['groq']['include_reasoning'] = True
    elif bad == 'missing_include': status['groq'].pop('include_reasoning')
    elif bad == 'string_false_include': status['groq']['include_reasoning'] = 'false'
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
        assert manager.require_ready(engine)['pay_zero_policy'] == {'free_tier_confirmed': True,
            'billing_enabled_by_manager': False, 'fallback': False}
    else:
        with pytest.raises(engine.Refusal): manager.require_ready(engine)
    assert paths == ['/health', '/api/v1/providers/status']


def test_existing_database_only_no_create_or_hba(engine):
    db = SimpleNamespace(verify_same=Mock(), sql=Mock(return_value='otravel|false|true'), receipt={}, create=Mock(), allow_local_database=Mock())
    manager.require_existing_database(engine, db)
    assert db.receipt['database_created'] is False and db.receipt['hba_modified_by_manager'] is False
    db.create.assert_not_called(); db.allow_local_database.assert_not_called()


def test_plan_is_zero_side_effect_even_on_windows_and_without_keys(engine, monkeypatch, capsys):
    forbidden = Mock(side_effect=AssertionError('Plan cannot enter runtime or secret lifecycle'))
    monkeypatch.setattr(manager, 'new_engine', forbidden)
    monkeypatch.setattr(manager, 'windows_dispatch', forbidden)
    monkeypatch.setattr(manager.sys, 'platform', 'win32')
    assert manager.main(['--action', 'plan']) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan['provider'] == 'groq' and plan['model'] == manager.MODEL
    assert plan['daily_engineering_bound'] == plan['generic_daily_engineering_bound'] == 40
    assert plan['groq_daily_engineering_bound'] == 1000
    assert plan['vendor_quota'] == 'UNKNOWN' and plan['inference_requests'] == 0
    assert plan['runtime_changed'] is False and plan['credentials_read'] is False
    forbidden.assert_not_called()


@pytest.mark.parametrize('change', ['creator', 'start_ticks', 'pid', 'argv', 'cwd', 'executable'])
def test_recorded_pid_scope_and_manifest_owner_cannot_be_borrowed_from_qwen(engine, monkeypatch, change):
    account = SimpleNamespace(pw_uid=1000)
    current = {'pid': 12345, 'uid': 1000, 'start_ticks': 99, 'argv': deepcopy(engine.ARGV),
        'cwd': str(manager.SOURCE), 'executable': str(engine.PYTHON.resolve())}
    saved = {**deepcopy(current), 'created_by': manager.CREATOR, 'database': manager.DATABASE,
        'port': manager.PORT, 'source_manifest': {'files': []}}
    if change == 'creator': saved['created_by'] = 'after80_groq_backend.py'
    elif change == 'start_ticks': current['start_ticks'] += 1
    elif change == 'pid': current['pid'] += 1
    elif change == 'argv': current['argv'] = ['foreign']
    elif change == 'cwd': current['cwd'] = '/tmp/foreign'
    elif change == 'executable': current['executable'] = '/bin/false'
    engine.IDENTITY = IdentityFile(saved)
    monkeypatch.setattr(engine, 'process_identity', lambda _: deepcopy(current))
    with pytest.raises(engine.Refusal): manager.recorded_process(engine, account)


@pytest.mark.parametrize('bad', [None, 'generic_limit', 'model_counter', 'pending_run', 'old_model', 'old_effort', 'old_transport', 'visible_reasoning'])
def test_start_import_proves_explicit_separate_cap_and_no_startup_model_calls(engine, monkeypatch, tmp_path, bad):
    engine.IDENTITY = IdentityFile()
    engine.LOG = tmp_path / 'synthetic-uvicorn.log'
    account = SimpleNamespace(pw_uid=1000, pw_gid=1000)
    facts = {'api_file': str(manager.SOURCE / 'otravel/api.py'), 'provider': 'groq', 'model': manager.MODEL,
        'daily_limit': 1000, 'generic_daily_limit': 40, 'run_limit': 1, 'local_limit': 0,
        'search_mode': 'HYBRID', 'gptoss_transport': 'true', 'gptoss_reasoning_effort': 'low', 'gptoss_include_reasoning': False,
        'other_model_credentials_present': False}
    if bad == 'generic_limit': facts['generic_daily_limit'] = 1000
    elif bad == 'old_model': facts['model'] = 'qwen/qwen3.8-27b'
    elif bad == 'old_effort': facts['gptoss_reasoning_effort'] = 'none'
    elif bad == 'old_transport': facts['gptoss_transport'] = 'false'
    elif bad == 'visible_reasoning': facts['gptoss_include_reasoning'] = True
    initial = {'sessions': 10, 'agent_runs': 20, 'agent_events': 30, 'model_call_started': 15,
        'pending_or_running': 1 if bad == 'pending_run' else 0}
    after = {**initial, 'model_call_started': 16 if bad == 'model_counter' else 15}
    database = SimpleNamespace(ip='172.18.0.2', password='synthetic-db', verify_same=Mock(),
        sql=Mock(return_value='otravel|false|true'), receipt={}, counts=Mock(side_effect=[initial, after]))
    manifest = {'format': 'synthetic', 'files': [{'path': 'otravel/api.py', 'sha256': 'a' * 64}]}
    monkeypatch.setattr(manager, 'read_master_profile', lambda _: profile())
    monkeypatch.setattr(engine, 'Database', lambda: database)
    monkeypatch.setattr(engine, 'source_manifest', lambda: deepcopy(manifest))
    monkeypatch.setattr(engine, 'listeners', list)
    captured = []
    def capture(argv, **kwargs):
        captured.append((argv, kwargs))
        ast.parse(argv[2])  # Validate generated child-import Python without executing it.
        assert kwargs['env']['MAX_GROQ_LLM_CALLS_PER_DAY'] == '1000'
        assert kwargs['env']['MAX_LLM_CALLS_PER_DAY'] == '40'
        assert kwargs['env']['GROQ_FREE_TIER_CONFIRMED'] == 'true'
        assert 'OPENROUTER_API_KEY' not in kwargs['env'] and 'ZAI_API_KEY' not in kwargs['env']
        return SimpleNamespace(returncode=0, stdout=json.dumps(facts))
    monkeypatch.setattr(engine, 'capture', capture)
    current = {'pid': 12345, 'uid': 1000, 'start_ticks': 99, 'argv': deepcopy(engine.ARGV),
        'cwd': str(manager.SOURCE), 'executable': str(engine.PYTHON.resolve())}
    monkeypatch.setattr(engine, 'process_identity', lambda _: deepcopy(current))
    monkeypatch.setattr(engine, 'atomic_json', lambda path, value, *args: setattr(engine.IDENTITY, 'data', deepcopy(value)))
    monkeypatch.setattr(engine, 'require_owned_listener', Mock())
    monkeypatch.setattr(manager, 'require_ready', lambda _: {'generation_performed': False})
    popen = Mock(return_value=SimpleNamespace(pid=12345, poll=lambda: None))
    monkeypatch.setattr(manager.subprocess, 'Popen', popen)
    saved = Mock(return_value={'status': 'RUNNING'})
    monkeypatch.setattr(engine, 'save_evidence', saved)
    if bad is None:
        assert manager.start(engine, account) == {'status': 'RUNNING'}
        assert popen.call_count == 1 and saved.call_count == 1
        receipt = saved.call_args.args[2]
        assert receipt['import_check'] == facts
        assert receipt['database_counts_before']['model_call_started'] == receipt['database_counts_after']['model_call_started']
        assert receipt['database_setup']['database_created'] is False
        assert receipt['database_setup']['hba_modified_by_manager'] is False
    else:
        with pytest.raises(engine.Refusal): manager.start(engine, account)
        if bad in {'generic_limit', 'pending_run', 'old_model', 'old_effort', 'old_transport', 'visible_reasoning'}: popen.assert_not_called()
        saved.assert_not_called()
    assert len(captured) == 1


def test_stopped_receipt_distinguishes_generic40_groq1000_and_unknown_vendor_quota(engine, monkeypatch, tmp_path):
    engine.IDENTITY = IdentityFile()
    engine.EVIDENCE = tmp_path / 'synthetic-evidence.json'
    monkeypatch.setattr(engine, 'listeners', list)
    monkeypatch.setattr(engine, 'source_manifest', lambda: {'files': []})
    monkeypatch.setattr(engine, 'atomic_json', Mock())
    forbidden = Mock(side_effect=AssertionError('Stopped status may not call HTTP or read credentials'))
    monkeypatch.setattr(engine, 'http', forbidden)
    monkeypatch.setattr(manager, 'read_master_profile', forbidden)
    record = manager.save_evidence(engine, 'status', SimpleNamespace(pw_uid=1000))
    assert record['status'] == 'STOPPED'
    assert record['limits'] == {'daily_engineering_bound': 40, 'generic_daily_engineering_bound': 40,
        'groq_daily_engineering_bound': 1000, 'vendor_quota': 'UNKNOWN', 'calls_per_run': 1, 'local_calls': 0, 'fallback': False}
    assert record['inference_requested_by_manager'] is False and record['supplier_requests_by_manager'] is False
    assert record['source_env_read'] is False and record['credentials_recorded'] is False
    forbidden.assert_not_called()
