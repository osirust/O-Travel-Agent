"""Manage only the recorded local Ollama backend on WSL loopback port 8083.

Uses native C-backed sources and a separate otravel_local_eval database. Never
loads provider credentials into the child, never invokes model inference, never
falls back to the runtime/test databases, and never signals an unverified PID.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import select
import signal
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener

if sys.platform != "win32":
    import pwd


DISTRO = "AegML-Hackathon"
SOURCE = Path("/home/aegml/projects/otravel")
PYTHON = Path("/home/aegml/venvs/otravel-harness-20261025/bin/python")
RUNTIME = SOURCE / ".local" / "local-backend-8083"
IDENTITY = RUNTIME / "process.json"
LOG = RUNTIME / "uvicorn.log"
EVIDENCE = Path("/mnt/e/work/aegml/docs/verification/local-backend-20261025.json")
DATABASE = "otravel_local_eval"
PORT = 8083
BASE = f"http://127.0.0.1:{PORT}"
HBA = "/var/lib/postgresql/data/pg_hba.conf"
KEYS = ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
        "LITEAPI_API_KEY", "DUFFEL_ACCESS_TOKEN", "SERPAPI_API_KEY", "HOTELBEDS_API_KEY",
        "HOTELBEDS_API_SECRET", "RATEHAWK_KEY_ID", "RATEHAWK_API_KEY", "TRAVELPAYOUTS_API_TOKEN",
        "ZAI_API_KEY", "COHERE_API_KEY", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "NVIDIA_API_KEY")
ARGV = [str(PYTHON), "-m", "uvicorn", "otravel.api:app", "--host", "127.0.0.1",
        "--port", str(PORT), "--no-access-log"]
# Historical receipts used only these five files. Retain them for compatibility,
# but never use them as evidence for a complete loaded package revision.
SOURCE_FILES = ("api.py", "providers.py", "config.py", "harness.py", "store.py")
MANIFEST_FORMAT = "otravel.package_source_manifest.v1"
SOURCE_SUFFIXES = {".py", ".html", ".css", ".js", ".json"}
PACKAGE_NOTICES = {"data/UNICODE-CLDR-LICENSE.txt", "data/CLDR-ATTRIBUTION.md"}
OPENER = build_opener(ProxyHandler({}))
CURRENT_STAGE = "preflight"
SAFE_CONTEXT = {}


class Refusal(Exception):
    pass


def require(condition, reason):
    if not condition:
        raise Refusal(reason)


def now():
    return datetime.now(timezone.utc).isoformat()


def capture(argv, **kwargs):
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=False, timeout=30, **kwargs)


def docker(*argv, stdin=None, env=None):
    result = capture(["docker", *argv], input=stdin, env=env)
    if result.returncode:
        flags = {"no_hba_entry": "no pg_hba.conf entry" in result.stderr,
                 "password_auth_failed": "password authentication failed" in result.stderr,
                 "sql_syntax_error": "syntax error" in result.stderr,
                 "permission_denied": "permission denied" in result.stderr,
                 "database_missing": "does not exist" in result.stderr}
        SAFE_CONTEXT["failed_docker_operation"] = {"operation": argv[0], "psql": "psql" in argv,
                                                   "exit_code": result.returncode, "error_flags": flags}
        raise Refusal("owned_docker_operation_failed")
    return result.stdout.strip()


def stage(name):
    global CURRENT_STAGE
    CURRENT_STAGE = name


def atomic_json(path, value, mode=0o600):
    temporary = path.with_suffix(path.suffix + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(temporary, flags, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def native_paths():
    require(os.geteuid() == 0, "controller_requires_root_only_for_verified_docker_and_setuid")
    require(SOURCE.is_dir() and SOURCE.resolve() == SOURCE and PYTHON.is_file(), "native_source_or_venv_missing")
    require((SOURCE / "otravel" / "api.py").is_file(), "native_api_source_missing")
    account = pwd.getpwnam("aegml")
    require(account.pw_uid != 0 and SOURCE.stat().st_uid == account.pw_uid, "unexpected_native_source_owner")
    RUNTIME.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(RUNTIME.resolve() == RUNTIME and RUNTIME.stat().st_uid == 0 and
            RUNTIME.stat().st_mode & 0o077 == 0, "runtime_metadata_directory_not_private_root_owned")
    return account


def process_identity(pid):
    proc = Path("/proc") / str(pid)
    try:
        stat = (proc / "stat").read_text()
        fields = stat.rsplit(") ", 1)[1].split()
        if fields[0] == "Z":
            return None
        command = [part.decode() for part in (proc / "cmdline").read_bytes().split(b"\0") if part]
        uid_line = next(line for line in (proc / "status").read_text().splitlines() if line.startswith("Uid:"))
        return {"pid": pid, "uid": int(uid_line.split()[1]), "start_ticks": int(fields[19]),
                "argv": command, "cwd": os.readlink(proc / "cwd"), "executable": os.readlink(proc / "exe")}
    except (OSError, ValueError, StopIteration, IndexError, UnicodeError):
        return None


def recorded_process(account):
    if not IDENTITY.is_file():
        return None
    require(IDENTITY.stat().st_uid == 0 and IDENTITY.stat().st_mode & 0o077 == 0, "untrusted_identity_file")
    expected = json.loads(IDENTITY.read_text())
    require(expected.get("created_by") == "local_backend.py" and expected.get("database") == DATABASE and
            expected.get("port") == PORT and expected.get("uid") == account.pw_uid and
            expected.get("argv") == ARGV and expected.get("cwd") == str(SOURCE), "recorded_identity_scope_mismatch")
    if expected.get("stopped_at_utc"):
        return None
    observed = process_identity(expected["pid"])
    if observed is None:
        return None
    require(all(observed.get(key) == expected.get(key) for key in ("pid", "uid", "start_ticks", "argv", "cwd", "executable")),
            "pid_reused_or_owned_process_identity_changed")
    require(Path(observed["executable"]).resolve() == PYTHON.resolve(), "owned_python_executable_changed")
    return observed


def verified_pidfd(identity, verifier):
    """Acquire the kernel process reference, then recheck its recorded identity.

    A recycled numeric PID cannot redirect a subsequent signal through this FD.
    No fallback to kill(pid) is allowed on hosts without pidfd support.
    """
    require(hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"), "pidfd_required_no_numeric_pid_signal_fallback")
    fd = os.pidfd_open(identity["pid"], 0)
    try:
        require(verifier() == identity, "process_identity_changed_after_pidfd_open_nothing_signalled")
        return fd
    except BaseException:
        os.close(fd)
        raise


def listeners():
    result = capture(["ss", "-H", "-ltnp", "sport", "=", ":" + str(PORT)])
    require(result.returncode == 0, "listener_inventory_unavailable")
    answer = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 5:
            pids = sorted(set(int(pid) for pid in re.findall(r"pid=(\d+)", line)))
            answer.append({"address": fields[3], "pids": pids})
    return answer


def require_owned_listener(identity):
    observed = listeners()
    require(observed and all(item["address"] == f"127.0.0.1:{PORT}" and item["pids"] == [identity["pid"]] for item in observed),
            "listener_not_owned_loopback_process")
    return observed


def read_database_password():
    # Parse only the permitted DB fields. Other .env keys are neither returned
    # nor copied to the process environment.
    path = SOURCE / ".env"
    require(path.is_file() and path.stat().st_mode & 0o077 == 0, "native_environment_not_private")
    values = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        if key.strip() not in {"POSTGRES_USER", "POSTGRES_DB", "POSTGRES_PASSWORD"}:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    require(values.get("POSTGRES_USER", "otravel") == "otravel" and
            values.get("POSTGRES_DB", "otravel") == "otravel" and bool(values.get("POSTGRES_PASSWORD")),
            "native_database_identity_unexpected")
    return values["POSTGRES_PASSWORD"]


class Database:
    def __init__(self):
        self.identifier = docker("inspect", "-f", "{{.Id}}", "otravel-db-1")
        labels = json.loads(docker("inspect", "-f", "{{json .Config.Labels}}", self.identifier))
        require(labels.get("com.docker.compose.project") == "otravel" and
                labels.get("com.docker.compose.service") == "db", "main_docker_database_labels_mismatch")
        require(docker("inspect", "-f", "{{.State.Running}}", self.identifier) == "true", "verified_database_not_running")
        attached = json.loads(docker("inspect", "-f", "{{json .NetworkSettings.Networks}}", self.identifier))
        require(set(attached) == {"otravel_default"}, "unexpected_database_networks")
        self.ip = str(ipaddress.ip_address(attached["otravel_default"]["IPAddress"]))
        network = json.loads(docker("network", "inspect", "otravel_default"))[0]
        require(network.get("Labels", {}).get("com.docker.compose.project") == "otravel" and network.get("Driver") == "bridge",
                "database_network_not_verified_project_bridge")
        configurations = network["IPAM"]["Config"]
        require(len(configurations) == 1, "unexpected_bridge_subnets")
        self.subnet = ipaddress.ip_network(configurations[0]["Subnet"])
        require(self.subnet.version == 4 and self.subnet.is_private and self.subnet.prefixlen >= 16 and
                ipaddress.ip_address(self.ip) in self.subnet, "bridge_cidr_not_narrow_private_ipv4")
        mounts = json.loads(docker("inspect", "-f", "{{json .Mounts}}", self.identifier))
        volumes = [mount for mount in mounts if mount.get("Destination") == "/var/lib/postgresql/data"]
        require(len(volumes) == 1 and volumes[0].get("Type") == "volume", "database_data_volume_unexpected")
        volume_labels = json.loads(docker("volume", "inspect", "-f", "{{json .Labels}}", volumes[0]["Name"]))
        require(volume_labels.get("com.docker.compose.project") == "otravel", "database_volume_not_project_owned")
        self.password = read_database_password()
        self.receipt = {"container_id": self.identifier, "verified_project": "otravel", "verified_service": "db",
                        "bridge_subnet": str(self.subnet), "target_database": DATABASE, "runtime_database_untouched": True,
                        "test_database_untouched": True, "database_created": False}

    def verify_same(self):
        require(docker("inspect", "-f", "{{.Id}}", "otravel-db-1") == self.identifier and
                docker("inspect", "-f", "{{.State.Running}}", self.identifier) == "true", "database_container_changed_mid_setup")

    def sql(self, query, database="postgres"):
        require(database in {"postgres", DATABASE}, "database_operation_outside_local_eval_scope")
        self.verify_same()
        address = [] if database == "postgres" else ["-h", "127.0.0.1"]
        return docker("exec", "-i", "-e", "PGPASSWORD", self.identifier, "psql", "-X", "-q", "-v", "ON_ERROR_STOP=1",
                      *address, "-U", "otravel", "-d", database, "-A", "-t", stdin=query,
                      env={**os.environ, "PGPASSWORD": self.password})

    def allow_local_database(self):
        self.verify_same()
        read_before = capture(["docker", "exec", self.identifier, "cat", HBA])
        require(read_before.returncode == 0, "hba_snapshot_read_failed")
        before = read_before.stdout
        required = [f"host {DATABASE} otravel 127.0.0.1/32 scram-sha-256",
                    f"host {DATABASE} otravel {self.subnet} scram-sha-256"]
        for raw in before.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            fields = line.split()
            require(fields[1] != "all", "broad_database_hba_rule_requires_owner_review")
            if DATABASE in fields[1].split(","):
                require(line in required, "existing_local_eval_hba_rule_outside_authorized_scope")
        after = before.rstrip() + "\n"
        missing = [line for line in required if line not in before.splitlines()]
        if missing:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup = RUNTIME / ("pg_hba-before-" + timestamp + ".conf")
            with backup.open("x", encoding="utf-8") as stream:
                stream.write(before)
            os.chmod(backup, 0o600)
            after += "# Local Ollama evaluation: exact database/role, private bridge only.\n" + "\n".join(missing) + "\n"
            shell = ("set -eu; p=/var/lib/postgresql/data/pg_hba.conf; "
                     "t=/var/lib/postgresql/data/.pg_hba.local_eval.tmp; test ! -e \"$t\"; "
                     "umask 077; cat > \"$t\"; chown postgres:postgres \"$t\"; chmod 600 \"$t\"; mv \"$t\" \"$p\"")
            self.verify_same()
            docker("exec", "-i", "-u", "0", self.identifier, "sh", "-c", shell, stdin=after)
            self.receipt["hba_before_snapshot"] = str(backup)
        observed_after = capture(["docker", "exec", self.identifier, "cat", HBA])
        require(observed_after.returncode == 0 and observed_after.stdout == after, "hba_written_bytes_mismatch")
        require(self.sql("SELECT pg_reload_conf();\n") == "t", "postgres_hba_reload_not_acknowledged")
        require(self.sql("SELECT count(*) FROM pg_hba_file_rules WHERE error IS NOT NULL;\n") == "0", "hba_parse_error_after_reload")
        self.receipt["hba"] = {"changed": bool(missing), "before_sha256": hashlib.sha256(before.encode()).hexdigest(),
                               "after_sha256": hashlib.sha256(after.encode()).hexdigest(), "rules": required,
                               "reload_acknowledged": True, "postgres_restarted": False}
        snapshots = sorted(RUNTIME.glob("pg_hba-before-*.conf"))
        if snapshots:
            self.receipt["hba_original_before_snapshot"] = str(snapshots[0])
            self.receipt["hba_original_before_sha256"] = hashlib.sha256(snapshots[0].read_bytes()).hexdigest()
        atomic_json(RUNTIME / "database-setup.json", self.receipt)

    def create(self):
        existing = self.sql(f"SELECT pg_get_userbyid(datdba)||'|'||datistemplate||'|'||datallowconn FROM pg_database WHERE datname='{DATABASE}';\n")
        if not existing:
            self.sql(f"CREATE DATABASE {DATABASE} OWNER otravel;\n")
            self.receipt["database_created"] = True
            existing = self.sql(f"SELECT pg_get_userbyid(datdba)||'|'||datistemplate||'|'||datallowconn FROM pg_database WHERE datname='{DATABASE}';\n")
        require(existing == "otravel|false|true", "local_eval_database_identity_mismatch")
        self.receipt["database_oid"] = int(self.sql(f"SELECT oid FROM pg_database WHERE datname='{DATABASE}';\n"))
        atomic_json(RUNTIME / "database-setup.json", self.receipt)

    def counts(self):
        names = {"sessions", "messages", "agent_runs", "agent_events", "quotes", "demo_orders"}
        existing = set(self.sql("SELECT tablename FROM pg_tables WHERE schemaname='public';\n", DATABASE).splitlines())
        counts = {name: int(self.sql(f"SELECT count(*) FROM {name};\n", DATABASE)) for name in sorted(existing & names)}
        if "agent_runs" in existing:
            counts["pending_or_running"] = int(self.sql("SELECT count(*) FROM agent_runs WHERE status IN ('pending','running');\n", DATABASE))
        if "agent_events" in existing:
            counts["model_call_started"] = int(self.sql("SELECT count(*) FROM agent_events WHERE event_type='MODEL_CALL_STARTED';\n", DATABASE))
        return counts


def environment(database):
    env = {"HOME": "/home/aegml", "USER": "aegml", "LOGNAME": "aegml", "LANG": "C.UTF-8",
           "PATH": str(PYTHON.parent) + ":/usr/local/bin:/usr/bin:/bin", "PYTHONPATH": str(SOURCE),
           "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    env.update({key: "" for key in KEYS})
    env.update(DATABASE_URL=f"postgresql+asyncpg://otravel:{quote(database.password, safe='')}@{database.ip}:5432/{DATABASE}",
               DATABASE_HOST=database.ip, DATABASE_PORT="5432", DATABASE_USER="otravel", DATABASE_NAME=DATABASE,
               DATABASE_PASSWORD=database.password, LLM_PROVIDER="ollama", OLLAMA_BASE_URL="http://127.0.0.1:11434",
               OLLAMA_MODEL="qwen3.5:4b", OLLAMA_REQUEST_TIMEOUT="105", OLLAMA_TIMEOUT="105",
               MAX_LOCAL_LLM_CALLS_PER_DAY="500", MAX_LLM_CALLS_PER_DAY="0", MAX_LLM_CALLS_PER_RUN="1",
               GEMINI_FREE_TIER_CONFIRMED="false", SERPAPI_FREE_TIER_CONFIRMED="false", HOTELBEDS_TEST_CONFIRMED="false",
               RATEHAWK_TEST_CONFIRMED="false", LITEAPI_MODE="sandbox", OPENROUTER_BACKUP_MODEL="",
               CORS_ORIGINS="http://localhost:8081,http://127.0.0.1:8081")
    return env


def http(path):
    require(path in {"/health", "/docs", "/debug", "/api/v1/providers/status"}, "non_status_http_forbidden")
    try:
        with OPENER.open(Request(BASE + path, headers={"Accept": "application/json"}), timeout=3) as response:
            body = response.read()
            return response.status, json.loads(body) if path in {"/health", "/api/v1/providers/status"} else None
    except HTTPError as response:
        return response.code, None


def source_hashes():
    """Legacy five-file snapshot; not a complete package fingerprint."""
    return {name: hashlib.sha256((SOURCE / "otravel" / name).read_bytes()).hexdigest() for name in SOURCE_FILES}


def source_manifest(root=None):
    """Complete package source/assets and pyproject, using relative paths only.

    Never traverse environment/runtime trees or follow symlinks. Private hidden
    files and bytecode are not package sources and are excluded before reading.
    """
    root = SOURCE if root is None else Path(root)
    root = root.absolute()
    require(root.is_dir() and not root.is_symlink() and root.resolve() == root, "source_manifest_root_not_canonical")
    package = root / "otravel"
    require(package.is_dir() and not package.is_symlink() and package.resolve() == package, "source_manifest_package_missing_or_symlinked")
    files = []
    for current, directories, names in os.walk(package, followlinks=False):
        current = Path(current)
        kept = []
        for name in sorted(directories):
            if name.startswith(".") or name == "__pycache__":
                continue
            path = current / name
            require(not path.is_symlink() and path.resolve() == path, "source_manifest_symlink_directory_refused")
            kept.append(name)
        directories[:] = kept
        for name in names:
            path = current / name
            if name.startswith(".") or (path.suffix not in SOURCE_SUFFIXES
                    and path.relative_to(package).as_posix() not in PACKAGE_NOTICES):
                continue
            require(not path.is_symlink() and path.is_file() and path.resolve() == path, "source_manifest_symlink_file_refused")
            files.append(path)
    project = root / "pyproject.toml"
    require(project.is_file() and not project.is_symlink() and project.resolve() == project, "source_manifest_pyproject_missing_or_symlinked")
    files.append(project)
    entries = []
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        raw = path.read_bytes()
        entries.append({"path": path.relative_to(root).as_posix(), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    canonical = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"format": MANIFEST_FORMAT, "sha256": hashlib.sha256(canonical).hexdigest(),
            "file_count": len(entries), "files": entries}


def loaded_manifest_status(metadata, current):
    """Legacy/mismatched snapshots cannot attest the loaded source revision."""
    started = metadata.get("source_manifest")
    matches = (isinstance(started, dict) and started.get("format") == MANIFEST_FORMAT
               and isinstance(started.get("files"), list) and bool(started["files"])
               and isinstance(current, dict) and started == current)
    reason = ("exact_complete_start_and_current_match" if matches else
              "legacy_process_has_no_complete_start_manifest" if not isinstance(started, dict) else
              "current_complete_manifest_unavailable" if current is None else
              "complete_start_and_current_manifest_differ")
    return {"status": "MATCHES_FULL_START_MANIFEST" if matches else "UNKNOWN",
            "reason": reason, "matches_current": matches, "restart_required": not matches}


def legacy_snapshots(previous):
    """Preserve old hashes as historical data, without private identity fields."""
    snapshots = list(previous.get("legacy_five_file_snapshots", []))
    historical = {"observed_at_utc": previous.get("checked_at_utc"),
                  "scope": "LEGACY_FIVE_FILES_NOT_FULL_PACKAGE_PROOF"}
    for key in ("source_sha256", "process_start_source_sha256"):
        value = previous.get(key)
        if isinstance(value, dict) and value and set(value) <= set(SOURCE_FILES):
            historical[key] = value
    if len(historical) > 2 and not any(
        item.get("source_sha256") == historical.get("source_sha256") and
        item.get("process_start_source_sha256") == historical.get("process_start_source_sha256")
        for item in snapshots if isinstance(item, dict)
    ):
        snapshots.append(historical)
    return snapshots


def save_evidence(action, account, extra=None):
    identity = recorded_process(account)
    metadata = json.loads(IDENTITY.read_text()) if IDENTITY.is_file() else {}
    try:
        current_manifest = source_manifest()
        manifest_error = None
    except (OSError, Refusal) as exc:
        current_manifest = None
        manifest_error = str(exc) if isinstance(exc, Refusal) else type(exc).__name__
    try:
        legacy_current = source_hashes()
    except OSError:
        legacy_current = None
    record = {"checked_at_utc": now(), "action": action, "source": str(SOURCE), "venv_python": str(PYTHON),
              "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "database": DATABASE, "url": BASE, "process": identity, "listeners": listeners(),
              "no_inference_requested": True, "no_supplier_requests": True,
              "provider_credentials_inherited": False, "source_sha256": legacy_current,
              "source_sha256_scope": "LEGACY_FIVE_FILES_NOT_FULL_PACKAGE_PROOF",
              "process_start_source_sha256": metadata.get("source_sha256", {}),
              "current_source_manifest": current_manifest,
              "process_start_source_manifest": metadata.get("source_manifest"),
              "loaded_manifest": loaded_manifest_status(metadata, current_manifest) if identity else
                  {"status": "NOT_RUNNING", "matches_current": None, "restart_required": False},
              "limits": {"provider": "ollama", "model": "qwen3.5:4b", "request_timeout_seconds": 105,
                         "max_local_calls_per_day": 500, "max_calls_per_run": 1, "max_remote_calls_per_day": 0},
              "limitations": ["Health/docs/debug are not a successful model turn or full acceptance suite.",
                              "No global service/autostart change; only the exact recorded process may be stopped.",
                              "Database and HBA rules are retained by stop; no DROP or runtime/test data mutation."]}
    if manifest_error:
        record["current_source_manifest_error"] = manifest_error
    if action == "start_reused_owned_process":
        record["reuse_full_source_manifest_verified"] = record["loaded_manifest"]["matches_current"] is True
    if identity:
        require_owned_listener(identity)
        observations = {}
        for path in ("/health", "/docs", "/debug", "/api/v1/providers/status"):
            code, payload = http(path)
            observations[path] = {"status": code}
            if path == "/health" and payload:
                observations[path]["database"] = payload.get("database")
            if path == "/api/v1/providers/status" and payload:
                observations[path]["runtime"] = payload.get("_runtime")
                observations[path]["configured_key_providers"] = [key for key in
                    ("gemini", "openrouter", "liteapi", "duffel", "serpapi", "hotelbeds", "ratehawk", "travelpayouts")
                    if payload.get(key, {}).get("configured")]
                observations[path]["ollama"] = payload.get("ollama")
        record["http"] = observations
    if extra:
        record.update(extra)
    if EVIDENCE.exists():
        previous = json.loads(EVIDENCE.read_text())
        record["legacy_five_file_snapshots"] = legacy_snapshots(previous)
        for key in ("database_setup", "database_counts_before", "database_counts_after", "import_check", "setup_corrections", "windows_loopback_health", "helper_guard_checks"):
            if key in previous and key not in record:
                record[key] = previous[key]
        if action != "start" and "startup_observed_at_utc" in previous:
            record["startup_observed_at_utc"] = previous["startup_observed_at_utc"]
    if action == "start":
        record["startup_observed_at_utc"] = record["checked_at_utc"]
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(EVIDENCE, record, 0o644)
    print(json.dumps({"action": action, "status": "RUNNING" if identity else "STOPPED", "url": BASE,
                      "database": DATABASE, "evidence": str(EVIDENCE), "inference_requests": 0}, ensure_ascii=False))
    return record


def start(account):
    stage("checking_owned_listener")
    identity = recorded_process(account)
    observed_listeners = listeners()
    if identity:
        require_owned_listener(identity)
        code, health = http("/health")
        require(code == 200 and isinstance(health, dict) and health.get("database") == "ready",
                "owned_existing_backend_not_ready_process_retained")
        return save_evidence("start_reused_owned_process", account, {"reuse_readiness_verified": True})
    require(not observed_listeners, "port_8083_is_occupied_by_unowned_process_nothing_stopped")
    stage("verifying_database_labels_network_and_credentials")
    database = Database()
    SAFE_CONTEXT["database_setup"] = database.receipt
    env = environment(database)
    initial_sources = source_hashes()
    initial_manifest = source_manifest()
    probe = ("import importlib,json,pathlib,otravel.api; from otravel.config import Settings; "
             "from otravel.providers import LLMGateway; s=Settings.from_env(); "
             "print(json.dumps({'api_file':str(pathlib.Path(otravel.api.__file__).resolve()),'provider':s.llm_mode,"
             "'local_limit':s.max_local_llm_calls_per_day,'run_limit':s.max_llm_calls_per_run}))")
    stage("native_gateway_import")
    imported = capture([str(PYTHON), "-c", probe], env=env, cwd=SOURCE,
                       user=account.pw_uid, group=account.pw_gid, extra_groups=[])
    require(imported.returncode == 0, "native_gateway_import_failed_details_suppressed")
    facts = json.loads(imported.stdout)
    require(facts == {"api_file": str(SOURCE / "otravel" / "api.py"), "provider": "ollama", "local_limit": 500, "run_limit": 1},
            "native_gateway_import_or_limits_mismatch")
    require(source_manifest() == initial_manifest, "native_complete_sources_changed_during_preflight")
    stage("allowing_exact_database_scram_hba_and_reload")
    database.allow_local_database()
    stage("creating_or_verifying_isolated_database")
    database.create()
    stage("checking_existing_local_eval_work")
    before = database.counts()
    require(before.get("pending_or_running", 0) == 0, "local_eval_pending_work_requires_owner_review_before_start")
    connection_probe = ("import asyncio,os,asyncpg; "
                        "exec('async def probe():\\n c=await asyncpg.connect(host=os.environ[\"DATABASE_HOST\"],port=5432,user=\"otravel\",password=os.environ[\"DATABASE_PASSWORD\"],database=\"otravel_local_eval\",timeout=5)\\n assert await c.fetchval(\"SELECT current_database()\")==\"otravel_local_eval\"\\n await c.close()'); "
                        "asyncio.run(probe())")
    stage("probing_isolated_database_from_wsl_user")
    connected = capture([str(PYTHON), "-c", connection_probe], env=env, cwd=SOURCE,
                        user=account.pw_uid, group=account.pw_gid, extra_groups=[])
    require(connected.returncode == 0, "isolated_database_bridge_connection_failed_details_suppressed")
    require(not listeners(), "port_8083_became_occupied_before_start_nothing_stopped")
    require(source_manifest() == initial_manifest, "native_complete_sources_changed_before_launch")
    stage("launching_owned_uvicorn")
    log_fd = os.open(LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        process = subprocess.Popen(ARGV, cwd=SOURCE, env=env, stdin=subprocess.DEVNULL, stdout=log_fd,
                                   stderr=subprocess.STDOUT, close_fds=True, start_new_session=True,
                                   user=account.pw_uid, group=account.pw_gid, extra_groups=[], umask=0o077)
    finally:
        os.close(log_fd)
    identity = process_identity(process.pid)
    require(identity is not None, "owned_backend_exited_immediately_private_log_retained")
    atomic_json(IDENTITY, {**identity, "created_by": "local_backend.py", "created_at_utc": now(),
                           "database": DATABASE, "port": PORT, "source_sha256": initial_sources,
                           "source_sha256_scope": "LEGACY_FIVE_FILES_NOT_FULL_PACKAGE_PROOF",
                           "source_manifest": initial_manifest})
    for _ in range(45):
        require(process.poll() is None, "owned_backend_exited_during_startup_private_log_retained")
        try:
            identity = recorded_process(account)
            require(identity is not None, "owned_process_identity_lost")
            require_owned_listener(identity)
            code, health = http("/health")
            if code == 200 and health.get("database") == "ready":
                break
        except (OSError, Refusal, ValueError):
            pass
        time.sleep(1)
    else:
        raise Refusal("owned_backend_health_timeout_process_retained_for_owner_review")
    require(source_manifest() == initial_manifest, "native_complete_sources_changed_during_startup_process_retained")
    after = database.counts()
    require(after.get("model_call_started", 0) == before.get("model_call_started", 0), "unexpected_model_event_during_status_only_start")
    return save_evidence("start", account, {"database_setup": database.receipt, "database_counts_before": before,
                                           "database_counts_after": after, "import_check": facts})


def stop(account):
    identity = recorded_process(account)
    if identity is None:
        require(not listeners(), "unowned_port_listener_nothing_stopped")
        return save_evidence("stop_already_stopped", account)
    fd = verified_pidfd(identity, lambda: recorded_process(account))
    try:
        require_owned_listener(identity)
        signal.pidfd_send_signal(fd, signal.SIGTERM)
        for _ in range(100):
            if select.select([fd], [], [], 0)[0]:
                saved = json.loads(IDENTITY.read_text())
                require(saved.get("pid") == identity["pid"] and saved.get("start_ticks") == identity["start_ticks"],
                        "recorded_identity_changed_during_owned_stop")
                saved["stopped_at_utc"] = now()
                atomic_json(IDENTITY, saved)
                require(not listeners(), "listener_remained_after_owned_process_exit")
                return save_evidence("stop", account, {"stopped_owned_process": identity, "signal": "SIGTERM", "signal_target": "pidfd"})
            time.sleep(0.1)
    finally:
        os.close(fd)
    raise Refusal("owned_backend_did_not_stop_cleanly_no_force_or_global_stop_attempted")


def windows_dispatch(action):
    import winreg
    base = None
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Lxss") as registry:
        for index in range(winreg.QueryInfoKey(registry)[0]):
            with winreg.OpenKey(registry, winreg.EnumKey(registry, index)) as distro:
                if winreg.QueryValueEx(distro, "DistributionName")[0] == DISTRO:
                    base = winreg.QueryValueEx(distro, "BasePath")[0]
    require(base is not None and str(base).removeprefix("\\\\?\\").lower().startswith("c:"), "distro_not_verified_c_backed")
    script = str(Path(__file__).resolve())
    wsl_script = "/mnt/" + script[0].lower() + script[2:].replace("\\", "/")
    process = subprocess.run(["wsl.exe", "-d", DISTRO, "-u", "root", "--", "python3", wsl_script,
                              "--action", action], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    print(process.stdout, end="")
    if process.stderr:
        print("Local-backend child diagnostic suppressed; consult sanitized evidence/private log.", file=sys.stderr)
    return process.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action", choices=("start", "status", "stop"), required=True)
    action = parser.parse_args().action
    try:
        if sys.platform == "win32":
            return windows_dispatch(action)
        import fcntl
        account = native_paths()
        with (RUNTIME / "controller.lock").open("a") as lock:
            os.chmod(RUNTIME / "controller.lock", 0o600)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            if action == "start":
                start(account)
            elif action == "stop":
                stop(account)
            else:
                save_evidence("status", account)
        return 0
    except Exception as exc:
        reason = str(exc) if isinstance(exc, Refusal) else type(exc).__name__
        failure = {"action": action, "status": "REFUSED_OR_FAILED", "reason": reason, "stage": CURRENT_STAGE,
                   "credentials_printed": False, "no_inference_requested": True, **SAFE_CONTEXT}
        if sys.platform != "win32":
            EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
            if EVIDENCE.exists():
                failure["legacy_five_file_snapshots"] = legacy_snapshots(json.loads(EVIDENCE.read_text()))
            atomic_json(EVIDENCE, failure, 0o644)
        print(json.dumps(failure))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
