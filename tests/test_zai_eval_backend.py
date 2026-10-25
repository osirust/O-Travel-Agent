"""Manager-only tests: synthetic env/metadata; no Docker, DB, process or HTTP."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("zai_manager_test", ROOT / "scripts/zai_eval_backend.py")
manager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manager)


@pytest.fixture
def engine():
    return manager.new_engine()


class IdentityFile:
    def __init__(self, data):
        self.data = data

    def is_file(self):
        return self.data is not None

    def stat(self):
        return SimpleNamespace(st_uid=0, st_mode=0o600)

    def read_text(self):
        return json.dumps(self.data)


def identity(engine):
    return {"pid": 24680, "uid": 1000, "start_ticks": 12345, "argv": deepcopy(engine.ARGV),
            "cwd": str(manager.SOURCE), "executable": str(engine.PYTHON.resolve())}


def owned(engine, monkeypatch):
    current = identity(engine)
    saved = {**deepcopy(current), "created_by": manager.CREATOR, "database": manager.DATABASE, "port": manager.PORT}
    engine.IDENTITY = IdentityFile(saved)
    monkeypatch.setattr(engine, "process_identity", lambda _: deepcopy(current))
    return saved, current


def test_fresh_engine_globals_never_reconfigure_old_8083_manager():
    original = (ROOT / "scripts/local_backend.py").read_bytes()
    first, second = manager.new_engine(), manager.new_engine()
    assert first is not second and first.SAFE_CONTEXT is not second.SAFE_CONTEXT
    assert first.SOURCE == Path("/home/aegml/projects/otravel-zai-eval")
    assert first.DATABASE == "otravel_zai_eval" and first.PORT == 8084
    assert "local-backend-8083" not in str(first.RUNTIME)
    assert first.ARGV[-3:] == ["--port", "8084", "--no-access-log"]
    first.SAFE_CONTEXT["test"] = True
    first.SOURCE = Path("/test-only")
    assert second.SOURCE == manager.SOURCE and second.SAFE_CONTEXT == {}
    assert (ROOT / "scripts/local_backend.py").read_bytes() == original


def test_child_env_does_not_inherit_other_keys_or_old_database(engine, monkeypatch):
    for name in engine.KEYS:
        monkeypatch.setenv(name, "do-not-inherit")
    monkeypatch.setenv("DATABASE_URL", "postgresql://forbidden-old-database")
    database = SimpleNamespace(password="test:#@pass", ip="172.18.0.2")
    env = manager.environment(engine, database, "synthetic-zai-key")
    assert env["LLM_PROVIDER"] == "zai" and env["ZAI_MODEL"] == "glm-4.7-flash"
    assert env["ZAI_API_KEY"] == "synthetic-zai-key" and env["ZAI_FREE_TIER_CONFIRMED"] == "true"
    assert env["DATABASE_NAME"] == "otravel_zai_eval" and env["DATABASE_URL"].endswith("/otravel_zai_eval")
    assert "test%3A%23%40pass" in env["DATABASE_URL"]
    assert env["PYTHONPATH"] == str(manager.SOURCE)
    assert env["MAX_LLM_CALLS_PER_DAY"] == "2000" and env["MAX_LLM_CALLS_PER_RUN"] == "1"
    assert env["MAX_LOCAL_LLM_CALLS_PER_DAY"] == "0" and env["OPENROUTER_BACKUP_MODEL"] == ""
    assert all(key not in env for key in engine.KEYS if key != "ZAI_API_KEY")
    assert "do-not-inherit" not in json.dumps(env)


@pytest.mark.parametrize("key", [None, "", "secret with space", "secret\nnewline"])
def test_invalid_key_is_rejected_without_reading_a_file(engine, key):
    with pytest.raises(engine.Refusal):
        manager.environment(engine, SimpleNamespace(password="fake", ip="172.18.0.2"), key)


def test_valid_new_identity_is_recognized_without_live_process_lookup(engine, monkeypatch):
    _, current = owned(engine, monkeypatch)
    assert manager.recorded_process(engine, SimpleNamespace(pw_uid=1000)) == current


@pytest.mark.parametrize("field,value", [
    ("created_by", "local_backend.py"), ("port", 8083), ("database", "otravel_local_eval"),
    ("database", "otravel"), ("cwd", "/home/aegml/projects/otravel"), ("uid", 0),
])
def test_old_manager_or_foreign_identity_is_rejected_before_signal(engine, monkeypatch, field, value):
    saved, _ = owned(engine, monkeypatch)
    saved[field] = value
    signal_guard = Mock(side_effect=AssertionError("No signal is allowed"))
    monkeypatch.setattr(engine, "verified_pidfd", signal_guard)
    with pytest.raises(engine.Refusal):
        engine.stop(SimpleNamespace(pw_uid=1000))
    signal_guard.assert_not_called()


@pytest.mark.parametrize("field,value", [("pid", 99999), ("start_ticks", 99999), ("uid", 0), ("argv", ["foreign"]), ("cwd", "/foreign")])
def test_pid_reuse_or_changed_process_never_becomes_owned(engine, monkeypatch, field, value):
    _, current = owned(engine, monkeypatch)
    current[field] = value
    with pytest.raises(engine.Refusal):
        manager.recorded_process(engine, SimpleNamespace(pw_uid=1000))


def test_busy_unowned_8084_is_not_stopped_or_replaced(engine, monkeypatch):
    engine.IDENTITY = IdentityFile(None)
    monkeypatch.setattr(engine, "listeners", lambda: [{"address": "127.0.0.1:8084", "pids": [99999]}])
    database = Mock(side_effect=AssertionError("DB creation must not occur"))
    monkeypatch.setattr(engine, "Database", database)
    with pytest.raises(engine.Refusal, match="occupied"):
        manager.start(engine, SimpleNamespace(pw_uid=1000))
    database.assert_not_called()
    with pytest.raises(engine.Refusal, match="unowned"):
        engine.stop(SimpleNamespace(pw_uid=1000))


def reusable(engine, monkeypatch):
    saved, current = owned(engine, monkeypatch)
    manifest = {"format": engine.MANIFEST_FORMAT, "files": [{"path": "otravel/api.py", "sha256": "unit"}]}
    saved["source_manifest"] = deepcopy(manifest)
    monkeypatch.setattr(engine, "source_manifest", lambda: deepcopy(manifest))
    monkeypatch.setattr(engine, "require_owned_listener", lambda _: None)
    monkeypatch.setattr(manager, "read_zai_key", Mock(side_effect=AssertionError("No key file reads on reuse")))
    monkeypatch.setattr(engine, "Database", Mock(side_effect=AssertionError("No DB setup on reuse")))
    return saved, current


def test_reuse_refuses_unhealthy_owned_backend_without_restart(engine, monkeypatch):
    reusable(engine, monkeypatch)
    monkeypatch.setattr(engine, "http", lambda _: (503, {"error": {"code": "database_unavailable"}}))
    with pytest.raises(engine.Refusal, match="not_ready"):
        manager.start(engine, SimpleNamespace(pw_uid=1000))


def test_reuse_refuses_changed_source_without_touching_process(engine, monkeypatch):
    saved, _ = reusable(engine, monkeypatch)
    saved["source_manifest"]["files"][0]["sha256"] = "old"
    monkeypatch.setattr(engine, "http", Mock(side_effect=AssertionError("No HTTP after changed-source guard")))
    with pytest.raises(engine.Refusal, match="source_changed"):
        manager.start(engine, SimpleNamespace(pw_uid=1000))


@pytest.mark.parametrize("bad", ["model", "provider", "confirmation", "other_key"])
def test_readiness_requires_zai_free_flash_and_no_other_key_providers(engine, monkeypatch, bad):
    status = {"_runtime": {"llm_provider": "zai", "model": manager.MODEL},
              "zai": {"configured": True, "free_tier_confirmed": True}}
    if bad == "model":
        status["_runtime"]["model"] = "paid-model"
    elif bad == "provider":
        status["_runtime"]["llm_provider"] = "cloudflare"
    elif bad == "confirmation":
        status["zai"]["free_tier_confirmed"] = False
    else:
        status["groq"] = {"configured": True}
    monkeypatch.setattr(engine, "http", lambda path: (200, {"database": "ready"} if path == "/health" else status))
    with pytest.raises(engine.Refusal):
        manager.require_ready(engine)


def test_cli_cannot_override_target_to_old_port_or_source(monkeypatch):
    factory = Mock(side_effect=AssertionError("No engine actions"))
    monkeypatch.setattr(manager, "new_engine", factory)
    with pytest.raises(SystemExit):
        manager.main(["--action", "start", "--port", "8083"])
    factory.assert_not_called()
