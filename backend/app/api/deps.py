"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.db.session import get_db


def get_session() -> Iterator[Session]:
    """Request-scoped session. Services own commit/rollback decisions."""
    yield from get_db()
