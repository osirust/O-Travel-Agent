"""Конфигурация только через окружение, без неявного чтения .env."""

import os
from dataclasses import dataclass
from typing import Mapping

from .contracts import LanguageModel


class ConfigurationError(ValueError):
    pass


class IntegrationNotConfigured(ConfigurationError):
    pass


@dataclass(frozen=True)
class Config:
    llm_mode: str = "mock"
    model_name: str = ""
    model_endpoint: str = ""

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Config":
        env = os.environ if environ is None else environ
        mode = env.get("AEGML_LLM_MODE", "mock").strip().lower()
        if mode not in {"mock", "local", "api"}:
            raise ConfigurationError(
                "AEGML_LLM_MODE должен быть mock, local или api."
            )
        return cls(
            llm_mode=mode,
            model_name=env.get("AEGML_MODEL_NAME", "").strip(),
            model_endpoint=env.get("AEGML_MODEL_ENDPOINT", "").strip(),
        )


class MockLanguageModel:
    """Фиксированная заглушка; не интерпретирует запрос и не вызывает сеть."""

    def complete(self, *, text: str) -> str:
        return "DEMO: обработка свободного текста ещё не подключена."


def create_language_model(config: Config) -> LanguageModel:
    if config.llm_mode == "mock":
        return MockLanguageModel()
    if config.llm_mode in {"local", "api"}:
        raise IntegrationNotConfigured(
            f"Режим LLM '{config.llm_mode}' выбран, но интеграция ещё не подключена. "
            "Автоматического перехода на mock нет. Для DEMO явно выберите mock."
        )
    raise ConfigurationError("Неизвестный режим LLM: используйте mock, local или api.")
