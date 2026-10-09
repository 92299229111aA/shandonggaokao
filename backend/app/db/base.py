"""Declarative base and shared column mixins."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.common.enums import SCHEMAS

# Explicit naming convention keeps Alembic autogenerate diffs stable and makes
# constraint names predictable in migrations.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# SQLAlchemy cannot map one MetaData to several schemas usefully, so all tables
# live in one MetaData and set __table_args__ = {"schema": ...} explicitly.
metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    metadata = metadata

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        pk = getattr(self, "id", None)
        return f"<{type(self).__name__} id={pk}>"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class VersionMixin:
    """Optimistic locking column; every edit path bumps it."""

    version: Mapped[int] = mapped_column(default=1, nullable=False)


__all__ = ["SCHEMAS", "Base", "TimestampMixin", "VersionMixin", "metadata"]
