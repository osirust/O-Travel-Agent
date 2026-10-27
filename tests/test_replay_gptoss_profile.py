"""Fixed GPT-OSS selection cannot borrow Qwen configuration or a replay vault."""
import json
from unittest.mock import Mock

import pytest
from test_prefrontend_dialogues import runner as dialogue_runner
from test_replay_after80_profiles import metadata, rows, runner


def gptoss_metadata():
    health, status, evidence = metadata('groq-gptoss120')
    evidence['limits']['groq_daily_engineering_bound'] = 1000
    status['groq'].update(structured_transport=True, transport_version=2, reasoning_effort='low', include_reasoning=False)
    return health, status, evidence


def test_fixed_120_model_uses_separate_evidence_and_shared_groq_pool_without_a_vendor_quota_claim():
    frozen = runner.plan(rows(), llm_profile='groq-gptoss120', message_interval_seconds=60)
    assert frozen['expected_runtime'] == {'llm_provider': 'groq', 'model': 'openai/gpt-oss-120b'}
    assert frozen['runtime_evidence'] == 'docs/verification/after80-gptoss-backend-20261027.json'
    assert frozen['provider_remaining'] == 'UNKNOWN' and frozen['native_budget_shared'] is True
    runner.check_profile(*gptoss_metadata(), 'groq-gptoss120')
    assert dialogue_runner.ModelPacer(60, llm_profile='groq-gptoss120').seconds == 60


@pytest.mark.parametrize('fault', ['qwen_model', 'qwen_manager', 'legacy_wire', 'none_effort',
    'visible_reasoning', 'missing_reasoning', 'other_pool', 'paid', 'cap_reset'])
def test_preflight_does_not_accept_a_stale_qwen_or_unqualified_120_profile(fault):
    health, status, evidence = gptoss_metadata()
    if fault == 'qwen_model':
        status['_runtime']['model'] = status['groq']['model'] = 'qwen/qwen3.8-27b'
    elif fault == 'qwen_manager':
        evidence['manager'] = 'after80_groq_backend.py'
    elif fault == 'legacy_wire':
        status['groq'].pop('structured_transport')
    elif fault == 'none_effort':
        status['groq']['reasoning_effort'] = 'none'
    elif fault == 'visible_reasoning':
        status['groq']['include_reasoning'] = True
    elif fault == 'missing_reasoning':
        status['groq'].pop('include_reasoning')
    elif fault == 'other_pool':
        status['openrouter']['configured'] = True
    elif fault == 'paid':
        status['groq']['paid_fallback'] = True
    elif fault == 'cap_reset':
        evidence['limits']['groq_daily_engineering_bound'] = 40
    with pytest.raises(ValueError):
        runner.check_profile(health, status, evidence, 'groq-gptoss120')


def test_gptoss_planning_is_zero_http_and_does_not_change_the_existing_profile(monkeypatch, capsys):
    qwen = dict(runner.LLM_PROFILES['groq-qwen'])
    monkeypatch.setattr(runner, 'load_source', rows)
    forbidden = Mock(side_effect=AssertionError('Dry planning has no runtime access'))
    for name in ('HTTP', 'private_json', 'check_profile', 'PostPacer'):
        monkeypatch.setattr(runner, name, forbidden)
    assert runner.main(['--llm-profile', 'groq-gptoss120', '--message-interval-seconds', '60']) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'DRY_RUN_ZERO_REQUESTS'
    assert runner.LLM_PROFILES['groq-qwen'] == qwen
    forbidden.assert_not_called()
