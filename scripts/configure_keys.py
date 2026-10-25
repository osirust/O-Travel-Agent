"""Masked local API-key entry. Secrets travel via stdin, never command arguments."""

import argparse
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile

PROJECT = Path("/home/aegml/projects/otravel")
KEYS = {
    "GEMINI_API_KEY": "Google AI Studio (только проект без billing)",
    "OPENROUTER_API_KEY": "OpenRouter (будет использоваться только :free)",
    "LITEAPI_API_KEY": "LiteAPI sandbox",
    "DUFFEL_ACCESS_TOKEN": "Duffel test token (duffel_test_...)",
    "SERPAPI_API_KEY": "SerpApi Free",
    "HOTELBEDS_API_KEY": "Hotelbeds test API key, если выдан",
    "HOTELBEDS_API_SECRET": "Hotelbeds test API secret, если выдан",
    "RATEHAWK_KEY_ID": "RateHawk test key ID, если выдан",
    "RATEHAWK_API_KEY": "RateHawk test API key, если выдан",
    "TRAVELPAYOUTS_API_TOKEN": "Travelpayouts token, если выдан",
}
ALLOWED = set(KEYS) | {"GEMINI_FREE_TIER_CONFIRMED", "SERPAPI_FREE_TIER_CONFIRMED", "HOTELBEDS_TEST_CONFIRMED", "RATEHAWK_TEST_CONFIRMED", "LLM_PROVIDER", "OPENROUTER_BACKUP_MODEL"}


def write_env(payload: dict) -> None:
    if not PROJECT.is_dir() or (PROJECT / ".env").is_symlink():
        raise ValueError("Нативная копия проекта не готова или .env является ссылкой.")
    if not isinstance(payload, dict) or any(k not in ALLOWED for k in payload):
        raise ValueError("Неизвестная настройка.")
    for name, value in payload.items():
        if name == "OPENROUTER_BACKUP_MODEL" and value == "":
            continue
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:/+=@-]{1,4096}", value):
            raise ValueError("Настройка содержит неподдерживаемые символы.")
    backup = payload.get("OPENROUTER_BACKUP_MODEL", "")
    if backup and not backup.endswith(":free"):
        raise ValueError("Резервная модель должна быть явно бесплатной (:free).")
    # Both the OAuth callback and masked entry may update this file. Hold the
    # lock across read/replace so a concurrent save cannot discard another key.
    import fcntl
    with (PROJECT / ".env.lock").open("a", encoding="utf-8") as lock:
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        _replace_env(payload)


def _replace_env(payload: dict) -> None:
    values = {}
    path = PROJECT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                values[k] = v
    values.setdefault("POSTGRES_PASSWORD", secrets.token_urlsafe(32))
    values.setdefault("APP_PORT", "8081")
    values.setdefault("LLM_PROVIDER", "gemini")
    values.setdefault("GEMINI_MODEL", "gemini-3.8-flash")
    values.setdefault("OPENROUTER_MODEL", "google/gemma-4-31b-it:free")
    values.setdefault("GEMINI_FREE_TIER_CONFIRMED", "false")
    values.setdefault("SERPAPI_FREE_TIER_CONFIRMED", "false")
    values.setdefault("LITEAPI_MODE", "sandbox")
    values.update(payload)
    fd, temp = tempfile.mkstemp(prefix=".env.", dir=PROJECT)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("# Local secrets; never commit or copy to chat.\n")
            for k, v in values.items():
                handle.write(f"{k}={v}\n")
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    print(json.dumps({"updated": sorted(payload), "path": str(path)}, ensure_ascii=False))


def status() -> None:
    path = PROJECT / ".env"
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                values[k] = v
    print(json.dumps({"configured": {k: bool(values.get(k)) for k in KEYS},
                      "gemini_free_confirmed": values.get("GEMINI_FREE_TIER_CONFIRMED") == "true",
                      "serpapi_free_confirmed": values.get("SERPAPI_FREE_TIER_CONFIRMED") == "true"}, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-stdin", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    try:
        if args.write_stdin:
            write_env(json.loads(sys.stdin.readline()))
            return 0
        script = str(Path(__file__).resolve())
        if sys.platform == "win32":
            script = "/mnt/" + script[0].lower() + script[2:].replace("\\", "/")
            base = ["wsl.exe", "-d", "AegML-Hackathon", "-u", "aegml", "--", "python3", script]
        else:
            base = [sys.executable, script]
        if args.status:
            if sys.platform == "win32":
                return subprocess.run(base + ["--status"], check=False).returncode
            status()
            return 0
        if not sys.stdin.isatty():
            print("Запустите этот скрипт в своём терминале: py scripts/configure_keys.py")
            return 2
        print("Ввод локальный и скрытый. Enter — пропустить. Не присылайте ключи в чат.")
        payload = {}
        for key, label in KEYS.items():
            value = getpass.getpass(label + ": ").strip()
            if value:
                payload[key] = value
                if key == "GEMINI_API_KEY":
                    answer = input("Это проект Google БЕЗ billing с доступной free quota? yes/no: ").strip().lower()
                    payload["GEMINI_FREE_TIER_CONFIRMED"] = "true" if answer == "yes" else "false"
                if key == "SERPAPI_API_KEY":
                    answer = input("Это бесплатный SerpApi Free без оплаты? yes/no: ").strip().lower()
                    payload["SERPAPI_FREE_TIER_CONFIRMED"] = "true" if answer == "yes" else "false"
                if key in {"HOTELBEDS_API_SECRET", "RATEHAWK_API_KEY"}:
                    flag = "HOTELBEDS_TEST_CONFIRMED" if key == "HOTELBEDS_API_SECRET" else "RATEHAWK_TEST_CONFIRMED"
                    answer = input("Это разрешённый sandbox/test доступ без финансовых обязательств? yes/no: ").strip().lower()
                    payload[flag] = "true" if answer == "yes" else "false"
        if "OPENROUTER_API_KEY" in payload and "GEMINI_API_KEY" not in payload:
            payload["LLM_PROVIDER"] = "openrouter"
        if sys.platform == "win32":
            result = subprocess.run(base + ["--write-stdin"], input=json.dumps(payload).encode("utf-8"), check=False)
            return result.returncode
        write_env(payload)
        return 0
    except (ValueError, OSError, subprocess.SubprocessError):
        print("Не удалось сохранить конфигурацию. Ключи не выводятся; проверьте готовность WSL-копии.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
