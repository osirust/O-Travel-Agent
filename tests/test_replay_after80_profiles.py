"""Only profile/preflight/dry/resume guards; no edits to Replay case or oracles."""
import importlib.util
import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('replay_profile_test', ROOT / 'scripts/replay_after80.py')
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def rows():
    return [{'id': f'ZAI100-{i:03d}', 'turns': [{'turn': 1, 'user': 'synthetic source'}], 'actions': []} for i in (1, 2)]


def metadata(profile='zai'):
    selected = runner.LLM_PROFILES[profile]
    health = {'database': 'ready', 'supplier_booking': False}
    status = {name: {'configured': False} for name in ('gemini', 'openrouter', 'groq', 'zai', 'cloudflare', 'ollama', 'hotelbeds', 'ratehawk', 'travelpayouts')}
    status.update({'_runtime': {'llm_provider': selected['provider'], 'model': selected['model']},
        selected['provider']: {'configured': True, 'free_tier_confirmed': True, 'paid_fallback': False},
        'liteapi': {'configured': True, 'mode': 'sandbox'}, 'duffel': {'configured': True, 'mode': 'sandbox'},
        'serpapi': {'configured': True, 'free_tier_confirmed': True, 'source_type': 'LIVE_INDICATIVE'},
        'open_meteo': {'configured': True, 'requires_key': False}})
    evidence = {'manager': selected['manager'], 'status': 'RUNNING', 'source': '/home/aegml/projects/otravel-after80-20261026',
        'database': 'otravel_zai_eval', 'url': runner.BASE, 'loaded_manifest': {'matches_current': True},
        'limits': {'calls_per_run': 1, 'local_calls': 0, 'fallback': False, 'cloudflare_daily_engineering_bound': 2000, 'daily_engineering_bound': 40},
        'current_source_manifest': {'sha256': 'synthetic-manifest', 'files': [{'path': 'otravel/orchestrator.py', 'sha256': 'synthetic-orchestrator'}]}}
    if profile == 'openrouter-dots':
        status['openrouter'].pop('free_tier_confirmed')
        status['openrouter'].update(model=selected['model'], backup_model='')
    if profile == 'zai47-flash':
        evidence['limits']['daily_engineering_bound'] = 2000
    return health, status, evidence


def test_default_zai_and_explicit_cf_profiles_are_frozen_and_not_interchangeable():
    zai = runner.plan(rows())
    cf = runner.plan(rows(), llm_profile='cloudflare-nemotron')
    assert zai['llm_profile'] == 'zai' and zai['expected_runtime'] == {'llm_provider': 'zai', 'model': 'glm-4.5-flash'}
    assert cf['llm_profile'] == 'cloudflare-nemotron'
    assert cf['expected_runtime'] == {'llm_provider': 'cloudflare', 'model': '@cf/nvidia/nemotron-3-120b-a12b'}
    assert cf['runtime_evidence'] == 'docs/verification/after80-cf-backend-20261026.json'
    assert zai['runtime_evidence'] == 'docs/verification/after80-backend-20261026.json'
    assert zai != cf and cf['base_url'] == zai['base_url'] == 'http://127.0.0.1:8084'
    assert cf['synthetic_initial_trip'] == zai['synthetic_initial_trip']


