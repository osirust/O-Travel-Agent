"""GigaChat-only hidden save; synthetic credentials/temp files, no service calls."""

import ast
import base64
import io
import json
import stat
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import configure_gigachat as collector
from scripts import configure_keys as writer

CLIENT = "synthetic-personal-client-not-real"
SECRET = "synthetic-personal-secret-not-real"
KEY = base64.b64encode((CLIENT + ":" + SECRET).encode()).decode()
NEW_KEY = base64.b64encode(b"different-client:different-secret").decode()


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(writer, "PROJECT", tmp_path)
    return tmp_path


def read_values(project):
    return dict(line.split("=", 1) for line in (project / ".env").read_text().splitlines()
        if line and not line.startswith("#"))


def current(project):
    values = {"LLM_PROVIDER": "groq", "GROQ_MODEL": "openai/gpt-oss-120b",
        "GROQ_FREE_TIER_CONFIRMED": "true", "GROQ_API_KEY": "gsk_existing_fixture",
        "MAX_GROQ_LLM_CALLS_PER_DAY": "1000", "POSTGRES_PASSWORD": "unchanged-private-fixture",
        "GROQ_STRUCTURED_TRANSPORT_ENABLED": "true", "GROQ_REASONING_EFFORT": "low",
        "GIGACHAT_AUTH_KEY": KEY, "GIGACHAT_FREEMIUM_CONFIRMED": "true"}
    (project / ".env").write_text("\n".join(k + "=" + v for k, v in values.items()) + "\n")
    return values


def terminal(monkeypatch, *, platform="linux", text=""):
    source = io.StringIO(text)
    source.isatty = lambda: True
    monkeypatch.setattr(collector, "sys", SimpleNamespace(platform=platform, executable="python",
        stdin=source, stderr=io.StringIO()))


@pytest.mark.parametrize("value", [KEY, base64.b64encode(b"client:secret:with:colon").decode()])
def test_personal_basic_authorization_value_is_validated_without_returning_parts(value):
    assert writer.validate_gigachat_auth_key(value) == value
    assert collector.PERSONAL_SCOPE == "GIGACHAT_API_PERS"


@pytest.mark.parametrize("value", [None, 1, "", "not-base64!", "abc", KEY + "\n", " " + KEY,
    base64.b64encode(b"no-colon").decode(), base64.b64encode(b":secret").decode(),
    base64.b64encode(b"client:").decode(), base64.b64encode(b"  :  ").decode(), "a" * 4097])
def test_invalid_or_empty_basic_parts_fail_before_lock_or_write(project, value, capsys):
    before = current(project)
    with pytest.raises(ValueError):
        writer.write_env({"GIGACHAT_AUTH_KEY": value})
    assert read_values(project) == before and not (project / ".env.lock").exists()
    output = capsys.readouterr()
    assert CLIENT not in output.out + output.err and SECRET not in output.out + output.err


def test_new_key_resets_its_confirmation_and_preserves_all_current_groq_and_database_values(project, capsys):
    before = current(project)
    payload = {"GIGACHAT_AUTH_KEY": NEW_KEY}
    collector.save_payload(payload)
    assert payload == {"GIGACHAT_AUTH_KEY": NEW_KEY}
    assert read_values(project) == {**before, "GIGACHAT_AUTH_KEY": NEW_KEY, "GIGACHAT_FREEMIUM_CONFIRMED": "false"}
    assert stat.S_IMODE((project / ".env").stat().st_mode) == 0o600
    assert stat.S_IMODE((project / ".env.lock").stat().st_mode) == 0o600
    text = capsys.readouterr().out
    assert NEW_KEY not in text and KEY not in text and CLIENT not in text and SECRET not in text


def test_save_only_on_empty_master_does_not_initialize_any_provider_model_or_database(project):
    collector.save_payload({"GIGACHAT_AUTH_KEY": KEY, "GIGACHAT_FREEMIUM_CONFIRMED": "true"})
    assert read_values(project) == {"GIGACHAT_AUTH_KEY": KEY, "GIGACHAT_FREEMIUM_CONFIRMED": "true"}
    assert "GIGACHAT_AUTH_KEY" not in writer.KEYS
    assert "GIGACHAT_FREEMIUM_CONFIRMED" not in writer.COMPARISON_CREDENTIALS
    assert len(writer.COMPARISON_CREDENTIALS) == 4


