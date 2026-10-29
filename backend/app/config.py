import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Effort = Literal["low", "medium", "high", "xhigh", "max"]
Provider = Literal["claude", "gemini", "ollama"]

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

# USD per million tokens. Used only for the cost estimate shown in the UI.
PRICING: dict[str, dict[str, float]] = {
    "claude-opus-5-5": {"input": 4.0, "output": 20.0, "cache_read": 0.20, "cache_write": 5.0},
    "claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_read": 0.20, "cache_write": 2.5},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write": 0.125},
    "demo-offline": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env", env_prefix="DATAPILOT_", extra="ignore",
        env_ignore_empty=True,
    )

    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    model: str = "claude-opus-5-5"
    effort: Effort = "medium"
    max_tokens: int = 32000
    max_agent_steps: int = 12
    enable_fallbacks: bool = True
    # Offline rule-based agent instead of Claude. None = on only when no API key is configured.
    demo_mode: bool | None = None

    # Alternative providers through their OpenAI-compatible endpoints (see README, "Other LLM providers").
    # None = auto: Claude when ANTHROPIC_API_KEY is set, else Gemini when GEMINI_API_KEY is set, else demo.
    provider: Provider | None = None
    gemini_api_key: str | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    gemini_model: str = "gemini-3.5-flash"
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "qwen3:8b"
    compat_timeout_s: float = 300.0

    data_dir: Path = Path(__file__).resolve().parent.parent / "data"
    samples_dir: Path = Path(__file__).resolve().parent.parent / "samples"
    max_upload_mb: int = 50

    sql_timeout_s: float = 20.0
    ui_max_rows: int = 200
    llm_preview_rows: int = 30
    max_chart_points: int = 1000
    max_chart_series: int = 8  # matches the 8-slot categorical palette in the UI

    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


    @property
    def anthropic_key_configured(self) -> bool:
        return bool(self.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY"))

    @property
    def active_provider(self) -> Provider:
        if self.provider is not None:
            return self.provider
        if not self.anthropic_key_configured and self.gemini_api_key:
            return "gemini"
        return "claude"

    @property
    def api_key_configured(self) -> bool:
        """Credentials for the active provider are present (Ollama needs none)."""
        provider = self.active_provider
        if provider == "gemini":
            return bool(self.gemini_api_key)
        return provider == "ollama" or self.anthropic_key_configured

    @property
    def active_model(self) -> str:
        return {"claude": self.model, "gemini": self.gemini_model, "ollama": self.ollama_model}[self.active_provider]

    @property
    def use_demo(self) -> bool:
        return self.demo_mode if self.demo_mode is not None else not self.api_key_configured


@lru_cache
def get_settings() -> Settings:
    return Settings()
