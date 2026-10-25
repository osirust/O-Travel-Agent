"""Comparison credential storage uses temporary files and mocked prompts only."""

import io
import json
import stat
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from scripts import configure_keys as setup


ACCOUNT_ID = "0123456789abcdef0123456789ABCDEF"
CREDENTIALS = {
    "ZAI_API_KEY": "synthetic.zai-secret",
    "COHERE_API_KEY": "synthetic-cohere-secret",
    "CLOUDFLARE_API_TOKEN": "synthetic-cf-secret",
    "CLOUDFLARE_ACCOUNT_ID": ACCOUNT_ID,
    "NVIDIA_API_KEY": "synthetic-nvidia-secret",
}


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "PROJECT", tmp_path)
    (tmp_path / ".env").write_text(
        "LLM_PROVIDER=ollama\nPOSTGRES_PASSWORD=existing-synthetic-password\n"
        "GROQ_API_KEY=gsk_existing_synthetic\nGROQ_FREE_TIER_CONFIRMED=true\n",
        encoding="utf-8",
    )
    return tmp_path


def values(project):
    return dict(line.split("=", 1) for line in (project / ".env").read_text(encoding="utf-8").splitlines()
                if line and not line.startswith("#"))


@pytest.mark.parametrize("answer,confirmed", [("yes", "true"), (" YES ", "true"), ("no", "false"), ("", "false"), ("maybe", "false")])
def test_comparison_hidden_entry_requires_explicit_confirmation_without_auto_provider(answer, confirmed, monkeypatch, capsys):
    prompts = []
    def hidden(prompt):
        prompts.append(prompt)
        for name, label in setup.KEYS.items():
            if prompt == label + ": ":
                return CREDENTIALS.get(name, "")
        pytest.fail("Unknown prompt")
    confirmations = []
    def confirm(prompt):
        confirmations.append(prompt)
        return answer
    monkeypatch.setattr(setup.getpass, "getpass", hidden)
    monkeypatch.setattr("builtins.input", confirm)
    payload = setup.collect_payload()
    assert payload == {**CREDENTIALS, **dict.fromkeys(setup.COMPARISON_CREDENTIALS, confirmed)}
    assert len(confirmations) == 4  # Cloudflare token and ID share one confirmation.
    assert "LLM_PROVIDER" not in payload and not any(name.endswith("_MODEL") for name in payload)
    assert all(secret not in "".join(prompts + confirmations) for secret in CREDENTIALS.values())
    output = capsys.readouterr()
    assert all(secret not in output.out + output.err for secret in CREDENTIALS.values())


def test_all_comparison_values_skip_without_confirmation(monkeypatch):
    monkeypatch.setattr(setup.getpass, "getpass", lambda prompt: "")
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("Skipped credential must not prompt"))
    assert setup.collect_payload() == {}


@pytest.mark.parametrize("credential", CREDENTIALS)
def test_new_credential_revokes_previous_confirmation_and_preserves_primary(project, credential, capsys, monkeypatch):
    path = project / ".env"
    with path.open("a", encoding="utf-8") as handle:
        for name, value in CREDENTIALS.items():
            handle.write(f"{name}={value}\n")
        for flag in setup.COMPARISON_CREDENTIALS:
            handle.write(f"{flag}=true\n")
    monkeypatch.setattr(setup.subprocess, "run", lambda *args, **kwargs: pytest.fail("Storage must not call services"))
    new_value = "fedcba9876543210fedcba9876543210" if credential.endswith("ACCOUNT_ID") else "new-synthetic-secret"
    payload = {credential: new_value}
    setup.write_env(payload)
    saved = values(project)
    assert payload == {credential: new_value}
    assert saved[credential] == new_value and saved["LLM_PROVIDER"] == "ollama"
    assert saved["POSTGRES_PASSWORD"] == "existing-synthetic-password"
    assert saved["GROQ_FREE_TIER_CONFIRMED"] == "true"
    for flag, names in setup.COMPARISON_CREDENTIALS.items():
        assert saved[flag] == ("false" if credential in names else "true")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    output = capsys.readouterr()
    assert new_value not in output.out + output.err
    assert stat.S_IMODE((project / ".env.lock").stat().st_mode) == 0o600


