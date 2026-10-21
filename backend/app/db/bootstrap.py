"""Create the schema on a fresh database without Alembic.

Alembic's migrations are PostgreSQL-shaped: they create four schemas and use JSONB. The
desktop build ships a pre-built SQLite file, so it never migrates — but it does need a way
to create the tables from the models, which is what this does.

This is also what makes the SQLite build testable: a test can create a throwaway database
from the current model definitions and exercise the real query paths against it.
"""

from __future__ import annotations

from sqlalchemy import Engine, func, inspect, select
from sqlalchemy.orm import Session

# Imported for the side effect of registering every model on Base.metadata. Without this
# the metadata is empty and create_all() silently creates nothing.
from app.admission import models as _admission_models  # noqa: F401
from app.candidate import models as _candidate_models  # noqa: F401
from app.db.base import Base
from app.recommendation import models as _recommendation_models  # noqa: F401
from app.reference import models as _reference_models  # noqa: F401


def create_all(engine: Engine) -> int:
    """Create every table that does not exist yet. Idempotent.

    Returns the number of tables the metadata defines, so callers can assert that the model
    set was actually registered rather than silently creating nothing.
    """
    Base.metadata.create_all(engine)
    return len(Base.metadata.tables)


def missing_tables(engine: Engine) -> list[str]:
    """Tables the models define but the database lacks. Empty means the schema is complete."""
    present = set(inspect(engine).get_table_names())
    expected = {table.name for table in Base.metadata.tables.values()}
    return sorted(expected - present)


def ensure_schema(engine: Engine) -> list[str]:
    """Create missing tables and report what was still missing afterwards."""
    create_all(engine)
    return missing_tables(engine)


def table_row_counts(session: Session) -> dict[str, int]:
    """Row count per table, in dependency order. Used by the smoke test and the exporter."""
    counts: dict[str, int] = {}
    for table in Base.metadata.sorted_tables:
        counts[table.name] = int(
            session.execute(select(func.count()).select_from(table)).scalar_one()
        )
    return counts
