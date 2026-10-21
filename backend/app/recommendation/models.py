"""recommendation schema: model versions, sessions, scored items, schemes, health checks."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.db.dialect import JSONType, fk_literal, fk_target, table_schema

SCHEMA = table_schema("recommendation")


class ModelVersion(Base, TimestampMixin):
    """A frozen set of algorithm parameters. ACS/Preference changes need a new row."""

    __tablename__ = "model_versions"
    __table_args__ = (
        UniqueConstraint("code", name="uq_model_versions_code"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    # Full ACS/Confidence/Preference/optimizer parameter set as published in docs/ALGORITHM.md
    parameters: Mapped[dict] = mapped_column(JSONType, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)


class Session(Base, TimestampMixin):
    """One recommendation run. Locks every version needed to replay it."""

    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint("status in ('RUNNING','SUCCEEDED','FAILED')", name="status_valid"),
        Index("ix_sessions_profile_created", "profile_id", "created_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey(fk_literal("candidate.profiles.id"), ondelete="CASCADE"), nullable=False
    )
    preference_profile_id: Mapped[int] = mapped_column(
        ForeignKey(fk_literal("candidate.preference_profiles.id"), ondelete="RESTRICT"), nullable=False
    )
    cycle_id: Mapped[int] = mapped_column(
        ForeignKey(fk_literal("admission.admission_cycles.id"), ondelete="RESTRICT"), nullable=False
    )
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey(fk_literal("admission.dataset_snapshots.id"), ondelete="RESTRICT"), nullable=False
    )
    model_version_id: Mapped[int] = mapped_column(
        ForeignKey(fk_target(SCHEMA, "model_versions.id"), ondelete="RESTRICT"), nullable=False
    )
    # Locked scalars — the replay contract
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    preference_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    algorithm_config_version: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="RUNNING")
    stage: Mapped[str | None] = mapped_column(String(32))
    # Pipeline counters: scanned / fail / unknown / hard-excluded / pooled ...
    stats: Mapped[dict | None] = mapped_column(JSONType)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list[RecommendationItem]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class RecommendationItem(Base, TimestampMixin):
    """Scored candidate. One row per (session, plan).

    Eligibility, ACS and Preference are stored side by side but never derived
    from one another — that separation is the core architectural rule.
    """

    __tablename__ = "recommendation_items"
    __table_args__ = (
        UniqueConstraint("session_id", "plan_id", name="uq_recommendation_items_session_id_plan_id"),
        CheckConstraint("eligibility in ('PASS','FAIL','UNKNOWN')", name="eligibility_valid"),
        CheckConstraint(
            "risk_bucket in ('HIGH_REACH','REACH','BORDER','STABLE','SAFE')", name="risk_bucket_valid"
        ),
        CheckConstraint("acs is null or acs between 0 and 100", name="acs_range"),
        CheckConstraint("base_acs is null or base_acs between 0 and 100", name="base_acs_range"),
        CheckConstraint("confidence is null or confidence between 0 and 100", name="confidence_range"),
        CheckConstraint(
            "preference_score is null or preference_score between 0 and 100",
            name="preference_score_range",
        ),
        # FAIL rows may be persisted for audit, but must never be pooled
        CheckConstraint(
            "eligibility <> 'FAIL' or in_candidate_pool = false", name="fail_never_pooled"
        ),
        Index("ix_recommendation_items_session_bucket", "session_id", "risk_bucket"),
        Index("ix_recommendation_items_session_pool", "session_id", "in_candidate_pool"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey(fk_target(SCHEMA, "sessions.id"), ondelete="CASCADE"), nullable=False
    )
    plan_id: Mapped[int] = mapped_column(
        ForeignKey(fk_literal("admission.admission_plans.id"), ondelete="CASCADE"), nullable=False
    )

    # ---- layer 1: Eligibility (can I apply at all?) ----
    eligibility: Mapped[str] = mapped_column(String(8), nullable=False, default="UNKNOWN")
    eligibility_reasons: Mapped[list | None] = mapped_column(JSONType)
    # ---- layer 2: ACS (how safe is it historically?) ----
    base_acs: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    acs: Mapped[float | None] = mapped_column(Float)
    risk_bucket: Mapped[str | None] = mapped_column(String(16))
    acs_explanation: Mapped[dict | None] = mapped_column(JSONType)
    # ---- layer 3: Preference (how much does the candidate want it?) ----
    preference_score: Mapped[float | None] = mapped_column(Float)
    preference_dimensions: Mapped[dict | None] = mapped_column(JSONType)
    # ---- pipeline bookkeeping ----
    in_candidate_pool: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    excluded_by: Mapped[str | None] = mapped_column(String(32))
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_plan_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey(fk_literal("admission.dataset_snapshots.id"), ondelete="SET NULL")
    )

    session: Mapped[Session] = relationship(back_populates="items")


class Scheme(Base, TimestampMixin):
    """A volunteer scheme. Content lives in immutable SchemeVersion rows."""

    __tablename__ = "schemes"
    __table_args__ = (
        CheckConstraint("current_version_no >= 1", name="current_version_no_positive"),
        CheckConstraint("lock_version >= 1", name="lock_version_positive"),
        CheckConstraint("target_count > 0", name="target_count_positive"),
        Index("ix_schemes_session", "session_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey(fk_target(SCHEMA, "sessions.id"), ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(128), nullable=False, default="志愿方案")
    # Policy number mirrored from admission_cycles at creation time for display,
    # but always re-validated against the live cycle by HealthCheck.
    target_count: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="BALANCED")
    current_version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Optimistic-locking token for editors; independent from version_no
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    versions: Mapped[list[SchemeVersion]] = relationship(
        back_populates="scheme", cascade="all, delete-orphan", order_by="SchemeVersion.version_no"
    )


class SchemeVersion(Base, TimestampMixin):
    """Immutable snapshot of a scheme after one edit/optimize operation."""

    __tablename__ = "scheme_versions"
    __table_args__ = (
        UniqueConstraint("scheme_id", "version_no", name="uq_scheme_versions_scheme_id_version_no"),
        CheckConstraint("version_no >= 1", name="version_no_positive"),
        CheckConstraint("item_count >= 0", name="item_count_non_negative"),
        Index("ix_scheme_versions_scheme_created", "scheme_id", "created_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scheme_id: Mapped[int] = mapped_column(
        ForeignKey(fk_target(SCHEMA, "schemes.id"), ondelete="CASCADE"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    # OPTIMIZE / MANUAL_EDIT / REORDER / LOCK / UNLOCK / REMOVE / ADD / IMPORT
    operation: Mapped[str] = mapped_column(String(24), nullable=False, default="MANUAL_EDIT")
    change_summary: Mapped[str | None] = mapped_column(Text)
    snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey(fk_literal("admission.dataset_snapshots.id"), ondelete="SET NULL")
    )
    model_version: Mapped[str | None] = mapped_column(String(64))
    optimizer_stats: Mapped[dict | None] = mapped_column(JSONType)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    scheme: Mapped[Scheme] = relationship(back_populates="versions")
    items: Mapped[list[SchemeItem]] = relationship(
        back_populates="scheme_version", cascade="all, delete-orphan", order_by="SchemeItem.position"
    )


class SchemeItem(Base, TimestampMixin):
    """One volunteer inside one scheme version. Rows are never updated across versions."""

    __tablename__ = "scheme_items"
    __table_args__ = (
        UniqueConstraint(
            "scheme_version_id", "position", name="uq_scheme_items_scheme_version_id_position"
        ),
        CheckConstraint("position >= 1", name="position_positive"),
        # Score fields are denormalised onto the version so历史方案 stays readable
        CheckConstraint("acs is null or acs between 0 and 100", name="acs_range"),
        CheckConstraint(
            "preference_score is null or preference_score between 0 and 100",
            name="preference_score_range",
        ),
        CheckConstraint(
            "eligibility is null or eligibility in ('PASS','FAIL','UNKNOWN')",
            name="eligibility_valid",
        ),
        Index("ix_scheme_items_version_position", "scheme_version_id", "position"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scheme_version_id: Mapped[int] = mapped_column(
        ForeignKey(fk_target(SCHEMA, "scheme_versions.id"), ondelete="CASCADE"), nullable=False
    )
    recommendation_item_id: Mapped[int | None] = mapped_column(
        ForeignKey(fk_target(SCHEMA, "recommendation_items.id"), ondelete="SET NULL")
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # True when the user pinned an absolute position (sorting step 1)
    fixed_position: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    eligibility: Mapped[str | None] = mapped_column(String(8))
    acs: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    preference_score: Mapped[float | None] = mapped_column(Float)
    desire_band: Mapped[int | None] = mapped_column(Integer)
    risk_bucket: Mapped[str | None] = mapped_column(String(16))
    note: Mapped[str | None] = mapped_column(Text)

    scheme_version: Mapped[SchemeVersion] = relationship(back_populates="items")
    recommendation_item: Mapped[RecommendationItem | None] = relationship()


class HealthCheck(Base, TimestampMixin):
    """Diagnostic report. Never mutates the scheme it inspects."""

    __tablename__ = "health_checks"
    __table_args__ = (
        CheckConstraint("score between 0 and 100", name="score_range"),
        CheckConstraint("error_count >= 0", name="error_count_non_negative"),
        Index("ix_health_checks_scheme_created", "scheme_id", "created_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scheme_id: Mapped[int] = mapped_column(
        ForeignKey(fk_target(SCHEMA, "schemes.id"), ondelete="CASCADE"), nullable=False
    )
    scheme_version_id: Mapped[int] = mapped_column(
        ForeignKey(fk_target(SCHEMA, "scheme_versions.id"), ondelete="RESTRICT"), nullable=False
    )
    snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey(fk_literal("admission.dataset_snapshots.id"), ondelete="SET NULL")
    )
    # Product indicator only — explicitly NOT a录取概率 (see docs/ALGORITHM.md)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    info_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary: Mapped[dict | None] = mapped_column(JSONType)

    issues: Mapped[list[HealthCheckIssue]] = relationship(
        back_populates="health_check", cascade="all, delete-orphan"
    )


class HealthCheckIssue(Base, TimestampMixin):
    __tablename__ = "health_check_issues"
    __table_args__ = (
        CheckConstraint("severity in ('ERROR','WARNING','INFO')", name="severity_valid"),
        Index("ix_health_check_issues_check_severity", "health_check_id", "severity"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    health_check_id: Mapped[int] = mapped_column(
        ForeignKey(fk_target(SCHEMA, "health_checks.id"), ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(48), nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    position: Mapped[int | None] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSONType)
    suggestion: Mapped[str | None] = mapped_column(Text)

    health_check: Mapped[HealthCheck] = relationship(back_populates="issues")


__all__ = [
    "HealthCheck",
    "HealthCheckIssue",
    "ModelVersion",
    "RecommendationItem",
    "Scheme",
    "SchemeItem",
    "SchemeVersion",
    "Session",
]
