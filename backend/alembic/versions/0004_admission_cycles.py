"""0004 — admission cycles, per-year candidate population, 一分一段表.

`admission_cycles.application_limit` is the policy number the optimizer and the
UI must read; it is never hard-coded anywhere else in the codebase.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_admission_cycles"
down_revision: str | None = "0003_admission_sources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admission_cycles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("province_code", sa.String(2), nullable=False, server_default="37"),
        sa.Column("exam_year", sa.Integer(), nullable=False),
        sa.Column("exam_category", sa.String(32), nullable=False),
        sa.Column("batch_code", sa.String(32), nullable=False),
        sa.Column("round_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("program_level", sa.String(16), nullable=False, server_default="UNDERGRAD"),
        sa.Column("application_limit", sa.Integer(), nullable=False),
        sa.Column("filling_rule", sa.Text()),
        sa.Column("policy_version", sa.String(32), nullable=False, server_default="v1"),
        sa.Column("published_on", sa.Date()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "province_code",
            "exam_year",
            "exam_category",
            "batch_code",
            "round_no",
            "program_level",
            name="uq_admission_cycles_scope",
        ),
        sa.CheckConstraint("application_limit > 0", name="ck_admission_cycles_application_limit_positive"),
        sa.CheckConstraint("round_no >= 1", name="ck_admission_cycles_round_no_positive"),
        # V1 deliberately rejects out-of-scope cycles at the database level
        sa.CheckConstraint(
            "exam_category in ('SUMMER_REGULAR')", name="ck_admission_cycles_exam_category_in_v1_scope"
        ),
        sa.CheckConstraint(
            "batch_code in ('REGULAR_BATCH_1')", name="ck_admission_cycles_batch_code_in_v1_scope"
        ),
        sa.CheckConstraint(
            "program_level in ('UNDERGRAD','JUNIOR_COLLEGE')",
            name="ck_admission_cycles_program_level_valid",
        ),
        schema="admission",
    )

    op.create_table(
        "exam_year_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("province_code", sa.String(2), nullable=False, server_default="37"),
        sa.Column("exam_year", sa.Integer(), nullable=False),
        sa.Column("exam_category", sa.String(32), nullable=False),
        sa.Column("program_level", sa.String(16), nullable=False, server_default="UNDERGRAD"),
        sa.Column("special_line", sa.Integer()),
        sa.Column("regular_line", sa.Integer()),
        sa.Column("ranked_candidates", sa.Integer(), nullable=False),
        sa.Column(
            "source_document_id",
            sa.Integer(),
            sa.ForeignKey("admission.source_documents.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "province_code", "exam_year", "exam_category", "program_level", name="uq_exam_year_stats_scope"
        ),
        sa.CheckConstraint(
            "ranked_candidates > 0", name="ck_exam_year_stats_ranked_candidates_positive"
        ),
        schema="admission",
    )

    op.create_table(
        "score_rank_segments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("exam_year", sa.Integer(), nullable=False),
        sa.Column("exam_category", sa.String(32), nullable=False),
        sa.Column("program_level", sa.String(16), nullable=False, server_default="UNDERGRAD"),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("same_score_count", sa.Integer(), nullable=False),
        sa.Column("cumulative_rank", sa.Integer(), nullable=False),
        sa.Column(
            "source_document_id",
            sa.Integer(),
            sa.ForeignKey("admission.source_documents.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "exam_year", "exam_category", "score", name="uq_score_rank_segments_key"
        ),
        sa.CheckConstraint("score between 0 and 750", name="ck_score_rank_segments_score_range"),
        sa.CheckConstraint(
            "same_score_count >= 0", name="ck_score_rank_segments_same_score_count_non_negative"
        ),
        sa.CheckConstraint(
            "cumulative_rank > 0", name="ck_score_rank_segments_cumulative_rank_positive"
        ),
        schema="admission",
    )
    op.create_index(
        "ix_score_rank_segments_year_cumulative",
        "score_rank_segments",
        ["exam_year", "cumulative_rank"],
        schema="admission",
    )


def downgrade() -> None:
    op.drop_table("score_rank_segments", schema="admission")
    op.drop_table("exam_year_stats", schema="admission")
    op.drop_table("admission_cycles", schema="admission")
