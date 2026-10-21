"""Health Check engine: run every check and aggregate into one report.

The engine is pure and read-only. It never reorders, deletes, or locks anything —
it only tells the candidate what is wrong and what they could do about it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.common.enums import HealthSeverity
from app.healthcheck.checks import DEFAULT_CHECKS
from app.healthcheck.types import (
    CheckResult,
    HealthCheck,
    HealthCheckContext,
    HealthIssue,
    score_for,
)

# Product indicator only. Explicitly NOT an admission probability.
HEALTH_SCORE_DISCLAIMER = (
    "方案健康分只是产品指标，用于提示方案质量，不是录取概率，也不代表录取结果。"
)


@dataclass(frozen=True, slots=True)
class HealthCheckReport:
    score: int
    error_count: int
    warning_count: int
    info_count: int
    results: tuple[CheckResult, ...]
    summary: dict[str, Any] = field(default_factory=dict)

    @property
    def issues(self) -> tuple[HealthIssue, ...]:
        return tuple(issue for check in self.results for issue in check.issues)

    @property
    def passed(self) -> bool:
        return self.error_count == 0

    @property
    def failed_checks(self) -> tuple[str, ...]:
        return tuple(check.code for check in self.results if not check.passed)

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "info_count": self.info_count,
            "passed": self.passed,
            "disclaimer": HEALTH_SCORE_DISCLAIMER,
            "summary": dict(self.summary),
            "issues": [issue.as_dict() for issue in self.issues],
            "checks": [
                {
                    "code": check.code,
                    "title": check.title,
                    "passed": check.passed,
                    "worst_severity": (
                        None if check.worst_severity is None else check.worst_severity.value
                    ),
                    **check.counts(),
                }
                for check in self.results
            ],
        }


class HealthCheckEngine:
    def __init__(self, checks: tuple[HealthCheck, ...] | None = None) -> None:
        self.checks: tuple[HealthCheck, ...] = checks if checks is not None else DEFAULT_CHECKS  # type: ignore[assignment]

    def run(self, ctx: HealthCheckContext) -> HealthCheckReport:
        results = tuple(check.run(ctx) for check in self.checks)
        issues = [issue for check in results for issue in check.issues]
        score, counts = score_for(issues)

        summary: dict[str, Any] = {
            "item_count": ctx.item_count,
            "application_limit": ctx.application_limit,
            "checks_run": len(results),
            "checks_failed": sum(1 for check in results if not check.passed),
            "risk_counts": _risk_counts(ctx),
            "checks_with_errors": [
                check.code
                for check in results
                if any(issue.severity is HealthSeverity.ERROR for issue in check.issues)
            ],
            # 逐项结果随报告一起持久化：否则 get() 从库里读回来时只能看到 issues，
            # 还原不出"哪几项检查执行了、通过没有"。
            "checks_detail": [
                {
                    "code": result.code,
                    "title": result.title,
                    "passed": result.passed,
                    "errors": sum(
                        1 for i in result.issues if i.severity is HealthSeverity.ERROR
                    ),
                    "warnings": sum(
                        1 for i in result.issues if i.severity is HealthSeverity.WARNING
                    ),
                    "infos": sum(1 for i in result.issues if i.severity is HealthSeverity.INFO),
                    # 记下这一项产出的 issue code，前端据此把"待核验"与"未通过"分开
                    "issue_codes": sorted({i.code for i in result.issues}),
                }
                for result in results
            ],
            "note": "本报告只做诊断，不会修改你的方案。",
        }
        return HealthCheckReport(
            score=score,
            error_count=counts["errors"],
            warning_count=counts["warnings"],
            info_count=counts["infos"],
            results=results,
            summary=summary,
        )


def _risk_counts(ctx: HealthCheckContext) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in ctx.items:
        key = item.risk_bucket or "UNKNOWN"
        counts[key] = counts.get(key, 0) + 1
    return counts


__all__ = ["HEALTH_SCORE_DISCLAIMER", "HealthCheckEngine", "HealthCheckReport"]
