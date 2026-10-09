"""Shared pytest fixtures.

Integration tests need a live PostgreSQL. `TEST_DATABASE_URL` (or the default
below) is used; the suite skips cleanly when no database is reachable so pure
algorithm tests still run anywhere.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

DEFAULT_TEST_URL = "postgresql+psycopg://gaokao:gaokao_dev_only@127.0.0.1:5432/gaokao_test"


def test_database_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_URL)


def _database_reachable(url: str) -> bool:
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import SQLAlchemyError

    try:
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except SQLAlchemyError:
        return False
    except Exception:  # pragma: no cover - driver level errors
        return False


requires_db = pytest.mark.skipif(
    not _database_reachable(test_database_url()),
    reason=f"no PostgreSQL reachable at {test_database_url()} (set TEST_DATABASE_URL)",
)


@pytest.fixture(scope="session")
def migrated_engine():
    """Run migrations once, then hand out an engine against the test database."""
    from sqlalchemy import create_engine

    from alembic import command
    from alembic.config import Config

    backend_root = Path(__file__).resolve().parents[1]
    url = test_database_url()

    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)

    # Start from a clean database so the full upgrade path is exercised every run.
    engine = create_engine(url, future=True)
    with engine.begin() as conn:
        for schema in ("reference", "admission", "candidate", "recommendation"):
            conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        conn.exec_driver_sql("DROP TABLE IF EXISTS public.alembic_version")
    engine.dispose()

    command.upgrade(cfg, "head")

    engine = create_engine(url, future=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(migrated_engine) -> Iterator:
    """Session wrapped in a transaction that is always rolled back."""
    from sqlalchemy.orm import Session

    connection = migrated_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture()
def api_client(db_session):
    """TestClient whose request-scoped session is this test's rolled-back session."""
    from fastapi.testclient import TestClient

    from app.api.deps import get_session
    from app.main import app

    app.dependency_overrides[get_session] = lambda: db_session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture()
def cycle(db_session):
    """An active 2026 Shandong cycle with the policy-mandated volunteer limit."""
    from app.admission.models import AdmissionCycle

    row = AdmissionCycle(
        province_code="37",
        exam_year=2026,
        exam_category="SUMMER_REGULAR",
        batch_code="REGULAR_BATCH_1",
        round_no=1,
        program_level="UNDERGRAD",
        application_limit=96,
        policy_version="sd-2026-v1",
    )
    db_session.add(row)
    db_session.flush()
    return row