def test_comparison_skip_preserves_existing_values_and_flags(project):
    setup.write_env({**CREDENTIALS, **dict.fromkeys(setup.COMPARISON_CREDENTIALS, "true")})
    before = values(project)
    setup.write_env({})
    assert values(project) == before


@pytest.mark.parametrize("account_id", ["", "a" * 31, "a" * 33, "g" * 32, "a" * 31 + "\n", "a" * 31 + "-", 7, None])
def test_cloudflare_account_id_rejected_before_any_write(project, account_id):
    before = (project / ".env").read_bytes()
    with pytest.raises(ValueError):
        setup.write_env({"CLOUDFLARE_ACCOUNT_ID": account_id})
    assert (project / ".env").read_bytes() == before
    assert not (project / ".env.lock").exists()


@pytest.mark.parametrize("flag", setup.COMPARISON_CREDENTIALS)
@pytest.mark.parametrize("value", ["yes", "TRUE", "1", True, 1, None])
def test_comparison_flags_strictly_boolean_strings(project, flag, value):
    before = (project / ".env").read_bytes()
    with pytest.raises(ValueError):
        setup.write_env({flag: value})
    assert (project / ".env").read_bytes() == before


@pytest.mark.parametrize("credential", [name for name in CREDENTIALS if name != "CLOUDFLARE_ACCOUNT_ID"])
@pytest.mark.parametrize("value", ["", "bad value", "bad\nvalue", "x" * 4097])
def test_comparison_credentials_cannot_inject_env_lines(project, credential, value):
    before = (project / ".env").read_bytes()
    with pytest.raises(ValueError):
        setup.write_env({credential: value})
    assert (project / ".env").read_bytes() == before


def test_comparison_status_is_boolean_only(project, capsys):
    setup.write_env({**CREDENTIALS, **dict.fromkeys(setup.COMPARISON_CREDENTIALS, "true")})
    capsys.readouterr()
    setup.status()
    output = capsys.readouterr().out
    status = json.loads(output)
    assert all(status["configured"][name] is True for name in CREDENTIALS)
    assert all(isinstance(value, bool) for value in status["configured"].values())
    assert all(isinstance(value, bool) for name, value in status.items() if name != "configured")
    assert all(status[flag.lower()] is True for flag in setup.COMPARISON_CREDENTIALS)
    assert all(secret not in output for secret in CREDENTIALS.values())


def test_windows_comparison_secrets_travel_only_in_stdin(monkeypatch, capsys):
    payload = {**CREDENTIALS, **dict.fromkeys(setup.COMPARISON_CREDENTIALS, "false")}
    namespace = SimpleNamespace(platform="win32", executable="python", stdin=SimpleNamespace(isatty=lambda: True), stderr=io.StringIO())
    monkeypatch.setattr(setup, "sys", namespace)
    monkeypatch.setattr(setup, "collect_payload", lambda: payload)
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(setup.subprocess, "run", run)
    assert setup.main([]) == 0
    argv, kwargs = calls[0]
    assert argv[-1] == "--write-stdin" and json.loads(kwargs["input"]) == payload
    assert "LLM_PROVIDER" not in payload
    output = capsys.readouterr()
    assert all(secret not in " ".join(argv) + output.out + output.err for secret in CREDENTIALS.values())


def test_stdin_validation_error_does_not_print_submitted_secret(project, monkeypatch, capsys):
    secret = "synthetic-rejected-secret"
    monkeypatch.setattr(setup.sys, "stdin", io.StringIO(json.dumps({"CLOUDFLARE_ACCOUNT_ID": secret}) + "\n"))
    assert setup.main(["--write-stdin"]) == 2
    output = capsys.readouterr()
    assert secret not in output.out + output.err
    assert "Ключи не выводятся" in output.err


def test_parallel_comparison_updates_keep_all_credentials(project):
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(lambda item: setup.write_env(dict([item])), CREDENTIALS.items()))
    saved = values(project)
    assert all(saved[name] == value for name, value in CREDENTIALS.items())
    assert all(saved[flag] == "false" for flag in setup.COMPARISON_CREDENTIALS)
    assert saved["LLM_PROVIDER"] == "ollama"
