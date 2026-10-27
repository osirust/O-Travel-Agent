"""Environment-only settings. Provider keys are never returned by the API."""

import os
from dataclasses import dataclass
from typing import Any
from sqlalchemy.engine import URL


def enabled(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class Settings:
    database_url: str
    llm_mode: str
    provider_timeout: float
    max_message_chars: int
    max_llm_calls_per_day: int
    max_llm_calls_per_run: int
    max_local_llm_calls_per_day: int = 500
    default_search_mode: str = "DEMO"
    default_hotel_provider: str = "liteapi"
    default_flight_provider: str = "serpapi"
    max_cloudflare_llm_calls_per_day: int = 40
    reference_fx_enabled: bool = False
    max_groq_llm_calls_per_day: int = 40

    def __post_init__(self):
        for value, allowed in (
            (self.default_search_mode, {"DEMO", "HYBRID"}),
            (self.default_hotel_provider, {"liteapi", "serpapi"}),
            (self.default_flight_provider, {"duffel", "serpapi"}),
        ):
            if value not in allowed:
                raise ValueError("Unsupported default travel search configuration")
        if type(self.max_cloudflare_llm_calls_per_day) is not int or not 0 <= self.max_cloudflare_llm_calls_per_day <= 2000:
            raise ValueError("Cloudflare application call budget must be an integer from 0 to 2000")
        if type(self.max_groq_llm_calls_per_day) is not int or not 0 <= self.max_groq_llm_calls_per_day <= 1000:
            raise ValueError("Groq application call budget must be an integer from 0 to 1000")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv("DATABASE_URL") or URL.create(
                "postgresql+asyncpg", username=os.getenv("DATABASE_USER", "otravel"),
                password=os.getenv("DATABASE_PASSWORD", "otravel"),
                host=os.getenv("DATABASE_HOST", "db"), port=int(os.getenv("DATABASE_PORT", "5432")),
                database=os.getenv("DATABASE_NAME", "otravel"),
            ).render_as_string(hide_password=False),
            llm_mode=os.getenv("LLM_PROVIDER", "openrouter"),
            provider_timeout=float(os.getenv("PROVIDER_TIMEOUT", "25")),
            max_message_chars=int(os.getenv("MAX_MESSAGE_CHARS", "8000")),
            max_llm_calls_per_day=int(os.getenv("MAX_LLM_CALLS_PER_DAY", "40")),
            max_llm_calls_per_run=int(os.getenv("MAX_LLM_CALLS_PER_RUN", "2")),
            max_local_llm_calls_per_day=int(os.getenv("MAX_LOCAL_LLM_CALLS_PER_DAY", "500")),
            default_search_mode=os.getenv("DEFAULT_SEARCH_MODE", "DEMO"),
            default_hotel_provider=os.getenv("DEFAULT_HOTEL_PROVIDER", "liteapi"),
            default_flight_provider=os.getenv("DEFAULT_FLIGHT_PROVIDER", "serpapi"),
            max_cloudflare_llm_calls_per_day=int(os.getenv("MAX_CLOUDFLARE_LLM_CALLS_PER_DAY", "40")),
            reference_fx_enabled=enabled("REFERENCE_FX_ENABLED"),
            max_groq_llm_calls_per_day=int(os.getenv("MAX_GROQ_LLM_CALLS_PER_DAY", "40")),
        )

    def provider_settings(self) -> dict[str, Any]:
        keys = [
            "GEMINI_API_KEY", "GEMINI_MODEL", "OPENROUTER_API_KEY", "OPENROUTER_MODEL", "OPENROUTER_BACKUP_MODEL",
            "GROQ_API_KEY", "GROQ_MODEL", "GROQ_REASONING_EFFORT", "GROQ_STRUCTURED_TRANSPORT_ENABLED",
            "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_MODEL",
            "ZAI_API_KEY", "ZAI_MODEL",
            "LITEAPI_API_KEY", "LITEAPI_MODE", "DUFFEL_ACCESS_TOKEN", "SERPAPI_API_KEY",
            "HOTELBEDS_API_KEY", "HOTELBEDS_API_SECRET", "RATEHAWK_KEY_ID", "RATEHAWK_API_KEY",
            "TRAVELPAYOUTS_API_TOKEN",
            "LLM_REQUEST_TIMEOUT", "LLM_REASONING_ENABLED", "LLM_MAX_OUTPUT_TOKENS",
            "OLLAMA_BASE_URL", "OLLAMA_MODEL", "OLLAMA_REQUEST_TIMEOUT",
        ]
        values: dict[str, Any] = {key: os.getenv(key, "") for key in keys}
        values.update({
            "GEMINI_MODEL": os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
            "OPENROUTER_MODEL": os.getenv("OPENROUTER_MODEL", "dots-studio/dots-3-note-preview:free"),
            "GEMINI_FREE_TIER_CONFIRMED": enabled("GEMINI_FREE_TIER_CONFIRMED"),
            "GROQ_MODEL": os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b"),
            "GROQ_FREE_TIER_CONFIRMED": enabled("GROQ_FREE_TIER_CONFIRMED"),
            "CLOUDFLARE_MODEL": os.getenv("CLOUDFLARE_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"),
            "CLOUDFLARE_FREE_TIER_CONFIRMED": enabled("CLOUDFLARE_FREE_TIER_CONFIRMED"),
            "ZAI_MODEL": os.getenv("ZAI_MODEL", "glm-4.7-flash"),
            "ZAI_FREE_TIER_CONFIRMED": enabled("ZAI_FREE_TIER_CONFIRMED"),
            "SERPAPI_FREE_TIER_CONFIRMED": enabled("SERPAPI_FREE_TIER_CONFIRMED"),
            "HOTELBEDS_TEST_CONFIRMED": enabled("HOTELBEDS_TEST_CONFIRMED"),
            "RATEHAWK_TEST_CONFIRMED": enabled("RATEHAWK_TEST_CONFIRMED"),
            "LLM_PROVIDER": self.llm_mode,
            "PROVIDER_TIMEOUT": self.provider_timeout,
            "LITEAPI_MODE": os.getenv("LITEAPI_MODE", "sandbox"),
            "OLLAMA_BASE_URL": os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            "OLLAMA_MODEL": os.getenv("OLLAMA_MODEL", "qwen3.5:4b"),
            "llm_provider": self.llm_mode,
            "provider_timeout": self.provider_timeout,
        })
        return values


