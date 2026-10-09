"""Health Check DTOs.

The report is diagnostic only: `disclaimer` travels with every payload so no
client can present the health score as an admission probability.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field


class HealthCheckIssueDTO(BaseModel):
    code: str
    severity: str
    message: str
    position: int | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    suggestion: str | None = None


class HealthCheckCheckDTO(BaseModel):
    code: str
    title: str
    passed: bool
    errors: int = 0
    warnings: int = 0
    infos: int = 0


class HealthCheckDTO(BaseModel):
    id: int | None = None
    scheme_id: int
    scheme_version_id: int | None = None
    version_no: int | None = None
    snapshot_id: int | None = None
    score: int = Field(ge=0, le=100)
    error_count: int = 0
    warning_count: int = 0
    info_count: int = 0
    passed: bool = True
    disclaimer: str = ""
    summary: dict[str, Any] = Field(default_factory=dict)
    issues: list[HealthCheckIssueDTO] = Field(default_factory=list)
    checks: list[HealthCheckCheckDTO] = Field(default_factory=list)


class HealthCheckRequest(BaseModel):
    """Spec: HealthCheckRequest 指定 scheme version."""

    version: Annotated[int, Field(ge=1)] | None = Field(
        default=None, description="缺省检查当前版本"
    )
    recheck_eligibility: bool = Field(
        default=False, description="是否按当前选科与规则重新核对报考资格"
    )
    persist: bool = Field(default=True, description="是否把本次体检结果存档")


class HealthCheckSummaryDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    scheme_id: int
    scheme_version_id: int
    score: int
    error_count: int
    warning_count: int
    info_count: int
    created_at: datetime
