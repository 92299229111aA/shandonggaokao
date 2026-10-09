"""0001 — create the four application schemas.

reference / admission / candidate / recommendation are the module boundaries
described in docs/ARCHITECTURE.md. No tables yet: each later migration owns one
cohesive slice of the model so failures stay easy to localise.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001_schemas"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMAS = ("reference", "admission", "candidate", "recommendation")


def upgrade() -> None:
    for schema in SCHEMAS:
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    # citext would be handy for code columns but requires an extension install
    # privilege many managed databases do not grant. Comparisons here are on
    # normalised uppercase codes, so plain text is sufficient.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")


def downgrade() -> None:
    for schema in reversed(SCHEMAS):
        op.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
