"""Owned, localhost-only Ollama stand for qwen3.5:4b; no cloud credentials.

Windows entry dispatches only to WSL AegML-Hackathon as user aegml. Linux manual
installation uses the pinned official release archive, verifies its published
SHA-256, and safely extracts into a private prefix. No install.sh, systemd,
driver changes, package installs, Docker commands or other models.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
from datetime import datetime, timezone
import hashlib
import io
import json
import os
import re
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import time
import urllib.request


DISTRO = "AegML-Hackathon"
VERSION = "0.40.11"
MODEL = "qwen3.5:4b"
SHA256 = "7bedab90dd122661be78798e9f0edf37fccf0cccf2699d2c58ea1a3afdc04b6d"
ARCHIVE_URL = f"https://github.com/ollama/ollama/releases/download/v{VERSION}/ollama-linux-amd64.tar.zst"
TOOLS = Path("/home/aegml/tools/ollama")
MODELS = Path("/home/aegml/models/ollama")
RELEASE = TOOLS / "releases" / VERSION
BINARY = RELEASE / "bin/ollama"
RUNTIME = TOOLS / "runtime"
EVIDENCE = RUNTIME / "evidence.json"
IDENTITY = RUNTIME / "process.json"
PROBE = RUNTIME / "micro-probe.json"
PULL = RUNTIME / "model-pull.json"
BASE = "http://127.0.0.1:11434"
LIMITS = {"OLLAMA_HOST": "127.0.0.1:11434", "OLLAMA_MODELS": str(MODELS),
          "OLLAMA_NUM_PARALLEL": "1", "OLLAMA_MAX_LOADED_MODELS": "1",
          "OLLAMA_CONTEXT_LENGTH": "16384", "OLLAMA_NO_CLOUD": "1"}


def now():
    return datetime.now(timezone.utc).isoformat()


def emit(stage, **values):
    print(json.dumps({"stage": stage, **values}, ensure_ascii=False), flush=True)


def save(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def api(route, payload=None, timeout=20):
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(BASE + route, data=data, headers={"Content-Type": "application/json"})
    # Loopback is direct; do not accidentally send it through a proxy.
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout) as response:
        return json.load(response)


def process_identity(pid):
    proc = Path("/proc") / str(pid)
    stat = (proc / "stat").read_text()
    fields = stat[stat.rfind(")") + 2:].split()
    ticks = int(fields[19])
    boot_time = next(int(line.split()[1]) for line in Path("/proc/stat").read_text().splitlines() if line.startswith("btime "))
    start = datetime.fromtimestamp(boot_time + ticks / os.sysconf("SC_CLK_TCK"), timezone.utc).isoformat()
    return {"pid": pid, "uid": proc.stat().st_uid, "start_ticks": ticks, "started_at_utc": start,
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "executable": os.readlink(proc / "exe"),
            "cwd": os.readlink(proc / "cwd"),
            "argv": [part.decode() for part in (proc / "cmdline").read_bytes().split(b"\0") if part]}


def owned_process():
    if not IDENTITY.is_file():
        return None
    recorded = json.loads(IDENTITY.read_text())
    try:
        actual = process_identity(recorded["pid"])
    except (OSError, ValueError):
        return None
    if (actual["uid"] == os.getuid() and actual["start_ticks"] == recorded["start_ticks"]
            and actual["boot_id"] == recorded["boot_id"] and actual["argv"] == [str(BINARY), "serve"]):
        return actual
    return None


def port_busy():
    with socket.socket() as connection:
        connection.settimeout(0.5)
        return connection.connect_ex(("127.0.0.1", 11434)) == 0


class ZstdReader(io.RawIOBase):
    """Streaming reader using the existing Ubuntu libzstd, without extra packages."""
    class Buffer(ctypes.Structure):
        _fields_ = [("data", ctypes.c_void_p), ("size", ctypes.c_size_t), ("pos", ctypes.c_size_t)]

    def __init__(self, source):
        self.source = source
        name = ctypes.util.find_library("zstd")
        if not name:
            raise RuntimeError("Existing libzstd not found; archive not executed")
        self.lib = ctypes.CDLL(name)
        self.lib.ZSTD_createDStream.restype = ctypes.c_void_p
        self.lib.ZSTD_initDStream.argtypes = [ctypes.c_void_p]
        self.lib.ZSTD_initDStream.restype = ctypes.c_size_t
        self.lib.ZSTD_decompressStream.argtypes = [ctypes.c_void_p, ctypes.POINTER(self.Buffer), ctypes.POINTER(self.Buffer)]
        self.lib.ZSTD_decompressStream.restype = ctypes.c_size_t
        self.lib.ZSTD_isError.argtypes = [ctypes.c_size_t]
        self.lib.ZSTD_isError.restype = ctypes.c_uint
        self.lib.ZSTD_freeDStream.argtypes = [ctypes.c_void_p]
        self.context = self.lib.ZSTD_createDStream()
        if not self.context or self.lib.ZSTD_isError(self.lib.ZSTD_initDStream(self.context)):
            raise RuntimeError("Could not initialize archive decompressor")
        self.input_data = None
        self.input = self.Buffer(None, 0, 0)
        self.finished = False
        self.frame_complete = False

    def readable(self):
        return True

    def readinto(self, target):
        if self.finished:
            return 0
        output_data = ctypes.create_string_buffer(len(target))
        output = self.Buffer(ctypes.addressof(output_data), len(target), 0)
        while output.pos == 0:
            if self.input.pos == self.input.size:
                chunk = self.source.read(1024 * 1024)
                if not chunk:
                    if not self.frame_complete:
                        raise RuntimeError("Truncated zstd archive")
                    self.finished = True
                    break
                self.input_data = ctypes.create_string_buffer(chunk)
                self.input = self.Buffer(ctypes.addressof(self.input_data), len(chunk), 0)
            remaining = self.lib.ZSTD_decompressStream(self.context, ctypes.byref(output), ctypes.byref(self.input))
            if self.lib.ZSTD_isError(remaining):
                raise RuntimeError("Invalid zstd archive")
            self.frame_complete = remaining == 0
        target[:output.pos] = output_data.raw[:output.pos]
        return output.pos

    def close(self):
        if getattr(self, "context", None):
            self.lib.ZSTD_freeDStream(self.context)
            self.context = None
        super().close()


def install():
    archive = TOOLS / "downloads" / f"ollama-linux-amd64-v{VERSION}.tar.zst"
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.is_file():
        partial = archive.with_suffix(archive.suffix + ".part")
        if partial.exists():
            raise RuntimeError("Archive download is in progress/partial; do not start a second downloader")
        emit("download_runtime", url=ARCHIVE_URL)
        with urllib.request.urlopen(ARCHIVE_URL, timeout=60) as response, partial.open("wb") as output:
            shutil.copyfileobj(response, output, 4 * 1024 * 1024)
        partial.replace(archive)
    with archive.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if digest != SHA256:
        raise RuntimeError("Official archive SHA-256 mismatch; refusing extraction/execution")
    marker = RELEASE / "verified-release.json"
    if not marker.is_file() or not BINARY.is_file():
        RELEASE.mkdir(parents=True, exist_ok=True)
        emit("extract_verified_runtime", version=VERSION, archive_bytes=archive.stat().st_size, sha256=digest)
        with archive.open("rb") as source, io.BufferedReader(ZstdReader(source)) as reader:
            with tarfile.open(fileobj=reader, mode="r|") as package:
                package.extractall(RELEASE, filter="data")
        if not BINARY.is_file():
            raise RuntimeError("Verified archive has no expected bin/ollama")
        save(marker, {"version": VERSION, "sha256": digest, "source": ARCHIVE_URL, "verified_at": now()})
    emit("runtime_installed", version=VERSION, binary=str(BINARY), sha256=digest)


def start():
    if not BINARY.is_file() or not (RELEASE / "verified-release.json").is_file():
        raise RuntimeError("Install/verify the pinned runtime first")
    before = RUNTIME / "before-start.json"
    if not before.is_file():
        save(before, {"observed_at": now(), "port_busy": port_busy(), "recorded_owned_process": owned_process(),
                      "models_path_existed": MODELS.exists(), "model_names_before": [] if not port_busy() else "not_queried_foreign_listener"})
    if port_busy():
        identity = owned_process()
        if not identity:
            detail = subprocess.run(["ss", "-ltnp", "sport", "=", ":11434"], capture_output=True, text=True).stdout.strip()
            emit("occupied_port_not_modified", listener=detail)
            raise RuntimeError("Port 11434 is not the recorded owned process; nothing stopped")
        emit("reuse_owned_server", **identity)
        return
    MODELS.mkdir(parents=True, exist_ok=True)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    environment = {"HOME": "/home/aegml", "USER": "aegml", "LOGNAME": "aegml",
                   "PATH": "/usr/lib/wsl/lib:/usr/local/bin:/usr/bin:/bin",
                   "LD_LIBRARY_PATH": "/usr/lib/wsl/lib", **LIMITS}
    with (RUNTIME / "ollama.log").open("ab", buffering=0) as log:
        process = subprocess.Popen([str(BINARY), "serve"], env=environment, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, cwd=RUNTIME,
                                   start_new_session=True, close_fds=True)
    identity = process_identity(process.pid)
    save(IDENTITY, {**identity, "limits": LIMITS, "created_by": "local_llm_setup.py"})
    for _ in range(60):
        if process.poll() is not None:
            raise RuntimeError("Owned Ollama exited during startup; inspect its private runtime log")
        try:
            version = api("/api/version", timeout=1)
            if version.get("version") != VERSION:
                raise RuntimeError("Unexpected Ollama server version")
            emit("owned_server_ready", **identity, version=version["version"])
            return
        except (OSError, ValueError):
            time.sleep(1)
    raise RuntimeError("Owned server startup did not become ready within 60 seconds")


def restart_owned():
    identity = owned_process()
    if identity is None:
        raise RuntimeError("No matching owned PID/start/argv; refusing any stop")
    # Only this exact owned user process is signalled; never a process search,
    # systemd service, Docker daemon or other distribution.
    os.kill(identity["pid"], signal.SIGTERM)
    for _ in range(100):
        if owned_process() is None and not port_busy():
            retired_path = RUNTIME / "retired-processes.json"
            retired = json.loads(retired_path.read_text()) if retired_path.exists() else []
            retired.append({**identity, "stopped_at_utc": now(), "reason": "explicit_owned_restart"})
            save(retired_path, retired)
            emit("owned_process_stopped", pid=identity["pid"], start_ticks=identity["start_ticks"])
            start()
            return
        time.sleep(0.1)
    raise RuntimeError("Owned process did not stop cleanly; no force/global stop attempted")


def pull():
    if owned_process() is None:
        raise RuntimeError("Only the recorded owned server may receive the model pull")
    if any(item.get("name") == MODEL for item in api("/api/tags").get("models", [])):
        emit("model_already_present", model=MODEL)
        return
    emit("pull_model", model=MODEL)
    started_at = now()
    request = urllib.request.Request(BASE + "/api/pull", data=json.dumps({"model": MODEL, "stream": True}).encode(), headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    last = None
    started = time.monotonic()
    with opener.open(request, timeout=120) as response:
        for line in response:
            item = json.loads(line)
            if item.get("error"):
                raise RuntimeError("Ollama model pull failed: " + str(item["error"])[:180])
            total, completed = item.get("total", 0), item.get("completed", 0)
            bucket = int(completed * 20 / total) * 5 if total else None
            key = (item.get("status"), item.get("digest"), bucket)
            if key != last:
                emit("model_pull_progress", status=item.get("status"), digest=item.get("digest"), percent=bucket,
                     completed=completed, total=total)
                last = key
            if time.monotonic() - started > 1800:
                raise RuntimeError("Model pull exceeded the 30 minute wall-clock budget")
    elapsed = round(time.monotonic() - started, 3)
    save(PULL, {"model": MODEL, "started_at": started_at, "completed_at": now(), "elapsed_seconds": elapsed, "status": "success"})
    emit("model_pull_completed", model=MODEL, elapsed_seconds=elapsed)


def micro_probe():
    if PROBE.is_file():
        emit("reuse_single_probe_record", path=str(PROBE))
        return
    if owned_process() is None:
        raise RuntimeError("Only the recorded owned local server may be probed")
    expected = {"destination": "Казань", "adults": 2, "nights": 3, "source": "Ollama-local"}
    schema = {"type": "object", "properties": {key: {"type": "integer" if type(value) is int else "string", "const": value} for key, value in expected.items()},
              "required": list(expected), "additionalProperties": False}
    record = {"attempted_at": now(), "source": "Ollama-local", "model": MODEL, "synthetic_data_only": True,
              "schema": schema, "request_count": 1, "status": "IN_PROGRESS"}
    save(PROBE, record)  # Durable one-call guard; no silent retry after an interruption.
    started = time.monotonic()
    try:
        answer = api("/api/generate", {"model": MODEL,
            "prompt": "Верни JSON по схеме. Вымышленная поездка: Казань, 2 взрослых, 3 ночи. source строго Ollama-local. Других полей не добавляй.",
            "format": schema, "stream": False, "think": False, "keep_alive": "2m",
            "options": {"num_ctx": 16384, "num_predict": 192, "temperature": 0, "seed": 42}}, timeout=180)
        parsed = json.loads(answer.get("response", ""))
        valid = answer.get("done") is True and parsed == expected and all(type(parsed[key]) is type(value) for key, value in expected.items())
        record.update(status="PASS_MICRO_SCHEMA_ONLY" if valid else "FAILED_MICRO_SCHEMA", response_facts=parsed,
                      latency_seconds=round(time.monotonic() - started, 3),
                      timing={key: answer.get(key) for key in ("total_duration", "load_duration", "prompt_eval_count", "prompt_eval_duration", "eval_count", "eval_duration", "done_reason")},
                      ps_after=api("/api/ps").get("models", []))
        processor = subprocess.run([str(BINARY), "ps"], env={"HOME": "/home/aegml", "PATH": "/usr/bin:/bin", "OLLAMA_HOST": LIMITS["OLLAMA_HOST"]}, capture_output=True, text=True)
        record["processor_cli"] = processor.stdout.strip() if processor.returncode == 0 else "not_observed"
    except Exception as error:
        record.update(status="FAILED_MICRO_SCHEMA", error_type=type(error).__name__, latency_seconds=round(time.monotonic() - started, 3))
    save(PROBE, record)
    emit("single_micro_probe_completed", status=record["status"], latency_seconds=record.get("latency_seconds"))


def status():
    identity = owned_process()
    if identity:
        raw_environment = (Path("/proc") / str(identity["pid"]) / "environ").read_bytes()
        identity["observed_limits"] = {key.decode(): value.decode() for pair in raw_environment.split(b"\0") if b"=" in pair
                                       for key, value in [pair.split(b"=", 1)] if key.decode() in LIMITS}
    result = {"observed_at": now(), "source": "Ollama-local", "ollama_version_requested": VERSION,
              "binary_prefix": str(RELEASE), "models_path": str(MODELS), "host": "127.0.0.1:11434",
              "official_sources": ["https://docs.ollama.com/linux", f"https://github.com/ollama/ollama/releases/tag/v{VERSION}",
                  f"https://github.com/ollama/ollama/releases/download/v{VERSION}/sha256sum.txt", "https://ollama.com/library/qwen3.5:4b", "https://docs.ollama.com/api/generate"],
              "archive_sha256": SHA256, "limits": LIMITS, "process": identity,
              "autostart_configured": False, "cloud_credentials_inherited": False,
              "limitations": ["A single synthetic schema probe is not full harness or 106-case NLU acceptance.",
                               "API main configuration was not changed.", "Loopback binding is not a Docker bridge exposure."]}
    initial = RUNTIME / "before-start.json"
    if initial.is_file():
        result["before_start"] = json.loads(initial.read_text())
    retired = RUNTIME / "retired-processes.json"
    if retired.is_file():
        result["retired_owned_processes"] = json.loads(retired.read_text())
    archive = TOOLS / "downloads" / f"ollama-linux-amd64-v{VERSION}.tar.zst"
    if archive.is_file():
        result["runtime_archive_bytes"] = archive.stat().st_size
        result["runtime_archive_source"] = ARCHIVE_URL
    if (RELEASE / "verified-release.json").is_file():
        result["release_verification"] = json.loads((RELEASE / "verified-release.json").read_text())
    storage = subprocess.run(["du", "-s", "-B1", str(TOOLS), str(MODELS)], capture_output=True, text=True)
    result["disk_allocated_bytes"] = {parts[1]: int(parts[0]) for line in storage.stdout.splitlines() if len(parts := line.split(maxsplit=1)) == 2}
    gpu = subprocess.run(["/usr/lib/wsl/lib/nvidia-smi", "--query-gpu=name,memory.total,memory.free,driver_version", "--format=csv,noheader"], capture_output=True, text=True)
    result["nvidia_smi"] = {"exit_code": gpu.returncode, "gpu_rows": gpu.stdout.strip().splitlines() if gpu.returncode == 0 else []}
    if identity:
        result["server"] = api("/api/version")
        result["model_tags"] = api("/api/tags").get("models", [])
        if any(model.get("name") == MODEL for model in result["model_tags"]):
            shown = api("/api/show", {"model": MODEL})
            result["model_show"] = {key: shown.get(key) for key in ("details", "capabilities", "modified_at")}
            result["model_info"] = {key: value for key, value in shown.get("model_info", {}).items() if key in {"general.architecture", "general.parameter_count", "qwen35.context_length"}}
    if PROBE.is_file():
        result["micro_probe"] = json.loads(PROBE.read_text())
        loaded = result["micro_probe"].get("ps_after", [])
        result["compute_observed"] = "GPU" if any(model.get("size_vram", 0) > 0 for model in loaded) else "CPU" if loaded else "NOT_OBSERVED"
    if PULL.is_file():
        result["model_pull"] = json.loads(PULL.read_text())
    listeners = subprocess.run(["ss", "-H", "-ltnp", "sport", "=", ":11434"], capture_output=True, text=True)
    result["observed_listeners"] = [{"local_address": fields[3], "pid": int(match[1]) if (match := re.search(r"pid=(\d+)", line)) else None}
                                    for line in listeners.stdout.splitlines() if len(fields := line.split()) >= 5]
    save(EVIDENCE, result)
    emit("status_saved", path=str(EVIDENCE), model=MODEL, compute=result.get("compute_observed", "NOT_OBSERVED"))
    return result


def windows_dispatch(action):
    import winreg
    base = None
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Lxss") as registry:
        for index in range(winreg.QueryInfoKey(registry)[0]):
            with winreg.OpenKey(registry, winreg.EnumKey(registry, index)) as distro:
                if winreg.QueryValueEx(distro, "DistributionName")[0] == DISTRO:
                    base = winreg.QueryValueEx(distro, "BasePath")[0]
    if base is None or not str(base).removeprefix("\\\\?\\").lower().startswith("c:"):
        raise RuntimeError("Refusing a distribution whose VHD base is not on C")
    native_script = (TOOLS / "local_llm_setup.py").as_posix()
    command = ["wsl.exe", "-d", DISTRO, "-u", "aegml", "--"]
    copy_code = "import pathlib,sys;p=pathlib.Path(sys.argv[1]);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(sys.stdin.buffer.read())"
    subprocess.run([*command, "python3", "-c", copy_code, native_script], input=Path(__file__).read_bytes(), check=True)
    outcome = subprocess.run([*command, "python3", native_script, "--action", action], check=False)
    if outcome.returncode == 0:
        report = subprocess.run([*command, "cat", EVIDENCE.as_posix()], capture_output=True, check=True)
        evidence = json.loads(report.stdout)
        evidence["wsl_distribution"] = DISTRO
        evidence["windows_vhd_base_path"] = base
        evidence["windows_c_free_bytes"] = shutil.disk_usage("C:\\").free
        target = Path(__file__).resolve().parents[1] / "docs/verification/local-llm-20261025.json"
        target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        emit("windows_evidence_saved", path=str(target))
    return outcome.returncode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=("install", "start", "restart-owned", "pull", "probe", "status", "setup"), default="status")
    action = parser.parse_args().action
    if os.name == "nt":
        return windows_dispatch(action)
    if os.getuid() != 1000 or Path.home() != Path("/home/aegml"):
        raise RuntimeError("Run only as the dedicated aegml user")
    for path in (TOOLS, MODELS):
        if not path.resolve().is_relative_to(Path("/home/aegml")):
            raise RuntimeError("Refusing non-native model/runtime path")
    os.umask(0o077)
    if action in {"install", "setup"}:
        install()
    if action in {"start", "setup"}:
        start()
    if action == "restart-owned":
        restart_owned()
    if action in {"pull", "setup"}:
        pull()
    if action in {"probe", "setup"}:
        micro_probe()
    status()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        emit("setup_failed", error_type=type(error).__name__, message=str(error)[:250])
        raise SystemExit(2) from None
