"""Own after80 HYBRID stand lifecycle; no copying, installs or inference.

Only explicit start/status/stop CLI actions operate on the fixed profile below.
The old 8084 manager and source are never stopped or reconfigured by this script.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path("/home/aegml/projects/otravel-after80-20261026")
MASTER = Path("/home/aegml/projects/otravel/.env")
DATABASE = "otravel_zai_eval"
PORT = 8084
MODEL = "glm-4.5-flash"
CREATOR = "after80_backend.py"
TRAVEL_KEYS = ("LITEAPI_API_KEY", "DUFFEL_ACCESS_TOKEN", "SERPAPI_API_KEY")
PROFILE_FIELDS = (*TRAVEL_KEYS, "LITEAPI_MODE", "ZAI_FREE_TIER_CONFIRMED", "SERPAPI_FREE_TIER_CONFIRMED")
DB_FIELDS = ("POSTGRES_USER", "POSTGRES_DB", "POSTGRES_PASSWORD")


def new_engine():
    """Fresh namespaces keep every override private to this lifecycle manager."""
    spec = importlib.util.spec_from_file_location("after80_zai_" + uuid4().hex, ROOT / "scripts/zai_eval_backend.py")
    manager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(manager)
    manager.SOURCE, manager.DATABASE, manager.PORT = SOURCE, DATABASE, PORT
    manager.MODEL, manager.CREATOR, manager.ROOT = MODEL, CREATOR, ROOT
    engine = manager.new_engine()
    runtime = SOURCE / ".local" / "after80-backend-8084"
    for name, value in {"RUNTIME": runtime, "IDENTITY": runtime / "process.json", "LOG": runtime / "uvicorn.log",
            "EVIDENCE": ROOT / "docs/verification/after80-backend-20261026.json"}.items():
        setattr(engine, name, value)
    engine.read_database_password = lambda: read_database_password(engine)
    engine.recorded_process = lambda account: manager.recorded_process(engine, account)
    engine.save_evidence = lambda action, account, extra=None: save_evidence(engine, action, account, extra)
    manager.require_ready = require_ready
    engine.after80_manager = manager
    return engine


def _private_values(engine, path, allowed, *, strict=False):
    engine.require(path.is_file() and not path.is_symlink() and path.resolve() == path
        and path.stat().st_mode & 0o077 == 0, "after80_private_environment_missing_or_untrusted")
    result = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip().removeprefix("export ")
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name not in allowed:
            engine.require(not strict, "after80_source_environment_must_only_contain_zai_key")
            continue
        engine.require(name not in result, "after80_duplicate_selected_environment_field")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        result[name] = value
    return result


def _key(engine, value, reason):
    engine.require(isinstance(value, str) and 0 < len(value) <= 4096
        and not any(char.isspace() for char in value), reason)
    return value


def _confirmed(value):
    return value is True or isinstance(value, str) and value.lower() in {"true", "1", "yes"}


def read_source_key(engine):
    values = _private_values(engine, SOURCE / ".env", ("ZAI_API_KEY",), strict=True)
    return _key(engine, values.get("ZAI_API_KEY"), "after80_zai_key_missing_or_malformed")


def read_master_profile(engine):
    values = _private_values(engine, MASTER, PROFILE_FIELDS)
    validate_profile(engine, values)
    return values


def read_database_password(engine):
    values = _private_values(engine, MASTER, DB_FIELDS)
    engine.require(values.get("POSTGRES_USER", "otravel") == "otravel"
        and values.get("POSTGRES_DB", "otravel") == "otravel" and values.get("POSTGRES_PASSWORD"),
        "after80_master_database_identity_unexpected")
    return values["POSTGRES_PASSWORD"]


def validate_profile(engine, profile):
    engine.require(isinstance(profile, dict) and _confirmed(profile.get("ZAI_FREE_TIER_CONFIRMED"))
        and _confirmed(profile.get("SERPAPI_FREE_TIER_CONFIRMED")), "after80_free_confirmation_missing")
    for name in TRAVEL_KEYS:
        _key(engine, profile.get(name), "after80_travel_key_missing_or_malformed")
    mode = profile.get("LITEAPI_MODE", "sandbox")
    engine.require(isinstance(mode, str) and mode.lower() == "sandbox"
        and profile["LITEAPI_API_KEY"].startswith(("sand_", "sandbox_")), "after80_liteapi_sandbox_required")
    engine.require(profile["DUFFEL_ACCESS_TOKEN"].startswith("duffel_test_")
        and len(profile["DUFFEL_ACCESS_TOKEN"]) > len("duffel_test_"), "after80_duffel_test_token_required")


def environment(engine, database, key, profile):
    # The inherited environment builder is itself allowlisted; no os.environ or
    # complete master mapping is ever copied into the application environment.
    return environment_with_base(engine, database, _key(engine, key, "after80_zai_key_invalid"),
        profile, engine.after80_manager.environment)


def require_ready(engine):
    code, health = engine.http("/health")
    engine.require(code == 200 and isinstance(health, dict) and health.get("database") == "ready"
        and health.get("application") == "backend" and health.get("supplier_booking") is False,
        "after80_backend_health_mismatch")
    code, status = engine.http("/api/v1/providers/status")
    engine.require(code == 200 and isinstance(status, dict), "after80_provider_status_unavailable")
    engine.require(status.get("_runtime") == {"llm_provider": "zai", "model": MODEL}
        and status.get("zai", {}).get("configured") is True
        and status.get("zai", {}).get("free_tier_confirmed") is True
        and status.get("zai", {}).get("paid_fallback") is False, "after80_zai_profile_mismatch")
    engine.require(status.get("liteapi", {}).get("configured") is True
        and status.get("liteapi", {}).get("mode") == "sandbox"
        and status.get("duffel", {}).get("configured") is True
        and status.get("duffel", {}).get("mode") == "sandbox"
        and status.get("serpapi", {}).get("configured") is True
        and status.get("serpapi", {}).get("free_tier_confirmed") is True
        and status.get("serpapi", {}).get("source_type") == "LIVE_INDICATIVE", "after80_travel_profile_mismatch")
    allowed = {"_runtime", "zai", "liteapi", "duffel", "serpapi", "open_meteo"}
    others = ("gemini", "openrouter", "groq", "cloudflare", "ollama", "hotelbeds", "ratehawk", "travelpayouts")
    engine.require(all(isinstance(status.get(name), dict) and status[name].get("configured") is False
        for name in others), "after80_disallowed_provider_status_not_explicitly_false")
    for name, value in status.items():
        engine.require(isinstance(value, dict), "after80_provider_status_malformed")
        if name not in allowed:
            engine.require(not value.get("configured") and not value.get("free_tier_confirmed"),
                "after80_disallowed_provider_configured")
        engine.require(value.get("paid_fallback") is not True, "after80_paid_fallback_forbidden")
    return {"database": "ready", "provider": "zai", "model": MODEL,
        "travel": {"liteapi": "SANDBOX", "duffel": "TEST", "serpapi": "LIVE_INDICATIVE"},
        "supplier_booking": False, "provider_quota_checked": False}


def save_evidence(engine, action, account, extra=None):
    identity, listeners = engine.recorded_process(account), engine.listeners()
    saved = json.loads(engine.IDENTITY.read_text()) if engine.IDENTITY.is_file() else {}
    manifest = engine.source_manifest()
    loaded = engine.loaded_manifest_status(saved, manifest) if identity else {"status": "NOT_RUNNING"}
    status = "RUNNING" if identity else "UNOWNED_LISTENER" if listeners else "STOPPED"
    record = {"checked_at_utc": engine.now(), "action": action, "status": status, "manager": CREATOR,
        "source": str(SOURCE), "database": DATABASE, "url": engine.BASE, "process": identity, "listeners": listeners,
        "current_source_manifest": manifest, "process_start_source_manifest": saved.get("source_manifest"),
        "loaded_manifest": loaded, "default_search": {"mode": "HYBRID", "hotel_provider": "liteapi",
            "flight_provider": "duffel", "guest_nationality": None},
        "limits": {"daily_engineering_bound": 2000, "calls_per_run": 1, "local_calls": 0, "fallback": False},
        "inference_requested_by_manager": False, "supplier_requests_by_manager": False,
        "credentials_recorded": False, "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    if identity:
        engine.require_owned_listener(identity)
        engine.require(loaded.get("matches_current") is True, "after80_loaded_source_changed_restart_required")
        record["readiness"] = require_ready(engine)
        record["status_pages"] = {path: engine.http(path)[0] for path in ("/docs", "/debug")}
    if extra:
        record.update(extra)
    engine.EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    engine.atomic_json(engine.EVIDENCE, record, 0o644)
    print(json.dumps({"action": action, "status": status, "url": engine.BASE,
        "database": DATABASE, "evidence": str(engine.EVIDENCE), "inference_requests": 0}))
    return record


def start(engine, account):
    manager = engine.after80_manager
    if engine.recorded_process(account) is not None:
        return manager.start(engine, account)  # Reuse checks identity/hash/health without reading keys.
    engine.require(not engine.listeners(), "after80_port_8084_unowned_nothing_stopped")
    profile, key = read_master_profile(engine), read_source_key(engine)
    old_environment, old_key = manager.environment, manager.read_zai_key
    try:
        manager.environment = lambda e, database, _key: environment_with_base(e, database, key, profile, old_environment)
        manager.read_zai_key = lambda _: key
        return manager.start(engine, account)
    finally:
        manager.environment, manager.read_zai_key = old_environment, old_key


def environment_with_base(engine, database, key, profile, base):
    validate_profile(engine, profile)
    env = base(engine, database, key)
    env.update({name: profile[name] for name in TRAVEL_KEYS})
    env.update({"LITEAPI_MODE": "sandbox", "SERPAPI_FREE_TIER_CONFIRMED": "true",
        "DEFAULT_SEARCH_MODE": "HYBRID", "DEFAULT_HOTEL_PROVIDER": "liteapi", "DEFAULT_FLIGHT_PROVIDER": "duffel"})
    return env


def windows_dispatch(action):
    import winreg

    base = None
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Lxss") as registry:
        for index in range(winreg.QueryInfoKey(registry)[0]):
            with winreg.OpenKey(registry, winreg.EnumKey(registry, index)) as item:
                if winreg.QueryValueEx(item, "DistributionName")[0] == "AegML-Hackathon":
                    base = winreg.QueryValueEx(item, "BasePath")[0]
    if base is None or not str(base).removeprefix("\\\\?\\").lower().startswith("c:"):
        raise ValueError("after80_distro_not_c_backed")
    path = str(Path(__file__).resolve())
    script = "/mnt/" + path[0].lower() + path[2:].replace("\\", "/")
    result = subprocess.run(["wsl.exe", "-d", "AegML-Hackathon", "-u", "root", "--", "python3", "-B", script, "--action", action],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    print(result.stdout, end="")
    if result.stderr:
        print("After80 manager child details suppressed; consult private log.", file=sys.stderr)
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
                engine.stop(account)
            else:
                engine.save_evidence("status", account)
        return 0
    except Exception as error:  # noqa: BLE001 - CLI boundary must suppress credential-bearing exception text.
        reason = str(error) if engine is not None and isinstance(error, engine.Refusal) else type(error).__name__
        print(json.dumps({"action": action, "status": "REFUSED_OR_FAILED", "reason": reason, "credentials_printed": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
