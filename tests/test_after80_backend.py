"""Synthetic controller guards only: no real keys, process, DB, HTTP or Docker."""
import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("after80_manager_test", ROOT / "scripts/after80_backend.py")
manager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manager)


@pytest.fixture
def engine():
    return manager.new_engine()


def profile():
    return {"LITEAPI_API_KEY": "sand_synthetic_liteapi", "DUFFEL_ACCESS_TOKEN": "duffel_test_synthetic",
        "SERPAPI_API_KEY": "synthetic-serp-key", "ZAI_FREE_TIER_CONFIRMED": "true",
        "SERPAPI_FREE_TIER_CONFIRMED": "true", "LITEAPI_MODE": "sandbox"}


class IdentityFile:
    def __init__(self, data, mode=0o600):
        self.data, self.mode = data, mode

    def is_file(self):
        return self.data is not None

    def stat(self):
        return SimpleNamespace(st_uid=0, st_mode=self.mode)

    def read_text(self, **kwargs):
        return json.dumps(self.data)


def owned(engine, monkeypatch):
    current = {"pid": 12345, "uid": 1000, "start_ticks": 99, "argv": deepcopy(engine.ARGV),
        "cwd": str(manager.SOURCE), "executable": str(engine.PYTHON.resolve())}
    saved = {**deepcopy(current), "created_by": manager.CREATOR, "database": manager.DATABASE,
        "port": manager.PORT, "source_manifest": {"format": engine.MANIFEST_FORMAT,
            "files": [{"path": "otravel/api.py", "sha256": "synthetic"}]}}
    engine.IDENTITY = IdentityFile(saved)
    monkeypatch.setattr(engine, "process_identity", lambda _: deepcopy(current))
    return saved, current


def test_fresh_engine_has_only_new_source_creator_identity_and_evidence():
    original = {path: (ROOT / path).read_bytes() for path in ("scripts/zai_eval_backend.py", "scripts/local_backend.py")}
    first, second = manager.new_engine(), manager.new_engine()
    assert first is not second and first.after80_manager is not second.after80_manager
    assert first.SOURCE == Path("/home/aegml/projects/otravel-after80-20261026")
    assert first.DATABASE == "otravel_zai_eval" and first.PORT == 8084
    assert "after80-backend-8084" in str(first.IDENTITY) and "after80-backend-20261026" in str(first.EVIDENCE)
    assert first.after80_manager.CREATOR == "after80_backend.py"
    assert first.ARGV[-3:] == ["--port", "8084", "--no-access-log"]
    first.SAFE_CONTEXT["unit"] = True
    assert second.SAFE_CONTEXT == {}
    assert all((ROOT / path).read_bytes() == value for path, value in original.items())


def test_child_environment_is_allowlisted_and_preserves_exact_profile(engine, monkeypatch):
    for key in engine.KEYS:
        monkeypatch.setenv(key, "MUST-NOT-INHERIT")
    monkeypatch.setenv("DATABASE_URL", "postgresql://wrong-db")
    values = {**profile(), "OPENAI_API_KEY": "MUST-NOT-INHERIT", "GUEST_NATIONALITY": "RU"}
    env = manager.environment(engine, SimpleNamespace(password="synthetic:#@", ip="172.18.0.2"), "synthetic-zai", values)
    assert env["ZAI_API_KEY"] == "synthetic-zai" and env["ZAI_MODEL"] == "glm-4.5-flash"
    assert env["LLM_PROVIDER"] == "zai" and env["ZAI_FREE_TIER_CONFIRMED"] == "true"
    assert env["LITEAPI_API_KEY"] == values["LITEAPI_API_KEY"] and env["LITEAPI_MODE"] == "sandbox"
    assert env["DUFFEL_ACCESS_TOKEN"].startswith("duffel_test_")
    assert env["SERPAPI_FREE_TIER_CONFIRMED"] == "true"
    assert env["DEFAULT_SEARCH_MODE"] == "HYBRID"
    assert (env["DEFAULT_HOTEL_PROVIDER"], env["DEFAULT_FLIGHT_PROVIDER"]) == ("liteapi", "duffel")
    assert env["DATABASE_URL"].endswith("/otravel_zai_eval") and "synthetic%3A%23%40" in env["DATABASE_URL"]
    assert env["MAX_LOCAL_LLM_CALLS_PER_DAY"] == "0" and env["MAX_LLM_CALLS_PER_RUN"] == "1"
    assert env["MAX_LLM_CALLS_PER_DAY"] == "2000" and env["OPENROUTER_BACKUP_MODEL"] == ""
    assert "MUST-NOT-INHERIT" not in json.dumps(env)
    assert all(key not in env for key in engine.KEYS if key not in {*manager.TRAVEL_KEYS, "ZAI_API_KEY"})
    assert not any("NATIONALITY" in key for key in env)


