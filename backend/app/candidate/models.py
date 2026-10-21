"""candidate schema: profile, subjects, attributes, versioned preferences."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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

SCHEMA = table_schema("candidate")

# 山东夏季高考 6 选 3 的可选科目代码
VALID_SUBJECT_CODES = ("PHY", "CHM", "BIO", "POL", "HIS", "GEO")


class Profile(Base, TimestampMixin):
    """Candidate profile. Immutable history is kept by version + status."""

    __tablename__ = "profiles"
    __table_args__ = (
        # score AND rank are not both required, but at least one must exist
        CheckConstraint("score is not null or rank is not null", name="score_or_rank_required"),
        CheckConstraint("score is null or score between 0 and 750", name="score_range"),
        CheckConstraint("rank is null or rank > 0", name="rank_positive"),
        CheckConstraint("exam_category in ('SUMMER_REGULAR')", name="exam_category_in_v1_scope"),
        CheckConstraint(
            "sex is null or sex in ('MALE','FEMALE','UNKNOWN')",
            name="sex_valid",
        ),
        Index("ix_profiles_cycle_status", "cycle_id", "status"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String(64))
    cycle_id: Mapped[int] = mapped_column(
        ForeignKey(fk_literal("admission.admission_cycles.id"), ondelete="RESTRICT"), nullable=False
    )
    exam_year: Mapped[int] = mapped_column(Integer, nullable=False)
    province_code: Mapped[str] = mapped_column(String(2), nullable=False, default="37")
    exam_category: Mapped[str] = mapped_column(String(32), nullable=False, default="SUMMER_REGULAR")
    score: Mapped[int | None] = mapped_column(Integer)
    rank: Mapped[int | None] = mapped_column(Integer)
    # How `rank` was obtained: MANUAL (candidate typed it) or FROM_SEGMENT (converted)
    rank_source: Mapped[str] = mapped_column(String(16), nullable=False, default="MANUAL")
    # Effective subject set for the current cycle (candidate may switch one subject year to year)
    subjects: Mapped[list[str]] = mapped_column(JSONType, nullable=False)
    foreign_language: Mapped[str | None] = mapped_column(String(16), default="ENG")
    sex: Mapped[str | None] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    notes: Mapped[str | None] = mapped_column(Text)

    subject_scores: Mapped[list[SubjectScore]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
    attributes: Mapped[list[Attribute]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
    preference_profiles: Mapped[list[PreferenceProfile]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )


class Subject(Base, TimestampMixin):
    """Dictionary of the six selectable subjects."""

    __tablename__ = "subjects"
    __table_args__ = (
        UniqueConstraint("code", name="uq_subjects_code"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(8), nullable=False)
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SubjectScore(Base, TimestampMixin):
    """Individual subject score; used by SingleSubjectRule."""

    __tablename__ = "subject_scores"
    __table_args__ = (
        UniqueConstraint("profile_id", "subject_code", name="uq_subject_scores_profile_id_subject_code"),
        CheckConstraint("score is null or score between 0 and 150", name="score_range"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey(fk_target(SCHEMA, "profiles.id"), ondelete="CASCADE"), nullable=False, index=True
    )
    subject_code: Mapped[str] = mapped_column(String(8), nullable=False)
    score: Mapped[int | None] = mapped_column(Integer)
    grade: Mapped[str | None] = mapped_column(String(8))

    profile: Mapped[Profile] = relationship(back_populates="subject_scores")


class Attribute(Base, TimestampMixin):
    """Special conditions (体检结论、单科、户籍、资格 …).

    Only collected when a rule needs it, and always explainable: `source` records
    whether the value is self-reported or verified, which drives UNKNOWN handling.
    """

    __tablename__ = "attributes"
    __table_args__ = (
        UniqueConstraint("profile_id", "attr_key", name="uq_attributes_profile_id_attr_key"),
        CheckConstraint(
            "value_type in ('BOOL','INT','TEXT','ENUM')",
            name="value_type_valid",
        ),
        CheckConstraint("source in ('SELF_REPORTED','VERIFIED')", name="source_valid"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey(fk_target(SCHEMA, "profiles.id"), ondelete="CASCADE"), nullable=False, index=True
    )
    attr_key: Mapped[str] = mapped_column(String(64), nullable=False)
    value_type: Mapped[str] = mapped_column(String(8), nullable=False, default="BOOL")
    value_text: Mapped[str | None] = mapped_column(Text)
    value_bool: Mapped[bool | None] = mapped_column(Boolean)
    value_int: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="SELF_REPORTED")
    note: Mapped[str | None] = mapped_column(Text)

    profile: Mapped[Profile] = relationship(back_populates="attributes")


class PreferenceProfile(Base, TimestampMixin):
    """A versioned preference snapshot. Never mutated after being used by a run."""

    __tablename__ = "preference_profiles"
    __table_args__ = (
        UniqueConstraint("profile_id", "version", name="uq_preference_profiles_profile_id_version"),
        CheckConstraint("risk_mode in ('STEADY','BALANCED','AGGRESSIVE')", name="risk_mode_valid"),
        CheckConstraint("max_tuition is null or max_tuition >= 0", name="max_tuition_non_negative"),
        CheckConstraint("min_tuition is null or min_tuition >= 0", name="min_tuition_non_negative"),
        CheckConstraint(
            "min_tuition is null or max_tuition is null or min_tuition <= max_tuition",
            name="tuition_range",
        ),
        CheckConstraint("target_count > 0", name="target_count_positive"),
        CheckConstraint(
            "min_safe_count is null or min_safe_count >= 0", name="min_safe_count_non_negative"
        ),
        CheckConstraint(
            "candidate_rank is null or candidate_rank > 0", name="candidate_rank_positive"
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey(fk_target(SCHEMA, "profiles.id"), ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    risk_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="BALANCED")
    # Frozen at creation so recommendations stay replayable even if the profile changes
    candidate_rank: Mapped[int | None] = mapped_column(Integer)
    max_tuition: Mapped[int | None] = mapped_column(Integer)
    min_tuition: Mapped[int | None] = mapped_column(Integer)
    exclude_private: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    exclude_cooperative: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    accept_unknown_requirements: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    target_count: Mapped[int] = mapped_column(Integer, nullable=False, default=96)
    # Hard constraint: how many SAFE/STABLE (UNKNOWN excluded) items the scheme must keep
    min_safe_count: Mapped[int | None] = mapped_column(Integer)
    # Per-dimension weights; keys absent from the mapping fall back to defaults and
    # dimensions with no user data at all are dropped from the denominator.
    dimension_weights: Mapped[dict | None] = mapped_column(JSONType)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    profile: Mapped[Profile] = relationship(back_populates="preference_profiles")
    major_preferences: Mapped[list[MajorPreference]] = relationship(
        back_populates="preference_profile", cascade="all, delete-orphan"
    )
    location_preferences: Mapped[list[LocationPreference]] = relationship(
        back_populates="preference_profile", cascade="all, delete-orphan"
    )
    university_preferences: Mapped[list[UniversityPreference]] = relationship(
        back_populates="preference_profile", cascade="all, delete-orphan"
    )
    other_preferences: Mapped[list[OtherPreference]] = relationship(
        back_populates="preference_profile", cascade="all, delete-orphan"
    )


class MajorPreference(Base, TimestampMixin):
    __tablename__ = "major_preferences"
    __table_args__ = (
        CheckConstraint("polarity in ('INCLUDE','EXCLUDE')", name="polarity_valid"),
        CheckConstraint("weight between 0 and 100", name="weight_range"),
        CheckConstraint(
            "(major_id is not null) or (discipline_code is not null) or (major_name is not null)",
            name="target_required",
        ),
        Index("ix_major_preferences_profile_polarity", "preference_profile_id", "polarity"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    preference_profile_id: Mapped[int] = mapped_column(
        ForeignKey(fk_target(SCHEMA, "preference_profiles.id"), ondelete="CASCADE"), nullable=False, index=True
    )
    # Any one of the three selectors may be used; major_id is the strongest signal
    major_id: Mapped[int | None] = mapped_column(ForeignKey(fk_literal("reference.majors.id"), ondelete="SET NULL"))
    discipline_code: Mapped[str | None] = mapped_column(String(2))
    major_name: Mapped[str | None] = mapped_column(String(128))
    polarity: Mapped[str] = mapped_column(String(8), nullable=False, default="INCLUDE")
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)
    is_hard_exclude: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)

    preference_profile: Mapped[PreferenceProfile] = relationship(back_populates="major_preferences")


class LocationPreference(Base, TimestampMixin):
    """Province/city preferences. INCLUDE boosts, EXCLUDE removes (hard)."""

    __tablename__ = "location_preferences"
    __table_args__ = (
        CheckConstraint("polarity in ('INCLUDE','EXCLUDE')", name="polarity_valid"),
        CheckConstraint("weight between 0 and 100", name="weight_range"),
        CheckConstraint("province_code is not null or city_code is not null", name="target_required"),
        Index("ix_location_preferences_profile_polarity", "preference_profile_id", "polarity"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    preference_profile_id: Mapped[int] = mapped_column(
        ForeignKey(fk_target(SCHEMA, "preference_profiles.id"), ondelete="CASCADE"), nullable=False, index=True
    )
    province_code: Mapped[str | None] = mapped_column(String(2))
    province_name: Mapped[str | None] = mapped_column(String(32))
    city_code: Mapped[str | None] = mapped_column(String(8))
    city_name: Mapped[str | None] = mapped_column(String(32))
    polarity: Mapped[str] = mapped_column(String(8), nullable=False, default="INCLUDE")
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)
    is_hard_exclude: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)

    preference_profile: Mapped[PreferenceProfile] = relationship(back_populates="location_preferences")


class UniversityPreference(Base, TimestampMixin):
    """University-level likes/dislikes plus trait requirements (985/211/双一流)."""

    __tablename__ = "university_preferences"
    __table_args__ = (
        CheckConstraint("polarity in ('INCLUDE','EXCLUDE')", name="polarity_valid"),
        CheckConstraint("weight between 0 and 100", name="weight_range"),
        CheckConstraint(
            "university_id is not null or trait is not null", name="target_required"
        ),
        Index("ix_university_preferences_profile_polarity", "preference_profile_id", "polarity"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    preference_profile_id: Mapped[int] = mapped_column(
        ForeignKey(fk_target(SCHEMA, "preference_profiles.id"), ondelete="CASCADE"), nullable=False, index=True
    )
    university_id: Mapped[int | None] = mapped_column(
        ForeignKey(fk_literal("reference.universities.id"), ondelete="SET NULL")
    )
    # e.g. IS_985 / IS_211 / IS_DOUBLE_FIRST_CLASS / HAS_POSTGRAD / IS_PUBLIC
    trait: Mapped[str | None] = mapped_column(String(32))
    polarity: Mapped[str] = mapped_column(String(8), nullable=False, default="INCLUDE")
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)
    is_hard_exclude: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)

    preference_profile: Mapped[PreferenceProfile] = relationship(
        back_populates="university_preferences"
    )


class OtherPreference(Base, TimestampMixin):
    """ACADEMIC / CAREER / TUITION / PERSONAL dimension inputs as key-value rows."""

    __tablename__ = "other_preferences"
    __table_args__ = (
        UniqueConstraint(
            "preference_profile_id", "dimension", "key",
            name="uq_other_preferences_profile_dimension_key",
        ),
        CheckConstraint(
            "dimension in ('ACADEMIC','CAREER','TUITION','PERSONAL')",
            name="dimension_valid",
        ),
        CheckConstraint("polarity in ('INCLUDE','EXCLUDE')", name="polarity_valid"),
        CheckConstraint("weight between 0 and 100", name="weight_range"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    preference_profile_id: Mapped[int] = mapped_column(
        ForeignKey(fk_target(SCHEMA, "preference_profiles.id"), ondelete="CASCADE"), nullable=False, index=True
    )
    dimension: Mapped[str] = mapped_column(String(16), nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value_text: Mapped[str | None] = mapped_column(Text)
    value_num: Mapped[float | None] = mapped_column(Float)
    polarity: Mapped[str] = mapped_column(String(8), nullable=False, default="INCLUDE")
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)
    is_hard_exclude: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)

    preference_profile: Mapped[PreferenceProfile] = relationship(back_populates="other_preferences")


__all__ = [
    "VALID_SUBJECT_CODES",
    "Attribute",
    "LocationPreference",
    "MajorPreference",
    "OtherPreference",
    "PreferenceProfile",
    "Profile",
    "Subject",
    "SubjectScore",
    "UniversityPreference",
]
