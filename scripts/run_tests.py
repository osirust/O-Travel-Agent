"""Run tests on the isolated Compose DB and redact captured output before printing.

Never inherit pytest's verbose driver frames directly: asyncpg can render a
password in ConnectionParameters even when SQLAlchemy masks its engine URL.
"""

import html
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote, quote_plus


NATIVE_ENV = Path("/home/aegml/projects/otravel/.env")
ROOT = Path("/mnt/e/work/aegml")
PYTHON = "/home/aegml/venvs/otravel-harness-20261025/bin/python"


def read_values(path: Path) -> dict[str, str]:
    values = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def redact(text: str, values: dict[str, str] | None = None) -> str:
    variants = set()
    for key, value in (values or {}).items():
        if value and re.search(r"PASSWORD|SECRET|TOKEN|API_?KEY|DATABASE_URL", key, re.I):
            variants.update((value, quote(value, safe=""), quote_plus(value, safe=""),
                             quote(quote(value, safe=""), safe=""), html.escape(value),
                             json.dumps(value, ensure_ascii=False)[1:-1]))
    for value in sorted(variants, key=len, reverse=True):
        text = text.replace(value, "[REDACTED]")
        lower_hex = re.sub(r"%[0-9A-F]{2}", lambda m: m.group().lower(), value)
        text = text.replace(lower_hex, "[REDACTED]")
    text = re.sub(r"((?:postgres(?:ql)?(?:\+[a-z0-9_]+)?|https?)://)[^/@\s]+@",
                  r"\1[REDACTED]@", text, flags=re.I)
    text = re.sub(r"([?&](?:api_?key|key|token|password)=)[^&\s'\"]+",
                  r"\1[REDACTED]", text, flags=re.I)
    text = re.sub(r"((?:password|api_key|access_token|secret)\s*[:=]\s*)(['\"])(.*?)\2",
                  r"\1\2[REDACTED]\2", text, flags=re.I)
    return text


def emit(process: subprocess.CompletedProcess, values: dict[str, str] | None = None) -> int:
    for output in (process.stdout, process.stderr):
        if output:
            print(redact(output, values), end="" if output.endswith("\n") else "\n")
    return process.returncode


def capture(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=False, **kwargs)


def main() -> int:
    values = {}
    try:
        if sys.platform == "win32":
            path = str(Path(__file__).resolve())
            script = "/mnt/" + path[0].lower() + path[2:].replace("\\", "/")
            return emit(capture(["wsl.exe", "-d", "AegML-Hackathon", "-u", "root", "--",
                                 "python3", script, *sys.argv[1:]], timeout=420))
        values = read_values(NATIVE_ENV)
        labels_result = capture(["docker", "inspect", "-f", "{{json .Config.Labels}}", "otravel-db-1"])
        if labels_result.returncode:
            return emit(labels_result, values)
        labels = json.loads(labels_result.stdout)
        if labels.get("com.docker.compose.project") != "otravel" or labels.get("com.docker.compose.service") != "db":
            raise RuntimeError("Refusing database outside the otravel Compose project")
        network_result = capture(["docker", "inspect", "-f", "{{json .NetworkSettings.Networks}}", "otravel-db-1"])
        if network_result.returncode:
            return emit(network_result, values)
        ip = json.loads(network_result.stdout)["otravel_default"]["IPAddress"]
        if not ip:
            raise RuntimeError("The isolated otravel database has no bridge address")
        user = values.get("POSTGRES_USER", "otravel")
        app_database = values.get("POSTGRES_DB", "otravel")
        if user != "otravel" or app_database != "otravel":
            raise RuntimeError("Refusing a database or role outside the otravel test scope")
        # Runtime is always otravel; pytest must never resume or pollute its runs.
        # No fallback to the runtime DB, even if otravel_test has not been created.
        database = "otravel_test"
        password = values["POSTGRES_PASSWORD"]
        env = {**os.environ, "TEST_DATABASE_URL":
               f"postgresql+asyncpg://{user}:{quote(password, safe='')}@{ip}:5432/{database}"}
        # Explicit arguments override config defaults; strip requested local dumps.
        args = [arg for arg in (sys.argv[1:] or ["-q"]) if arg not in {"--showlocals", "-l"}]
        args.extend(["--tb=short", "--no-showlocals"])
        return emit(capture([PYTHON, "-m", "pytest", *args], cwd=ROOT, env=env, timeout=360), values)
    except subprocess.TimeoutExpired:
        print("Test command timed out; partial output suppressed to protect credentials.", file=sys.stderr)
        return 2
    except Exception as exc:
        print(redact(f"Test helper failed ({type(exc).__name__}): {exc}", values), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
