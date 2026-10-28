"""Own GigaChat Ultra freemium/HYBRID lifecycle on8085. Explicit actions only.

No copying, installs, generation, supplier searches or billing setup. SOURCE.env
is never read: credentials come only from selected fields of the native MASTER.
An existing Qwen/ZAI/foreign listener is refused, never stopped or reconfigured.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import ipaddress
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path('/home/aegml/projects/otravel-gigachat-20261027')
MASTER = Path('/home/aegml/projects/otravel/.env')
DATABASE = 'otravel_gigachat_eval'
PORT = 8085
MODEL = 'GigaChat-3-Ultra'
CREATOR = 'gigachat_backend.py'
GIGACHAT_KEYS = ('GIGACHAT_AUTH_KEY',)
TRAVEL_KEYS = ('LITEAPI_API_KEY', 'DUFFEL_ACCESS_TOKEN', 'SERPAPI_API_KEY')
PROFILE_FIELDS = (*GIGACHAT_KEYS, 'GIGACHAT_FREEMIUM_CONFIRMED', 'GIGACHAT_CA_FILE',
    *TRAVEL_KEYS, 'SERPAPI_FREE_TIER_CONFIRMED', 'LITEAPI_MODE')
DB_FIELDS = ('POSTGRES_USER', 'POSTGRES_DB', 'POSTGRES_PASSWORD')
EXTRA_FIELDS = {'database_setup', 'database_counts_before', 'database_counts_after', 'import_check',
                'database_counts_after_import', 'stopped_owned_process', 'signal', 'signal_target'}


def new_engine():
    """Reuse only bounded identity/manifest/Docker guards in an isolated module."""
    spec = importlib.util.spec_from_file_location('gigachat_core_' + uuid4().hex, ROOT / 'scripts/local_backend.py')
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    runtime = SOURCE / '.local' / 'gigachat-backend-8085'
    overrides = {'SOURCE': SOURCE, 'DATABASE': DATABASE, 'PORT': PORT, 'BASE': f'http://127.0.0.1:{PORT}',
        'RUNTIME': runtime, 'IDENTITY': runtime / 'process.json', 'LOG': runtime / 'uvicorn.log',
        'EVIDENCE': ROOT / 'docs/verification/gigachat-backend-20261027.json',
        'SAFE_CONTEXT': {}, 'CURRENT_STAGE': 'gigachat_preflight'}
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
        and path.stat().st_mode & 0o077 == 0, 'gigachat_private_master_missing_or_untrusted')
    result = {}
    for raw in path.read_text(encoding='utf-8-sig').splitlines():
        line = raw.strip().removeprefix('export ')
        if not line or line.startswith('#') or '=' not in line:
            continue
        name, value = line.split('=', 1)
        name, value = name.strip(), value.strip()
        if name not in allowed:
            continue
        engine.require(name not in result, 'gigachat_duplicate_selected_master_field')
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
    engine.require(isinstance(profile, dict) and confirmed(profile.get('GIGACHAT_FREEMIUM_CONFIRMED'))
        and confirmed(profile.get('SERPAPI_FREE_TIER_CONFIRMED')), 'gigachat_free_confirmation_missing')
    for name in (*GIGACHAT_KEYS, *TRAVEL_KEYS):
        key_value(engine, profile.get(name), 'gigachat_selected_key_missing_or_malformed')
    engine.require(MODEL == 'GigaChat-3-Ultra',
        'gigachat_fixed_model_required')
    mode = profile.get('LITEAPI_MODE', 'sandbox')
    engine.require(isinstance(mode, str) and mode.lower() == 'sandbox'
        and profile['LITEAPI_API_KEY'].startswith(('sand_', 'sandbox_')), 'gigachat_liteapi_sandbox_required')
    engine.require(profile['DUFFEL_ACCESS_TOKEN'].startswith('duffel_test_')
        and len(profile['DUFFEL_ACCESS_TOKEN']) > len('duffel_test_'), 'gigachat_duffel_test_token_required')
    ca = profile.get('GIGACHAT_CA_FILE', '')
    engine.require(isinstance(ca, str) and (not ca or ca.startswith('/home/aegml/')
        and not any(char.isspace() for char in ca) and '\\' not in ca and Path(ca).is_absolute()
        and '..' not in Path(ca).parts), 'gigachat_ca_path_outside_native_scope')


def read_master_profile(engine):
    values = private_values(engine, MASTER, PROFILE_FIELDS)
    validate_profile(engine, values)
    return values


def read_database_password(engine):
    values = private_values(engine, MASTER, DB_FIELDS)
    engine.require(values.get('POSTGRES_USER', 'otravel') == 'otravel'
        and values.get('POSTGRES_DB', 'otravel') == 'otravel' and values.get('POSTGRES_PASSWORD'),
        'gigachat_master_database_identity_unexpected')
    return values['POSTGRES_PASSWORD']


def environment(engine, database, profile):
    validate_profile(engine, profile)
    env = {'HOME': '/home/aegml', 'USER': 'aegml', 'LOGNAME': 'aegml', 'LANG': 'C.UTF-8',
        'PATH': str(engine.PYTHON.parent) + ':/usr/local/bin:/usr/bin:/bin', 'PYTHONPATH': str(SOURCE),
        'PYTHONUNBUFFERED': '1', 'PYTHONDONTWRITEBYTECODE': '1',
        'DATABASE_URL': f"postgresql+asyncpg://otravel:{quote(database.password, safe='')}@{database.ip}:5432/{DATABASE}",
        'DATABASE_HOST': database.ip, 'DATABASE_PORT': '5432', 'DATABASE_USER': 'otravel',
        'DATABASE_NAME': DATABASE, 'DATABASE_PASSWORD': database.password,
        'LLM_PROVIDER': 'gigachat', 'GIGACHAT_MODEL': MODEL, 'GIGACHAT_SCOPE': 'GIGACHAT_API_PERS',
        'MAX_LLM_CALLS_PER_DAY': '40',
        'MAX_GIGACHAT_LLM_CALLS_PER_DAY': '1000',
        'MAX_LLM_CALLS_PER_RUN': '1', 'MAX_LOCAL_LLM_CALLS_PER_DAY': '0',
        'LLM_REQUEST_TIMEOUT': '45', 'LLM_MAX_OUTPUT_TOKENS': '2048', 'LLM_REASONING_ENABLED': 'false',
        'REFERENCE_FX_ENABLED': 'true',
        'OPENROUTER_BACKUP_MODEL': '', 'GEMINI_FREE_TIER_CONFIRMED': 'false', 'GIGACHAT_FREEMIUM_CONFIRMED': 'true',
        'GROQ_FREE_TIER_CONFIRMED': 'false',
        'ZAI_FREE_TIER_CONFIRMED': 'false', 'CLOUDFLARE_FREE_TIER_CONFIRMED': 'false',
        'COHERE_EVALUATION_CONFIRMED': 'false', 'NVIDIA_PROTOTYPING_CONFIRMED': 'false',
        'HOTELBEDS_TEST_CONFIRMED': 'false', 'RATEHAWK_TEST_CONFIRMED': 'false',
        'LITEAPI_MODE': 'sandbox', 'SERPAPI_FREE_TIER_CONFIRMED': 'true',
        'DEFAULT_SEARCH_MODE': 'HYBRID', 'DEFAULT_HOTEL_PROVIDER': 'liteapi', 'DEFAULT_FLIGHT_PROVIDER': 'duffel',
        'CORS_ORIGINS': 'http://localhost:8081,http://127.0.0.1:8081'}
    env.update({name: profile[name] for name in (*GIGACHAT_KEYS, *TRAVEL_KEYS)})
    if profile.get('GIGACHAT_CA_FILE'):
        env['GIGACHAT_CA_FILE'] = profile['GIGACHAT_CA_FILE']
    return env


def recorded_process(engine, account):
    if not engine.IDENTITY.is_file():
        return None
    stat = engine.IDENTITY.stat()
    engine.require(stat.st_uid == 0 and stat.st_mode & 0o077 == 0, 'gigachat_untrusted_identity_file')
    saved = json.loads(engine.IDENTITY.read_text())
    engine.require(saved.get('created_by') == CREATOR and saved.get('database') == DATABASE
        and saved.get('port') == PORT and saved.get('uid') == account.pw_uid
        and saved.get('argv') == engine.ARGV and saved.get('cwd') == str(SOURCE), 'gigachat_recorded_identity_scope_mismatch')
    if saved.get('stopped_at_utc'):
        return None
    current = engine.process_identity(saved['pid'])
    if current is None:
        return None
    engine.require(all(current.get(key) == saved.get(key) for key in ('pid', 'uid', 'start_ticks', 'argv', 'cwd', 'executable')),
        'gigachat_pid_reused_or_identity_changed')
    engine.require(Path(current['executable']).resolve() == engine.PYTHON.resolve(), 'gigachat_python_executable_changed')
    return current


def require_ready(engine):
    code, health = engine.http('/health')
    engine.require(code == 200 and isinstance(health, dict) and health.get('database') == 'ready'
        and health.get('application') == 'backend' and health.get('supplier_booking') is False, 'gigachat_health_mismatch')
    code, status = engine.http('/api/v1/providers/status')
    engine.require(code == 200 and isinstance(status, dict), 'gigachat_provider_status_unavailable')
    engine.require(status.get('_runtime') == {'llm_provider': 'gigachat', 'model': MODEL}
        and status.get('gigachat', {}).get('configured') is True
        and status.get('gigachat', {}).get('model') == MODEL
        and status.get('gigachat', {}).get('freemium_confirmed') is True
        and status.get('gigachat', {}).get('paid_fallback') is False, 'gigachat_runtime_profile_mismatch')
    engine.require(status.get('liteapi', {}).get('configured') is True and status.get('liteapi', {}).get('mode') == 'sandbox'
        and status.get('duffel', {}).get('configured') is True and status.get('duffel', {}).get('mode') == 'sandbox'
        and status.get('serpapi', {}).get('configured') is True and status.get('serpapi', {}).get('free_tier_confirmed') is True
        and status.get('serpapi', {}).get('source_type') == 'LIVE_INDICATIVE', 'gigachat_travel_profile_mismatch')
    allowed = {'_runtime', 'gigachat', 'liteapi', 'duffel', 'serpapi', 'open_meteo'}
    for name in ('gemini', 'groq', 'cloudflare', 'openrouter', 'zai', 'ollama', 'hotelbeds', 'ratehawk', 'travelpayouts'):
        engine.require(isinstance(status.get(name), dict) and status[name].get('configured') is False,
            'gigachat_disallowed_provider_not_explicitly_false')
    for name, value in status.items():
        engine.require(isinstance(value, dict), 'gigachat_provider_status_malformed')
        engine.require(value.get('paid_fallback') is not True, 'gigachat_paid_fallback_forbidden')
        if name not in allowed:
            engine.require(not value.get('configured') and not value.get('free_tier_confirmed')
                and not value.get('freemium_confirmed'), 'gigachat_disallowed_provider_configured')
    return {'database': 'ready', 'provider': 'gigachat', 'model': MODEL,
        'travel': {'liteapi': 'SANDBOX', 'duffel': 'TEST', 'serpapi': 'LIVE_INDICATIVE'},
        'supplier_booking': False, 'provider_quota_checked': False, 'generation_performed': False,
        'pay_zero_policy': {'freemium_confirmed': True, 'billing_enabled_by_manager': False, 'fallback': False}}


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
            'gigachat_daily_engineering_bound': 1000, 'vendor_quota': 'UNKNOWN', 'calls_per_run': 1, 'local_calls': 0, 'fallback': False},
        'source_env_read': False, 'inference_requested_by_manager': False, 'supplier_requests_by_manager': False,
        'credentials_recorded': False, 'helper_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    if identity:
        engine.require_owned_listener(identity)
        engine.require(loaded.get('matches_current') is True, 'gigachat_loaded_source_changed_restart_required')
        record['readiness'] = require_ready(engine)
        record['status_pages'] = {path: engine.http(path)[0] for path in ('/docs', '/debug')}
    if extra:
        engine.require(set(extra) <= EXTRA_FIELDS, 'gigachat_public_evidence_extra_not_allowlisted')
        record.update(extra)
    engine.EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    engine.atomic_json(engine.EVIDENCE, record, 0o644)
    print(json.dumps({'action': action, 'status': status, 'url': engine.BASE, 'database': DATABASE,
        'evidence': str(engine.EVIDENCE), 'inference_requests': 0}))
    return record


def ensure_owned_database(engine, database):
    """Create only this fixed DB; never adopt an unrecorded existing database."""
    database.verify_same()
    actual = database.sql(f"SELECT pg_get_userbyid(datdba)||'|'||datistemplate||'|'||datallowconn FROM pg_database WHERE datname='{DATABASE}';\n")
    path = engine.RUNTIME / 'database-setup.json'
    old = None
    if path.exists():
        engine.require(path.is_file() and not path.is_symlink() and path.stat().st_uid == 0
            and path.stat().st_mode & 0o077 == 0, 'gigachat_untrusted_database_receipt')
        old = json.loads(path.read_text())
        engine.require(old.get('created_by') == CREATOR and old.get('target_database') == DATABASE
            and old.get('container_id') == database.identifier, 'gigachat_database_receipt_scope_mismatch')
    if actual:
        engine.require(actual == 'otravel|false|true' and old is not None,
            'gigachat_existing_database_unowned_or_wrong_identity')
    else:
        engine.require(old is None, 'gigachat_recorded_database_missing_no_recreation')
        database.sql(f'CREATE DATABASE {DATABASE} OWNER otravel;\n')
        database.receipt['database_created'] = True
        actual = database.sql(f"SELECT pg_get_userbyid(datdba)||'|'||datistemplate||'|'||datallowconn FROM pg_database WHERE datname='{DATABASE}';\n")
        engine.require(actual == 'otravel|false|true', 'gigachat_created_database_identity_mismatch')
    oid = int(database.sql(f"SELECT oid FROM pg_database WHERE datname='{DATABASE}';\n"))
    if old:
        engine.require(old.get('database_oid') == oid, 'gigachat_database_oid_changed_no_recreation')
    database.receipt.update(database_oid=oid, created_by=CREATOR, target_database=DATABASE)
    engine.atomic_json(path, database.receipt)


def scoped_hba_plan(engine, before, client_ip, subnet):
    """Pure exact-/32 append plan; unrelated existing bytes remain a prefix."""
    client = ipaddress.ip_address(client_ip)
    network = ipaddress.ip_network(subnet)
    engine.require(client.version == 4 and client.is_private and network.version == 4
        and network.is_private and network.prefixlen >= 16 and client in network,
        'gigachat_client_route_not_verified_private_bridge')
    engine.require(isinstance(before, str) and '\ufffd' not in before, 'gigachat_hba_text_untrusted')
    required = list(dict.fromkeys([f'host {DATABASE} otravel 127.0.0.1/32 scram-sha-256',
        f'host {DATABASE} otravel {client}/32 scram-sha-256']))
    existing = []
    for raw in before.splitlines():
        fields = raw.split('#', 1)[0].split()
        if not fields:
            continue
        if len(fields) >= 2 and DATABASE in fields[1].split(','):
            normalized = ' '.join(fields)
            engine.require(normalized in required, 'gigachat_existing_hba_rule_outside_fixed_scope')
            existing.append(normalized)
    missing = [line for line in required if line not in existing]
    after = before
    if missing:
        after += ('' if before.endswith('\n') or not before else '\n')
        after += '# GigaChat evaluation: exact database/role and observed client IPv4 only.\n'
        after += '\n'.join(missing) + '\n'
    engine.require(after.startswith(before), 'gigachat_unrelated_hba_bytes_changed')
    return after, {'changed': bool(missing), 'rules': required,
        'before_sha256': hashlib.sha256(before.encode()).hexdigest(),
        'after_sha256': hashlib.sha256(after.encode()).hexdigest()}


def ensure_scoped_hba(engine, database):
    database.verify_same()
    route = engine.capture(['ip', '-j', 'route', 'get', database.ip])
    engine.require(route.returncode == 0, 'gigachat_client_route_unavailable')
    routes = json.loads(route.stdout)
    engine.require(type(routes) is list and len(routes) == 1 and routes[0].get('prefsrc'),
        'gigachat_client_route_ambiguous')
    client_ip = routes[0]['prefsrc']
    engine.require(client_ip != database.ip, 'gigachat_client_route_is_database_host')
    def read_hba():
        result = engine.capture(['docker', 'exec', database.identifier, 'base64', engine.HBA])
        engine.require(result.returncode == 0, 'gigachat_hba_snapshot_unavailable')
        try:
            return base64.b64decode(''.join(result.stdout.split()), validate=True).decode('utf-8')
        except (ValueError, UnicodeError):
            raise engine.Refusal('gigachat_hba_snapshot_encoding_invalid') from None
    before = read_hba()
    after, facts = scoped_hba_plan(engine, before, client_ip, str(database.subnet))
    if facts['changed']:
        backup = engine.RUNTIME / ('pg_hba-before-' + uuid4().hex + '.conf')
        with backup.open('x', encoding='utf-8', newline='') as stream:
            stream.write(before)
        os.chmod(backup, 0o600)
        shell = ('set -eu; p=/var/lib/postgresql/data/pg_hba.conf; '
            't=/var/lib/postgresql/data/.pg_hba.gigachat_eval.tmp; test ! -e "$t"; '
            'umask 077; cat > "$t"; chown postgres:postgres "$t"; chmod 600 "$t"; mv "$t" "$p"')
        database.verify_same()
        engine.docker('exec', '-i', '-u', '0', database.identifier, 'sh', '-c', shell, stdin=after)
        database.receipt['hba_before_snapshot'] = str(backup)
    engine.require(read_hba() == after, 'gigachat_hba_bytes_changed_unexpectedly')
    engine.require(database.sql('SELECT pg_reload_conf();\n') == 't', 'gigachat_hba_reload_failed')
    engine.require(database.sql('SELECT count(*) FROM pg_hba_file_rules WHERE error IS NOT NULL;\n') == '0',
        'gigachat_hba_parse_error')
    addresses = list(dict.fromkeys(['127.0.0.1', str(ipaddress.ip_address(client_ip))]))
    literals = ','.join("'" + value + "'" for value in addresses)
    count = database.sql(f"SELECT count(*) FROM pg_hba_file_rules WHERE type='host' AND database=ARRAY['{DATABASE}'] "
        f"AND user_name=ARRAY['otravel'] AND auth_method='scram-sha-256' AND address IN ({literals}) "
        "AND netmask='255.255.255.255';\n")
    engine.require(count == str(len(addresses)), 'gigachat_scoped_hba_rows_not_confirmed')
    database.receipt['hba'] = {**facts, 'client_ipv4': client_ip,
        'reload_acknowledged': True, 'postgres_restarted': False, 'unrelated_bytes_preserved': True}
    engine.atomic_json(engine.RUNTIME / 'database-setup.json', database.receipt)


def initialize_owned_schema(engine, database, env, account):
    before = database.counts()
    engine.require(before.get('pending_or_running', 0) == 0,
        'gigachat_pending_runs_require_owner_review_no_auto_inference')
    # One loop owns the engine during migration and disposal.
    code = ('import asyncio,os; from otravel.store import Store\n'
        'async def main():\n s=Store(os.environ["DATABASE_URL"])\n'
        ' try:\n  await s.initialize()\n finally:\n  await s.close()\nasyncio.run(main())')
    result = engine.capture([str(engine.PYTHON), '-c', code], env=env, cwd=SOURCE,
        user=account.pw_uid, group=account.pw_gid, extra_groups=[])
    engine.require(result.returncode == 0, 'gigachat_own_schema_initialize_failed_details_suppressed')
    after = database.counts()
    engine.require({'sessions', 'agent_runs', 'agent_events'} <= set(after)
        and after.get('pending_or_running', 0) == 0
        and after.get('model_call_started', 0) == before.get('model_call_started', 0),
        'gigachat_own_schema_counts_or_no_inference_mismatch')
    database.receipt['schema_initialized'] = True
    return after


def start(engine, account):
    identity = engine.recorded_process(account)
    if identity:
        engine.require_owned_listener(identity)
        saved = json.loads(engine.IDENTITY.read_text())
        engine.require(engine.loaded_manifest_status(saved, engine.source_manifest()).get('matches_current') is True,
            'gigachat_source_changed_owned_restart_required')
        require_ready(engine)
        return engine.save_evidence('start_reused_owned_process', account)
    engine.require(not engine.listeners(), 'gigachat_port8085_unowned_nothing_stopped')
    profile = read_master_profile(engine)
    database = engine.Database()
    env = environment(engine, database, profile)
    manifest = engine.source_manifest()
    if profile.get('GIGACHAT_CA_FILE'):
        ca = Path(profile['GIGACHAT_CA_FILE'])
        engine.require(ca.is_file() and not ca.is_symlink() and ca.resolve() == ca
            and ca.stat().st_mode & 0o022 == 0 and ca.stat().st_size <= 1_048_576,
            'gigachat_configured_ca_file_untrusted')
    ensure_owned_database(engine, database)
    ensure_scoped_hba(engine, database)
    before = initialize_owned_schema(engine, database, env, account)
    code = ("import json,os,pathlib,otravel.api; from otravel.config import Settings, provider_status; s=Settings.from_env(); "
        "print(json.dumps({'api_file':str(pathlib.Path(otravel.api.__file__).resolve()),'provider':s.llm_mode,"
        "'model':os.environ['GIGACHAT_MODEL'],'daily_limit':s.max_gigachat_llm_calls_per_day,"
        "'generic_daily_limit':s.max_llm_calls_per_day,"
        "'freemium_confirmed':provider_status()['gigachat'].get('freemium_confirmed'),"
        "'run_limit':s.max_llm_calls_per_run,'local_limit':s.max_local_llm_calls_per_day,"
        "'search_mode':s.default_search_mode,'other_model_credentials_present':any(k in os.environ for k in "
        "('GROQ_API_KEY','OPENROUTER_API_KEY','GEMINI_API_KEY','ZAI_API_KEY','CLOUDFLARE_API_TOKEN','COHERE_API_KEY','NVIDIA_API_KEY','OPENAI_API_KEY','ANTHROPIC_API_KEY'))}))")
    imported = engine.capture([str(engine.PYTHON), '-c', code], env=env, cwd=SOURCE,
        user=account.pw_uid, group=account.pw_gid, extra_groups=[])
    engine.require(imported.returncode == 0, 'gigachat_native_import_failed_details_suppressed')
    facts = json.loads(imported.stdout)
    expected = {'api_file': str(SOURCE / 'otravel/api.py'), 'provider': 'gigachat', 'model': MODEL,
        'daily_limit': 1000, 'generic_daily_limit': 40, 'run_limit': 1, 'local_limit': 0, 'search_mode': 'HYBRID',
        'freemium_confirmed': True, 'other_model_credentials_present': False}
    engine.require(facts == expected, 'gigachat_native_source_or_explicit_limits_mismatch')
    engine.require(engine.source_manifest() == manifest, 'gigachat_source_changed_during_import')
    after_import = database.counts()
    engine.require(after_import == before, 'gigachat_import_changed_database_or_inference_counts')
    engine.require(not engine.listeners() and engine.source_manifest() == manifest, 'gigachat_port_or_source_changed_before_launch')
    fd = os.open(engine.LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        process = subprocess.Popen(engine.ARGV, cwd=SOURCE, env=env, stdin=subprocess.DEVNULL, stdout=fd,
            stderr=subprocess.STDOUT, close_fds=True, start_new_session=True, user=account.pw_uid,
            group=account.pw_gid, extra_groups=[], umask=0o077)
    finally:
        os.close(fd)
    identity = engine.process_identity(process.pid)
    engine.require(identity is not None, 'gigachat_process_exited_immediately_private_log_retained')
    engine.atomic_json(engine.IDENTITY, {**identity, 'created_by': CREATOR, 'created_at_utc': engine.now(),
        'database': DATABASE, 'port': PORT, 'source_manifest': manifest})
    for _ in range(45):
        engine.require(process.poll() is None, 'gigachat_process_exited_private_log_retained')
        try:
            engine.require_owned_listener(engine.recorded_process(account))
            require_ready(engine)
            break
        except (OSError, engine.Refusal, ValueError):
            time.sleep(1)
    else:
        raise engine.Refusal('gigachat_health_timeout_owned_process_retained_no_force_stop')
    engine.require(engine.source_manifest() == manifest, 'gigachat_source_changed_during_startup_process_retained')
    after = database.counts()
    engine.require(after.get('model_call_started', 0) == before.get('model_call_started', 0),
        'gigachat_unexpected_inference_during_start')
    return engine.save_evidence('start', account, {'database_setup': database.receipt,
        'database_counts_before': before, 'database_counts_after_import': after_import,
        'database_counts_after': after, 'import_check': facts})


def windows_dispatch(action):
    import winreg

    base = None
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Lxss') as registry:
        for index in range(winreg.QueryInfoKey(registry)[0]):
            with winreg.OpenKey(registry, winreg.EnumKey(registry, index)) as item:
                if winreg.QueryValueEx(item, 'DistributionName')[0] == 'AegML-Hackathon':
                    base = winreg.QueryValueEx(item, 'BasePath')[0]
    if base is None or not str(base).removeprefix("\\\\?\\").lower().startswith('c:'):
        raise ValueError('gigachat_distro_not_c_backed')
    path = str(Path(__file__).resolve())
    script = '/mnt/' + path[0].lower() + path[2:].replace("\\", "/")
    result = subprocess.run(['wsl.exe', '-d', 'AegML-Hackathon', '-u', 'root', '--', 'python3', '-B', script, '--action', action],
        capture_output=True, text=True, encoding='utf-8', errors='replace', check=False)
    print(result.stdout, end='')
    if result.stderr:
        print('GigaChat manager child details suppressed; consult private log.', file=sys.stderr)
    return result.returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--action', choices=('plan', 'start', 'status', 'stop'), required=True)
    action = parser.parse_args(argv).action
    if action == 'plan':
        print(json.dumps({'action': 'plan', 'manager': CREATOR, 'url': f'http://127.0.0.1:{PORT}',
            'provider': 'gigachat', 'model': MODEL, 'database': DATABASE, 'source': str(SOURCE),
            'daily_engineering_bound': 40, 'generic_daily_engineering_bound': 40, 'gigachat_daily_engineering_bound': 1000,
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
