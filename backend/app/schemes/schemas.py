"""Scheme DTOs: versioned volunteer schemes and their edits."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

RiskModeT = Literal["STEADY", "BALANCED", "AGGRESSIVE"]


class OptimizerConfigDTO(BaseModel):
    """Only the knobs a candidate may reasonably tune are exposed."""

    confidence_weight: Annotated[float, Field(ge=0, le=1)] = 0.10
    deviation_weight: Annotated[float, Field(ge=0, le=5)] = 0.60
    university_concentration_weight: Annotated[float, Field(ge=0, le=1)] = 0.05
    major_concentration_weight: Annotated[float, Field(ge=0, le=1)] = 0.05
    max_time_seconds: Annotated[float, Field(gt=0, le=120)] = 10.0


class SchemeCreate(BaseModel):
    recommendation_session_id: int
    risk_mode: RiskModeT = "BALANCED"
    target_count: Annotated[int, Field(gt=0, le=200)] | None = Field(
        default=None, description="缺省时使用 admission_cycles.application_limit"
    )
    title: str = Field(default="志愿方案", max_length=128)
    item_ids: list[int] = Field(
        default_factory=list,
        description="留空则由优化器自动选出一套方案；否则按给定条目初始化",
    )
    optimizer: OptimizerConfigDTO | None = None

    def optimizer_config(self):
        from app.optimizer.types import OptimizerConfig

        if self.optimizer is None:
            return OptimizerConfig()
        return OptimizerConfig(**self.optimizer.model_dump())


class SchemeOrderEntry(BaseModel):
    recommendation_item_id: int
    position: Annotated[int, Field(ge=1)] | None = Field(
        default=None, description="填写即表示把该志愿钉死在绝对位置（fixed_position）"
    )
    locked: bool | None = Field(
        default=None, description="缺省表示保持原有锁定状态"
    )


class SchemeOrderUpdate(BaseModel):
    """Optimistic locking: `version` must equal `schemes.lock_version`."""

    version: Annotated[int, Field(ge=1)]
    items: Annotated[list[SchemeOrderEntry], Field(min_length=1)]
    change_summary: str | None = Field(default=None, max_length=255)


class SchemeAddItems(BaseModel):
    version: Annotated[int, Field(ge=1)]
    recommendation_item_ids: Annotated[list[int], Field(min_length=1)]


class SchemeLockUpdate(BaseModel):
    version: Annotated[int, Field(ge=1)]
    locked: bool


class OptimizeRequest(BaseModel):
    version: Annotated[int, Field(ge=1)]
    risk_mode: RiskModeT | None = None
    target_count: Annotated[int, Field(gt=0, le=200)] | None = None
    min_safe_count: Annotated[int, Field(ge=0)] | None = Field(
        default=None, description="最低安全数量（硬约束），UNKNOWN 志愿不计入"
    )
    optimizer: OptimizerConfigDTO | None = None

    def optimizer_config(self):
        from app.optimizer.types import OptimizerConfig

        if self.optimizer is None:
            return OptimizerConfig()
        return OptimizerConfig(**self.optimizer.model_dump())


class SchemeItemDTO(BaseModel):
    position: int
    recommendation_item_id: int
    plan_id: int
    program_label: str
    plan_name: str
    university_name: str
    eligibility: str
    acs: float | None = None
    confidence: float | None = None
    preference_score: float | None = None
    desire_band: int | None = None
    risk_bucket: str | None = None
    locked: bool = False
    fixed_position: bool = False
    tuition_fee: int = 0
    plan_count: int = 0
    is_demo: bool = False


class SchemeDTO(BaseModel):
    id: int
    session_id: int
    title: str
    target_count: int
    risk_mode: str
    current_version_no: int
    lock_version: int
    is_demo: bool
    version_no: int
    operation: str
    change_summary: str | None = None
    item_count: int
    optimizer_stats: dict[str, Any] | None = None
    risk_distribution: dict[str, int] = Field(default_factory=dict)
    items: list[SchemeItemDTO] = Field(default_factory=list)
    is_historical: bool = False


class SchemeVersionDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    scheme_id: int
    version_no: int
    operation: str
    change_summary: str | None = None
    item_count: int
    model_version: str | None = None
    optimizer_stats: dict[str, Any] | None = None
    created_at: datetime


class ExportRow(BaseModel):
    position: int
    program_label: str
    university_name: str
    plan_name: str
    risk_bucket: str | None = None
    acs: float | None = None
    confidence: float | None = None
    preference_score: float | None = None
    tuition_fee: int = 0
    plan_count: int = 0


class ExportResponse(BaseModel):
    scheme_id: int
    version_no: int
    target_count: int
    application_limit: int | None = None
    disclaimer: str = "本方案为决策辅助结果，ACS 是录取安全指数而非录取概率。"
    rows: list[ExportRow] = Field(default_factory=list)
