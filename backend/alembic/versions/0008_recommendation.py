"""0008 — recommendation schema: model versions, sessions, scored items.

Nothing here is ever overwritten in place: a session locks
(profile_version, preference_version, snapshot_id, policy_version, model_version)
so any past run can be replayed from the stored inputs.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_recommendation"
down_revision: str | None = "0007_candidate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("parameters", postgresql.JSONB(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("code", name="uq_model_versions_code"),
        schema="recommendation",
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("candidate.profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "preference_profile_id",
            sa.Integer(),
            sa.ForeignKey("candidate.preference_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "cycle_id",
            sa.Integer(),
            sa.ForeignKey("admission.admission_cycles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "snapshot_id",
            sa.Integer(),
            sa.ForeignKey("admission.dataset_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "model_version_id",
            sa.Integer(),
            sa.ForeignKey("recommendation.model_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("preference_version", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("algorithm_config_version", sa.String(32)),
        sa.Column("status", sa.String(16), nullable=False, server_default="RUNNING"),
        sa.Column("stage", sa.String(32)),
        sa.Column("stats", postgresql.JSONB()),
        sa.Column("error_message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status in ('RUNNING','SUCCEEDED','FAILED')", name="ck_sessions_status_valid"),
        schema="recommendation",
    )
    op.create_index(
        "ix_sessions_profile_created", "sessions", ["profile_id", "created_at"], schema="recommendation"
    )

    op.create_table(
        "recommendation_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("recommendation.sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "plan_id",
            sa.Integer(),
            sa.ForeignKey("admission.admission_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # ---- Eligibility layer ----
        sa.Column("eligibility", sa.String(8), nullable=False, server_default="UNKNOWN"),
        sa.Column("eligibility_reasons", postgresql.JSONB()),
        # ---- ACS layer ----
        sa.Column("base_acs", sa.Float()),
        sa.Column("confidence", sa.Float()),
        sa.Column("acs", sa.Float()),
        sa.Column("risk_bucket", sa.String(16)),
        sa.Column("acs_explanation", postgresql.JSONB()),
        # ---- Preference layer ----
        sa.Column("preference_score", sa.Float()),
        sa.Column("preference_dimensions", postgresql.JSONB()),
        # ---- pipeline bookkeeping ----
        sa.Column("in_candidate_pool", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("excluded_by", sa.String(32)),
        sa.Column("is_locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "source_plan_snapshot_id",
            sa.Integer(),
            sa.ForeignKey("admission.dataset_snapshots.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("session_id", "plan_id", name="uq_recommendation_items_session_id_plan_id"),
        sa.CheckConstraint(
            "eligibility in ('PASS','FAIL','UNKNOWN')", name="ck_recommendation_items_eligibility_valid"
        ),
        sa.CheckConstraint(
            "risk_bucket is null or risk_bucket in ('HIGH_REACH','REACH','BORDER','STABLE','SAFE')",
            name="ck_recommendation_items_risk_bucket_valid",
        ),
        sa.CheckConstraint("acs is null or acs between 0 and 100", name="ck_recommendation_items_acs_range"),
        sa.CheckConstraint(
            "base_acs is null or base_acs between 0 and 100", name="ck_recommendation_items_base_acs_range"
        ),
        sa.CheckConstraint(
            "confidence is null or confidence between 0 and 100",
            name="ck_recommendation_items_confidence_range",
        ),
        sa.CheckConstraint(
            "preference_score is null or preference_score between 0 and 100",
            name="ck_recommendation_items_preference_score_range",
        ),
        # FAIL rows may be persisted for audit, but can never be pooled
        sa.CheckConstraint(
            "eligibility <> 'FAIL' or in_candidate_pool = false",
            name="ck_recommendation_items_fail_never_pooled",
        ),
        schema="recommendation",
    )
    op.create_index(
        "ix_recommendation_items_session_bucket",
        "recommendation_items",
        ["session_id", "risk_bucket"],
        schema="recommendation",
    )
    op.create_index(
        "ix_recommendation_items_session_pool",
        "recommendation_items",
        ["session_id", "in_candidate_pool"],
        schema="recommendation",
    )

    op.create_table(
        "schemes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("recommendation.sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(128), nullable=False, server_default="志愿方案"),
        sa.Column("target_count", sa.Integer(), nullable=False),
        sa.Column("risk_mode", sa.String(16), nullable=False, server_default="BALANCED"),
        sa.Column("current_version_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("current_version_no >= 1", name="ck_schemes_current_version_no_positive"),
        sa.CheckConstraint("lock_version >= 1", name="ck_schemes_lock_version_positive"),
        sa.CheckConstraint("target_count > 0", name="ck_schemes_target_count_positive"),
        schema="recommendation",
    )
    op.create_index("ix_schemes_session", "schemes", ["session_id"], schema="recommendation")

    op.create_table(
        "scheme_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scheme_id",
            sa.Integer(),
            sa.ForeignKey("recommendation.schemes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(24), nullable=False, server_default="MANUAL_EDIT"),
        sa.Column("change_summary", sa.Text()),
        sa.Column(
            "snapshot_id",
            sa.Integer(),
            sa.ForeignKey("admission.dataset_snapshots.id", ondelete="SET NULL"),
        ),
        sa.Column("model_version", sa.String(64)),
        sa.Column("optimizer_stats", postgresql.JSONB()),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("scheme_id", "version_no", name="uq_scheme_versions_scheme_id_version_no"),
        sa.CheckConstraint("version_no >= 1", name="ck_scheme_versions_version_no_positive"),
        sa.CheckConstraint("item_count >= 0", name="ck_scheme_versions_item_count_non_negative"),
        schema="recommendation",
    )
    op.create_index(
        "ix_scheme_versions_scheme_created",
        "scheme_versions",
        ["scheme_id", "created_at"],
        schema="recommendation",
    )

    op.create_table(
        "scheme_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scheme_version_id",
            sa.Integer(),
            sa.ForeignKey("recommendation.scheme_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "recommendation_item_id",
            sa.Integer(),
            sa.ForeignKey("recommendation.recommendation_items.id", ondelete="SET NULL"),
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("fixed_position", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("eligibility", sa.String(8)),
        sa.Column("acs", sa.Float()),
        sa.Column("confidence", sa.Float()),
        sa.Column("preference_score", sa.Float()),
        sa.Column("desire_band", sa.Integer()),
        sa.Column("risk_bucket", sa.String(16)),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "scheme_version_id", "position", name="uq_scheme_items_scheme_version_id_position"
        ),
        sa.CheckConstraint("position >= 1", name="ck_scheme_items_position_positive"),
        sa.CheckConstraint("acs is null or acs between 0 and 100", name="ck_scheme_items_acs_range"),
        sa.CheckConstraint(
            "preference_score is null or preference_score between 0 and 100",
            name="ck_scheme_items_preference_score_range",
        ),
        sa.CheckConstraint(
            "eligibility is null or eligibility in ('PASS','FAIL','UNKNOWN')",
            name="ck_scheme_items_eligibility_valid",
        ),
        schema="recommendation",
    )
    op.create_index(
        "ix_scheme_items_version_position",
        "scheme_items",
        ["scheme_version_id", "position"],
        schema="recommendation",
    )

    op.create_table(
        "health_checks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scheme_id",
            sa.Integer(),
            sa.ForeignKey("recommendation.schemes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "scheme_version_id",
            sa.Integer(),
            sa.ForeignKey("recommendation.scheme_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "snapshot_id",
            sa.Integer(),
            sa.ForeignKey("admission.dataset_snapshots.id", ondelete="SET NULL"),
        ),
        # Product indicator (0-100). Explicitly NOT a录取概率.
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warning_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("info_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("summary", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("score between 0 and 100", name="ck_health_checks_score_range"),
        sa.CheckConstraint("error_count >= 0", name="ck_health_checks_error_count_non_negative"),
        sa.CheckConstraint("warning_count >= 0", name="ck_health_checks_warning_count_non_negative"),
        sa.CheckConstraint("info_count >= 0", name="ck_health_checks_info_count_non_negative"),
        schema="recommendation",
    )
    op.create_index(
        "ix_health_checks_scheme_created", "health_checks", ["scheme_id", "created_at"], schema="recommendation"
    )

    op.create_table(
        "health_check_issues",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "health_check_id",
            sa.Integer(),
            sa.ForeignKey("recommendation.health_checks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(48), nullable=False),
        sa.Column("severity", sa.String(8), nullable=False),
        sa.Column("position", sa.Integer()),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("detail", postgresql.JSONB()),
        sa.Column("suggestion", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("severity in ('ERROR','WARNING','INFO')", name="ck_health_check_issues_severity_valid"),
        schema="recommendation",
    )
    op.create_index(
        "ix_health_check_issues_check_severity",
        "health_check_issues",
        ["health_check_id", "severity"],
        schema="recommendation",
    )


def downgrade() -> None:
    op.drop_table("health_check_issues", schema="recommendation")
    op.drop_table("health_checks", schema="recommendation")
    op.drop_table("scheme_items", schema="recommendation")
    op.drop_table("scheme_versions", schema="recommendation")
    op.drop_table("schemes", schema="recommendation")
    op.drop_table("recommendation_items", schema="recommendation")
    op.drop_table("sessions", schema="recommendation")
    op.drop_table("model_versions", schema="recommendation")
