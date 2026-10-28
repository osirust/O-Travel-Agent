"""Explicit travel-only delivery must never silently export an LLM key."""
import importlib.util
from pathlib import Path

import pytest

path = Path(__file__).resolve().parents[1] / 'scripts/encrypt_jury_secrets.py'
spec = importlib.util.spec_from_file_location('jury_secret_encryption', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_missing_llm_key_is_rejected_by_default_but_explicit_travel_delivery_is_labelled():
    values = {'LLM_PROVIDER': 'zai', 'ZAI_MODEL': 'glm-4.5-flash', 'ZAI_FREE_TIER_CONFIRMED': 'true',
              'ZAI_API_KEY': '', 'LITEAPI_API_KEY': 'sand_synthetic-only'}
    with pytest.raises(module.SubmissionError, match='JURY_FREE_LLM_CONFIGURATION_REQUIRED'):
        module.selected_jury_llm(values)
    assert module.external_jury_llm(values) == {
        'llm_key_provided': False, 'recipient_llm_configuration_required': True,
        'free_dialogue_ready_without_recipient_key': False}


@pytest.mark.parametrize('key', [
    'LLM_API_KEY', 'GROQ_API_KEY', 'ZAI_API_KEY', 'CLOUDFLARE_API_TOKEN',
    'GIGACHAT_AUTH_KEY', 'OPENROUTER_API_KEY', 'GEMINI_API_KEY', 'ANTHROPIC_API_KEY',
])
def test_external_key_mode_rejects_each_nonempty_llm_credential(key):
    with pytest.raises(module.SubmissionError, match='EXTERNAL_LLM_ENVELOPE_MUST_NOT_CONTAIN_LLM_KEYS'):
        module.external_jury_llm({'LITEAPI_API_KEY': 'sand_synthetic-only', key: 'synthetic-not-exportable'})


def test_external_mode_does_not_confuse_token_budget_with_a_secret():
    result = module.external_jury_llm({'SERPAPI_API_KEY': 'synthetic-only', 'LLM_API_KEY': '',
                                      'LLM_MAX_OUTPUT_TOKENS': '2048'})
    assert result['recipient_llm_configuration_required']


def test_empty_envelope_does_not_claim_travel_api_access():
    with pytest.raises(module.SubmissionError, match='TRAVEL_ONLY_ENVELOPE_REQUIRES_TRAVEL_KEY'):
        module.external_jury_llm({'LLM_API_KEY': ''})
