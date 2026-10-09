"""Database layer: declarative base, engine/session, and full model registry.

Use `from app.db.registry import ALL_MODELS` when you need every table loaded
(Alembic, tests). Importing it here would be circular, so it stays separate.
"""

from app.db.base import Base, TimestampMixin, VersionMixin, metadata
from app.db.session import (
    build_engine,
    get_db,
    get_engine,
    get_session_factory,
    session_scope,
)

__all__ = [
    "Base",
    "TimestampMixin",
    "VersionMixin",
    "build_engine",
    "get_db",
    "get_engine",
    "get_session_factory",
    "metadata",
    "session_scope",
]
