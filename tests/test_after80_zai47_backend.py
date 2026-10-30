"""Pure ZAI47 lifecycle/admission guards; no native process, DB, key or HTTP."""
import hashlib
import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_after80_cf_backend import IdentityFile, PrivateFile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('zai47_backend_test', ROOT / 'scripts/after80_zai47_backend.py')
manager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manager)


@pytest.fixture
def engine():
    return manager.new_engine()


def profile():
    return {'ZAI_API_KEY': 'synthetic-zai47-key', 'ZAI_FREE_TIER_CONFIRMED': 'true',
        'LITEAPI_API_KEY': 'sand_synthetic', 'DUFFEL_ACCESS_TOKEN': 'duffel_test_synthetic',
        'SERPAPI_API_KEY': 'synthetic-serp', 'SERPAPI_FREE_TIER_CONFIRMED': 'true', 'LITEAPI_MODE': 'sandbox'}


def rows():
    return [{'id': 'synthetic-run', 'session_id': 'synthetic-session', 'status': 'pending',
        'based_version': 3, 'state_version': 3, 'waiting_reason': 'user_pause', 'active_run_id': None,
        'input_sha256': 'c' * 64}]


def review(pending=None):
    return {'date': '2026-10-27', 'scope': 'Keep only exact reviewed paused rows frozen.',
        'database': manager.DATABASE, 'package_sha256': 'a' * 64, 'pending_rows': deepcopy(rows() if pending is None else pending),
        'database_mutations': 0, 'inference_calls': 0}


def readiness():
    status = {name: {'configured': False} for name in ('gemini', 'openrouter', 'groq', 'cloudflare', 'ollama',
        'hotelbeds', 'ratehawk', 'travelpayouts', 'cohere', 'nvidia')}
    status.update({'_runtime': {'llm_provider': 'zai', 'model': manager.MODEL},
        'zai': {'configured': True, 'free_tier_confirmed': True, 'paid_fallback': False},
        'liteapi': {'configured': True, 'mode': 'sandbox'}, 'duffel': {'configured': True, 'mode': 'sandbox'},
        'serpapi': {'configured': True, 'free_tier_confirmed': True, 'source_type': 'LIVE_INDICATIVE'},
        'open_meteo': {'configured': True}})
    return status


def test_new_manager_is_fixed_zai47_namespace_and_does_not_relabel_or_edit_legacy_controllers():
    names = ['scripts/after80_backend.py', 'scripts/zai_eval_backend.py', 'scripts/after80_cf_backend.py', 'scripts/local_backend.py']
    before = {name: (ROOT / name).read_bytes() for name in names}
    a, b = manager.new_engine(), manager.new_engine()
    assert a is not b and a.SAFE_CONTEXT is not b.SAFE_CONTEXT
    assert manager.MODEL == 'glm-4.7-flash' and manager.CREATOR == 'after80_zai47_backend.py'
    assert a.SOURCE == Path('/home/aegml/projects/otravel-after80-20261026')
    assert a.DATABASE == 'otravel_zai_eval' and a.PORT == 8084
    assert 'after80-zai47-backend-8084' in str(a.IDENTITY)
    assert a.EVIDENCE.name == 'after80-zai47-backend-20261027.json'
    assert a.ARGV[-5:] == ['--host', '127.0.0.1', '--port', '8084', '--no-access-log']
    assert all((ROOT / name).read_bytes() == value for name, value in before.items())
    assert 'MODEL = "glm-4.5-flash"' in before['scripts/after80_backend.py'].decode()


def test_environment_selects_only_master_zai_travel_and_fixed_free_limits(engine, monkeypatch):
    for key in (*engine.KEYS, 'ZAI_MODEL', 'DATABASE_URL'):
        monkeypatch.setenv(key, 'FORBIDDEN-INHERITED')
    supplied = {**profile(), 'OPENROUTER_API_KEY': 'FORBIDDEN-EXTRA', 'ZAI_MODEL': 'paid-model'}
    env = manager.environment(engine, SimpleNamespace(password='synthetic:#@', ip='172.18.0.2'), supplied)
    assert env['ZAI_MODEL'] == 'glm-4.7-flash' and env['LLM_PROVIDER'] == 'zai'
    assert env['ZAI_API_KEY'] == profile()['ZAI_API_KEY'] and env['ZAI_FREE_TIER_CONFIRMED'] == 'true'
    assert env['MAX_LLM_CALLS_PER_DAY'] == '2000' and env['MAX_LLM_CALLS_PER_RUN'] == '1'
    assert env['MAX_LOCAL_LLM_CALLS_PER_DAY'] == '0' and env['OPENROUTER_BACKUP_MODEL'] == ''
    assert env['DEFAULT_SEARCH_MODE'] == 'HYBRID' and env['LITEAPI_MODE'] == 'sandbox'
    assert env['DATABASE_URL'].endswith('/otravel_zai_eval') and 'synthetic%3A%23%40' in env['DATABASE_URL']
    assert all(key not in env for key in engine.KEYS if key not in {*manager.ZAI_KEYS, *manager.TRAVEL_KEYS})
    assert all(value not in json.dumps(env) for value in ('FORBIDDEN-INHERITED', 'FORBIDDEN-EXTRA', 'paid-model'))


