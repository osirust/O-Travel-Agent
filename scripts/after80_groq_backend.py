"""Own Groq Free Qwen/HYBRID lifecycle on8084. Explicit CLI actions only.

No copying, installs, generation, supplier searches or billing setup. SOURCE.env
is never read: credentials come only from selected fields of the native MASTER.
An existing ZAI/foreign listener is refused, never stopped or reconfigured.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path('/home/aegml/projects/otravel-after80-20261026')
MASTER = Path('/home/aegml/projects/otravel/.env')
DATABASE = 'otravel_zai_eval'
PORT = 8084
MODEL = 'qwen/qwen3.8-27b'
CREATOR = 'after80_groq_backend.py'
GROQ_KEYS = ('GROQ_API_KEY',)
TRAVEL_KEYS = ('LITEAPI_API_KEY', 'DUFFEL_ACCESS_TOKEN', 'SERPAPI_API_KEY')
PROFILE_FIELDS = (*GROQ_KEYS, 'GROQ_FREE_TIER_CONFIRMED', *TRAVEL_KEYS, 'SERPAPI_FREE_TIER_CONFIRMED', 'LITEAPI_MODE')
DB_FIELDS = ('POSTGRES_USER', 'POSTGRES_DB', 'POSTGRES_PASSWORD')
EXTRA_FIELDS = {'database_setup', 'database_counts_before', 'database_counts_after', 'import_check',
                'stopped_owned_process', 'signal', 'signal_target'}


def new_engine():
    """Reuse pidfd/Docker/manifest guards in a fresh isolated core namespace."""
    spec = importlib.util.spec_from_file_location('after80_groq_core_' + uuid4().hex, ROOT / 'scripts/local_backend.py')
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    runtime = SOURCE / '.local' / 'after80-groq-backend-8084'
    overrides = {'SOURCE': SOURCE, 'DATABASE': DATABASE, 'PORT': PORT, 'BASE': f'http://127.0.0.1:{PORT}',
        'RUNTIME': runtime, 'IDENTITY': runtime / 'process.json', 'LOG': runtime / 'uvicorn.log',
        'EVIDENCE': ROOT / 'docs/verification/after80-groq-backend-20261026.json',
        'SAFE_CONTEXT': {}, 'CURRENT_STAGE': 'groq_preflight'}
    for key, value in overrides.items():
        setattr(engine, key, value)
    engine.ARGV = [str(engine.PYTHON), '-m', 'uvicorn', 'otravel.api:app', '--host', '127.0.0.1',
                   '--port', str(PORT), '--no-access-log']
    engine.read_database_password = lambda: read_database_password(engine)
    engine.recorded_process = lambda account: recorded_process(engine, account)
    engine.save_evidence = lambda action, account, extra=None: save_evidence(engine, action, account, extra)
    return engine


def private_values(engine, path, allowed):
    engine.require(path.is_file() and not path.is_symlink() and path.resolve() == path
        and path.stat().st_mode & 0o077 == 0, 'groq_private_master_missing_or_untrusted')
    result = {}
    for raw in path.read_text(encoding='utf-8-sig').splitlines():
        line = raw.strip().removeprefix('export ')
        if not line or line.startswith('#') or '=' not in line:
            continue
        name, value = line.split('=', 1)
        name, value = name.strip(), value.strip()
        if name not in allowed:
            continue
        engine.require(name not in result, 'groq_duplicate_selected_master_field')
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        result[name] = value
    return result


def key_value(engine, value, reason):
    engine.require(isinstance(value, str) and 0 < len(value) <= 4096
        and not any(char.isspace() for char in value), reason)
    return value


def confirmed(value):
    return value is True or isinstance(value, str) and value.lower() in {'true', '1', 'yes'}


def validate_profile(engine, profile):
    engine.require(isinstance(profile, dict) and confirmed(profile.get('GROQ_FREE_TIER_CONFIRMED'))
        and confirmed(profile.get('SERPAPI_FREE_TIER_CONFIRMED')), 'groq_free_confirmation_missing')
    for name in (*GROQ_KEYS, *TRAVEL_KEYS):
        key_value(engine, profile.get(name), 'groq_selected_key_missing_or_malformed')
    engine.require(MODEL == 'qwen/qwen3.8-27b',
        'groq_fixed_model_required')
    mode = profile.get('LITEAPI_MODE', 'sandbox')
    engine.require(isinstance(mode, str) and mode.lower() == 'sandbox'
        and profile['LITEAPI_API_KEY'].startswith(('sand_', 'sandbox_')), 'groq_liteapi_sandbox_required')
    engine.require(profile['DUFFEL_ACCESS_TOKEN'].startswith('duffel_test_')
        and len(profile['DUFFEL_ACCESS_TOKEN']) > len('duffel_test_'), 'groq_duffel_test_token_required')


def read_master_profile(engine):
    values = private_values(engine, MASTER, PROFILE_FIELDS)
    validate_profile(engine, values)
    return values


def read_database_password(engine):
    values = private_values(engine, MASTER, DB_FIELDS)
    engine.require(values.get('POSTGRES_USER', 'otravel') == 'otravel'
        and values.get('POSTGRES_DB', 'otravel') == 'otravel' and values.get('POSTGRES_PASSWORD'),
        'groq_master_database_identity_unexpected')
    return values['POSTGRES_PASSWORD']


def environment(engine, database, profile):
    validate_profile(engine, profile)
    env = {'HOME': '/home/aegml', 'USER': 'aegml', 'LOGNAME': 'aegml', 'LANG': 'C.UTF-8',
        'PATH': str(engine.PYTHON.parent) + ':/usr/local/bin:/usr/bin:/bin', 'PYTHONPATH': str(SOURCE),
        'PYTHONUNBUFFERED': '1', 'PYTHONDONTWRITEBYTECODE': '1',
        'DATABASE_URL': f"postgresql+asyncpg://otravel:{quote(database.password, safe='')}@{database.ip}:5432/{DATABASE}",
        'DATABASE_HOST': database.ip, 'DATABASE_PORT': '5432', 'DATABASE_USER': 'otravel',
        'DATABASE_NAME': DATABASE, 'DATABASE_PASSWORD': database.password,
        'LLM_PROVIDER': 'groq', 'GROQ_MODEL': MODEL, 'MAX_LLM_CALLS_PER_DAY': '40',
        'MAX_GROQ_LLM_CALLS_PER_DAY': '1000',
        'MAX_LLM_CALLS_PER_RUN': '1', 'MAX_LOCAL_LLM_CALLS_PER_DAY': '0',
        'LLM_REQUEST_TIMEOUT': '45', 'LLM_MAX_OUTPUT_TOKENS': '2048', 'LLM_REASONING_ENABLED': 'false',
        'REFERENCE_FX_ENABLED': 'true',
        'OPENROUTER_BACKUP_MODEL': '', 'GEMINI_FREE_TIER_CONFIRMED': 'false', 'GROQ_FREE_TIER_CONFIRMED': 'true',
        'ZAI_FREE_TIER_CONFIRMED': 'false', 'CLOUDFLARE_FREE_TIER_CONFIRMED': 'false',
        'COHERE_EVALUATION_CONFIRMED': 'false', 'NVIDIA_PROTOTYPING_CONFIRMED': 'false',
        'HOTELBEDS_TEST_CONFIRMED': 'false', 'RATEHAWK_TEST_CONFIRMED': 'false',
        'LITEAPI_MODE': 'sandbox', 'SERPAPI_FREE_TIER_CONFIRMED': 'true',
        'DEFAULT_SEARCH_MODE': 'HYBRID', 'DEFAULT_HOTEL_PROVIDER': 'liteapi', 'DEFAULT_FLIGHT_PROVIDER': 'duffel',
        'CORS_ORIGINS': 'http://localhost:8081,http://127.0.0.1:8081'}
    env.update({name: profile[name] for name in (*GROQ_KEYS, *TRAVEL_KEYS)})
    return env


def recorded_process(engine, account):
    if not engine.IDENTITY.is_file():
        return None
    stat = engine.IDENTITY.stat()
    engine.require(stat.st_uid == 0 and stat.st_mode & 0o077 == 0, 'groq_untrusted_identity_file')
    saved = json.loads(engine.IDENTITY.read_text())
    engine.require(saved.get('created_by') == CREATOR and saved.get('database') == DATABASE
        and saved.get('port') == PORT and saved.get('uid') == account.pw_uid
        and saved.get('argv') == engine.ARGV and saved.get('cwd') == str(SOURCE), 'groq_recorded_identity_scope_mismatch')
    if saved.get('stopped_at_utc'):
        return None
    current = engine.process_identity(saved['pid'])
    if current is None:
        return None
    engine.require(all(current.get(key) == saved.get(key) for key in ('pid', 'uid', 'start_ticks', 'argv', 'cwd', 'executable')),
        'groq_pid_reused_or_identity_changed')
    engine.require(Path(current['executable']).resolve() == engine.PYTHON.resolve(), 'groq_python_executable_changed')
    return current


def require_ready(engine):
    code, health = engine.http('/health')
    engine.require(code == 200 and isinstance(health, dict) and health.get('database') == 'ready'
        and health.get('application') == 'backend' and health.get('supplier_booking') is False, 'groq_health_mismatch')
    code, status = engine.http('/api/v1/providers/status')
    engine.require(code == 200 and isinstance(status, dict), 'groq_provider_status_unavailable')
    engine.require(status.get('_runtime') == {'llm_provider': 'groq', 'model': MODEL}
        and status.get('groq', {}).get('configured') is True
        and status.get('groq', {}).get('model') == MODEL
        and status.get('groq', {}).get('free_tier_confirmed') is True
        and status.get('groq', {}).get('paid_fallback') is False, 'groq_runtime_profile_mismatch')
    engine.require(status.get('liteapi', {}).get('configured') is True and status.get('liteapi', {}).get('mode') == 'sandbox'
        and status.get('duffel', {}).get('configured') is True and status.get('duffel', {}).get('mode') == 'sandbox'
        and status.get('serpapi', {}).get('configured') is True and status.get('serpapi', {}).get('free_tier_confirmed') is True
        and status.get('serpapi', {}).get('source_type') == 'LIVE_INDICATIVE', 'groq_travel_profile_mismatch')
    allowed = {'_runtime', 'groq', 'liteapi', 'duffel', 'serpapi', 'open_meteo'}
    for name in ('gemini', 'cloudflare', 'openrouter', 'zai', 'ollama', 'hotelbeds', 'ratehawk', 'travelpayouts'):
        engine.require(isinstance(status.get(name), dict) and status[name].get('configured') is False,
            'groq_disallowed_provider_not_explicitly_false')
    for name, value in status.items():
        engine.require(isinstance(value, dict), 'groq_provider_status_malformed')
        engine.require(value.get('paid_fallback') is not True, 'groq_paid_fallback_forbidden')
        if name not in allowed:
            engine.require(not value.get('configured') and not value.get('free_tier_confirmed'), 'groq_disallowed_provider_configured')
    return {'database': 'ready', 'provider': 'groq', 'model': MODEL,
        'travel': {'liteapi': 'SANDBOX', 'duffel': 'TEST', 'serpapi': 'LIVE_INDICATIVE'},
        'supplier_booking': False, 'provider_quota_checked': False, 'generation_performed': False,
        'pay_zero_policy': {'free_tier_confirmed': True, 'billing_enabled_by_manager': False, 'fallback': False}}


def save_evidence(engine, action, account, extra=None):
    identity, listeners = engine.recorded_process(account), engine.listeners()
    saved = json.loads(engine.IDENTITY.read_text()) if engine.IDENTITY.is_file() else {}
    manifest = engine.source_manifest()
    loaded = engine.loaded_manifest_status(saved, manifest) if identity else {'status': 'NOT_RUNNING'}
    status = 'RUNNING' if identity else 'UNOWNED_LISTENER' if listeners else 'STOPPED'
    record = {'checked_at_utc': engine.now(), 'action': action, 'status': status, 'manager': CREATOR,
        'source': str(SOURCE), 'database': DATABASE, 'url': engine.BASE, 'process': identity, 'listeners': listeners,
        'current_source_manifest': manifest, 'process_start_source_manifest': saved.get('source_manifest'), 'loaded_manifest': loaded,
        'default_search': {'mode': 'HYBRID', 'hotel_provider': 'liteapi', 'flight_provider': 'duffel', 'guest_nationality': None},
        'limits': {'daily_engineering_bound': 40, 'generic_daily_engineering_bound': 40,
            'groq_daily_engineering_bound': 1000, 'vendor_quota': 'UNKNOWN', 'calls_per_run': 1, 'local_calls': 0, 'fallback': False},
        'source_env_read': False, 'inference_requested_by_manager': False, 'supplier_requests_by_manager': False,
        'credentials_recorded': False, 'helper_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    if identity:
        engine.require_owned_listener(identity)
        engine.require(loaded.get('matches_current') is True, 'groq_loaded_source_changed_restart_required')
        record['readiness'] = require_ready(engine)
        record['status_pages'] = {path: engine.http(path)[0] for path in ('/docs', '/debug')}
    if extra:
        engine.require(set(extra) <= EXTRA_FIELDS, 'groq_public_evidence_extra_not_allowlisted')
        record.update(extra)
    engine.EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    engine.atomic_json(engine.EVIDENCE, record, 0o644)
    print(json.dumps({'action': action, 'status': status, 'url': engine.BASE, 'database': DATABASE,
        'evidence': str(engine.EVIDENCE), 'inference_requests': 0}))
    return record


def require_existing_database(engine, database):
    """Do not create a replacement DB or edit HBA while switching providers."""
    database.verify_same()
    actual = database.sql(f"SELECT pg_get_userbyid(datdba)||'|'||datistemplate||'|'||datallowconn FROM pg_database WHERE datname='{DATABASE}';\n")
    engine.require(actual == 'otravel|false|true', 'groq_shared_database_missing_or_wrong_identity')
    database.receipt.update(database_created=False, hba_modified_by_manager=False)


def start(engine, account):
    identity = engine.recorded_process(account)
    if identity:
        engine.require_owned_listener(identity)
        saved = json.loads(engine.IDENTITY.read_text())
        engine.require(engine.loaded_manifest_status(saved, engine.source_manifest()).get('matches_current') is True,
            'groq_source_changed_owned_restart_required')
        require_ready(engine)
        return engine.save_evidence('start_reused_owned_process', account)
    engine.require(not engine.listeners(), 'groq_port8084_unowned_nothing_stopped')
    profile = read_master_profile(engine)
    database = engine.Database()
    env = environment(engine, database, profile)
    manifest = engine.source_manifest()
    code = ("import json,os,pathlib,otravel.api; from otravel.config import Settings; s=Settings.from_env(); "
        "print(json.dumps({'api_file':str(pathlib.Path(otravel.api.__file__).resolve()),'provider':s.llm_mode,"
        "'model':os.environ['GROQ_MODEL'],'daily_limit':s.max_groq_llm_calls_per_day,"
        "'generic_daily_limit':s.max_llm_calls_per_day,"
        "'run_limit':s.max_llm_calls_per_run,'local_limit':s.max_local_llm_calls_per_day,"
        "'search_mode':s.default_search_mode,'other_model_credentials_present':any(k in os.environ for k in "
        "('OPENROUTER_API_KEY','GEMINI_API_KEY','ZAI_API_KEY','CLOUDFLARE_API_TOKEN','COHERE_API_KEY','NVIDIA_API_KEY','OPENAI_API_KEY','ANTHROPIC_API_KEY'))}))")
    imported = engine.capture([str(engine.PYTHON), '-c', code], env=env, cwd=SOURCE,
        user=account.pw_uid, group=account.pw_gid, extra_groups=[])
    engine.require(imported.returncode == 0, 'groq_native_import_failed_details_suppressed')
    facts = json.loads(imported.stdout)
    expected = {'api_file': str(SOURCE / 'otravel/api.py'), 'provider': 'groq', 'model': MODEL,
        'daily_limit': 1000, 'generic_daily_limit': 40, 'run_limit': 1, 'local_limit': 0, 'search_mode': 'HYBRID', 'other_model_credentials_present': False}
    engine.require(facts == expected, 'groq_native_source_or_explicit_limits_mismatch')
    engine.require(engine.source_manifest() == manifest, 'groq_source_changed_before_database_check')
    require_existing_database(engine, database)
    before = database.counts()
    engine.require({'sessions', 'agent_runs', 'agent_events'} <= set(before), 'groq_shared_database_schema_missing')
    engine.require(before.get('pending_or_running', 0) == 0, 'groq_pending_runs_require_owner_review_no_auto_inference')
    engine.require(not engine.listeners() and engine.source_manifest() == manifest, 'groq_port_or_source_changed_before_launch')
    fd = os.open(engine.LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        process = subprocess.Popen(engine.ARGV, cwd=SOURCE, env=env, stdin=subprocess.DEVNULL, stdout=fd,
            stderr=subprocess.STDOUT, close_fds=True, start_new_session=True, user=account.pw_uid,
            group=account.pw_gid, extra_groups=[], umask=0o077)
    finally:
        os.close(fd)
    identity = engine.process_identity(process.pid)
    engine.require(identity is not None, 'groq_process_exited_immediately_private_log_retained')
    engine.atomic_json(engine.IDENTITY, {**identity, 'created_by': CREATOR, 'created_at_utc': engine.now(),
        'database': DATABASE, 'port': PORT, 'source_manifest': manifest})
    for _ in range(45):
        engine.require(process.poll() is None, 'groq_process_exited_private_log_retained')
        try:
            engine.require_owned_listener(engine.recorded_process(account))
            require_ready(engine)
            break
        except (OSError, engine.Refusal, ValueError):
            time.sleep(1)
    else:
        raise engine.Refusal('groq_health_timeout_owned_process_retained_no_force_stop')
    engine.require(engine.source_manifest() == manifest, 'groq_source_changed_during_startup_process_retained')
    after = database.counts()
    engine.require(after.get('model_call_started', 0) == before.get('model_call_started', 0),
        'groq_unexpected_inference_during_start')
    return engine.save_evidence('start', account, {'database_setup': database.receipt,
        'database_counts_before': before, 'database_counts_after': after, 'import_check': facts})


def windows_dispatch(action):
    import winreg

    base = None
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Lxss') as registry:
        for index in range(winreg.QueryInfoKey(registry)[0]):
            with winreg.OpenKey(registry, winreg.EnumKey(registry, index)) as item:
                if winreg.QueryValueEx(item, 'DistributionName')[0] == 'AegML-Hackathon':
                    base = winreg.QueryValueEx(item, 'BasePath')[0]
    if base is None or not str(base).removeprefix("\\\\?\\").lower().startswith('c:'):
        raise ValueError('groq_distro_not_c_backed')
    path = str(Path(__file__).resolve())
    script = '/mnt/' + path[0].lower() + path[2:].replace("\\", "/")
    result = subprocess.run(['wsl.exe', '-d', 'AegML-Hackathon', '-u', 'root', '--', 'python3', '-B', script, '--action', action],
        capture_output=True, text=True, encoding='utf-8', errors='replace', check=False)
    print(result.stdout, end='')
    if result.stderr:
        print('Groq manager child details suppressed; consult private log.', file=sys.stderr)
    return result.returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--action', choices=('plan', 'start', 'status', 'stop'), required=True)
    action = parser.parse_args(argv).action
    if action == 'plan':
        print(json.dumps({'action': 'plan', 'manager': CREATOR, 'url': f'http://127.0.0.1:{PORT}',
            'provider': 'groq', 'model': MODEL, 'database': DATABASE, 'source': str(SOURCE),
            'daily_engineering_bound': 40, 'generic_daily_engineering_bound': 40, 'groq_daily_engineering_bound': 1000,
            'vendor_quota': 'UNKNOWN', 'calls_per_run': 1, 'local_calls': 0,
            'inference_requests': 0, 'credentials_read': False, 'runtime_changed': False}))
        return 0
    engine = None
    try:
        if sys.platform == 'win32':
            return windows_dispatch(action)
        import fcntl

        engine = new_engine()
        account = engine.native_paths()
        with (engine.RUNTIME / 'controller.lock').open('a') as lock:
            os.chmod(engine.RUNTIME / 'controller.lock', 0o600)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            if action == 'start':
                start(engine, account)
            elif action == 'stop':
                engine.stop(account)
            else:
                engine.save_evidence('status', account)
        return 0
    except Exception as error:  # noqa: BLE001 - no credential-bearing traceback at CLI boundary.
        reason = str(error) if engine is not None and isinstance(error, engine.Refusal) else type(error).__name__
        print(json.dumps({'action': action, 'status': 'REFUSED_OR_FAILED', 'reason': reason, 'credentials_printed': False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
