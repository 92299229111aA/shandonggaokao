"""admission schema: data provenance, cycles, plans, historical outcomes, rules."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.reference.models import University

SCHEMA = "admission"


class SourceDocument(Base, TimestampMixin):
    """Every important fact must be traceable to a source file."""

    __tablename__ = "source_documents"
    __table_args__ = (
        UniqueConstraint("checksum_sha256", name="uq_source_documents_checksum_sha256"),
        CheckConstraint(
            "quality in ('OFFICIAL','UNIVERSITY','THIRD_PARTY','DEMO')",
            name="quality_valid",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    publisher: Mapped[str | None] = mapped_column(String(128))
    url: Mapped[str | None] = mapped_column(String(512))
    # Local archive path (data/raw/...) — never a URL we depend on at runtime
    archive_path: Mapped[str | None] = mapped_column(String(512))
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    quality: Mapped[str] = mapped_column(String(16), nullable=False)
    published_on: Mapped[date | None] = mapped_column(Date)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)


class DatasetSnapshot(Base, TimestampMixin):
    """A frozen, reproducible slice of admission data used by a recommendation run."""

    __tablename__ = "dataset_snapshots"
    __table_args__ = (
        UniqueConstraint("code", name="uq_dataset_snapshots_code"),
        CheckConstraint(
            "status in ('DRAFT','VALIDATING','PUBLISHED','RETIRED')",
            name="status_valid",
        ),
        Index("ix_dataset_snapshots_status_published_at", "status", "published_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_document_id: Mapped[int | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.source_documents.id", ondelete="SET NULL")
    )
    notes: Mapped[str | None] = mapped_column(Text)


class AdmissionCycle(Base, TimestampMixin):
    """One admission cycle. Every policy number (including the 96 limit) lives here."""

    __tablename__ = "admission_cycles"
    __table_args__ = (
        UniqueConstraint(
            "province_code",
            "exam_year",
            "exam_category",
            "batch_code",
            "round_no",
            "program_level",
            name="uq_admission_cycles_scope",
        ),
        CheckConstraint("application_limit > 0", name="application_limit_positive"),
        CheckConstraint("round_no >= 1", name="round_no_positive"),
        CheckConstraint(
            "exam_category in ('SUMMER_REGULAR')",
            name="exam_category_in_v1_scope",
        ),
        CheckConstraint("batch_code in ('REGULAR_BATCH_1')", name="batch_code_in_v1_scope"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    province_code: Mapped[str] = mapped_column(String(2), nullable=False, default="37")
    exam_year: Mapped[int] = mapped_column(Integer, nullable=False)
    exam_category: Mapped[str] = mapped_column(String(32), nullable=False)
    batch_code: Mapped[str] = mapped_column(String(32), nullable=False)
    round_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    program_level: Mapped[str] = mapped_column(String(16), nullable=False, default="UNDERGRAD")
    # 志愿数量上限 — the single source of truth for UI and optimizer
    application_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    # 平行志愿投档规则文本（如“分数优先、遵循志愿”）
    filling_rule: Mapped[str | None] = mapped_column(Text)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1")
    published_on: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)


class ExamYearStats(Base, TimestampMixin):
    """Per-year candidate population, used to normalise ranks across years."""

    __tablename__ = "exam_year_stats"
    __table_args__ = (
        UniqueConstraint(
            "province_code", "exam_year", "exam_category", "program_level",
            name="uq_exam_year_stats_scope",
        ),
        CheckConstraint("ranked_candidates > 0", name="ranked_candidates_positive"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    province_code: Mapped[str] = mapped_column(String(2), nullable=False, default="37")
    exam_year: Mapped[int] = mapped_column(Integer, nullable=False)
    exam_category: Mapped[str] = mapped_column(String(32), nullable=False)
    program_level: Mapped[str] = mapped_column(String(16), nullable=False, default="UNDERGRAD")
    # 一段线 / 特招线 / 本科线, kept for score→rank conversion sanity checks
    special_line: Mapped[int | None] = mapped_column(Integer)
    regular_line: Mapped[int | None] = mapped_column(Integer)
    ranked_candidates: Mapped[int] = mapped_column(Integer, nullable=False)
    source_document_id: Mapped[int | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.source_documents.id", ondelete="SET NULL")
    )


class ScoreRankSegment(Base, TimestampMixin):
    """一分一段表 row: score -> cumulative rank."""

    __tablename__ = "score_rank_segments"
    __table_args__ = (
        UniqueConstraint("exam_year", "exam_category", "score", name="uq_score_rank_segments_key"),
        CheckConstraint("score between 0 and 750", name="score_range"),
        CheckConstraint("same_score_count >= 0", name="same_score_count_non_negative"),
        CheckConstraint("cumulative_rank > 0", name="cumulative_rank_positive"),
        Index("ix_score_rank_segments_year_cumulative", "exam_year", "cumulative_rank"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exam_year: Mapped[int] = mapped_column(Integer, nullable=False)
    exam_category: Mapped[str] = mapped_column(String(32), nullable=False)
    program_level: Mapped[str] = mapped_column(String(16), nullable=False, default="UNDERGRAD")
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    same_score_count: Mapped[int] = mapped_column(Integer, nullable=False)
    cumulative_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    source_document_id: Mapped[int | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.source_documents.id", ondelete="SET NULL")
    )


class ProgramSeries(Base, TimestampMixin):
    """Stable cross-year identity of one 招生专业（类）+学校+校区 组合.

    `series_key` is what makes historical matching reproducible; ACS for a plan
    is computed over the outcomes attached to its series.
    """

    __tablename__ = "program_series"
    __table_args__ = (
        UniqueConstraint("series_key", name="uq_program_series_series_key"),
        CheckConstraint(
            "match_quality in ('EXACT','RENAMED','MERGED','SPLIT','FUZZY','NONE')",
            name="match_quality_valid",
        ),
        CheckConstraint(
            "first_year is null or last_year is null or first_year <= last_year",
            name="year_order",
        ),
        Index("ix_program_series_university_major", "university_id", "major_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    series_key: Mapped[str] = mapped_column(String(160), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(160), nullable=False)
    university_id: Mapped[int] = mapped_column(
        ForeignKey("reference.universities.id", ondelete="RESTRICT"), nullable=False
    )
    major_id: Mapped[int | None] = mapped_column(ForeignKey("reference.majors.id", ondelete="SET NULL"))
    campus_id: Mapped[int | None] = mapped_column(ForeignKey("reference.campuses.id", ondelete="SET NULL"))
    cooperative: Mapped[str] = mapped_column(String(24), nullable=False, default="NONE")
    match_quality: Mapped[str] = mapped_column(String(16), nullable=False, default="EXACT")
    first_year: Mapped[int | None] = mapped_column(Integer)
    last_year: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    university: Mapped[University] = relationship()
    plans: Mapped[list[AdmissionPlan]] = relationship(back_populates="series")
    outcomes: Mapped[list[AdmissionOutcome]] = relationship(back_populates="series")


class SubjectRuleGroup(Base, TimestampMixin):
    """DNF group: within a group all items must hold (AND), groups are OR-ed."""

    __tablename__ = "subject_rule_groups"
    __table_args__ = (
        UniqueConstraint("rule_set_id", "group_no", name="uq_subject_rule_groups_rule_set_id_group_no"),
        CheckConstraint("group_no >= 0", name="group_no_non_negative"),
        CheckConstraint("mode in ('ANY','ALL')", name="mode_valid"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_set_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.subject_rule_sets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    group_no: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False, default="ALL")
    note: Mapped[str | None] = mapped_column(Text)

    rule_set: Mapped[SubjectRuleSet] = relationship(back_populates="groups")
    items: Mapped[list[SubjectRuleItem]] = relationship(
        back_populates="group", cascade="all, delete-orphan", order_by="SubjectRuleItem.id"
    )


class SubjectRuleItem(Base, TimestampMixin):
    __tablename__ = "subject_rule_items"
    __table_args__ = (
        UniqueConstraint("group_id", "subject_code", name="uq_subject_rule_items_group_id_subject_code"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.subject_rule_groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_code: Mapped[str] = mapped_column(String(8), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)

    group: Mapped[SubjectRuleGroup] = relationship(back_populates="items")


class SubjectRuleSet(Base, TimestampMixin):
    """A named 选科要求: mode ANY = "listed subjects, any of them"; ALL = every listed one."""

    __tablename__ = "subject_rule_sets"
    __table_args__ = (
        UniqueConstraint("code", name="uq_subject_rule_sets_code"),
        CheckConstraint("mode in ('ANY','ALL')", name="mode_valid"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False, default="ALL")
    # 官方原文，例如“物理和化学(2门科目考生均须选考方可报考)”
    raw_text: Mapped[str | None] = mapped_column(Text)
    source_document_id: Mapped[int | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.source_documents.id", ondelete="SET NULL")
    )
    # False when the official text could not be parsed deterministically
    is_authoritative: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    groups: Mapped[list[SubjectRuleGroup]] = relationship(
        back_populates="rule_set", cascade="all, delete-orphan", order_by="SubjectRuleGroup.group_no"
    )


class AdmissionPlan(Base, TimestampMixin):
    """当年招生计划 for one series inside one cycle."""

    __tablename__ = "admission_plans"
    __table_args__ = (
        UniqueConstraint(
            "cycle_id", "series_id", "subject_rule_set_id",
            name="uq_admission_plans_cycle_series_rule",
        ),
        CheckConstraint("plan_count >= 0", name="plan_count_non_negative"),
        CheckConstraint("tuition_fee >= 0", name="tuition_fee_non_negative"),
        CheckConstraint("duration_years between 2 and 8", name="duration_years_range"),
        CheckConstraint(
            "series_match_quality in ('EXACT','RENAMED','MERGED','SPLIT','FUZZY','NONE')",
            name="series_match_quality_valid",
        ),
        Index("ix_admission_plans_cycle_snapshot", "cycle_id", "snapshot_id"),
        Index("ix_admission_plans_tuition", "tuition_fee"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cycle_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.admission_cycles.id", ondelete="CASCADE"), nullable=False
    )
    series_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.program_series.id", ondelete="CASCADE"), nullable=False
    )
    subject_rule_set_id: Mapped[int | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.subject_rule_sets.id", ondelete="SET NULL")
    )
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.dataset_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    source_document_id: Mapped[int | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.source_documents.id", ondelete="SET NULL")
    )
    # Official plan text — the display名称 for this year (专业名称 may be reworded)
    plan_name: Mapped[str] = mapped_column(String(200), nullable=False)
    plan_count: Mapped[int] = mapped_column(Integer, nullable=False)
    tuition_fee: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_years: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    # Free text from 招生章程: 语种、单科、体检、性别、校区、办学地点等
    special_requirements: Mapped[str | None] = mapped_column(Text)
    # The 专业代码 printed in the Shandong plan files is a *per-school sequence number*
    # ('E1', '17'), not the MOE major code. It identifies a plan within one school and is
    # kept for debugging only. It needs its own column: the rules engine reads
    # `special_requirements` as unverified 招生章程 text and answers UNKNOWN for any
    # non-empty value, so storing a sequence number there silently disables the safety
    # bucket for every plan.
    major_sequence: Mapped[str | None] = mapped_column(String(8))
    campus_name: Mapped[str | None] = mapped_column(String(64))
    study_mode: Mapped[str | None] = mapped_column(String(32))
    series_match_quality: Mapped[str] = mapped_column(String(16), nullable=False, default="EXACT")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    series: Mapped[ProgramSeries] = relationship(back_populates="plans")
    subject_rule_set: Mapped[SubjectRuleSet | None] = relationship()
    outcomes: Mapped[list[AdmissionOutcome]] = relationship(back_populates="plan")


class AdmissionOutcome(Base, TimestampMixin):
    """Historical 投档/录取结果 for a series in a given year (not per plan row)."""

    __tablename__ = "admission_outcomes"
    __table_args__ = (
        UniqueConstraint("series_id", "exam_year", name="uq_admission_outcomes_series_id_exam_year"),
        CheckConstraint("min_rank is null or min_rank > 0", name="min_rank_positive"),
        CheckConstraint(
            "max_rank is null or min_rank is null or max_rank >= min_rank", name="rank_order"
        ),
        CheckConstraint("min_score is null or min_score between 0 and 750", name="min_score_range"),
        CheckConstraint("admitted_count is null or admitted_count >= 0", name="admitted_count_non_negative"),
        Index("ix_admission_outcomes_year_min_rank", "exam_year", "min_rank"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    series_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.program_series.id", ondelete="CASCADE"), nullable=False
    )
    # plan_id is optional: a series may have outcomes from years whose plan rows
    # were imported later, but linking lets us cross-check plan vs. outcome.
    plan_id: Mapped[int | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.admission_plans.id", ondelete="SET NULL")
    )
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.dataset_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    source_document_id: Mapped[int | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.source_documents.id", ondelete="SET NULL")
    )
    exam_year: Mapped[int] = mapped_column(Integer, nullable=False)
    batch_code: Mapped[str] = mapped_column(String(32), nullable=False, default="REGULAR_BATCH_1")
    round_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    plan_count: Mapped[int | None] = mapped_column(Integer)
    admitted_count: Mapped[int | None] = mapped_column(Integer)
    min_score: Mapped[int | None] = mapped_column(Integer)
    min_rank: Mapped[int | None] = mapped_column(Integer)
    avg_rank: Mapped[int | None] = mapped_column(Integer)
    max_rank: Mapped[int | None] = mapped_column(Integer)
    # 投档最低位次波动; NULL means "unknown", never 0
    rank_volatility: Mapped[float | None] = mapped_column(Numeric(10, 4))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    series: Mapped[ProgramSeries] = relationship(back_populates="outcomes")
    plan: Mapped[AdmissionPlan | None] = relationship(back_populates="outcomes")


class AdmissionRequirement(Base, TimestampMixin):
    """Structured special requirements attached to a plan.

    `operand` is JSONB because requirement shapes differ per type; `raw_text` is
    always kept so UNKNOWN decisions can be explained to the candidate.
    """

    __tablename__ = "admission_requirements"
    __table_args__ = (
        UniqueConstraint(
            "plan_id", "requirement_type", "operator", name="uq_admission_requirements_plan_type_op"
        ),
        CheckConstraint(
            "requirement_type in ('SUBJECT','TOTAL_SCORE','SINGLE_SUBJECT','LANGUAGE',"
            "'SEX','MEDICAL','QUALIFICATION','OTHER')",
            name="requirement_type_valid",
        ),
        CheckConstraint("severity in ('BLOCK','WARN','INFO')", name="severity_valid"),
        Index("ix_admission_requirements_plan_severity", "plan_id", "severity"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.admission_plans.id", ondelete="CASCADE"), nullable=False
    )
    requirement_type: Mapped[str] = mapped_column(String(24), nullable=False)
    operator: Mapped[str] = mapped_column(String(16), nullable=False, default="EQ")
    operand: Mapped[dict | None] = mapped_column(JSONB)
    severity: Mapped[str] = mapped_column(String(8), nullable=False, default="BLOCK")
    # False => rule text could not be resolved deterministically => UNKNOWN, never PASS
    is_deterministic: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    raw_text: Mapped[str | None] = mapped_column(Text)
    source_document_id: Mapped[int | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.source_documents.id", ondelete="SET NULL")
    )


__all__ = [
    "AdmissionCycle",
    "AdmissionOutcome",
    "AdmissionPlan",
    "AdmissionRequirement",
    "DatasetSnapshot",
    "ExamYearStats",
    "ProgramSeries",
    "ScoreRankSegment",
    "SourceDocument",
    "SubjectRuleGroup",
    "SubjectRuleItem",
    "SubjectRuleSet",
]
