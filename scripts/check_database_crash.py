"""Authorized SIGKILL recovery check in a NEW, label-verified Compose project.

Never operates on otravel's containers/volumes. No provider keys are loaded.
The temporary internal network has no vendor egress. All created resources are
verified by Compose project + a per-run owner nonce before mutation/cleanup.
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
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4


PROJECT = "otravel-recoverycheck-20261025"
OWNER_LABEL = "io.otravel.crashcheck.owner"
PORT = 8082
KEYS = ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY", "LITEAPI_API_KEY",
        "DUFFEL_ACCESS_TOKEN", "SERPAPI_API_KEY", "HOTELBEDS_API_KEY", "HOTELBEDS_API_SECRET",
        "RATEHAWK_KEY_ID", "RATEHAWK_API_KEY", "TRAVELPAYOUTS_API_TOKEN")


class CheckFailure(Exception):
    pass


def require(condition, code):
    if not condition:
        raise CheckFailure(code)


class RecoveryCheck:
    def __init__(self, output):
        self.output = output
        self.owner = str(uuid4())
        self.volume = PROJECT + "_pgdata"
        self.network = PROJECT + "_isolated"
        self.workdir = None
        self.compose = None
        self.owned = {}
        self.created = False
        self.private = set()
        self.local_http = 0
        self.opener = build_opener(ProxyHandler({}))
        self.api_base = f"http://127.0.0.1:{PORT}"
        self.assertion_failures = []
        self.report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "project": PROJECT,
                       "finding_id": "HARNESS-007",
                       "scope": "new_temporary_internal_compose_project", "status": "RUNNING",
                       "checks": [], "external_api_calls": 0, "llm_calls": 0,
                       "main_project_mutations": 0, "existing_volumes_deleted": False,
                       "session_tokens_recorded": False, "secrets_recorded": False}

    def save(self):
        self.report["local_http_attempts"] = self.local_http
        self.output.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.report, ensure_ascii=False, indent=2) + "\n"
        require(all(value not in text for value in self.private), "secret_in_evidence_blocked")
        temporary = self.output.with_suffix(".tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, self.output)

    def stage(self, name, **facts):
        self.report["stage"] = name
        if facts:
            self.report["checks"].append({"name": name, **facts})
        self.save()

    def docker(self, *args, check=True, stdin=None, env=None):
        result = subprocess.run(["docker", *args], input=stdin, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", check=False, env=env, timeout=120)
        if check and result.returncode:
            raise CheckFailure("docker_operation_failed_" + str(result.returncode))
        return result

    def inspect(self, container, expression):
        return self.docker("inspect", "-f", expression, container).stdout.strip()

    def owned_container(self, service):
        name = PROJECT + "-" + service + "-1"
        labels = json.loads(self.inspect(name, "{{json .Config.Labels}}"))
        identifier = self.inspect(name, "{{.Id}}")
        require(labels.get("com.docker.compose.project") == PROJECT and
                labels.get("com.docker.compose.service") == service and labels.get(OWNER_LABEL) == self.owner,
                "container_ownership_mismatch")
        require(identifier not in self.report["main_container_ids"].values(), "main_container_forbidden")
        if service in self.owned:
            require(identifier == self.owned[service], "temporary_container_identity_changed")
        self.owned[service] = identifier
        return identifier

    def http(self, method, path, token=None, body=None, timeout=15, allow_non_json=False):
        require("/live/" not in path, "live_route_forbidden")
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.local_http += 1
        request = Request(self.api_base + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=timeout) as response:
                status, raw = response.status, response.read()
        except HTTPError as response:
            status, raw = response.code, response.read()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError):
            if allow_non_json:
                return status, {"response_format": "non_json", "bytes": len(raw),
                                "body_sha256": hashlib.sha256(raw).hexdigest(),
                                "standard_internal_server_error": raw.strip() == b"Internal Server Error"}
            raise CheckFailure("local_api_non_json") from None
        return status, payload

    def check_health_paths(self, app_id):
        """Keep separate evidence for app-local, WSL-published and bridge routes."""
        local_probe = (
            "import json,urllib.request,urllib.error; "
            "r=urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=5); "
            "p=json.loads(r.read()); print(json.dumps({'http_status':r.status,'database':p.get('database')}))"
        )
        inside = self.docker("exec", app_id, "python", "-c", local_probe, check=False)
        try:
            inside_fact = json.loads(inside.stdout) if inside.returncode == 0 else {"error": "owned_app_probe_failed"}
        except ValueError:
            inside_fact = {"error": "owned_app_probe_unparseable"}
        bridge_base = self.api_base
        try:
            self.api_base = f"http://127.0.0.1:{PORT}"
            code, body = self.http("GET", "/health", timeout=3, allow_non_json=True)
            published = {"http_status": code, "database": body.get("database"), "response_format": body.get("response_format", "json")}
        except Exception as exc:
            published = {"error_type": type(exc).__name__}
        finally:
            self.api_base = bridge_base
        code, body = self.http("GET", "/health", timeout=5)
        self.report["health_route_comparison"] = {
            "inside_owned_app_loopback": inside_fact,
            "wsl_host_published_loopback": published,
            "wsl_to_verified_owned_app_bridge": {"http_status": code, "database": body.get("database")},
            "interpretation": "Endpoint observations only; no causal NAT diagnosis or Windows-port qualification"}
        source_probe = (
            "import hashlib,json,pathlib,sysconfig; "
            "p=pathlib.Path(sysconfig.get_paths()['purelib'])/'otravel'/'api.py'; s=p.read_bytes(); "
            "print(json.dumps({'api_sha256':hashlib.sha256(s).hexdigest(),"
            "'has_database_error_handler':b'async def database_error' in s,"
            "'handles_connection_error':b'exception_handler(ConnectionError)' in s,"
            "'handles_timeout_error':b'exception_handler(TimeoutError)' in s,"
            "'handles_gaierror':b'exception_handler(socket.gaierror)' in s}))"
        )
        source = self.docker("exec", app_id, "python", "-c", source_probe, check=False)
        if source.returncode == 0:
            self.report["pinned_image_api_source"] = json.loads(source.stdout)

    def app_error_summary(self):
        logs = self.docker("logs", "--since", self.kill_time, self.owned_container("app"), check=False)
        text = logs.stdout + logs.stderr
        # Retain types and known flags, never raw traceback/SQL/HTTP text.
        text = re.sub(r"\x1b\[[0-9;]*m", "", text)
        exception_types = sorted(set(re.findall(r"(?:^|\n)[ \t|+]*([A-Za-z_][A-Za-z0-9_.]*(?:error|exception)):", text, re.IGNORECASE)))
        return {"exception_types": exception_types, "traceback_present": "Traceback (most recent call last)" in text,
                "connection_refused": "Connection refused" in text or "Connect call failed" in text,
                "connection_closed": "connection was closed" in text or "connection is closed" in text,
                "name_resolution_failure": "Name or service not known" in text or "Temporary failure in name resolution" in text,
                "raw_logs_saved": False}

    def healthy(self, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                code, body = self.http("GET", "/health", timeout=3)
                if code == 200 and body.get("database") == "ready":
                    return body
            except Exception:
                pass
            time.sleep(1)
        raise CheckFailure("temporary_backend_health_timeout")

    def run_done(self, run_id, token):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            code, run = self.http("GET", "/api/v1/runs/" + run_id, token)
            require(code == 200, "run_read_failed")
            if run.get("status") == "completed":
                return run
            require(run.get("status") in {"pending", "running"}, "demo_run_failed")
            time.sleep(0.1)
        raise CheckFailure("demo_run_timeout")

    @staticmethod
    def plans(value):
        if isinstance(value, dict):
            if isinstance(value.get("plans"), list):
                return value["plans"]
            for key in ("result", "run"):
                found = RecoveryCheck.plans(value.get(key))
                if found:
                    return found
        return []

    def sql(self, query):
        identifier = self.owned_container("db")
        result = self.docker("exec", "-i", "-e", "PGPASSWORD", identifier,
                             "psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-U", "otravel", "-d", "otravel", "-A", "-t",
                             stdin=query, env={**os.environ, "PGPASSWORD": self.password})
        return result.stdout.strip()

    def prepare(self):
        self.stage("preflight")
        require(not self.docker("ps", "-aq", "--filter", "label=com.docker.compose.project=" + PROJECT).stdout.strip(), "project_already_exists")
        require(self.docker("volume", "inspect", self.volume, check=False).returncode != 0, "volume_already_exists")
        require(self.docker("network", "inspect", self.network, check=False).returncode != 0, "network_already_exists")
        with socket.socket() as port_check:
            port_check.bind(("127.0.0.1", PORT))
        self.report["main_container_ids"] = {service: self.inspect("otravel-"+service+"-1", "{{.Id}}") for service in ("app", "db")}
        images = {service: self.inspect("otravel-"+service+"-1", "{{.Image}}") for service in ("app", "db")}
        require(all(value.startswith("sha256:") for value in images.values()), "immutable_image_id_missing")
        self.report["immutable_image_ids"] = images
        self.password = secrets.token_urlsafe(36)
        self.private.add(self.password)
        self.workdir = Path(tempfile.mkdtemp(prefix=PROJECT+"-", dir="/home/aegml/projects"))
        os.chmod(self.workdir, 0o700)
        self.compose = self.workdir / "compose.json"
        app_env = {key: "" for key in KEYS}
        app_env.update(DATABASE_URL="", DATABASE_HOST="db", DATABASE_PORT="5432", DATABASE_USER="otravel",
                       DATABASE_NAME="otravel", DATABASE_PASSWORD=self.password,
                       LLM_PROVIDER="none", OPENROUTER_BACKUP_MODEL="", MAX_LLM_CALLS_PER_DAY="0",
                       MAX_LLM_CALLS_PER_RUN="0", GEMINI_FREE_TIER_CONFIRMED="false", SERPAPI_FREE_TIER_CONFIRMED="false",
                       HOTELBEDS_TEST_CONFIRMED="false", RATEHAWK_TEST_CONFIRMED="false", CORS_ORIGINS="")
        labels = {OWNER_LABEL: self.owner}
        config = {"name": PROJECT, "services": {
            "db": {"image": images["db"], "pull_policy": "never", "restart": "no", "labels": labels,
                   "environment": {"POSTGRES_DB": "otravel", "POSTGRES_USER": "otravel", "POSTGRES_PASSWORD": self.password,
                                   "POSTGRES_INITDB_ARGS": "--auth-host=scram-sha-256 --auth-local=scram-sha-256"},
                   "volumes": ["pgdata:/var/lib/postgresql/data"], "networks": ["isolated"],
                   "healthcheck": {"test": ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U otravel -d otravel"], "interval": "1s", "timeout": "3s", "retries": 60}},
            "app": {"image": images["app"], "pull_policy": "never", "restart": "no", "labels": labels,
                    "environment": app_env, "ports": [f"127.0.0.1:{PORT}:8080"], "networks": ["isolated"],
                    "depends_on": {"db": {"condition": "service_healthy"}}}},
            "volumes": {"pgdata": {"name": self.volume, "labels": labels}},
            "networks": {"isolated": {"name": self.network, "internal": True, "labels": labels}}}
        self.compose.write_text(json.dumps(config), encoding="utf-8")
        os.chmod(self.compose, 0o600)
        self.created = True  # Track partial resources even when compose up fails.
        self.stage("starting_temporary_project")
        result = self.docker("compose", "-p", PROJECT, "-f", str(self.compose), "up", "-d", check=False)
        require(result.returncode == 0, "temporary_compose_start_failed")
        app_id = self.owned_container("app"); self.owned_container("db")
        require(self.docker("network", "inspect", "-f", "{{.Internal}}", self.network).stdout.strip() == "true", "network_egress_not_isolated")
        network_labels = json.loads(self.docker("network", "inspect", "-f", "{{json .Labels}}", self.network).stdout)
        require(network_labels.get(OWNER_LABEL) == self.owner and network_labels.get("com.docker.compose.project") == PROJECT,
                "temporary_network_ownership_mismatch")
        attached = json.loads(self.inspect(app_id, "{{json .NetworkSettings.Networks}}"))
        require(set(attached) == {self.network}, "unexpected_app_network")
        bridge_ip = ipaddress.ip_address(attached[self.network]["IPAddress"])
        subnet = json.loads(self.docker("network", "inspect", "-f", "{{json .IPAM.Config}}", self.network).stdout)[0]["Subnet"]
        require(bridge_ip in ipaddress.ip_network(subnet), "app_bridge_address_mismatch")
        # This WSL daemon's published loopback path is separately unqualified.
        # Access only our verified app endpoint on its no-egress private bridge.
        self.api_base = f"http://{bridge_ip}:8080"
        self.report["local_api_access"] = "WSL to verified temporary internal-bridge app address"
        self.report["windows_published_port_qualified"] = False
        previous = self.output.with_name("database-crash-recovery-20261025-attempt1.json")
        if previous.exists():
            self.report["previous_pre_crash_attempt"] = previous.name
        self.report["previous_attempts"] = sorted(path.name for path in self.output.parent.glob("database-crash-recovery-20261025-attempt*.json"))
        self.healthy()
        self.check_health_paths(app_id)
        self.stage("temporary_health_ready", passed=True, port=PORT, internal_network=True, provider_keys_blank=True,
                   llm_mode="none", immutable_images=True)

    def exercise(self):
        code, session = self.http("POST", "/api/v1/sessions", body={})
        require(code == 201, "session_create_failed")
        token = session.pop("session_token")
        self.private.add(token)
        sid = session["session_id"]
        path = "/api/v1/sessions/" + sid
        code, accepted = self.http("POST", path+"/demo", token, {"mode": "DEMO", "expected_version": 0,
                                                               "client_message_id": str(uuid4())})
        require(code == 202, "demo_intake_failed")
        shown = self.plans(self.run_done(accepted["run_id"], token))
        require(bool(shown) and shown[0]["source_type"] == "DEMO", "demo_plan_missing")
        code, snapshot = self.http("GET", path, token)
        require(code == 200, "session_snapshot_failed")
        code, quote = self.http("POST", path+"/checkout/preview", token,
                                {"plan_id": shown[0]["plan_id"], "state_version": snapshot["state_version"]})
        require(code == 200, "quote_preview_failed")
        confirmation = {"quote_id": quote["quote_id"], "quote_version": quote["quote_version"],
                        "state_version": snapshot["state_version"], "idempotency_key": str(uuid4()), "confirmed": True}
        code, order = self.http("POST", path+"/checkout/confirm", token, confirmation)
        require(code == 200 and order.get("is_demo") is True and order.get("booking_created_at_supplier") is False, "demo_order_failed")
        code, paused = self.http("POST", path+"/control", token, {"action": "pause"})
        require(code == 200 and paused.get("waiting_reason") == "user_pause", "pause_failed")
        _, before_messages = self.http("GET", path+"/messages", token)
        _, before_events = self.http("GET", path+"/events", token)
        self.stage("persisted_before_crash", passed=True, session_version=paused["state_version"],
                   status=paused["status"], waiting_reason=paused["waiting_reason"], orders=1,
                   trip_sha256=hashlib.sha256(json.dumps(paused["trip"], sort_keys=True).encode()).hexdigest())
        db = self.owned_container("db")
        mounts = json.loads(self.inspect(db, "{{json .Mounts}}"))
        require(any(m.get("Name") == self.volume and m.get("Destination") == "/var/lib/postgresql/data" for m in mounts), "temporary_volume_mismatch")
        self.kill_time = datetime.now(timezone.utc).isoformat()
        self.stage("killing_only_owned_database")
        self.docker("kill", "--signal=KILL", db)
        state = json.loads(self.inspect(db, "{{json .State}}"))
        require(not state["Running"] and state["ExitCode"] == 137 and not state["OOMKilled"], "expected_sigkill_exit_missing")
        self.stage("owned_database_sigkilled", passed=True, signal="SIGKILL", exit_code=state["ExitCode"], oom_killed=False)
        rejected_id = str(uuid4())
        self.stage("checking_intake_while_database_down")
        code, rejected = self.http("POST", path+"/messages", token,
                                   {"client_message_id": rejected_id, "expected_version": paused["state_version"],
                                    "text": "Crash-test input: must not be partially persisted."}, timeout=85, allow_non_json=True)
        intake_pass = code == 503 and rejected.get("error", {}).get("code") == "database_unavailable"
        if not intake_pass:
            self.assertion_failures.append("down_database_intake_not_safe_503")
        self.stage("down_intake_rejected", passed=intake_pass, http_status=code,
                   error_code=rejected.get("error", {}).get("code"), response_format=rejected.get("response_format", "json"),
                   standard_internal_server_error=rejected.get("standard_internal_server_error", False),
                   app_error_summary=self.app_error_summary())
        self.owned_container("db")
        self.docker("start", db)
        self.healthy()
        log_result = self.docker("logs", "--since", self.kill_time, db)
        logs = log_result.stdout + log_result.stderr
        wal = {"interrupted": "database system was interrupted" in logs,
               "automatic_recovery": "automatic recovery in progress" in logs,
               "redo_started": "redo starts at" in logs, "redo_finished": "redo done at" in logs,
               "ready": "database system is ready to accept connections" in logs}
        require(wal["interrupted"] and wal["automatic_recovery"] and wal["ready"], "wal_recovery_evidence_missing")
        code, restored = self.http("GET", path, token)
        require(code == 200 and restored["trip"] == paused["trip"] and restored["state_version"] == paused["state_version"], "trip_or_version_not_restored")
        require(restored["status"] == paused["status"] and restored["waiting_reason"] == "user_pause", "pause_not_restored")
        _, messages_after = self.http("GET", path+"/messages", token)
        require(messages_after == before_messages, "partial_message_persisted")
        counts = self.sql("SELECT (SELECT count(*) FROM messages WHERE client_message_id='"+rejected_id+"'), (SELECT count(*) FROM agent_runs WHERE client_message_id='"+rejected_id+"'), (SELECT count(*) FROM demo_orders), (SELECT count(*) FROM agent_events WHERE event_type='MODEL_CALL_STARTED');\n")
        require(counts == "0|0|1|0", "partial_run_order_or_model_event_after_recovery")
        code, repeated = self.http("POST", path+"/checkout/confirm", token, confirmation)
        require(code == 200 and repeated["order_id"] == order["order_id"], "idempotency_not_restored")
        code, saved_orders = self.http("GET", path+"/orders", token)
        require(code == 200 and len(saved_orders["orders"]) == 1, "order_lost_or_duplicated")
        _, events_after = self.http("GET", path+"/events", token)
        require(events_after == before_events, "recovery_or_replay_duplicated_events")
        self.stage("recovered_after_sigkill", passed=True, wal_recovery=wal, token_still_accepted=True,
                   trip_and_version_equal=True, user_pause_retained=True, rejected_messages=0, rejected_runs=0,
                   orders=1, duplicate_confirmation_status=200, same_order=True, model_attempts=0)

    def cleanup(self):
        if not self.created:
            self.report["cleanup"] = {"required": False, "preexisting_resources_untouched": True}
            return
        removed = []
        for service in ("app", "db"):
            name = PROJECT+"-"+service+"-1"
            if self.docker("inspect", name, check=False).returncode != 0:
                continue
            identifier = self.owned_container(service)
            if self.inspect(identifier, "{{.State.Running}}") == "true":
                self.docker("stop", "--time", "5", identifier)
            self.docker("rm", identifier)
            removed.append(service)
        for kind, name in (("volume", self.volume), ("network", self.network)):
            if self.docker(kind, "inspect", name, check=False).returncode != 0:
                continue
            labels = json.loads(self.docker(kind, "inspect", "-f", "{{json .Labels}}", name).stdout)
            require(labels.get(OWNER_LABEL) == self.owner and labels.get("com.docker.compose.project") == PROJECT, "cleanup_resource_ownership_mismatch")
            self.docker(kind, "rm", name)
            removed.append(kind)
        if self.compose and self.compose.exists():
            require(self.compose.parent == self.workdir and self.workdir.name.startswith(PROJECT+"-"), "cleanup_directory_mismatch")
            self.compose.unlink()
            self.workdir.rmdir()  # Fails safely if an unexpected file exists.
        main_db = self.inspect("otravel-db-1", "{{.Id}}")
        require(main_db == self.report["main_container_ids"]["db"] and self.inspect(main_db, "{{.State.Running}}") == "true", "main_database_changed")
        self.report["cleanup"] = {"complete": True, "removed_owned_resources": removed,
                                  "main_database_same_running_container": True}

    def run(self):
        try:
            self.prepare()
            self.exercise()
            self.report["status"] = "FAIL" if self.assertion_failures else "PASS"
            if self.assertion_failures:
                self.report["failed_assertions"] = self.assertion_failures
        except Exception as exc:
            self.report.update(status="FAIL", error=str(exc) if isinstance(exc, CheckFailure) else type(exc).__name__)
        finally:
            try:
                self.cleanup()
            except Exception as exc:
                self.report["cleanup"] = {"complete": False, "error": str(exc) if isinstance(exc, CheckFailure) else type(exc).__name__}
                self.report["status"] = "FAIL"
            self.report["limitations"] = ["Одна контролируемая точка SIGKILL после committed order/pause, не полный fault matrix",
                                          "Нет проверки Windows reboot, основной БД или frontend", "Локальный HTTP проверен из собственной WSL",
                                          "Только DEMO; поставщики и LLM недоступны в internal network"]
            self.save()
        print("database_crash_recovery=" + self.report["status"] + "; evidence=" + str(self.output))
        return 0 if self.report["status"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-owned-crash-test", action="store_true")
    args = parser.parse_args()
    if not args.confirm_owned_crash_test:
        print("Not run. --confirm-owned-crash-test explicitly permits SIGKILL only in the new temporary project.")
        return 2
    if sys.platform == "win32":
        with socket.socket() as port_check:
            port_check.bind(("127.0.0.1", PORT))
        here = str(Path(__file__).resolve())
        script = "/mnt/" + here[0].lower() + here[2:].replace("\\", "/")
        process = subprocess.run(["wsl.exe", "-d", "AegML-Hackathon", "-u", "root", "--", "python3", script,
                                  "--confirm-owned-crash-test"], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", check=False)
        # Child emits fixed stage/failure codes only, never provider/env/HTTP data.
        print(process.stdout, end="")
        if process.stderr:
            print("Recovery-check child diagnostic suppressed; consult sanitized evidence.", file=sys.stderr)
        return process.returncode
    output = Path("/mnt/e/work/aegml/docs/verification/database-crash-recovery-20261025.json")
    return RecoveryCheck(output).run()


if __name__ == "__main__":
    raise SystemExit(main())