@pytest.mark.parametrize('field,value', [('ZAI_API_KEY', ''), ('ZAI_API_KEY', 'bad key'),
    ('ZAI_FREE_TIER_CONFIRMED', False), ('SERPAPI_FREE_TIER_CONFIRMED', False),
    ('LITEAPI_API_KEY', 'prod-key'), ('LITEAPI_MODE', 'production'), ('DUFFEL_ACCESS_TOKEN', 'duffel_live_key')])
def test_unconfirmed_paid_or_live_profile_is_refused(engine, field, value):
    with pytest.raises(engine.Refusal):
        manager.environment(engine, SimpleNamespace(password='synthetic', ip='172.18.0.2'), {**profile(), field: value})


def test_selected_profile_and_database_read_master_only_never_source_dotenv(engine, monkeypatch):
    reader = Mock(side_effect=[profile(), {'POSTGRES_PASSWORD': 'synthetic-db'}])
    monkeypatch.setattr(manager, 'private_values', reader)
    assert manager.read_master_profile(engine) == profile()
    assert engine.read_database_password() == 'synthetic-db'
    assert [call.args[1] for call in reader.call_args_list] == [manager.MASTER, manager.MASTER]
    assert reader.call_args_list[0].args[2] == manager.PROFILE_FIELDS
    assert manager.private_values is reader


def test_master_duplicate_and_public_file_guards(engine):
    for private in (PrivateFile('ZAI_API_KEY=x', 0o644), PrivateFile('ZAI_API_KEY=x', symlink=True),
        PrivateFile('ZAI_API_KEY=a\nZAI_API_KEY=b')):
        with pytest.raises(engine.Refusal):
            manager.private_values(engine, private, manager.ZAI_KEYS)


@pytest.mark.parametrize('field,value', [('status', 'running'), ('waiting_reason', 'clarification'),
    ('active_run_id', 'active'), ('based_version', 2), ('state_version', True), ('input_sha256', 'bad')])
def test_pending_running_nonpaused_active_stale_or_unhashed_jobs_are_unreviewable(engine, field, value):
    pending = rows()
    pending[0][field] = value
    with pytest.raises(engine.Refusal):
        manager.canonical_pending_rows(engine, pending)


def test_pending_admission_requires_exact_review_and_source_payload_identity_without_writes(engine):
    pending, manifest = rows(), {'sha256': 'a' * 64}
    counts = {'pending_or_running': 1}
    with pytest.raises(engine.Refusal, match='review_required'):
        manager.admit_pending(engine, pending, counts, manifest)
    admitted = manager.admit_pending(engine, pending, counts, manifest, review(), 'b' * 64)
    assert admitted['frozen_user_pause_count'] == 1 and admitted['admission_database_writes'] == 0
    assert admitted['inference_performed'] is False and admitted['owner_review_sha256'] == 'b' * 64
    assert 'synthetic-run' not in json.dumps(admitted) and 'synthetic-session' not in json.dumps(admitted)
    assert manager.admit_pending(engine, [], {'pending_or_running': 0}, manifest)['review_used'] is False


@pytest.mark.parametrize('change', ['package', 'database', 'payload', 'id', 'extra_job', 'writes', 'inference', 'count'])
def test_review_mismatch_never_admits_other_pending_work(engine, change):
    approved, pending, counts = review(), rows(), {'pending_or_running': 1}
    if change == 'package': approved['package_sha256'] = 'f' * 64
    elif change == 'database': approved['database'] = 'otravel'
    elif change == 'payload': approved['pending_rows'][0]['input_sha256'] = 'f' * 64
    elif change == 'id': approved['pending_rows'][0]['id'] = 'different'
    elif change == 'extra_job': pending.append({**rows()[0], 'id': 'extra'}); counts['pending_or_running'] = 2
    elif change == 'writes': approved['database_mutations'] = 1
    elif change == 'inference': approved['inference_calls'] = True
    else: counts['pending_or_running'] = 2
    with pytest.raises(engine.Refusal):
        manager.admit_pending(engine, pending, counts, {'sha256': 'a' * 64}, approved, 'b' * 64)