@pytest.mark.parametrize("flag", [True, 1, None, {}, "TRUE", "yes", "1"])
def test_noncanonical_confirmation_rejected_without_touching_previous_config(project, flag):
    before = current(project)
    with pytest.raises(ValueError):
        collector.save_payload({"GIGACHAT_AUTH_KEY": NEW_KEY, "GIGACHAT_FREEMIUM_CONFIRMED": flag})
    assert read_values(project) == before and not (project / ".env.lock").exists()


def test_atomic_replace_failure_leaves_existing_values_and_removes_private_temp(project, monkeypatch):
    before = current(project)
    def fail(temp, path):
        assert Path(path) == project / ".env"
        assert stat.S_IMODE(Path(temp).stat().st_mode) == 0o600
        raise OSError("synthetic failed replace")
    monkeypatch.setattr(writer.os, "replace", fail)
    with pytest.raises(OSError):
        collector.save_payload({"GIGACHAT_AUTH_KEY": NEW_KEY})
    assert read_values(project) == before
    assert {x.name for x in project.iterdir()} == {".env", ".env.lock"}


def test_parallel_giga_and_other_key_updates_use_same_existing_atomic_lock(project):
    before = current(project)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(collector.save_payload, {"GIGACHAT_AUTH_KEY": NEW_KEY}),
            pool.submit(writer.write_env, {"SERPAPI_API_KEY": "synthetic-concurrent-serp"})]
        for job in jobs:
            job.result()
    saved = read_values(project)
    assert saved["GIGACHAT_AUTH_KEY"] == NEW_KEY and saved["GIGACHAT_FREEMIUM_CONFIRMED"] == "false"
    assert saved["SERPAPI_API_KEY"] == "synthetic-concurrent-serp"
    assert all(saved[k] == v for k, v in before.items() if not k.startswith("GIGACHAT_"))


@pytest.mark.parametrize("answer,flag", [("yes", "true"), (" YES ", "true"), ("no", "false"), ("", "false"), ("maybe", "false")])
def test_hidden_collector_asks_only_one_key_and_one_free_package_confirmation(answer, flag, monkeypatch, capsys):
    hidden, questions = [], []
    monkeypatch.setattr(collector.getpass, "getpass", lambda prompt: hidden.append(prompt) or KEY)
    monkeypatch.setattr("builtins.input", lambda prompt: questions.append(prompt) or answer)
    payload = collector.collect_payload()
    assert payload == {"GIGACHAT_AUTH_KEY": KEY, "GIGACHAT_FREEMIUM_CONFIRMED": flag}
    assert len(hidden) == len(questions) == 1
    assert "Authorization Key" in hidden[0] and "бесплатный пакет" in questions[0]
    assert KEY not in "".join(hidden + questions) + capsys.readouterr().out


def test_enter_skips_question_writer_and_wsl_without_modifying_master(project, monkeypatch):
    before = current(project)
    terminal(monkeypatch)
    monkeypatch.setattr(collector.getpass, "getpass", lambda _: "")
    forbidden = Mock(side_effect=AssertionError("No followup/write/child after Enter"))
    monkeypatch.setattr("builtins.input", forbidden)
    monkeypatch.setattr(collector, "save_payload", forbidden)
    monkeypatch.setattr(collector.subprocess, "run", forbidden)
    assert collector.main([]) == 0 and read_values(project) == before
    forbidden.assert_not_called()


def test_getpass_echo_fallback_is_interrupted_without_printing_warning_value(monkeypatch, capsys):
    terminal(monkeypatch)
    def unsafe(_prompt):
        warnings.warn(KEY, getpass_warning())
        raise AssertionError("Visible fallback must be unreachable")
    monkeypatch.setattr(collector.getpass, "getpass", unsafe)
    forbidden = Mock(side_effect=AssertionError("No save or confirmation after unsafe input"))
    monkeypatch.setattr("builtins.input", forbidden)
    monkeypatch.setattr(collector, "save_payload", forbidden)
    assert collector.main([]) == 2
    assert KEY not in capsys.readouterr().out + collector.sys.stderr.getvalue()
    forbidden.assert_not_called()


