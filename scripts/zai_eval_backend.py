"""Manage the separate Z.AI evaluation backend; actions require explicit CLI use.

No copying, dependency installation, inference, or changes to ports8081/8083.
Start/status/stop operate only on the fixed source/PID/port/database below.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import quote
from uuid import uuid4

SOURCE = Path("/home/aegml/projects/otravel-zai-eval")
DATABASE = "otravel_zai_eval"
PORT = 8084
MODEL = "glm-4.5-flash"
CREATOR = "zai_eval_backend.py"
ROOT = Path(__file__).resolve().parents[1]


def new_engine():
    """A fresh module namespace prevents overriding the 8083 manager instance."""
    spec = importlib.util.spec_from_file_location("zai_engine_" + uuid4().hex, ROOT / "scripts/local_backend.py")
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    runtime = SOURCE / ".local" / "zai-eval-backend-8084"
    overrides = {"SOURCE": SOURCE, "DATABASE": DATABASE, "PORT": PORT, "BASE": f"http://127.0.0.1:{PORT}",
        "RUNTIME": runtime, "IDENTITY": runtime / "process.json", "LOG": runtime / "uvicorn.log",
        "EVIDENCE": ROOT / "docs/verification/zai-eval-backend-20261026.json",
        "CURRENT_STAGE": "preflight", "SAFE_CONTEXT": {}}
    for key, value in overrides.items():
        setattr(engine, key, value)
    engine.ARGV = [str(engine.PYTHON), "-m", "uvicorn", "otravel.api:app", "--host", "127.0.0.1", "--port", str(PORT), "--no-access-log"]
    engine.recorded_process = lambda account: recorded_process(engine, account)
    engine.save_evidence = lambda action, account, extra=None: save_evidence(engine, action, account, extra)
    return engine


def recorded_process(engine, account):
    if not engine.IDENTITY.is_file():
        return None
    stat = engine.IDENTITY.stat()
    engine.require(stat.st_uid == 0 and stat.st_mode & 0o077 == 0, "untrusted_zai_identity_file")
    saved = json.loads(engine.IDENTITY.read_text())
    engine.require(saved.get("created_by") == CREATOR and saved.get("database") == DATABASE and
        saved.get("port") == PORT and saved.get("uid") == account.pw_uid and saved.get("argv") == engine.ARGV and
        saved.get("cwd") == str(SOURCE), "recorded_zai_identity_scope_mismatch")
    if saved.get("stopped_at_utc"):
        return None
    current = engine.process_identity(saved["pid"])
    if current is None:
        return None
    engine.require(all(current.get(key) == saved.get(key) for key in ("pid", "uid", "start_ticks", "argv", "cwd", "executable")),
                   "zai_pid_reused_or_identity_changed")
    engine.require(Path(current["executable"]).resolve() == engine.PYTHON.resolve(), "zai_python_executable_changed")
    return current


def read_zai_key(engine):
    """Called only by start after native-path checks; no dotenv interpolation."""
    path = SOURCE / ".env"
    engine.require(path.is_file() and not path.is_symlink() and path.resolve().parent == SOURCE and
                   path.stat().st_mode & 0o077 == 0, "new_zai_environment_not_private_or_missing")
    key = ""
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[7:]
        if "=" not in line or line.startswith("#"):
            continue
        name, value = line.split("=", 1)
        if name.strip() != "ZAI_API_KEY":
            continue
        key = value.strip()
        if len(key) >= 2 and key[0] == key[-1] and key[0] in "\"'":
            key = key[1:-1]
    engine.require(bool(key) and len(key) <= 4096 and not any(c.isspace() for c in key), "zai_standard_api_key_missing_or_malformed")
    return key


def environment(engine, database, key):
    engine.require(isinstance(key, str) and bool(key) and not any(c.isspace() for c in key), "zai_key_invalid")
    # Construct from constants, never os.environ or the complete .env mapping.
    return {"HOME": "/home/aegml", "USER": "aegml", "LOGNAME": "aegml", "LANG": "C.UTF-8",
        "PATH": str(engine.PYTHON.parent) + ":/usr/local/bin:/usr/bin:/bin", "PYTHONPATH": str(SOURCE),
        "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "DATABASE_URL": f"postgresql+asyncpg://otravel:{quote(database.password, safe='')}@{database.ip}:5432/{DATABASE}",
        "DATABASE_HOST": database.ip, "DATABASE_PORT": "5432", "DATABASE_USER": "otravel",
        "DATABASE_NAME": DATABASE, "DATABASE_PASSWORD": database.password,
        "LLM_PROVIDER": "zai", "ZAI_API_KEY": key, "ZAI_MODEL": MODEL, "ZAI_FREE_TIER_CONFIRMED": "true",
        "MAX_LLM_CALLS_PER_DAY": "2000", "MAX_LLM_CALLS_PER_RUN": "1", "MAX_LOCAL_LLM_CALLS_PER_DAY": "0",
        "LLM_REQUEST_TIMEOUT": "45", "OPENROUTER_BACKUP_MODEL": "", "LLM_REASONING_ENABLED": "false",
        "GEMINI_FREE_TIER_CONFIRMED": "false", "GROQ_FREE_TIER_CONFIRMED": "false",
        "CLOUDFLARE_FREE_TIER_CONFIRMED": "false", "SERPAPI_FREE_TIER_CONFIRMED": "false",
        "HOTELBEDS_TEST_CONFIRMED": "false", "RATEHAWK_TEST_CONFIRMED": "false",
        "CORS_ORIGINS": "http://localhost:8081,http://127.0.0.1:8081"}


def require_ready(engine):
    code, health = engine.http("/health")
    engine.require(code == 200 and isinstance(health, dict) and health.get("database") == "ready", "zai_backend_not_ready")
    code, status = engine.http("/api/v1/providers/status")
    engine.require(code == 200 and status.get("_runtime") == {"llm_provider": "zai", "model": MODEL} and
        status.get("zai", {}).get("configured") is True and status.get("zai", {}).get("free_tier_confirmed") is True,
        "zai_runtime_provider_model_or_free_confirmation_mismatch")
    others = ("gemini", "openrouter", "groq", "cloudflare", "ollama", "cohere", "nvidia", "liteapi", "duffel", "serpapi", "hotelbeds", "ratehawk", "travelpayouts")
    engine.require(not any(status.get(name, {}).get("configured") for name in others), "other_provider_configured_in_zai_child")
    return {"health": health, "provider": "zai", "model": MODEL, "free_tier_confirmed": True}


def save_evidence(engine, action, account, extra=None):
    identity = engine.recorded_process(account)
    saved = json.loads(engine.IDENTITY.read_text()) if engine.IDENTITY.is_file() else {}
    manifest = engine.source_manifest()
    record = {"checked_at_utc": engine.now(), "action": action, "manager": CREATOR, "source": str(SOURCE),
        "database": DATABASE, "url": engine.BASE, "process": identity, "listeners": engine.listeners(),
        "current_source_manifest": manifest, "process_start_source_manifest": saved.get("source_manifest"),
        "loaded_manifest": engine.loaded_manifest_status(saved, manifest) if identity else {"status": "NOT_RUNNING"},
        "limits": {"provider": "zai", "model": MODEL, "daily_engineering_bound": 2000, "calls_per_run": 1, "fallback": False},
        "inference_requested_by_manager": False, "supplier_requests": False, "credentials_recorded": False,
        "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    if identity:
        engine.require_owned_listener(identity)
        record["readiness"] = require_ready(engine)
        record["status_pages"] = {path: engine.http(path)[0] for path in ("/docs", "/debug")}
    if extra:
        record.update(extra)
    engine.EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    engine.atomic_json(engine.EVIDENCE, record, 0o644)
    print(json.dumps({"action": action, "status": "RUNNING" if identity else "STOPPED", "url": engine.BASE,
                      "database": DATABASE, "evidence": str(engine.EVIDENCE), "inference_requests": 0}))
    return record


def start(engine, account):
    engine.stage("checking_zai_owned_listener")
    identity = engine.recorded_process(account)
    if identity:
        engine.require_owned_listener(identity)
        current = engine.source_manifest()
        saved = json.loads(engine.IDENTITY.read_text())
        engine.require(engine.loaded_manifest_status(saved, current)["matches_current"], "zai_source_changed_owned_restart_required")
        require_ready(engine)
        return engine.save_evidence("start_reused_owned_process", account)
    engine.require(not engine.listeners(), "port_8084_is_occupied_by_unowned_process_nothing_stopped")
    engine.stage("verifying_zai_database_and_key")
    database = engine.Database()  # Labels, private CIDR, role and DB-name guards use this fresh engine's globals.
    engine.SAFE_CONTEXT["database_setup"] = database.receipt
    env = environment(engine, database, read_zai_key(engine))
    manifest = engine.source_manifest()
    code = ("import json,pathlib,otravel.api; from otravel.config import Settings; s=Settings.from_env(); "
            "print(json.dumps({'api_file':str(pathlib.Path(otravel.api.__file__).resolve()),'provider':s.llm_mode,"
            "'daily_limit':s.max_llm_calls_per_day,'run_limit':s.max_llm_calls_per_run}))")
    imported = engine.capture([str(engine.PYTHON), "-c", code], env=env, cwd=SOURCE,
                              user=account.pw_uid, group=account.pw_gid, extra_groups=[])
    engine.require(imported.returncode == 0, "zai_native_import_failed_details_suppressed")
    facts = json.loads(imported.stdout)
    engine.require(facts == {"api_file": str(SOURCE / "otravel/api.py"), "provider": "zai", "daily_limit": 2000, "run_limit": 1}, "zai_native_source_or_limits_mismatch")
    engine.require(engine.source_manifest() == manifest, "zai_source_changed_before_database_setup")
    engine.stage("preparing_only_zai_eval_database")
    database.allow_local_database()
    database.create()
    before = database.counts()
    engine.require(before.get("pending_or_running", 0) == 0, "zai_pending_runs_require_owner_review_no_auto_inference")
    engine.require(not engine.listeners() and engine.source_manifest() == manifest, "zai_port_or_source_changed_before_launch")
    engine.stage("launching_zai_owned_process")
    fd = os.open(engine.LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        process = subprocess.Popen(engine.ARGV, cwd=SOURCE, env=env, stdin=subprocess.DEVNULL, stdout=fd,
            stderr=subprocess.STDOUT, close_fds=True, start_new_session=True, user=account.pw_uid,
            group=account.pw_gid, extra_groups=[], umask=0o077)
    finally:
        os.close(fd)
    identity = engine.process_identity(process.pid)
    engine.require(identity is not None, "zai_process_exited_immediately_private_log_retained")
    engine.atomic_json(engine.IDENTITY, {**identity, "created_by": CREATOR, "created_at_utc": engine.now(),
                                       "database": DATABASE, "port": PORT, "source_manifest": manifest})
    for _ in range(45):
        engine.require(process.poll() is None, "zai_process_exited_private_log_retained")
        try:
            engine.require_owned_listener(engine.recorded_process(account))
            require_ready(engine)
            break
        except (OSError, engine.Refusal, ValueError):
            time.sleep(1)
    else:
        raise engine.Refusal("zai_health_timeout_owned_process_retained_no_force_stop")
    engine.require(engine.source_manifest() == manifest, "zai_source_changed_during_startup_process_retained")
    after = database.counts()
    engine.require(after.get("model_call_started", 0) == before.get("model_call_started", 0), "unexpected_inference_during_zai_start")
    return engine.save_evidence("start", account, {"database_setup": database.receipt,
        "database_counts_before": before, "database_counts_after": after, "import_check": facts})


def windows_dispatch(action):
    import winreg
    base = None
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Lxss") as registry:
        for i in range(winreg.QueryInfoKey(registry)[0]):
            with winreg.OpenKey(registry, winreg.EnumKey(registry, i)) as item:
                if winreg.QueryValueEx(item, "DistributionName")[0] == "AegML-Hackathon":
                    base = winreg.QueryValueEx(item, "BasePath")[0]
    if base is None or not str(base).removeprefix("\\\\?\\").lower().startswith("c:"):
        raise ValueError("zai_distro_not_c_backed")
    path = str(Path(__file__).resolve())
    script = "/mnt/" + path[0].lower() + path[2:].replace("\\", "/")
    result = subprocess.run(["wsl.exe", "-d", "AegML-Hackathon", "-u", "root", "--", "python3", script, "--action", action],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    print(result.stdout, end="")
    if result.stderr:
        print("ZAI manager child details suppressed; consult private log.", file=sys.stderr)
    return result.returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action", choices=("start", "status", "stop"), required=True)
    action = parser.parse_args(argv).action
    engine = None
    try:
        if sys.platform == "win32":
            return windows_dispatch(action)
        import fcntl
        engine = new_engine()
        account = engine.native_paths()
        with (engine.RUNTIME / "controller.lock").open("a") as lock:
            os.chmod(engine.RUNTIME / "controller.lock", 0o600)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            if action == "start":
                start(engine, account)
            elif action == "stop":
                engine.stop(account)  # Reuses pidfd-only stop with this engine's identity/path/port globals.
            else:
                engine.save_evidence("status", account)
        return 0
    except Exception as error:
        reason = str(error) if engine is not None and isinstance(error, engine.Refusal) else type(error).__name__
        print(json.dumps({"action": action, "status": "REFUSED_OR_FAILED", "reason": reason, "credentials_printed": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
