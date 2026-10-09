"""Alembic environment.

DATABASE_URL is read from app settings so migrations use the exact same URL as
the application. Pass `-x compare=1` to verify that the migrated database
matches the ORM metadata exactly (used by tests/test_migrations.py).
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import create_engine, pool

from alembic import context

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.db.base import metadata
from app.db.registry import ALL_MODELS

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata

# Importing ALL_MODELS guarantees every table is registered on the metadata.
assert len(ALL_MODELS) == len(metadata.tables), "model import list is out of sync with metadata"


def get_url() -> str:
    # -x db_url=... wins, then alembic.ini, then application settings
    override = context.get_x_argument(as_dictionary=True).get("db_url")
    if override:
        return override
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(get_url(), poolclass=pool.NullPool, future=True)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_schemas=True,
            version_table_schema="public",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