def provider_status() -> dict[str, Any]:
    return {
        "_runtime": {"llm_provider": os.getenv("LLM_PROVIDER", "openrouter"),
                     "model": os.getenv("OLLAMA_MODEL", "qwen3.5:4b") if os.getenv("LLM_PROVIDER") == "ollama"
                              else os.getenv("GEMINI_MODEL", "gemini-3.8-flash") if os.getenv("LLM_PROVIDER") == "gemini"
                              else os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b") if os.getenv("LLM_PROVIDER") == "groq"
                              else os.getenv("CLOUDFLARE_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast") if os.getenv("LLM_PROVIDER") == "cloudflare"
                              else os.getenv("ZAI_MODEL", "glm-4.7-flash") if os.getenv("LLM_PROVIDER") == "zai"
                              else os.getenv("OPENROUTER_MODEL", "dots-studio/dots-3-note-preview:free")},
        "gemini": {"configured": bool(os.getenv("GEMINI_API_KEY")), "free_tier_confirmed": enabled("GEMINI_FREE_TIER_CONFIRMED"), "model": os.getenv("GEMINI_MODEL", "gemini-3.8-flash")},
        "openrouter": {"configured": bool(os.getenv("OPENROUTER_API_KEY")), "model": os.getenv("OPENROUTER_MODEL", "dots-studio/dots-3-note-preview:free"), "backup_model": os.getenv("OPENROUTER_BACKUP_MODEL", ""), "paid_fallback": False},
        "groq": {"configured": bool(os.getenv("GROQ_API_KEY")), "model": os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b"), "free_tier_confirmed": enabled("GROQ_FREE_TIER_CONFIRMED"), "paid_fallback": False,
            **({"include_reasoning": False} if os.getenv("GROQ_MODEL") == "openai/gpt-oss-120b" else {}),
            **({"structured_transport": True,
                "reasoning_effort": os.getenv("GROQ_REASONING_EFFORT") if os.getenv("GROQ_REASONING_EFFORT") in {"none", "default", "low", "medium", "high"} else None}
                if enabled("GROQ_STRUCTURED_TRANSPORT_ENABLED") else {})},
        "cloudflare": {"configured": bool(os.getenv("CLOUDFLARE_API_TOKEN") and os.getenv("CLOUDFLARE_ACCOUNT_ID")), "model": os.getenv("CLOUDFLARE_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"), "free_tier_confirmed": enabled("CLOUDFLARE_FREE_TIER_CONFIRMED"), "paid_fallback": False},
        "zai": {"configured": bool(os.getenv("ZAI_API_KEY")), "model": os.getenv("ZAI_MODEL", "glm-4.7-flash"), "free_tier_confirmed": enabled("ZAI_FREE_TIER_CONFIRMED"), "paid_fallback": False},
        "ollama": {"configured": os.getenv("LLM_PROVIDER") == "ollama", "model": os.getenv("OLLAMA_MODEL", "qwen3.5:4b"), "source": "LOCAL_MODEL", "paid_fallback": False},
        "liteapi": {"configured": bool(os.getenv("LITEAPI_API_KEY")), "mode": os.getenv("LITEAPI_MODE", "sandbox")},
        "duffel": {"configured": bool(os.getenv("DUFFEL_ACCESS_TOKEN")), "mode": "sandbox"},
        "serpapi": {"configured": bool(os.getenv("SERPAPI_API_KEY")), "free_tier_confirmed": enabled("SERPAPI_FREE_TIER_CONFIRMED"), "source_type": "LIVE_INDICATIVE"},
        "open_meteo": {"configured": True, "requires_key": False},
        "hotelbeds": {"configured": bool(os.getenv("HOTELBEDS_API_KEY") and os.getenv("HOTELBEDS_API_SECRET")), "status": "onboarding_required"},
        "ratehawk": {"configured": bool(os.getenv("RATEHAWK_KEY_ID") and os.getenv("RATEHAWK_API_KEY")), "status": "partner_access_required"},
        "travelpayouts": {"configured": bool(os.getenv("TRAVELPAYOUTS_API_TOKEN")), "status": "cached_indicative_data_only"},
    }