@pytest.mark.parametrize("field,value", [
    ("ZAI_FREE_TIER_CONFIRMED", "false"), ("SERPAPI_FREE_TIER_CONFIRMED", "false"),
    ("LITEAPI_MODE", "production"), ("LITEAPI_MODE", None), ("LITEAPI_API_KEY", "prod_not_allowed"),
    ("DUFFEL_ACCESS_TOKEN", "duffel_live_forbidden"), ("DUFFEL_ACCESS_TOKEN", "duffel_test_"),
    ("SERPAPI_API_KEY", ""), ("SERPAPI_API_KEY", "bad key"),
])
def test_paid_live_missing_or_unconfirmed_profile_is_rejected(engine, field, value):
    values = {**profile(), field: value}
    with pytest.raises(engine.Refusal):
        manager.environment(engine, SimpleNamespace(password="fake", ip="172.18.0.2"), "synthetic-zai", values)


class PrivateFile:
    def __init__(self, text, mode=0o600, symlink=False):
        self.text, self.mode, self.symlink = text, mode, symlink

    def is_file(self):
        return True

    def is_symlink(self):
        return self.symlink

    def resolve(self):
        return self

    def stat(self):
        return SimpleNamespace(st_mode=self.mode)

    def read_text(self, **kwargs):
        return self.text


def test_env_reader_selects_only_whitelist_and_never_interpolates_or_exports_extra(engine):
    fake = PrivateFile('OPENAI_API_KEY=not-selected\nPOSTGRES_PASSWORD="fake:#@"\nSERPAPI_API_KEY=${OPENAI_API_KEY}\n')
    values = manager._private_values(engine, fake, ("POSTGRES_PASSWORD",))
    assert values == {"POSTGRES_PASSWORD": "fake:#@"}
    literal = manager._private_values(engine, fake, ("SERPAPI_API_KEY",))
    assert literal["SERPAPI_API_KEY"] == "${OPENAI_API_KEY}"


@pytest.mark.parametrize("fake,strict", [
    (PrivateFile("ZAI_API_KEY=fake", mode=0o644), False),
    (PrivateFile("ZAI_API_KEY=fake", symlink=True), False),
    (PrivateFile("ZAI_API_KEY=fake\nOTHER_KEY=not-allowed"), True),
    (PrivateFile("ZAI_API_KEY=first\nZAI_API_KEY=second"), False),
])
def test_private_file_guards_reject_unsafe_or_ambiguous_data(engine, fake, strict):
    with pytest.raises(engine.Refusal):
        manager._private_values(engine, fake, ("ZAI_API_KEY",), strict=strict)


def test_database_password_reader_uses_master_not_new_source_file(engine, monkeypatch):
    reader = Mock(return_value={"POSTGRES_USER": "otravel", "POSTGRES_DB": "otravel", "POSTGRES_PASSWORD": "fake"})
    monkeypatch.setattr(manager, "_private_values", reader)
    assert engine.read_database_password() == "fake"
    reader.assert_called_once_with(engine, manager.MASTER, manager.DB_FIELDS)


@pytest.mark.parametrize("field,value", [
    ("created_by", "zai_eval_backend.py"), ("cwd", "/home/aegml/projects/otravel-zai-eval"),
    ("database", "otravel"), ("port", 8083), ("uid", 0),
])
def test_old_or_foreign_identity_never_authorizes_stop(engine, monkeypatch, field, value):
    saved, _ = owned(engine, monkeypatch)
    saved[field] = value
    signal = Mock(side_effect=AssertionError("No signal is authorized"))
    monkeypatch.setattr(engine, "verified_pidfd", signal)
    with pytest.raises(engine.Refusal):
        engine.stop(SimpleNamespace(pw_uid=1000))
    signal.assert_not_called()