class ReviewFile:
    def __init__(self, *, mode=0o600, uid=0, canonical=True, inside=True, symlink=False, payload=None):
        self.mode, self.uid, self.canonical, self.inside, self.symlink = mode, uid, canonical, inside, symlink
        self.raw = json.dumps(review() if payload is None else payload).encode()
    def is_absolute(self): return True
    def is_relative_to(self, base): return self.inside
    def is_file(self): return True
    def is_symlink(self): return self.symlink
    def resolve(self): return self if self.canonical else object()
    def stat(self): return SimpleNamespace(st_uid=self.uid, st_mode=self.mode, st_size=len(self.raw))
    def read_bytes(self): return self.raw


def test_private_review_is_bound_to_explicit_hash_and_does_not_print_rows(engine):
    path = ReviewFile()
    assert manager.read_pending_review(engine, path, hashlib.sha256(path.raw).hexdigest()) == review()
    with pytest.raises(engine.Refusal): manager.read_pending_review(engine, path, '0' * 64)
    with pytest.raises(engine.Refusal): manager.read_pending_review(engine, path, None)


@pytest.mark.parametrize('kwargs', [{'mode': 0o644}, {'uid': 1000}, {'canonical': False}, {'inside': False}, {'symlink': True}])
def test_untrusted_review_file_is_refused_before_payload_use(engine, kwargs):
    path = ReviewFile(**kwargs)
    with pytest.raises(engine.Refusal):
        manager.read_pending_review(engine, path, hashlib.sha256(path.raw).hexdigest())


def test_pending_query_is_bounded_select_only_and_returns_hash_not_input_text(engine):
    database = SimpleNamespace(sql=Mock(side_effect=[json.dumps(rows()), '0']))
    assert manager.read_pending_rows(engine, database) == rows()
    for call in database.sql.call_args_list:
        assert call.args[0].lstrip().startswith('SELECT') and call.args[1] == manager.DATABASE
    query = database.sql.call_args_list[0].args[0]
    assert 'LIMIT 101' in query and "encode(sha256(convert_to(r.input_text,'UTF8')),'hex')" in query
    assert "'input_text'" not in query


def fake_start(engine, monkeypatch, tmp_path, *, pending=None, changed_after=False, after_rows=None):
    pending = [] if pending is None else pending
    engine.IDENTITY = IdentityFile()
    engine.LOG, engine.EVIDENCE = tmp_path / 'private.log', tmp_path / 'public.json'
    manifest = {'format': engine.MANIFEST_FORMAT, 'sha256': 'a' * 64,
                'files': [{'path': 'otravel/api.py', 'sha256': 'synthetic'}]}
    identity = {'pid': 12345, 'uid': 1000, 'start_ticks': 99, 'argv': deepcopy(engine.ARGV),
                'cwd': str(manager.SOURCE), 'executable': str(engine.PYTHON.resolve())}
    monkeypatch.setattr(engine, 'listeners', list)
    monkeypatch.setattr(engine, 'source_manifest', lambda: deepcopy(manifest))
    monkeypatch.setattr(engine, 'process_identity', lambda _: deepcopy(identity))
    monkeypatch.setattr(engine, 'require_owned_listener', lambda _: None)
    monkeypatch.setattr(manager, 'read_master_profile', lambda _: profile())
    count = {'sessions': 10, 'messages': 30, 'agent_runs': 20, 'agent_events': 100, 'quotes': 3,
             'demo_orders': 0, 'pending_or_running': len(pending), 'model_call_started': 5}
    after = {**count, 'model_call_started': 6} if changed_after else deepcopy(count)
    snapshots = [deepcopy(pending), deepcopy(pending), deepcopy(pending if after_rows is None else after_rows)]
    def sql(query, database='postgres'):
        assert query.lstrip().startswith('SELECT')
        if 'pg_get_userbyid' in query: return 'otravel|false|true'
        if 'jsonb_agg' in query: return json.dumps(snapshots.pop(0))
        assert 'active_run_id IS NOT NULL' in query
        return '0'
    database = SimpleNamespace(password='synthetic-db', ip='172.18.0.2', receipt={}, verify_same=Mock(),
        sql=Mock(side_effect=sql), counts=Mock(side_effect=[count, deepcopy(count), after]),
        create=Mock(side_effect=AssertionError('No database creation')), allow_local_database=Mock(side_effect=AssertionError('No HBA write')))
    monkeypatch.setattr(engine, 'Database', lambda: database)
    imported = {'api_file': str(manager.SOURCE / 'otravel/api.py'), 'provider': 'zai', 'model': manager.MODEL,
                'daily_limit': 2000, 'run_limit': 1, 'local_limit': 0, 'search_mode': 'HYBRID'}
    capture = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps(imported)))
    monkeypatch.setattr(engine, 'capture', capture)
    monkeypatch.setattr(engine, 'http', lambda path: (200, {'database': 'ready', 'application': 'backend', 'supplier_booking': False})
        if path == '/health' else (200, readiness()) if path == '/api/v1/providers/status' else (200, None))
    stored = []
    def atomic(path, value, mode=0o600):
        stored.append((path, deepcopy(value), mode))
        if path is engine.IDENTITY: engine.IDENTITY.data = deepcopy(value)
    monkeypatch.setattr(engine, 'atomic_json', atomic)
    launch = Mock(return_value=SimpleNamespace(pid=12345, poll=lambda: None))
    monkeypatch.setattr(manager.subprocess, 'Popen', launch)
    return database, capture, launch, stored


