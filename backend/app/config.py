"""Application settings.

Everything environment-dependent lives here so that Rule Engine, Scoring and
Optimizer stay constructible without a running app (see docs/ARCHITECTURE.md).
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import AliasChoices, Field, field_validator
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

    # ---- desktop / SQLite mode ----
    # "postgres" keeps every existing behaviour; "sqlite" is the offline desktop build.
    # The two live side by side on purpose: the dev machine, the tests and the data build
    # stay on PostgreSQL, while the shipped app runs off one SQLite file.
    db_mode: Annotated[str, Field(validation_alias=AliasChoices("GAOKAO_DB_MODE", "DB_MODE"))] = (
        "postgres"
    )
    # Where the SQLite file lives. Empty means %LOCALAPPDATA%\山东高考智能志愿 on Windows,
    # ~/.local/share/gaokao elsewhere. Only used when db_mode == "sqlite".
    data_dir: Annotated[str, Field(validation_alias=AliasChoices("GAOKAO_DATA_DIR", "DATA_DIR"))] = ""
    # Static frontend bundle to serve; empty means "do not mount static files".
    static_dir: Annotated[
        str, Field(validation_alias=AliasChoices("GAOKAO_STATIC_DIR", "STATIC_DIR"))
    ] = ""

    @field_validator("db_mode", mode="before")
    @classmethod
    def _normalise_db_mode(cls, value: object) -> object:
        if isinstance(value, str):
            mode = value.strip().lower()
            if mode not in {"postgres", "postgresql", "sqlite"}:
                raise ValueError(f"db_mode 只接受 postgres 或 sqlite，收到 {value!r}")
            return "postgres" if mode == "postgresql" else mode
        return value

    @property
    def is_sqlite(self) -> bool:
        return self.db_mode == "sqlite"

    @property
    def resolved_data_dir(self) -> Path:
        """Directory for the SQLite file, logs and downloaded updates.

        Under the user profile rather than next to the executable: a per-user location
        needs no administrator rights and survives reinstall.
        """
        if self.data_dir:
            return Path(self.data_dir).expanduser()
        if os.name == "nt":
            base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
            return Path(base) / "山东高考智能志愿"
        return Path.home() / ".local" / "share" / "gaokao"

    @property
    def sqlite_path(self) -> Path:
        return self.resolved_data_dir / "gaokao.db"

    def effective_database_url(self) -> str:
        """The URL to actually connect to, honouring `db_mode`.

        `database_url` stays the PostgreSQL default so nothing that reads it directly
        (Alembic, ops scripts) changes behaviour.
        """
        if self.is_sqlite:
            return f"sqlite:///{self.sqlite_path.as_posix()}"
        return self.database_url

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
