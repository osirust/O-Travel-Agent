"""Hidden Groq setup using temporary files/mocked prompts only; no real .env."""

import io
import json
import stat
from types import SimpleNamespace
import warnings

import pytest

from scripts import configure_keys as setup


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "PROJECT", tmp_path)
    return tmp_path


def values(project):
    return dict(line.split("=", 1) for line in (project / ".env").read_text(encoding="utf-8").splitlines()
                if line and not line.startswith("#"))


@pytest.mark.parametrize("answer,confirmed", [("yes", "true"), (" YES ", "true"), ("no", "false"), ("", "false"), ("maybe", "false")])
def test_groq_hidden_key_requires_explicit_free_without_billing_confirmation(answer, confirmed, monkeypatch, capsys):
    seen = []
    def hidden(prompt):
        seen.append(prompt)
        return "gsk_offline_fixture_secret" if prompt.startswith("Groq Free") else ""
    def confirm(prompt):
        assert "Free" in prompt and "БЕЗ billing" in prompt and "платного плана" in prompt
        return answer
    monkeypatch.setattr(setup.getpass, "getpass", hidden)
    monkeypatch.setattr("builtins.input", confirm)
    payload = setup.collect_payload()
    assert payload == {"GROQ_API_KEY": "gsk_offline_fixture_secret", "GROQ_FREE_TIER_CONFIRMED": confirmed,
                       "GROQ_MODEL": "qwen/qwen3.8-27b"}
    assert all("gsk_offline_fixture_secret" not in prompt for prompt in seen)
    assert "gsk_offline_fixture_secret" not in capsys.readouterr().out


def test_enter_skips_all_updates_and_does_not_ask_to_reconfirm_existing_keys(monkeypatch):
    monkeypatch.setattr(setup.getpass, "getpass", lambda prompt: "")
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("Skipped key must not ask confirmation"))
    assert setup.collect_payload() == {}


def test_skip_preserves_groq_other_keys_flags_and_current_primary(project, capsys):
    (project / ".env").write_text("LLM_PROVIDER=ollama\nGROQ_API_KEY=gsk_old_fixture\nGROQ_MODEL=qwen/qwen3.8-27b\nGROQ_FREE_TIER_CONFIRMED=true\nOPENROUTER_API_KEY=sk-old-fixture\nPOSTGRES_PASSWORD=existing-fixture\n", encoding="utf-8")
    setup.write_env({})
    saved = values(project)
    assert saved["GROQ_API_KEY"] == "gsk_old_fixture" and saved["GROQ_FREE_TIER_CONFIRMED"] == "true"
    assert saved["OPENROUTER_API_KEY"] == "sk-old-fixture" and saved["POSTGRES_PASSWORD"] == "existing-fixture"
    assert saved["LLM_PROVIDER"] == "ollama"
    output = capsys.readouterr().out
    assert not any(secret in output for secret in ("gsk_old_fixture", "sk-old-fixture", "existing-fixture"))
    assert stat.S_IMODE((project / ".env").stat().st_mode) == 0o600


def test_new_groq_key_defaults_free_unconfirmed_without_changing_primary(project, capsys):
    (project / ".env").write_text("LLM_PROVIDER=gemini\nGROQ_API_KEY=gsk_old_fixture\nGROQ_FREE_TIER_CONFIRMED=true\n", encoding="utf-8")
    payload = {"GROQ_API_KEY": "gsk_new_fixture"}
    setup.write_env(payload)
    saved = values(project)
    assert saved["GROQ_FREE_TIER_CONFIRMED"] == "false" and saved["GROQ_MODEL"] == setup.GROQ_MODEL
    assert saved["LLM_PROVIDER"] == "gemini" and saved["GROQ_API_KEY"] == "gsk_new_fixture"
    assert payload == {"GROQ_API_KEY": "gsk_new_fixture"}
    assert "gsk_new_fixture" not in capsys.readouterr().out


