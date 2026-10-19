"""Запуск: py -m aegml --request examples/request.json."""

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .config import Config, ConfigurationError, IntegrationNotConfigured
from .service import prepare_demo


def _reject_non_json_number(value: str) -> None:
    raise ValueError(f"Недопустимое JSON-число: {value}.")


def _emit(payload: dict) -> None:
    # Корректный UTF-8 при перенаправлении вывода в файл в Windows.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    # argparse тоже пишет в stdout/stderr; выставляем UTF-8 до --help и ошибок.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="AegML: публичный DEMO-каркас поиска вымышленных отелей. Бронирования нет."
    )
    parser.add_argument("--request", type=Path, required=True, help="Путь к структурированному JSON-запросу")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    try:
        with args.request.open(encoding="utf-8-sig") as handle:
            data = json.load(handle, parse_constant=_reject_non_json_number)
    except (OSError, UnicodeError, ValueError) as exc:
        _emit({
            "status": "input_error",
            "is_demo": True,
            "source": "none",
            "booking": {"status": "not_supported", "created": False},
            "errors": [f"Не удалось прочитать JSON-запрос: {exc}"],
        })
        return 2
    try:
        payload, exit_code = prepare_demo(data, Config.from_env())
    except ConfigurationError as exc:
        _emit({
            "status": "integration_not_configured" if isinstance(exc, IntegrationNotConfigured) else "configuration_error",
            "is_demo": True,
            "source": "none",
            "booking": {"status": "not_supported", "created": False},
            "errors": [str(exc)],
        })
        return 2
    _emit(payload)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
