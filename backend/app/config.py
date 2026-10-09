"""Application settings.

Everything environment-dependent lives here so that Rule Engine, Scoring and
Optimizer stay constructible without a running app (see docs/ARCHITECTURE.md).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_ROOT / ".env",),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- app ----
    app_name: str = "Shandong Gaokao Intelligent Volunteer System"
    api_v1_prefix: str = "/api/v1"
    environment: str = "development"
    log_level: str = "INFO"

    # ---- http ----
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    # Comma-separated in .env; NoDecode stops pydantic-settings from demanding JSON
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # ---- storage ----
    database_url: str = "postgresql+psycopg://gaokao:gaokao_dev_only@127.0.0.1:5432/gaokao"
    redis_url: str | None = None
    sql_echo: bool = False

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def is_test(self) -> bool:
        return self.environment.lower() in {"test", "testing"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
