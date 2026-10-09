"""0006 — 选科要求 rules as DNF, admission plans, historical outcomes, special requirements.

Subject rules are stored as groups (OR across groups) of items (AND inside a
group), which is exactly how the Shandong subject requirement text is phrased:
"物理和化学" is one ALL group, "物理或化学" is two ANY-mode groups.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_admission_rules_plans"
down_revision: str | None = "0005_program_series"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "subject_rule_sets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        # ANY = 所列科目任选一门; ALL = 所列科目均须选考
        sa.Column("mode", sa.String(8), nullable=False, server_default="ALL"),
        sa.Column("raw_text", sa.Text()),
        sa.Column(
            "source_document_id",
            sa.Integer(),
            sa.ForeignKey("admission.source_documents.id", ondelete="SET NULL"),
        ),
        sa.Column("is_authoritative", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("code", name="uq_subject_rule_sets_code"),
        sa.CheckConstraint("mode in ('ANY','ALL')", name="ck_subject_rule_sets_mode_valid"),
        schema="admission",
    )

    op.create_table(
        "subject_rule_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "rule_set_id",
            sa.Integer(),
            sa.ForeignKey("admission.subject_rule_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("group_no", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(8), nullable=False, server_default="ALL"),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("rule_set_id", "group_no", name="uq_subject_rule_groups_rule_set_id_group_no"),
        sa.CheckConstraint("group_no >= 0", name="ck_subject_rule_groups_group_no_non_negative"),
        sa.CheckConstraint("mode in ('ANY','ALL')", name="ck_subject_rule_groups_mode_valid"),
        schema="admission",
    )
    op.create_index("ix_subject_rule_groups_rule_set_id", "subject_rule_groups", ["rule_set_id"], schema="admission")

    op.create_table(
        "subject_rule_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("admission.subject_rule_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject_code", sa.String(8), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("group_id", "subject_code", name="uq_subject_rule_items_group_id_subject_code"),
        schema="admission",
    )
    op.create_index("ix_subject_rule_items_group_id", "subject_rule_items", ["group_id"], schema="admission")

    op.create_table(
        "admission_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "cycle_id",
            sa.Integer(),
            sa.ForeignKey("admission.admission_cycles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "series_id",
            sa.Integer(),
            sa.ForeignKey("admission.program_series.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subject_rule_set_id",
            sa.Integer(),
            sa.ForeignKey("admission.subject_rule_sets.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "snapshot_id",
            sa.Integer(),
            sa.ForeignKey("admission.dataset_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "source_document_id",
            sa.Integer(),
            sa.ForeignKey("admission.source_documents.id", ondelete="SET NULL"),
        ),
        sa.Column("plan_name", sa.String(200), nullable=False),
        sa.Column("plan_count", sa.Integer(), nullable=False),
        sa.Column("tuition_fee", sa.Integer(), nullable=False),
        sa.Column("duration_years", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("special_requirements", sa.Text()),
        sa.Column("campus_name", sa.String(64)),
        sa.Column("study_mode", sa.String(32)),
        sa.Column("series_match_quality", sa.String(16), nullable=False, server_default="EXACT"),
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "cycle_id", "series_id", "subject_rule_set_id", name="uq_admission_plans_cycle_series_rule"
        ),
        sa.CheckConstraint("plan_count >= 0", name="ck_admission_plans_plan_count_non_negative"),
        sa.CheckConstraint("tuition_fee >= 0", name="ck_admission_plans_tuition_fee_non_negative"),
        sa.CheckConstraint("duration_years between 2 and 8", name="ck_admission_plans_duration_years_range"),
        sa.CheckConstraint(
            "series_match_quality in ('EXACT','RENAMED','MERGED','SPLIT','FUZZY','NONE')",
            name="ck_admission_plans_series_match_quality_valid",
        ),
        schema="admission",
    )
    op.create_index(
        "ix_admission_plans_cycle_snapshot", "admission_plans", ["cycle_id", "snapshot_id"], schema="admission"
    )
    op.create_index("ix_admission_plans_tuition", "admission_plans", ["tuition_fee"], schema="admission")

    op.create_table(
        "admission_outcomes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "series_id",
            sa.Integer(),
            sa.ForeignKey("admission.program_series.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "plan_id", sa.Integer(), sa.ForeignKey("admission.admission_plans.id", ondelete="SET NULL")
        ),
        sa.Column(
            "snapshot_id",
            sa.Integer(),
            sa.ForeignKey("admission.dataset_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "source_document_id",
            sa.Integer(),
            sa.ForeignKey("admission.source_documents.id", ondelete="SET NULL"),
        ),
        sa.Column("exam_year", sa.Integer(), nullable=False),
        sa.Column("batch_code", sa.String(32), nullable=False, server_default="REGULAR_BATCH_1"),
        sa.Column("round_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("plan_count", sa.Integer()),
        sa.Column("admitted_count", sa.Integer()),
        sa.Column("min_score", sa.Integer()),
        sa.Column("min_rank", sa.Integer()),
        sa.Column("avg_rank", sa.Integer()),
        sa.Column("max_rank", sa.Integer()),
        sa.Column("rank_volatility", sa.Numeric(10, 4)),
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("series_id", "exam_year", name="uq_admission_outcomes_series_id_exam_year"),
        sa.CheckConstraint("min_rank is null or min_rank > 0", name="ck_admission_outcomes_min_rank_positive"),
        sa.CheckConstraint(
            "max_rank is null or min_rank is null or max_rank >= min_rank",
            name="ck_admission_outcomes_rank_order",
        ),
        sa.CheckConstraint(
            "min_score is null or min_score between 0 and 750", name="ck_admission_outcomes_min_score_range"
        ),
        sa.CheckConstraint(
            "admitted_count is null or admitted_count >= 0",
            name="ck_admission_outcomes_admitted_count_non_negative",
        ),
        schema="admission",
    )
    op.create_index(
        "ix_admission_outcomes_year_min_rank",
        "admission_outcomes",
        ["exam_year", "min_rank"],
        schema="admission",
    )

    op.create_table(
        "admission_requirements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "plan_id",
            sa.Integer(),
            sa.ForeignKey("admission.admission_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("requirement_type", sa.String(24), nullable=False),
        sa.Column("operator", sa.String(16), nullable=False, server_default="EQ"),
        sa.Column("operand", postgresql.JSONB()),
        sa.Column("severity", sa.String(8), nullable=False, server_default="BLOCK"),
        # False => the rule text could not be resolved deterministically => UNKNOWN
        sa.Column("is_deterministic", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("raw_text", sa.Text()),
        sa.Column(
            "source_document_id",
            sa.Integer(),
            sa.ForeignKey("admission.source_documents.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "plan_id", "requirement_type", "operator", name="uq_admission_requirements_plan_type_op"
        ),
        sa.CheckConstraint(
            "requirement_type in ('SUBJECT','TOTAL_SCORE','SINGLE_SUBJECT','LANGUAGE',"
            "'SEX','MEDICAL','QUALIFICATION','OTHER')",
            name="ck_admission_requirements_requirement_type_valid",
        ),
        sa.CheckConstraint(
            "severity in ('BLOCK','WARN','INFO')", name="ck_admission_requirements_severity_valid"
        ),
        schema="admission",
    )
    op.create_index(
        "ix_admission_requirements_plan_severity",
        "admission_requirements",
        ["plan_id", "severity"],
        schema="admission",
    )


def downgrade() -> None:
    op.drop_table("admission_requirements", schema="admission")
    op.drop_table("admission_outcomes", schema="admission")
    op.drop_table("admission_plans", schema="admission")
    op.drop_table("subject_rule_items", schema="admission")
    op.drop_table("subject_rule_groups", schema="admission")
    op.drop_table("subject_rule_sets", schema="admission")
