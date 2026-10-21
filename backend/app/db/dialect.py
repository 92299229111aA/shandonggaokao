"""Dialect-dependent model plumbing: PostgreSQL schemas and the JSON column type.

The desktop build runs on SQLite, which has no schemas and no `JSONB`. Both differences are
confined here so the 33 model definitions stay readable:

    SCHEMA = table_schema("reference")        # "reference" on PG, None on SQLite
    operand: Mapped[dict | None] = mapped_column(JSONType)

Why SQLite has no schemas, and what we do about it: SQLite resolves names through attached
databases, and `ATTACH DATABASE ... AS reference` would let the qualified names keep working,
but foreign keys cannot cross attached databases — and this model has 33 tables joined by
foreign keys. Dropping the qualification is the only arrangement that keeps referential
integrity, so on SQLite every table simply lands in the main database.

Nothing in the application queries with raw `schema.table` SQL, so qualification is purely a
storage-layout concern here.
"""

from __future__ import annotations

from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

# PostgreSQL keeps JSONB (indexable, validated). SQLite falls back to JSON, which stores the
# same Python objects as text. No application code uses JSONB operators, so behaviour is
# identical — only the on-disk representation differs.
JSONType = JSON().with_variant(JSONB, "postgresql")


def table_schema(name: str) -> str | None:
    """Return the PostgreSQL schema name, or None when running on SQLite.

    Read from settings at import time: models are imported once per process, and the mode is
    fixed for the lifetime of that process (a desktop process never switches dialect).
    """
    from app.config import get_settings

    return None if get_settings().is_sqlite else name


def fk_target(schema: str | None, qualified: str) -> str:
    """Build a foreign-key target for the current dialect.

    PostgreSQL needs `schema.table.column`. SQLite rejects any database qualifier
    ("unknown database reference"), and interpolating a None schema would produce the
    literal string "None.table.column" — so both cases must go through here.
    """
    return qualified if schema is None else f"{schema}.{qualified}"


def fk_literal(qualified: str) -> str:
    """Foreign-key target given as a literal `schema.table.column` string.

    Used where the referenced table lives in another schema than the model module
    (e.g. `admission` rows pointing at `reference.majors`). SQLite has no schemas, so
    the qualifier must be dropped there or SQLite raises "unknown database reference".
    """
    from app.config import get_settings

    if not get_settings().is_sqlite:
        return qualified
    parts = qualified.split(".")
    return ".".join(parts[1:]) if len(parts) == 3 else qualified
