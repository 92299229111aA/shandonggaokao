"""recommendation schema DTOs."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field


class RecommendationCreate(BaseModel):
    """profile_id + preference_id + cycle_id, per the spec."""

    profile_id: int
    preference_id: int | None = None
    cycle_id: int | None = Field(default=None, description="缺省时使用考生档案绑定的周期")
    snapshot_id: int | None = Field(
        default=None, description="缺省时使用最新 PUBLISHED 快照；DRAFT 快照会被拒绝"
    )
    locked_plan_ids: list[int] = Field(
        default_factory=list, description="用户手动锁定的志愿，必须无条件进入候选池"
    )


class SessionDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    profile_id: int
    preference_profile_id: int
    cycle_id: int
    snapshot_id: int
    model_version_id: int
    # ---- replay contract ----
    profile_version: int
    preference_version: int
    policy_version: str
    model_version: str
    algorithm_config_version: str | None = None
    status: str
    stage: str | None = None
    stats: dict[str, Any] | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime


class HistoryYearDTO(BaseModel):
    exam_year: int
    min_rank: int | None = None
    min_score: int | None = None
    plan_count: int | None = None
    admitted_count: int | None = None


class RecommendationItemDTO(BaseModel):
    """Everything a recommendation card must display (spec §9.3)."""

    item_id: int
    plan_id: int
    # 学校 + 专业
    program_label: str
    plan_name: str
    university_id: int
    university_name: str
    province_name: str | None = None
    city_name: str | None = None
    is_985: bool = False
    is_211: bool = False
    is_double_first_class: bool = False
    # 资格
    eligibility: str
    eligibility_reasons: list[dict[str, Any]] = Field(default_factory=list)
    excluded_by: str | None = None
    # 风险
    acs: float | None = None
    base_acs: float | None = None
    confidence: float | None = None
    confidence_label: str | None = None
    risk_bucket: str | None = None
    acs_explanation: dict[str, Any] | None = None
    # 偏好
    preference_score: float | None = None
    preference_dimensions: dict[str, Any] | None = None
    # 历史与计划
    history: list[HistoryYearDTO] = Field(default_factory=list)
    plan_count: int = 0
    plan_count_history: list[int] = Field(default_factory=list)
    plan_change_ratio: float | None = None
    median_plan_history: float | None = None
    tuition_fee: int = 0
    duration_years: int = 4
    campus_name: str | None = None
    # 特殊要求
    # 专业代码（山东计划表口径，院校内部序号，非教育部代码）
    major_sequence: str | None = None
    subject_requirement_text: str | None = None
    special_requirements: str | None = None
    requirement_notes: list[str] = Field(default_factory=list)
    cooperative: str = "NONE"
    is_demo: bool = False
    in_candidate_pool: bool = False


class RecommendationItemPage(BaseModel):
    items: list[RecommendationItemDTO]
    total: int
    limit: int
    offset: int
    risk_distribution: dict[str, int] = Field(default_factory=dict)
    pool_size: int = 0
    sort: str = "PREFERENCE"
    # Counts per risk bucket under the *current* filters (the bucket filter excluded, since
    # it is the grouping dimension). The editor's bucket tabs use these so the numbers match
    # the list; `risk_distribution` remains the whole-session distribution.
    bucket_counts: dict[str, int] = Field(default_factory=dict)


class RecommendationRunResponse(BaseModel):
    """Returned by POST /recommendations."""

    session: SessionDTO
    pool_size: int
    risk_distribution: dict[str, int] = Field(default_factory=dict)
    shortfall_notice: str | None = None
    relax_suggestions: list[str] = Field(default_factory=list)
    input_warnings: list[str] = Field(default_factory=list)


class PoolShortfall(BaseModel):
    target: int
    available: int
    suggestions: list[str] = Field(default_factory=list)


class FeedbackRequest(BaseModel):
    """Reserved for phase 6+; kept out of the recommendation contract for now."""

    plan_id: Annotated[int, Field(gt=0)]
    verdict: Annotated[str, Field(pattern="^(LIKE|DISLIKE|NEUTRAL)$")]
    note: str | None = None