@pytest.mark.parametrize('profile', ['zai', 'zai47-flash', 'cloudflare-nemotron', 'openrouter-dots'])
def test_exact_profile_metadata_passes_and_dry_run_does_not_read_runtime_or_http(profile, monkeypatch, capsys):
    runner.check_profile(*metadata(profile), profile)
    monkeypatch.setattr(runner, 'load_source', rows)
    forbidden = Mock(side_effect=AssertionError('No HTTP, output, code scan or runtime evidence in dry mode'))
    monkeypatch.setattr(runner, 'HTTP', forbidden)
    monkeypatch.setattr(runner, 'private_json', forbidden)
    monkeypatch.setattr(runner, 'code_manifest', forbidden)
    monkeypatch.setattr(runner, 'check_profile', forbidden)
    assert runner.main(['--llm-profile', profile]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'DRY_RUN_ZERO_REQUESTS' and result['plan']['llm_profile'] == profile
    forbidden.assert_not_called()


@pytest.mark.parametrize('profile', ['cloudflare', 'paid-model', '../runtime.env', 'http://remote.test:8084'])
def test_unlisted_profile_cannot_select_model_endpoint_or_metadata_file(profile, monkeypatch):
    with pytest.raises(ValueError, match='fixed_llm_profile'):
        runner.plan(rows(), llm_profile=profile)
    loader = Mock(side_effect=AssertionError('No data actions before CLI validation'))
    monkeypatch.setattr(runner, 'load_source', loader)
    with pytest.raises(SystemExit):
        runner.main(['--llm-profile', profile])
    loader.assert_not_called()


@pytest.mark.parametrize('field,value', [
    ('runtime_provider', 'zai'), ('runtime_model', '@cf/meta/llama-3.3-70b-instruct-fp8-fast'),
    ('selected_free', False), ('selected_configured', False), ('paid_fallback', True),
    ('other_zai', True), ('other_ollama', True), ('missing_other_false', None), ('other_flag', True),
    ('manager', 'after80_backend.py'), ('url', 'http://127.0.0.1:8083'), ('database', 'otravel'),
    ('loaded', False), ('status', 'STOPPED'), ('calls', 2), ('calls', True), ('local', 1), ('local', False),
    ('fallback', True), ('cf_daily', 40), ('limits', None), ('liteapi_mode', 'production'),
    ('duffel_mode', 'live'), ('serp_free', False), ('serp_source', 'LIVE_BOOKABLE'),
])
def test_cf_preflight_rejects_wrong_or_unconfirmed_or_cross_profile_runtime(field, value):
    health, status, evidence = metadata('cloudflare-nemotron')
    if field == 'runtime_provider': status['_runtime']['llm_provider'] = value
    elif field == 'runtime_model': status['_runtime']['model'] = value
    elif field == 'selected_free': status['cloudflare']['free_tier_confirmed'] = value
    elif field == 'selected_configured': status['cloudflare']['configured'] = value
    elif field == 'paid_fallback': status['cloudflare']['paid_fallback'] = value
    elif field == 'other_zai': status['zai']['configured'] = value
    elif field == 'other_ollama': status['ollama']['configured'] = value
    elif field == 'missing_other_false': del status['zai']
    elif field == 'other_flag': status['zai']['free_tier_confirmed'] = value
    elif field in {'manager', 'url', 'database', 'status', 'limits'}: evidence[field] = value
    elif field == 'loaded': evidence['loaded_manifest']['matches_current'] = value
    elif field == 'calls': evidence['limits']['calls_per_run'] = value
    elif field == 'local': evidence['limits']['local_calls'] = value
    elif field == 'fallback': evidence['limits']['fallback'] = value
    elif field == 'cf_daily': evidence['limits']['cloudflare_daily_engineering_bound'] = value
    elif field == 'liteapi_mode': status['liteapi']['mode'] = value
    elif field == 'duffel_mode': status['duffel']['mode'] = value
    elif field == 'serp_free': status['serpapi']['free_tier_confirmed'] = value
    elif field == 'serp_source': status['serpapi']['source_type'] = value
    with pytest.raises(ValueError):
        runner.check_profile(health, status, evidence, 'cloudflare-nemotron')


def test_default_zai_profile_does_not_implicitly_switch_to_cf():
    with pytest.raises(ValueError, match='profile_mismatch'):
        runner.check_profile(*metadata('cloudflare-nemotron'))


def test_router_fixed_free_profile_plan_and_exact_metadata_without_invented_free_flag():
    frozen = runner.plan(rows(), llm_profile='openrouter-dots')
    assert frozen['runtime_evidence'] == 'docs/verification/after80-router-backend-20261026.json'
    assert frozen['expected_runtime'] == {'llm_provider': 'openrouter', 'model': 'dots-studio/dots-3-note-preview:free'}
    assert frozen['base_url'] == 'http://127.0.0.1:8084'
    runner.check_profile(*metadata('openrouter-dots'), 'openrouter-dots')


@pytest.mark.parametrize('bad', ['paid_model', 'backup', 'cf_key', 'evidence', 'daily2000'])
def test_router_profile_cannot_gain_paid_backup_other_provider_or_larger_shared_pool(bad):
    health, status, evidence = metadata('openrouter-dots')
    if bad == 'paid_model': status['openrouter']['model'] = 'paid/model'
    elif bad == 'backup': status['openrouter']['backup_model'] = 'another:free'
    elif bad == 'cf_key': status['cloudflare']['configured'] = True
    elif bad == 'evidence': evidence['manager'] = 'after80_cf_backend.py'
    elif bad == 'daily2000': evidence['limits']['daily_engineering_bound'] = 2000
    with pytest.raises(ValueError): runner.check_profile(health, status, evidence, 'openrouter-dots')


@pytest.mark.skipif(os.name != 'posix', reason='Private actual resume output is POSIX-only by contract')
@pytest.mark.parametrize('target_profile', ['cloudflare-nemotron', 'openrouter-dots'])
def test_resume_zai_plan_cannot_switch_to_cf_before_any_request_or_case(monkeypatch, tmp_path, target_profile):
    saved = {'source_sha256': runner.SOURCE_SHA, 'plan': runner.plan(rows()), 'sessions': {}}
    vault = tmp_path / 'resume-vault.json'
    vault.write_text(json.dumps(saved)); vault.chmod(0o600)
    monkeypatch.setattr(runner, 'load_source', rows)
    forbidden = Mock(side_effect=AssertionError('Cross-profile resume must stop before any request or case'))
    monkeypatch.setattr(runner, 'HTTP', forbidden)
    monkeypatch.setattr(runner, 'Replay', forbidden)
    with pytest.raises(ValueError, match='resume_source_or_plan_mismatch'):
        runner.main(['--run', '--llm-profile', target_profile, '--resume', str(vault)])
    forbidden.assert_not_called()


@pytest.mark.skipif(os.name != 'posix', reason='Actual preflight output is POSIX-only by contract')
def test_cf_main_reads_only_fixed_cf_evidence_and_binds_profile_to_resume(monkeypatch, tmp_path):
    health, status, evidence = metadata('cloudflare-nemotron')
    path = tmp_path / 'docs/verification/after80-cf-backend-20261026.json'
    path.parent.mkdir(parents=True); path.write_text(json.dumps(evidence))
    monkeypatch.setattr(runner, 'ROOT', tmp_path)
    monkeypatch.setattr(runner, 'load_source', rows)
    gets = []
    class LocalMetadata:
        def get(self, route):
            gets.append(route)
            assert route in {'/health', '/api/v1/providers/status'}
            return health if route == '/health' else status
    monkeypatch.setattr(runner, 'HTTP', LocalMetadata)
    monkeypatch.setattr(runner, 'code_manifest', lambda: {'files': {'otravel/orchestrator.py': 'synthetic-orchestrator'}})
    replay = SimpleNamespace(vault={}, report={}, save=Mock(), stop=threading.Event(), case=Mock(),
        budget=SimpleNamespace(snapshot=lambda: {'models': 0, 'tools': 0, 'held': {}}))
    monkeypatch.setattr(runner, 'Replay', lambda *args, **kwargs: replay)
    assert runner.main(['--run', '--llm-profile', 'cloudflare-nemotron', '--max-dialogues', '1', '--output-root', str(tmp_path / 'private')]) == 0
    assert gets == ['/health', '/api/v1/providers/status']
    assert replay.vault['run_binding']['llm_profile'] == 'cloudflare-nemotron'
    assert replay.vault['run_binding']['runtime_evidence'] == 'docs/verification/after80-cf-backend-20261026.json'
    assert replay.report['runtime_evidence_sha256'] == runner.digest(path.read_bytes())
    replay.case.assert_called_once()
