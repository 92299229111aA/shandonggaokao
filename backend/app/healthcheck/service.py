"""Health Check service: run the engine, persist the report, serve it back."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.errors import NotFoundError
from app.healthcheck.assembly import build_context
from app.healthcheck.engine import HealthCheckEngine, HealthCheckReport
from app.healthcheck.schemas import HealthCheckCheckDTO, HealthCheckDTO, HealthCheckIssueDTO
from app.recommendation.models import HealthCheck, HealthCheckIssue

HEALTH_DISCLAIMER = (
    "方案健康分只是产品指标，不是录取概率，也不代表录取结果；"
    "体检只报告问题，不会修改你的方案。"
)


class HealthCheckService:
    def __init__(self, session: Session, engine: HealthCheckEngine | None = None) -> None:
        self.session = session
        self.engine = engine or HealthCheckEngine()

    def run(
        self,
        *,
        scheme_id: int,
        version_no: int | None = None,
        recheck_eligibility: bool = False,
        persist: bool = True,
    ) -> HealthCheckDTO:
        ctx = build_context(
            self.session,
            scheme_id=scheme_id,
            version_no=version_no,
            recheck_eligibility=recheck_eligibility,
        )
        report = self.engine.run(ctx)

        if not persist:
            return self._to_dto(
                report, scheme_id=scheme_id, version_no=version_no, check_id=None
            )

        from app.recommendation.models import Scheme
        from app.schemes.service import SchemeService

        scheme = self.session.get(Scheme, scheme_id)
        if scheme is None:
            raise NotFoundError(f"志愿方案 {scheme_id} 不存在")
        resolved_version_no = version_no or scheme.current_version_no
        version_id = SchemeService(self.session).require_version_id(scheme_id, resolved_version_no)

        row = HealthCheck(
            scheme_id=scheme_id,
            scheme_version_id=version_id,
            snapshot_id=ctx.snapshot.id if ctx.snapshot else None,
            score=report.score,
            error_count=report.error_count,
            warning_count=report.warning_count,
            info_count=report.info_count,
            summary=report.summary,
        )
        self.session.add(row)
        self.session.flush()
        self.session.add_all(
            [
                HealthCheckIssue(
                    health_check_id=row.id,
                    code=issue.code,
                    severity=issue.severity.value,
                    position=issue.position,
                    message=issue.message,
                    detail=issue.detail,
                    suggestion=issue.suggestion,
                )
                for issue in report.issues
            ]
        )
        self.session.flush()

        dto = self._to_dto(
            report, scheme_id=scheme_id, version_no=resolved_version_no, check_id=row.id
        )
        dto.scheme_version_id = version_id
        self.session.refresh(row)
        dto.created_at = row.created_at
        dto.checks = self._checks_from_summary(row.summary)
        return dto


    @staticmethod
    def _checks_from_summary(summary: dict | None) -> list[HealthCheckCheckDTO]:
        """从报告摘要还原逐项检查结果。

        报告落库时把逐项结果存进了 summary（见 engine.run），这里读回来，
        这样 get() 与 list_for_scheme() 也能给出"哪几项跑了、通过没有"。
        """
        detail = (summary or {}).get("checks_detail") or []
        return [
            HealthCheckCheckDTO(
                code=item.get("code", ""),
                title=item.get("title", ""),
                passed=bool(item.get("passed")),
                errors=int(item.get("errors") or 0),
                warnings=int(item.get("warnings") or 0),
                infos=int(item.get("infos") or 0),
                issue_codes=list(item.get("issue_codes") or []),
            )
            for item in detail
            if isinstance(item, dict)
        ]

    def get(self, check_id: int) -> HealthCheckDTO:
        row = self.session.get(HealthCheck, check_id)
        if row is None:
            raise NotFoundError(f"体检报告 {check_id} 不存在")
        issues = list(
            self.session.execute(
                select(HealthCheckIssue)
                .where(HealthCheckIssue.health_check_id == check_id)
                .order_by(HealthCheckIssue.position, HealthCheckIssue.id)
            ).scalars()
        )
        return HealthCheckDTO(
            id=row.id,
            scheme_id=row.scheme_id,
            scheme_version_id=row.scheme_version_id,
            # 报告必须说明它检查的是哪个方案版本，前端据此判断是否过期。
            version_no=self._version_no_of(row.scheme_version_id),
            snapshot_id=row.snapshot_id,
            created_at=row.created_at,
            score=row.score,
            error_count=row.error_count,
            warning_count=row.warning_count,
            info_count=row.info_count,
            passed=row.error_count == 0,
            disclaimer=HEALTH_DISCLAIMER,
            summary=row.summary or {},
            issues=[self._issue_dto(issue) for issue in issues],
            checks=self._checks_from_summary(row.summary),
        )


    def _version_no_of(self, scheme_version_id: int | None) -> int | None:
        """方案版本 id -> 版本号。报告要能说出"我检查的是 v几"。"""
        if scheme_version_id is None:
            return None
        from app.recommendation.models import SchemeVersion

        row = self.session.get(SchemeVersion, scheme_version_id)
        return row.version_no if row is not None else None

    def list_for_scheme(self, scheme_id: int) -> list[HealthCheckDTO]:
        rows = self.session.execute(
            select(HealthCheck)
            .where(HealthCheck.scheme_id == scheme_id)
            .order_by(HealthCheck.created_at.desc(), HealthCheck.id.desc())
        ).scalars()
        return [self.get(row.id) for row in rows]

    # ---------------- mapping ----------------

    @staticmethod
    def _to_dto(
        report: HealthCheckReport,
        *,
        scheme_id: int,
        version_no: int | None,
        check_id: int | None,
    ) -> HealthCheckDTO:
        return HealthCheckDTO(
            id=check_id,
            scheme_id=scheme_id,
            scheme_version_id=None,
            version_no=version_no or None,
            snapshot_id=None,
            score=report.score,
            error_count=report.error_count,
            warning_count=report.warning_count,
            info_count=report.info_count,
            passed=report.passed,
            disclaimer=HEALTH_DISCLAIMER,
            summary=report.summary,
            issues=[
                HealthCheckIssueDTO(
                    code=issue.code,
                    severity=issue.severity.value,
                    message=issue.message,
                    position=issue.position,
                    detail=issue.detail,
                    suggestion=issue.suggestion,
                )
                for issue in report.issues
            ],
            checks=[
                {
                    "code": check.code,
                    "title": check.title,
                    "passed": check.passed,
                    **check.counts(),
                }
                for check in report.results
            ],
        )

    @staticmethod
    def _issue_dto(issue: HealthCheckIssue) -> HealthCheckIssueDTO:
        return HealthCheckIssueDTO(
            code=issue.code,
            severity=issue.severity,
            message=issue.message,
            position=issue.position,
            detail=issue.detail or {},
            suggestion=issue.suggestion,
        )


__all__ = ["HEALTH_DISCLAIMER", "HealthCheckService"]