@pytest.mark.parametrize('paused', [False, True])
def test_mock_start_preserves_reviewed_pause_queue_and_has_no_inference_or_database_change(engine, monkeypatch, tmp_path, paused):
    database, _, launch, stored = fake_start(engine, monkeypatch, tmp_path, pending=rows() if paused else [])
    if paused: monkeypatch.setattr(manager, 'read_pending_review', lambda *_: review())
    kwargs = {'pending_review': ReviewFile(), 'expected_review_sha256': 'b' * 64} if paused else {}
    result = manager.start(engine, SimpleNamespace(pw_uid=1000, pw_gid=1000), **kwargs)
    assert result['status'] == 'RUNNING' and result['pending_admission']['frozen_user_pause_count'] == int(paused)
    assert result['database_counts_before'] == result['database_counts_after']
    assert launch.call_args.kwargs['umask'] == 0o077 and launch.call_args.kwargs['user'] == 1000
    public = next(value for path, value, _ in stored if path == engine.EVIDENCE)
    assert all(secret not in json.dumps(public) for secret in ('synthetic-zai47-key', 'synthetic-db', 'synthetic-run'))
    database.create.assert_not_called(); database.allow_local_database.assert_not_called()


def test_unreviewed_pending_refuses_before_import_and_launch(engine, monkeypatch, tmp_path):
    database, capture, launch, _ = fake_start(engine, monkeypatch, tmp_path, pending=rows())
    with pytest.raises(engine.Refusal, match='review_required'):
        manager.start(engine, SimpleNamespace(pw_uid=1000, pw_gid=1000))
    capture.assert_not_called(); launch.assert_not_called(); database.create.assert_not_called()


@pytest.mark.parametrize('change', ['models', 'pending'])
def test_postlaunch_inference_or_queue_change_refuses_without_replaying_or_force_stopping(engine, monkeypatch, tmp_path, change):
    changed = [{**rows()[0], 'input_sha256': 'f' * 64}]
    _, _, launch, _ = fake_start(engine, monkeypatch, tmp_path, pending=rows(), changed_after=change == 'models',
        after_rows=changed if change == 'pending' else None)
    monkeypatch.setattr(manager, 'read_pending_review', lambda *_: review())
    stop = Mock(side_effect=AssertionError('No automatic force stop'))
    monkeypatch.setattr(engine, 'stop', stop)
    with pytest.raises(engine.Refusal, match='during_start'):
        manager.start(engine, SimpleNamespace(pw_uid=1000, pw_gid=1000), pending_review=ReviewFile(), expected_review_sha256='b' * 64)
    launch.assert_called_once(); stop.assert_not_called()


def test_foreign_listener_never_authorizes_stopping_or_reading_master(engine, monkeypatch):
    engine.IDENTITY = IdentityFile()
    monkeypatch.setattr(engine, 'listeners', lambda: [{'address': '127.0.0.1:8084', 'pids': [999]}])
    forbidden = Mock(side_effect=AssertionError('No foreign side effects'))
    monkeypatch.setattr(manager, 'read_master_profile', forbidden)
    monkeypatch.setattr(engine, 'Database', forbidden)
    monkeypatch.setattr(engine, 'verified_pidfd', forbidden)
    with pytest.raises(engine.Refusal): manager.start(engine, SimpleNamespace(pw_uid=1000, pw_gid=1000))
    with pytest.raises(engine.Refusal): engine.stop(SimpleNamespace(pw_uid=1000, pw_gid=1000))
    forbidden.assert_not_called()


def test_cli_review_options_cannot_change_identity_or_sneak_into_status(monkeypatch):
    forbidden = Mock(side_effect=AssertionError('No lifecycle'))
    monkeypatch.setattr(manager, 'new_engine', forbidden)
    for argv in (['--action', 'start', '--port', '8083'], ['--action', 'start', '--pending-review', '/home/aegml/x'],
        ['--action', 'status', '--pending-review', '/home/aegml/x', '--expected-review-sha256', 'b' * 64]):
        with pytest.raises(SystemExit): manager.main(argv)
    forbidden.assert_not_called()
