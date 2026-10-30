"""Store one GigaChat Personal Authorization Key through hidden local input.

Save only: no authorization request, model selection, billing or SSL changes.
Windows sends the key to the fixed WSL writer via stdin, never argv or env.
"""

from __future__ import annotations

import argparse
import getpass
import json
import subprocess
import sys
import warnings
from pathlib import Path

if __package__:
    from . import configure_keys as writer
else:
    import configure_keys as writer


AUTH_FIELD = "GIGACHAT_AUTH_KEY"
CONFIRMED_FIELD = "GIGACHAT_FREEMIUM_CONFIRMED"
PERSONAL_SCOPE = "GIGACHAT_API_PERS"


class _SafeParser(argparse.ArgumentParser):
    def error(self, _message):
        # Unknown argv may itself contain a mistakenly pasted key.
        raise ValueError("Неизвестные параметры команды.")


def collect_payload() -> dict:
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        value = getpass.getpass("Authorization Key GigaChat Personal (ввод скрыт; Enter — пропустить): ").strip()
    if not value:
        return {}
    writer.validate_gigachat_auth_key(value)
    answer = input("В личном проекте подключён действующий бесплатный пакет GigaChat, без платных услуг? yes/no: ")
    return {AUTH_FIELD: value, CONFIRMED_FIELD: "true" if answer.strip().casefold() == "yes" else "false"}


def save_payload(payload) -> None:
    if not isinstance(payload, dict) or set(payload) - {AUTH_FIELD, CONFIRMED_FIELD}:
        raise ValueError("Разрешено сохранить только ключ GigaChat и его подтверждение.")
    if not payload:
        return
    if AUTH_FIELD not in payload:
        raise ValueError("Нужен новый Authorization Key.")
    writer.validate_gigachat_auth_key(payload[AUTH_FIELD])
    writer.write_env(payload)


def status() -> None:
    path = writer.PROJECT / ".env"
    if path.is_symlink():
        raise ValueError("Файл конфигурации является ссылкой.")
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#") and "=" in line:
                name, value = line.split("=", 1)
                if name in {AUTH_FIELD, CONFIRMED_FIELD}:
                    values[name] = value
    print(json.dumps({"configured": bool(values.get(AUTH_FIELD)),
        "freemium_confirmed": values.get(CONFIRMED_FIELD) == "true"}))


def _wsl_base() -> list[str]:
    script = str(Path(__file__).resolve())
    script = "/mnt/" + script[0].lower() + script[2:].replace("\\", "/")
    return ["wsl.exe", "-d", "AegML-Hackathon", "-u", "aegml", "--", "python3", script]


def main(argv=None) -> int:
    parser = _SafeParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--status", action="store_true", help="Показать только наличие ключа и подтверждения.")
    modes.add_argument("--write-stdin", action="store_true", help=argparse.SUPPRESS)
    try:
        args = parser.parse_args(argv)
        if args.write_stdin:
            if sys.platform == "win32":
                raise ValueError("Stdin writer используется только внутри WSL.")
            # A key is at most4096 characters; never read an unbounded pipe.
            line = sys.stdin.readline(8193)
            if len(line) > 8192:
                raise ValueError("Слишком большой ввод.")
            save_payload(json.loads(line))
            return 0
        if args.status:
            if sys.platform == "win32":
                return subprocess.run(_wsl_base() + ["--status"], check=False).returncode
            status()
            return 0
        if not sys.stdin.isatty():
            print("Запустите в своём PowerShell: py E:\\work\\aegml\\scripts\\configure_gigachat.py")
            return 2
        print("Ввод локальный и скрытый. Ключ в чат не отправляйте. Это только сохранение; текущая модель не переключается.")
        payload = collect_payload()
        if not payload:
            print("Пропущено. Конфигурация не изменена.")
            return 0
        if sys.platform == "win32":
            return subprocess.run(_wsl_base() + ["--write-stdin"],
                input=json.dumps(payload).encode("utf-8"), check=False).returncode
        save_payload(payload)
        return 0
    except (ValueError, OSError, EOFError, getpass.GetPassWarning, subprocess.SubprocessError, KeyboardInterrupt):
        print("Не удалось сохранить ключ GigaChat. Значение ключа не выводится; проверьте ввод и готовность WSL.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
