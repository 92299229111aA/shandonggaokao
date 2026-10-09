"""Catalog DTOs: universities, majors, admission plans, score→rank lookup."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.candidate.schemas import ScoreRankResult  # re-exported for the router


class UniversityDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    ministry_code: str
    name: str
    short_name: str | None = None
    province_code: str
    province_name: str
    city_code: str | None = None
    city_name: str | None = None
    is_985: bool
    is_211: bool
    is_double_first_class: bool
    is_public: bool | None = None


class MajorDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    level: str
    discipline_code: str | None = None
    discipline_name: str | None = None
    category_code: str | None = None
    category_name: str | None = None
    degree_awarded: str | None = None


class SubjectRuleDTO(BaseModel):
    """选科要求 in DNF form: groups are OR-ed, subjects inside a group are AND-ed."""

    rule_set_id: int | None = None
    mode: str | None = None
    groups: list[list[str]] = Field(default_factory=list)
    raw_text: str | None = None
    is_authoritative: bool | None = None


class PlanDTO(BaseModel):
    """One 专业（专业类）+学校 志愿单位."""

    plan_id: int
    cycle_id: int
    plan_name: str
    program_label: str
    university_id: int
    university_name: str
    province_code: str
    province_name: str
    city_code: str | None = None
    city_name: str | None = None
    is_985: bool
    is_211: bool
    is_double_first_class: bool
    series_id: int
    series_name: str
    cooperative: str
    first_year: int | None = None
    last_year: int | None = None
    plan_count: int
    tuition_fee: int
    duration_years: int
    campus_name: str | None = None
    special_requirements: str | None = None
    series_match_quality: str
    is_demo: bool
    subject_rule: SubjectRuleDTO | None = None


class RequirementDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    requirement_type: str
    operator: str
    operand: dict | None = None
    severity: str
    is_deterministic: bool
    raw_text: str | None = None


class PlanDetail(PlanDTO):
    requirements: list[RequirementDTO] = Field(default_factory=list)


class AdmissionCycleDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    province_code: str
    exam_year: int
    exam_category: str
    batch_code: str
    round_no: int
    program_level: str
    application_limit: int
    filling_rule: str | None = None
    policy_version: str
    is_active: bool


class SnapshotDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    label: str
    status: str
    policy_version: str
    is_demo: bool
    published_at: object | None = None


class DatasetMetaDTO(BaseModel):
    """What the frontend must show on the home page: which data is in use."""

    cycle: AdmissionCycleDTO | None = None
    snapshot: SnapshotDTO | None = None
    model_version: str | None = None
    model_label: str | None = None
    data_year_label: str | None = None
    is_demo: bool = False
    plan_count: int = 0
    university_count: int = 0
    warnings: list[str] = Field(default_factory=list)


class ScoreRankRequest(BaseModel):
    cycle_id: int
    score: Annotated[int, Field(ge=0, le=750)]


__all__ = [
    "AdmissionCycleDTO",
    "DatasetMetaDTO",
    "MajorDTO",
    "PlanDTO",
    "PlanDetail",
    "RequirementDTO",
    "ScoreRankRequest",
    "ScoreRankResult",
    "SnapshotDTO",
    "SubjectRuleDTO",
    "UniversityDTO",
]