def test_approved_groq_model_and_free_flag_can_be_written(project):
    setup.write_env({"GROQ_API_KEY": "gsk_fixture", "GROQ_MODEL": "qwen/qwen3.8-27b", "GROQ_FREE_TIER_CONFIRMED": "true"})
    assert values(project)["GROQ_FREE_TIER_CONFIRMED"] == "true"


@pytest.mark.parametrize("model", ["qwen/qwen3-32b", "qwen/qwen3.8-27b:paid", "other/model", "", "QWEN/qwen3.8-27b"])
def test_other_or_ambiguous_groq_model_is_refused_before_writing(project, model):
    with pytest.raises(ValueError):
        setup.write_env({"GROQ_API_KEY": "gsk_fixture", "GROQ_MODEL": model})
    assert not (project / ".env").exists()


@pytest.mark.parametrize("flag", ["yes", "1", "TRUE", "Free", True])
def test_noncanonical_free_flag_is_refused_before_writing(project, flag):
    with pytest.raises(ValueError):
        setup.write_env({"GROQ_FREE_TIER_CONFIRMED": flag})
    assert not (project / ".env").exists()


def test_status_reports_only_booleans_without_values_or_model(project, capsys):
    (project / ".env").write_text("GROQ_API_KEY=gsk_private_fixture\nGROQ_MODEL=qwen/qwen3.8-27b\nGROQ_FREE_TIER_CONFIRMED=true\nOPENROUTER_API_KEY=sk_private_fixture\n", encoding="utf-8")
    setup.status()
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["configured"]["GROQ_API_KEY"] is True and result["groq_free_confirmed"] is True
    assert all(isinstance(value, bool) for value in result["configured"].values())
    assert all(isinstance(value, bool) for name, value in result.items() if name != "configured")
    assert not any(value in output for value in ("gsk_private_fixture", "sk_private_fixture", "qwen/qwen3.8-27b"))


def test_getpass_cannot_fall_back_to_visible_key_entry(monkeypatch):
    def unsafe(prompt):
        warnings.warn("Echo unavailable", setup.getpass.GetPassWarning)
        pytest.fail("Visible fallback must be interrupted")
    monkeypatch.setattr(setup.getpass, "getpass", unsafe)
    with pytest.raises(setup.getpass.GetPassWarning):
        setup.collect_payload()


def test_windows_child_receives_secret_via_stdin_only_and_no_auto_primary(monkeypatch, capsys):
    key = "gsk_stdin_only_fixture"
    namespace = SimpleNamespace(platform="win32", executable="python", stdin=SimpleNamespace(isatty=lambda: True), stderr=io.StringIO())
    monkeypatch.setattr(setup, "sys", namespace)
    monkeypatch.setattr(setup, "collect_payload", lambda: {"GROQ_API_KEY": key, "GROQ_MODEL": setup.GROQ_MODEL, "GROQ_FREE_TIER_CONFIRMED": "true", "OPENROUTER_API_KEY": "sk_fixture"})
    sent = []
    def run(argv, **kwargs):
        sent.append((argv, kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(setup.subprocess, "run", run)
    assert setup.main([]) == 0
    argv, kwargs = sent[0]
    assert argv[-1] == "--write-stdin" and key not in " ".join(argv)
    payload = json.loads(kwargs["input"])
    assert payload["GROQ_API_KEY"] == key and "LLM_PROVIDER" not in payload
    assert key not in capsys.readouterr().out


def test_noninteractive_main_refuses_key_prompt_without_reading_env(monkeypatch):
    monkeypatch.setattr(setup, "sys", SimpleNamespace(platform="linux", executable="python", stdin=SimpleNamespace(isatty=lambda: False)))
    monkeypatch.setattr(setup, "collect_payload", lambda: pytest.fail("No prompt without terminal"))
    monkeypatch.setattr(setup, "write_env", lambda payload: pytest.fail("No write without terminal"))
    assert setup.main([]) == 2