@pytest.mark.parametrize("field,value", [("pid", 999), ("start_ticks", 100), ("argv", ["foreign"]), ("cwd", "/foreign")])
def test_reused_pid_or_changed_identity_is_rejected_before_key_reads(engine, monkeypatch, field, value):
    _, current = owned(engine, monkeypatch)
    current[field] = value
    reader = Mock(side_effect=AssertionError("No credential reads after identity mismatch"))
    monkeypatch.setattr(manager, "read_master_profile", reader)
    with pytest.raises(engine.Refusal, match="identity_changed"):
        manager.start(engine, SimpleNamespace(pw_uid=1000))
    reader.assert_not_called()


def test_busy_old_8084_refuses_start_before_any_key_or_database_read(engine, monkeypatch):
    engine.IDENTITY = IdentityFile(None)
    monkeypatch.setattr(engine, "listeners", lambda: [{"address": "127.0.0.1:8084", "pids": [999]}])
    reader = Mock(side_effect=AssertionError("No credentials read"))
    monkeypatch.setattr(manager, "read_master_profile", reader)
    monkeypatch.setattr(manager, "read_source_key", reader)
    database = Mock(side_effect=AssertionError("No database action"))
    monkeypatch.setattr(engine, "Database", database)
    with pytest.raises(engine.Refusal, match="unowned"):
        manager.start(engine, SimpleNamespace(pw_uid=1000))
    reader.assert_not_called()
    database.assert_not_called()


def readiness():
    status = {name: {"configured": False} for name in
        ("gemini", "openrouter", "groq", "cloudflare", "ollama", "hotelbeds", "ratehawk", "travelpayouts")}
    status.update({"_runtime": {"llm_provider": "zai", "model": manager.MODEL},
        "zai": {"configured": True, "free_tier_confirmed": True, "paid_fallback": False},
        "liteapi": {"configured": True, "mode": "sandbox"}, "duffel": {"configured": True, "mode": "sandbox"},
        "serpapi": {"configured": True, "free_tier_confirmed": True, "source_type": "LIVE_INDICATIVE"},
        "open_meteo": {"configured": True, "requires_key": False}})
    return status


@pytest.mark.parametrize("bad", [None, "model", "missing_travel", "production", "free", "other_llm", "paid", "missing_false"])
def test_readiness_requires_exact_profile_without_model_or_supplier_requests(engine, monkeypatch, bad):
    status = readiness()
    if bad == "model":
        status["_runtime"]["model"] = "paid-model"
    elif bad == "missing_travel":
        status["liteapi"]["configured"] = False
    elif bad == "production":
        status["duffel"]["mode"] = "live"
    elif bad == "free":
        status["serpapi"]["free_tier_confirmed"] = False
    elif bad == "other_llm":
        status["cloudflare"]["configured"] = True
    elif bad == "paid":
        status["zai"]["paid_fallback"] = True
    elif bad == "missing_false":
        del status["gemini"]
    paths = []

    def local_metadata(path):
        paths.append(path)
        assert path in {"/health", "/api/v1/providers/status"}
        return 200, ({"database": "ready", "application": "backend", "supplier_booking": False} if path == "/health" else status)

    monkeypatch.setattr(engine, "http", local_metadata)
    if bad is None:
        result = manager.require_ready(engine)
        assert result["travel"] == {"liteapi": "SANDBOX", "duffel": "TEST", "serpapi": "LIVE_INDICATIVE"}
        assert result["provider_quota_checked"] is False
    else:
        with pytest.raises(engine.Refusal):
            manager.require_ready(engine)
    assert paths == ["/health", "/api/v1/providers/status"]


