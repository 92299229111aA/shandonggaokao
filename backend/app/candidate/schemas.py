"""candidate schema DTOs."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.candidate.models import VALID_SUBJECT_CODES

SubjectCode = Literal["PHY", "CHM", "BIO", "POL", "HIS", "GEO"]
# 单科成绩 can also be a foreign language (英语单科要求很常见), so the score row
# accepts a wider set than the three selectable 选考科目.
ScoreSubjectCode = Literal["PHY", "CHM", "BIO", "POL", "HIS", "GEO", "ENG", "JPN", "RUS", "GER", "FRA", "SPA"]
RiskModeT = Literal["STEADY", "BALANCED", "AGGRESSIVE"]


class SubjectScoreDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    subject_code: str
    score: int | None = None
    grade: str | None = None


class SubjectScoreIn(BaseModel):
    subject_code: ScoreSubjectCode
    score: Annotated[int, Field(ge=0, le=150)] | None = None
    grade: str | None = None


class AttributeDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    attr_key: str
    value_type: str
    value_text: str | None = None
    value_bool: bool | None = None
    value_int: int | None = None
    source: str
    note: str | None = None


class AttributeIn(BaseModel):
    attr_key: Annotated[str, Field(min_length=1, max_length=64)]
    value_type: Literal["BOOL", "INT", "TEXT", "ENUM"] = "BOOL"
    value_text: str | None = None
    value_bool: bool | None = None
    value_int: int | None = None
    source: Literal["SELF_REPORTED", "VERIFIED"] = "SELF_REPORTED"
    note: str | None = None

    @model_validator(mode="after")
    def _value_matches_type(self) -> AttributeIn:
        present = {
            "BOOL": self.value_bool is not None,
            "INT": self.value_int is not None,
            "TEXT": self.value_text is not None,
            "ENUM": self.value_text is not None,
        }[self.value_type]
        if not present:
            raise ValueError(f"value_type={self.value_type} 需要对应类型的取值")
        return self


class ProfileCreate(BaseModel):
    """At least one of score / rank is required; subjects are mandatory.

    When only `score` is given the API converts it to a rank through the
    published 一分一段表 of the target cycle (see ScoreRankService).
    """

    display_name: str | None = Field(default=None, max_length=64)
    cycle_id: int
    score: Annotated[int, Field(ge=0, le=750)] | None = None
    rank: Annotated[int, Field(gt=0)] | None = None
    subjects: Annotated[list[SubjectCode], Field(min_length=1, max_length=6)]
    foreign_language: str | None = Field(default="ENG", max_length=16)
    sex: Literal["MALE", "FEMALE", "UNKNOWN"] | None = None
    notes: str | None = None
    subject_scores: list[SubjectScoreIn] = Field(default_factory=list)
    attributes: list[AttributeIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _score_or_rank(self) -> ProfileCreate:
        if self.score is None and self.rank is None:
            raise ValueError("score 与 rank 至少需要提供一个")
        if len(set(self.subjects)) != len(self.subjects):
            raise ValueError("选科不能重复")
        unknown = [code for code in self.subjects if code not in VALID_SUBJECT_CODES]
        if unknown:
            raise ValueError(f"未知选科代码: {sorted(unknown)}")
        return self


class ProfileUpdate(BaseModel):
    """Partial update. Editing bumps profile.version (immutable history)."""

    display_name: str | None = Field(default=None, max_length=64)
    score: Annotated[int, Field(ge=0, le=750)] | None = None
    rank: Annotated[int, Field(gt=0)] | None = None
    subjects: Annotated[list[SubjectCode], Field(min_length=1, max_length=6)] | None = None
    foreign_language: str | None = Field(default=None, max_length=16)
    sex: Literal["MALE", "FEMALE", "UNKNOWN"] | None = None
    notes: str | None = None
    expected_version: Annotated[int, Field(ge=1)] | None = Field(
        default=None, description="乐观锁：与当前 version 不一致时返回 409"
    )


class ProfileDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    display_name: str | None = None
    cycle_id: int
    exam_year: int
    province_code: str
    exam_category: str
    score: int | None = None
    rank: int | None = None
    rank_source: str
    subjects: list[str]
    foreign_language: str | None = None
    sex: str | None = None
    status: str
    version: int
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class ProfileDetail(ProfileDTO):
    subject_scores: list[SubjectScoreDTO] = Field(default_factory=list)
    attributes: list[AttributeDTO] = Field(default_factory=list)


class ScoreRankQuery(BaseModel):
    cycle_id: int
    score: Annotated[int, Field(ge=0, le=750)]


class ScoreRankResult(BaseModel):
    exam_year: int
    score: int
    rank: int
    segment_rank: int | None = None
    same_score_count: int | None = None
    ranked_candidates: int | None = None
    source: Literal["SEGMENT", "INTERPOLATED"] = "SEGMENT"
    is_demo: bool = False
    note: str | None = None


# ---------------- preferences ----------------


class MajorPreferenceIn(BaseModel):
    major_id: int | None = None
    discipline_code: str | None = Field(default=None, max_length=2)
    major_name: str | None = Field(default=None, max_length=128)
    polarity: Literal["INCLUDE", "EXCLUDE"] = "INCLUDE"
    weight: Annotated[float, Field(ge=0, le=100)] = 100.0
    is_hard_exclude: bool = False
    note: str | None = None

    @model_validator(mode="after")
    def _target_required(self) -> MajorPreferenceIn:
        if not (self.major_id or self.discipline_code or self.major_name):
            raise ValueError("major_id / discipline_code / major_name 至少需要一个")
        return self


class LocationPreferenceIn(BaseModel):
    province_code: str | None = Field(default=None, max_length=2)
    province_name: str | None = Field(default=None, max_length=32)
    city_code: str | None = Field(default=None, max_length=8)
    city_name: str | None = Field(default=None, max_length=32)
    polarity: Literal["INCLUDE", "EXCLUDE"] = "INCLUDE"
    weight: Annotated[float, Field(ge=0, le=100)] = 100.0
    is_hard_exclude: bool = False
    note: str | None = None

    @model_validator(mode="after")
    def _target_required(self) -> LocationPreferenceIn:
        if not (self.province_code or self.city_code):
            raise ValueError("province_code / city_code 至少需要一个")
        return self


class UniversityPreferenceIn(BaseModel):
    university_id: int | None = None
    trait: Literal[
        "IS_985", "IS_211", "IS_DOUBLE_FIRST_CLASS", "IS_PUBLIC", "IS_PRIVATE", "HAS_POSTGRAD"
    ] | None = None
    polarity: Literal["INCLUDE", "EXCLUDE"] = "INCLUDE"
    weight: Annotated[float, Field(ge=0, le=100)] = 100.0
    is_hard_exclude: bool = False
    note: str | None = None

    @model_validator(mode="after")
    def _target_required(self) -> UniversityPreferenceIn:
        if self.university_id is None and self.trait is None:
            raise ValueError("university_id 或 trait 至少需要一个")
        return self


class OtherPreferenceIn(BaseModel):
    dimension: Literal["ACADEMIC", "CAREER", "TUITION", "PERSONAL"]
    key: Annotated[str, Field(min_length=1, max_length=64)]
    value_text: str | None = None
    value_num: float | None = None
    polarity: Literal["INCLUDE", "EXCLUDE"] = "INCLUDE"
    weight: Annotated[float, Field(ge=0, le=100)] = 100.0
    is_hard_exclude: bool = False
    note: str | None = None


class PreferenceCreate(BaseModel):
    risk_mode: RiskModeT = "BALANCED"
    max_tuition: Annotated[int, Field(ge=0)] | None = None
    min_tuition: Annotated[int, Field(ge=0)] | None = None
    exclude_private: bool = False
    exclude_cooperative: bool = False
    accept_unknown_requirements: bool = True
    target_count: Annotated[int, Field(gt=0, le=200)] | None = Field(
        default=None, description="缺省时使用 admission_cycle.application_limit"
    )
    min_safe_count: Annotated[int, Field(ge=0)] | None = None
    dimension_weights: dict[str, float] | None = None
    notes: str | None = None
    major_preferences: list[MajorPreferenceIn] = Field(default_factory=list)
    location_preferences: list[LocationPreferenceIn] = Field(default_factory=list)
    university_preferences: list[UniversityPreferenceIn] = Field(default_factory=list)
    other_preferences: list[OtherPreferenceIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _tuition_range(self) -> PreferenceCreate:
        if (
            self.min_tuition is not None
            and self.max_tuition is not None
            and self.min_tuition > self.max_tuition
        ):
            raise ValueError("min_tuition 不能大于 max_tuition")
        if self.dimension_weights:
            allowed = {"MAJOR", "UNIVERSITY", "LOCATION", "ACADEMIC", "CAREER", "TUITION", "PERSONAL"}
            unknown = set(self.dimension_weights) - allowed
            if unknown:
                raise ValueError(f"未知偏好维度: {sorted(unknown)}")
        return self


class MajorPreferenceDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    major_id: int | None = None
    discipline_code: str | None = None
    major_name: str | None = None
    polarity: str
    weight: float
    is_hard_exclude: bool
    note: str | None = None


class LocationPreferenceDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    province_code: str | None = None
    province_name: str | None = None
    city_code: str | None = None
    city_name: str | None = None
    polarity: str
    weight: float
    is_hard_exclude: bool
    note: str | None = None


class UniversityPreferenceDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    university_id: int | None = None
    trait: str | None = None
    polarity: str
    weight: float
    is_hard_exclude: bool
    note: str | None = None


class OtherPreferenceDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    dimension: str
    key: str
    value_text: str | None = None
    value_num: float | None = None
    polarity: str
    weight: float
    is_hard_exclude: bool
    note: str | None = None


class PreferenceDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    profile_id: int
    version: int
    risk_mode: str
    candidate_rank: int | None = None
    max_tuition: int | None = None
    min_tuition: int | None = None
    exclude_private: bool
    exclude_cooperative: bool
    accept_unknown_requirements: bool
    target_count: int
    min_safe_count: int | None = None
    dimension_weights: dict[str, float] | None = None
    is_active: bool
    notes: str | None = None
    created_at: datetime
    major_preferences: list[MajorPreferenceDTO] = Field(default_factory=list)
    location_preferences: list[LocationPreferenceDTO] = Field(default_factory=list)
    university_preferences: list[UniversityPreferenceDTO] = Field(default_factory=list)
    other_preferences: list[OtherPreferenceDTO] = Field(default_factory=list)
