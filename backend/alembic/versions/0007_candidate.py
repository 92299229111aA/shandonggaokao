"""0007 — candidate schema: profiles, subjects, attributes, versioned preferences.

Preference rows are versioned rather than updated: a recommendation run locks a
preference_profile row, so editing preferences afterwards must create version N+1.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_candidate"
down_revision: str | None = "0006_admission_rules_plans"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("display_name", sa.String(64)),
        sa.Column(
            "cycle_id",
            sa.Integer(),
            sa.ForeignKey("admission.admission_cycles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("exam_year", sa.Integer(), nullable=False),
        sa.Column("province_code", sa.String(2), nullable=False, server_default="37"),
        sa.Column("exam_category", sa.String(32), nullable=False, server_default="SUMMER_REGULAR"),
        sa.Column("score", sa.Integer()),
        sa.Column("rank", sa.Integer()),
        sa.Column("rank_source", sa.String(16), nullable=False, server_default="MANUAL"),
        sa.Column("subjects", postgresql.JSONB(), nullable=False),
        sa.Column("foreign_language", sa.String(16), server_default="ENG"),
        sa.Column("sex", sa.String(8)),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        # DTO also requires subjects; the DB enforces score-or-rank
        sa.CheckConstraint("score is not null or rank is not null", name="ck_profiles_score_or_rank_required"),
        sa.CheckConstraint("score is null or score between 0 and 750", name="ck_profiles_score_range"),
        sa.CheckConstraint("rank is null or rank > 0", name="ck_profiles_rank_positive"),
        sa.CheckConstraint(
            "exam_category in ('SUMMER_REGULAR')", name="ck_profiles_exam_category_in_v1_scope"
        ),
        sa.CheckConstraint(
            "rank_source in ('MANUAL','FROM_SEGMENT')", name="ck_profiles_rank_source_valid"
        ),
        sa.CheckConstraint("sex is null or sex in ('MALE','FEMALE','UNKNOWN')", name="ck_profiles_sex_valid"),
        schema="candidate",
    )
    op.create_index("ix_profiles_cycle_status", "profiles", ["cycle_id", "status"], schema="candidate")

    op.create_table(
        "subjects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(8), nullable=False),
        sa.Column("name", sa.String(32), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("code", name="uq_subjects_code"),
        schema="candidate",
    )

    op.create_table(
        "subject_scores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("candidate.profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject_code", sa.String(8), nullable=False),
        sa.Column("score", sa.Integer()),
        sa.Column("grade", sa.String(8)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("profile_id", "subject_code", name="uq_subject_scores_profile_id_subject_code"),
        sa.CheckConstraint("score is null or score between 0 and 150", name="ck_subject_scores_score_range"),
        schema="candidate",
    )
    op.create_index("ix_subject_scores_profile_id", "subject_scores", ["profile_id"], schema="candidate")

    op.create_table(
        "attributes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("candidate.profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attr_key", sa.String(64), nullable=False),
        sa.Column("value_type", sa.String(8), nullable=False, server_default="BOOL"),
        sa.Column("value_text", sa.Text()),
        sa.Column("value_bool", sa.Boolean()),
        sa.Column("value_int", sa.Integer()),
        sa.Column("source", sa.String(16), nullable=False, server_default="SELF_REPORTED"),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("profile_id", "attr_key", name="uq_attributes_profile_id_attr_key"),
        sa.CheckConstraint("value_type in ('BOOL','INT','TEXT','ENUM')", name="ck_attributes_value_type_valid"),
        sa.CheckConstraint(
            "source in ('SELF_REPORTED','VERIFIED')", name="ck_attributes_source_valid"
        ),
        schema="candidate",
    )
    op.create_index("ix_attributes_profile_id", "attributes", ["profile_id"], schema="candidate")

    op.create_table(
        "preference_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("candidate.profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("risk_mode", sa.String(16), nullable=False, server_default="BALANCED"),
        sa.Column("candidate_rank", sa.Integer()),
        sa.Column("max_tuition", sa.Integer()),
        sa.Column("min_tuition", sa.Integer()),
        sa.Column("exclude_private", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("exclude_cooperative", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("accept_unknown_requirements", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("target_count", sa.Integer(), nullable=False, server_default="96"),
        sa.Column("min_safe_count", sa.Integer()),
        sa.Column("dimension_weights", postgresql.JSONB()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("profile_id", "version", name="uq_preference_profiles_profile_id_version"),
        sa.CheckConstraint(
            "risk_mode in ('STEADY','BALANCED','AGGRESSIVE')", name="ck_preference_profiles_risk_mode_valid"
        ),
        sa.CheckConstraint(
            "max_tuition is null or max_tuition >= 0", name="ck_preference_profiles_max_tuition_non_negative"
        ),
        sa.CheckConstraint(
            "min_tuition is null or min_tuition >= 0", name="ck_preference_profiles_min_tuition_non_negative"
        ),
        sa.CheckConstraint(
            "min_tuition is null or max_tuition is null or min_tuition <= max_tuition",
            name="ck_preference_profiles_tuition_range",
        ),
        sa.CheckConstraint("target_count > 0", name="ck_preference_profiles_target_count_positive"),
        sa.CheckConstraint(
            "min_safe_count is null or min_safe_count >= 0",
            name="ck_preference_profiles_min_safe_count_non_negative",
        ),
        sa.CheckConstraint(
            "candidate_rank is null or candidate_rank > 0",
            name="ck_preference_profiles_candidate_rank_positive",
        ),
        schema="candidate",
    )
    op.create_index(
        "ix_preference_profiles_profile_id", "preference_profiles", ["profile_id"], schema="candidate"
    )

    op.create_table(
        "major_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "preference_profile_id",
            sa.Integer(),
            sa.ForeignKey("candidate.preference_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("major_id", sa.Integer(), sa.ForeignKey("reference.majors.id", ondelete="SET NULL")),
        sa.Column("discipline_code", sa.String(2)),
        sa.Column("major_name", sa.String(128)),
        sa.Column("polarity", sa.String(8), nullable=False, server_default="INCLUDE"),
        sa.Column("weight", sa.Float(), nullable=False, server_default="100"),
        sa.Column("is_hard_exclude", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("polarity in ('INCLUDE','EXCLUDE')", name="ck_major_preferences_polarity_valid"),
        sa.CheckConstraint("weight between 0 and 100", name="ck_major_preferences_weight_range"),
        sa.CheckConstraint(
            "(major_id is not null) or (discipline_code is not null) or (major_name is not null)",
            name="ck_major_preferences_target_required",
        ),
        schema="candidate",
    )
    op.create_index(
        "ix_major_preferences_preference_profile_id",
        "major_preferences",
        ["preference_profile_id"],
        schema="candidate",
    )
    op.create_index(
        "ix_major_preferences_profile_polarity",
        "major_preferences",
        ["preference_profile_id", "polarity"],
        schema="candidate",
    )

    op.create_table(
        "location_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "preference_profile_id",
            sa.Integer(),
            sa.ForeignKey("candidate.preference_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("province_code", sa.String(2)),
        sa.Column("province_name", sa.String(32)),
        sa.Column("city_code", sa.String(8)),
        sa.Column("city_name", sa.String(32)),
        sa.Column("polarity", sa.String(8), nullable=False, server_default="INCLUDE"),
        sa.Column("weight", sa.Float(), nullable=False, server_default="100"),
        sa.Column("is_hard_exclude", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("polarity in ('INCLUDE','EXCLUDE')", name="ck_location_preferences_polarity_valid"),
        sa.CheckConstraint("weight between 0 and 100", name="ck_location_preferences_weight_range"),
        sa.CheckConstraint(
            "province_code is not null or city_code is not null",
            name="ck_location_preferences_target_required",
        ),
        schema="candidate",
    )
    op.create_index(
        "ix_location_preferences_preference_profile_id",
        "location_preferences",
        ["preference_profile_id"],
        schema="candidate",
    )
    op.create_index(
        "ix_location_preferences_profile_polarity",
        "location_preferences",
        ["preference_profile_id", "polarity"],
        schema="candidate",
    )

    op.create_table(
        "university_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "preference_profile_id",
            sa.Integer(),
            sa.ForeignKey("candidate.preference_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "university_id", sa.Integer(), sa.ForeignKey("reference.universities.id", ondelete="SET NULL")
        ),
        sa.Column("trait", sa.String(32)),
        sa.Column("polarity", sa.String(8), nullable=False, server_default="INCLUDE"),
        sa.Column("weight", sa.Float(), nullable=False, server_default="100"),
        sa.Column("is_hard_exclude", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "polarity in ('INCLUDE','EXCLUDE')", name="ck_university_preferences_polarity_valid"
        ),
        sa.CheckConstraint("weight between 0 and 100", name="ck_university_preferences_weight_range"),
        sa.CheckConstraint(
            "university_id is not null or trait is not null",
            name="ck_university_preferences_target_required",
        ),
        schema="candidate",
    )
    op.create_index(
        "ix_university_preferences_preference_profile_id",
        "university_preferences",
        ["preference_profile_id"],
        schema="candidate",
    )
    op.create_index(
        "ix_university_preferences_profile_polarity",
        "university_preferences",
        ["preference_profile_id", "polarity"],
        schema="candidate",
    )

    op.create_table(
        "other_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "preference_profile_id",
            sa.Integer(),
            sa.ForeignKey("candidate.preference_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("dimension", sa.String(16), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("value_text", sa.Text()),
        sa.Column("value_num", sa.Float()),
        sa.Column("polarity", sa.String(8), nullable=False, server_default="INCLUDE"),
        sa.Column("weight", sa.Float(), nullable=False, server_default="100"),
        sa.Column("is_hard_exclude", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "preference_profile_id", "dimension", "key", name="uq_other_preferences_profile_dimension_key"
        ),
        sa.CheckConstraint(
            "dimension in ('ACADEMIC','CAREER','TUITION','PERSONAL')",
            name="ck_other_preferences_dimension_valid",
        ),
        sa.CheckConstraint("polarity in ('INCLUDE','EXCLUDE')", name="ck_other_preferences_polarity_valid"),
        sa.CheckConstraint("weight between 0 and 100", name="ck_other_preferences_weight_range"),
        schema="candidate",
    )
    op.create_index(
        "ix_other_preferences_preference_profile_id",
        "other_preferences",
        ["preference_profile_id"],
        schema="candidate",
    )


def downgrade() -> None:
    op.drop_table("other_preferences", schema="candidate")
    op.drop_table("university_preferences", schema="candidate")
    op.drop_table("location_preferences", schema="candidate")
    op.drop_table("major_preferences", schema="candidate")
    op.drop_table("preference_profiles", schema="candidate")
    op.drop_table("attributes", schema="candidate")
    op.drop_table("subject_scores", schema="candidate")
    op.drop_table("subjects", schema="candidate")
    op.drop_table("profiles", schema="candidate")
