"""0005 — program_series: the cross-year identity used for historical matching.

A series is (university, major/major-class, campus, cooperative flag). Historical
outcomes attach to the series, not to a plan row, because plan rows are per-year.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_program_series"
down_revision: str | None = "0004_admission_cycles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "program_series",
        sa.Column("id", sa.Integer(), primary_key=True),
        # Deterministic natural key: sha1 of university|major|major_name|campus|cooperative
        sa.Column("series_key", sa.String(160), nullable=False),
        sa.Column("canonical_name", sa.String(160), nullable=False),
        sa.Column(
            "university_id",
            sa.Integer(),
            sa.ForeignKey("reference.universities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("major_id", sa.Integer(), sa.ForeignKey("reference.majors.id", ondelete="SET NULL")),
        sa.Column("campus_id", sa.Integer(), sa.ForeignKey("reference.campuses.id", ondelete="SET NULL")),
        sa.Column("cooperative", sa.String(24), nullable=False, server_default="NONE"),
        sa.Column("match_quality", sa.String(16), nullable=False, server_default="EXACT"),
        sa.Column("first_year", sa.Integer()),
        sa.Column("last_year", sa.Integer()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("series_key", name="uq_program_series_series_key"),
        sa.CheckConstraint(
            "match_quality in ('EXACT','RENAMED','MERGED','SPLIT','FUZZY','NONE')",
            name="ck_program_series_match_quality_valid",
        ),
        sa.CheckConstraint(
            "first_year is null or last_year is null or first_year <= last_year",
            name="ck_program_series_year_order",
        ),
        schema="admission",
    )
    op.create_index(
        "ix_program_series_university_major",
        "program_series",
        ["university_id", "major_id"],
        schema="admission",
    )


def downgrade() -> None:
    op.drop_table("program_series", schema="admission")
