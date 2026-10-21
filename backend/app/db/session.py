"""SQLAlchemy 2 engine / session factory.

Sync SQLAlchemy is used deliberately: the workload is short transactional
requests plus a CPU-bound CP-SAT solve, so async buys nothing here.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def build_engine(url: str | None = None, *, echo: bool | None = None) -> Engine:
    settings = get_settings()
    target = url or settings.effective_database_url()
    echo_flag = settings.sql_echo if echo is None else echo

    if target.startswith("sqlite"):
        # SQLite needs `check_same_thread=False` because FastAPI runs sync routes on a
        # thread pool, and the default guard rejects a connection reused across threads.
        # `pool_pre_ping` and a sized pool are PostgreSQL concerns; the desktop app serves
        # one local user, so the default pool is both sufficient and cheaper.
        return create_engine(
            target,
            echo=echo_flag,
            connect_args={"check_same_thread": False},
            future=True,
        )

    return create_engine(
        target,
        echo=echo_flag,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        future=True,
    )


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = build_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for scripts/seeds/tests."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency. Routers never commit directly — services do."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def set_schemas(engine: Engine, schemas: list[str]) -> None:
    """Pin search_path so ORM models need no schema qualification on writes.

    PostgreSQL only. SQLite has neither schemas nor `set_config`, and in that mode the
    models already resolve to unqualified names (see app/db/dialect.py), so there is
    nothing to pin.
    """
    if engine.dialect.name != "postgresql":
        return

    @event.listens_for(engine, "connect")
    def _set_search_path(dbapi_connection, _record):  # pragma: no cover - driver hook
        with dbapi_connection.cursor() as cursor:
            cursor.execute("SELECT set_config('search_path', %s, false)", (", ".join(schemas),))


def reset_engine_state() -> None:
    """Test helper: drop cached engine/factory so a new URL takes effect."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