def getpass_warning():
    return collector.getpass.GetPassWarning


def test_windows_transfers_only_giga_pair_to_fixed_wsl_via_stdin_not_argv_or_env(monkeypatch, capsys):
    terminal(monkeypatch, platform="win32")
    monkeypatch.setattr(collector, "Path", lambda _: SimpleNamespace(resolve=lambda: "E:\\work\\aegml\\scripts\\configure_gigachat.py"))
    monkeypatch.setattr(collector, "collect_payload", lambda: {"GIGACHAT_AUTH_KEY": KEY, "GIGACHAT_FREEMIUM_CONFIRMED": "true"})
    child = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(collector.subprocess, "run", child)
    assert collector.main([]) == 0
    argv, = child.call_args.args
    assert argv == ["wsl.exe", "-d", "AegML-Hackathon", "-u", "aegml", "--", "python3",
        "/mnt/e/work/aegml/scripts/configure_gigachat.py", "--write-stdin"]
    assert json.loads(child.call_args.kwargs["input"]) == {"GIGACHAT_AUTH_KEY": KEY, "GIGACHAT_FREEMIUM_CONFIRMED": "true"}
    assert "env" not in child.call_args.kwargs and "shell" not in child.call_args.kwargs
    assert KEY not in " ".join(argv) + capsys.readouterr().out


@pytest.mark.parametrize("text", ["{not-json-" + KEY, json.dumps({"GIGACHAT_AUTH_KEY": KEY, "LLM_PROVIDER": "gigachat"}),
    json.dumps({"GIGACHAT_FREEMIUM_CONFIRMED": "true"}), "x" * 8193])
def test_internal_stdin_rejects_invalid_or_overbroad_payload_without_secret_output(project, monkeypatch, capsys, text):
    before = current(project)
    terminal(monkeypatch, text=text)
    assert collector.main(["--write-stdin"]) == 2
    assert read_values(project) == before
    assert KEY not in capsys.readouterr().out + collector.sys.stderr.getvalue()


def test_noninteractive_and_unknown_argv_refuse_without_reading_or_echoing_a_key(monkeypatch, capsys):
    monkeypatch.setattr(collector, "sys", SimpleNamespace(platform="linux", stdin=SimpleNamespace(isatty=lambda: False), stderr=io.StringIO()))
    forbidden = Mock(side_effect=AssertionError("No key input/write/child"))
    monkeypatch.setattr(collector, "collect_payload", forbidden)
    monkeypatch.setattr(collector, "save_payload", forbidden)
    monkeypatch.setattr(collector.subprocess, "run", forbidden)
    assert collector.main([]) == 2
    assert collector.main([KEY]) == 2
    assert KEY not in capsys.readouterr().out + collector.sys.stderr.getvalue()
    forbidden.assert_not_called()


def test_status_reports_giga_presence_only_without_secret_parts_or_other_provider(project, monkeypatch, capsys):
    current(project)
    terminal(monkeypatch)
    monkeypatch.setattr(collector, "collect_payload", Mock(side_effect=AssertionError("No prompt for status")))
    assert collector.main(["--status"]) == 0
    text = capsys.readouterr().out
    assert json.loads(text) == {"configured": True, "freemium_confirmed": True}
    assert all(x not in text for x in (KEY, CLIENT, SECRET, "groq", "gsk_", "openai/", "password"))


def test_standalone_module_has_no_auth_network_billing_or_ssl_client_imports():
    tree = ast.parse(Path(collector.__file__).read_text())
    modules = {name.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for name in node.names}
    assert not {"httpx", "requests", "socket", "ssl", "urllib"}.intersection(modules)
    assert writer.PROJECT == Path("/home/aegml/projects/otravel")