def test_status_uses_only_owned_metadata_local_gets_and_no_credentials_or_database(engine, monkeypatch):
    saved, current = owned(engine, monkeypatch)
    monkeypatch.setattr(engine, "source_manifest", lambda: deepcopy(saved["source_manifest"]))
    monkeypatch.setattr(engine, "listeners", lambda: [{"address": "127.0.0.1:8084", "pids": [current["pid"]]}])
    monkeypatch.setattr(engine, "require_owned_listener", lambda _: None)
    forbidden = Mock(side_effect=AssertionError("Status cannot read keys or set up DB"))
    monkeypatch.setattr(manager, "read_master_profile", forbidden)
    monkeypatch.setattr(manager, "read_source_key", forbidden)
    monkeypatch.setattr(engine, "Database", forbidden)
    paths = []

    def metadata_get(path):
        paths.append(path)
        assert path in {"/health", "/api/v1/providers/status", "/docs", "/debug"}
        if path == "/health":
            return 200, {"database": "ready", "application": "backend", "supplier_booking": False}
        return (200, readiness()) if path == "/api/v1/providers/status" else (200, None)

    monkeypatch.setattr(engine, "http", metadata_get)
    engine.EVIDENCE = SimpleNamespace(parent=SimpleNamespace(mkdir=Mock()))
    saved_record = Mock()
    monkeypatch.setattr(engine, "atomic_json", saved_record)
    result = manager.save_evidence(engine, "status", SimpleNamespace(pw_uid=1000))
    assert result["status"] == "RUNNING" and result["loaded_manifest"]["matches_current"] is True
    assert result["inference_requested_by_manager"] is False and result["supplier_requests_by_manager"] is False
    assert paths == ["/health", "/api/v1/providers/status", "/docs", "/debug"]
    forbidden.assert_not_called()
    saved_record.assert_called_once()


def test_start_wrapper_restores_fresh_manager_functions_even_after_refusal(engine, monkeypatch):
    engine.IDENTITY = IdentityFile(None)
    monkeypatch.setattr(engine, "listeners", list)
    monkeypatch.setattr(manager, "read_master_profile", lambda _: profile())
    monkeypatch.setattr(manager, "read_source_key", lambda _: "synthetic-zai")
    nested = engine.after80_manager
    environment_before, key_before = nested.environment, nested.read_zai_key

    def refused(e, account):
        env = nested.environment(e, SimpleNamespace(password="fake", ip="172.18.0.2"), nested.read_zai_key(e))
        assert env["LITEAPI_MODE"] == "sandbox" and env["DEFAULT_FLIGHT_PROVIDER"] == "duffel"
        raise e.Refusal("synthetic downstream refusal")

    monkeypatch.setattr(nested, "start", refused)
    with pytest.raises(engine.Refusal, match="downstream"):
        manager.start(engine, SimpleNamespace(pw_uid=1000))
    assert nested.environment is environment_before and nested.read_zai_key is key_before


class RegistryKey:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.mark.parametrize("base", [r"C:\WSL\AegML-Hackathon", r"D:\WSL\not-allowed"])
def test_windows_dispatch_calls_only_own_wrapper_in_verified_distro(monkeypatch, base):
    registry = SimpleNamespace(HKEY_CURRENT_USER=1, OpenKey=lambda *args: RegistryKey(),
        QueryInfoKey=lambda _: (1, 0, 0), EnumKey=lambda *args: "test",
        QueryValueEx=lambda _, name: (("AegML-Hackathon" if name == "DistributionName" else base), 0))
    monkeypatch.setitem(manager.sys.modules, "winreg", registry)
    monkeypatch.setattr(manager, "Path", lambda _: SimpleNamespace(resolve=lambda: r"E:\work\aegml\scripts\after80_backend.py"))
    process = Mock(return_value=SimpleNamespace(returncode=0, stdout="synthetic-status\n", stderr=""))
    monkeypatch.setattr(manager.subprocess, "run", process)
    if base.startswith("D:"):
        with pytest.raises(ValueError):
            manager.windows_dispatch("status")
        process.assert_not_called()
    else:
        assert manager.windows_dispatch("status") == 0
        argv = process.call_args.args[0]
        assert argv[:5] == ["wsl.exe", "-d", "AegML-Hackathon", "-u", "root"]
        assert argv[-3:] == ["/mnt/e/work/aegml/scripts/after80_backend.py", "--action", "status"]
        assert "zai_eval_backend.py" not in " ".join(argv)


def test_cli_cannot_override_source_database_or_port(monkeypatch):
    factory = Mock(side_effect=AssertionError("No lifecycle actions"))
    monkeypatch.setattr(manager, "new_engine", factory)
    with pytest.raises(SystemExit):
        manager.main(["--action", "start", "--port", "8083"])
    factory.assert_not_called()
