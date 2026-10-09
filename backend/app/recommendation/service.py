"""Recommendation service: runs the pipeline and serves scored items for the UI."""

from __future__ import annotations

import statistics
from collections import Counter
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.admission.models import AdmissionPlan, ProgramSeries
from app.admission.repository import PlanRepository
from app.common.enums import EligibilityStatus
from app.common.errors import NotFoundError
from app.recommendation.models import RecommendationItem
from app.recommendation.models import Session as RunSession
from app.recommendation.pipeline import RecommendationPipeline
from app.recommendation.pipeline_types import PipelineResult
from app.recommendation.schemas import (
    HistoryYearDTO,
    RecommendationItemDTO,
    RecommendationItemPage,
    RecommendationRunResponse,
    SessionDTO,
)
from app.reference.models import University
from app.scoring.acs import confidence_label


class RecommendationService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.plans = PlanRepository(session)

    # ---------------- run ----------------

    def run(
        self,
        *,
        profile_id: int,
        preference_id: int | None,
        cycle_id: int | None,
        snapshot_id: int | None,
        locked_plan_ids: list[int] | None = None,
    ) -> RecommendationRunResponse:
        pipeline = RecommendationPipeline(self.session)
        result: PipelineResult = pipeline.run(
            profile_id=profile_id,
            preference_id=preference_id,
            cycle_id=cycle_id,
            snapshot_id=snapshot_id,
            locked_plan_ids=set(locked_plan_ids or ()),
        )
        self.session.commit()
        return self._run_response(result, locked_plan_ids or [])

    def _run_response(self, result: PipelineResult, locked: list[int]) -> RecommendationRunResponse:
        run = self.session.get(RunSession, result.session_id)
        if run is None:  # pragma: no cover - defensive
            raise NotFoundError(f"推荐会话 {result.session_id} 不存在")

        distribution = self._risk_distribution(result.session_id)
        pool_size = sum(distribution.values())

        target = None
        preference_id = run.preference_profile_id
        from app.candidate.models import PreferenceProfile

        preference = self.session.get(PreferenceProfile, preference_id)
        if preference is not None:
            target = preference.target_count

        shortfall = None
        suggestions: list[str] = []
        if target is not None and pool_size < target:
            shortfall = (
                f"符合条件的志愿只有 {pool_size} 个，少于目标 {target} 个；"
                "系统不会为凑数加入考生不接受或资格不符的志愿。"
            )
            suggestions = self._relax_suggestions(result)

        return RecommendationRunResponse(
            session=SessionDTO.model_validate(run),
            pool_size=pool_size,
            risk_distribution=distribution,
            shortfall_notice=shortfall,
            relax_suggestions=suggestions,
        )

    def _relax_suggestions(self, result: PipelineResult) -> list[str]:
        """Tell the candidate exactly which constraints are costing them options."""
        stats = result.stats
        suggestions: list[str] = []
        if stats.hard_excluded:
            suggestions.append(
                f"有 {stats.hard_excluded} 个志愿被你的硬性排除条件过滤"
                "（专业/地域/学校/学费上限/中外合作），放宽这些条件可增加候选"
            )
        if stats.eligibility_fail:
            suggestions.append(
                f"有 {stats.eligibility_fail} 个志愿因选科或硬性报考条件不符被排除，"
                "这部分无法放宽"
            )
        if stats.no_history:
            suggestions.append(
                f"有 {stats.no_history} 个志愿没有可用历史数据，其 ACS 取中性值 50，"
                "不建议作为保底"
            )
        if stats.eligibility_unknown:
            suggestions.append(
                f"有 {stats.eligibility_unknown} 个志愿存在待确认的特殊要求，"
                "补齐体检/单科/语种等信息后可以更准确地判断"
            )
        suggestions.append("放宽地域范围、学费上限或专业范围，通常能显著增加候选数量")
        return suggestions

    # ---------------- read ----------------

    def get_session(self, session_id: int) -> SessionDTO:
        run = self.session.get(RunSession, session_id)
        if run is None:
            raise NotFoundError(f"推荐会话 {session_id} 不存在")
        return SessionDTO.model_validate(run)

    def list_items(
        self,
        *,
        session_id: int,
        limit: int,
        offset: int,
        risk_bucket: str | None = None,
        eligibility: str | None = None,
        only_pooled: bool = True,
        search: str | None = None,
    ) -> RecommendationItemPage:
        if self.session.get(RunSession, session_id) is None:
            raise NotFoundError(f"推荐会话 {session_id} 不存在")

        base = select(RecommendationItem).where(RecommendationItem.session_id == session_id)
        if only_pooled:
            base = base.where(RecommendationItem.in_candidate_pool.is_(True))
        if risk_bucket:
            base = base.where(RecommendationItem.risk_bucket == risk_bucket)
        if eligibility:
            base = base.where(RecommendationItem.eligibility == eligibility)

        rows = list(self.session.execute(base).scalars())
        details = self._plan_details([row.plan_id for row in rows])

        if search:
            needle = search.strip()
            rows = [
                row
                for row in rows
                if needle in details.get(row.plan_id, {}).get("plan_name", "")
                or needle in details.get(row.plan_id, {}).get("university_name", "")
            ]

        # Default ordering mirrors the final sort intent: preference first.
        rows.sort(
            key=lambda row: (
                -(row.preference_score if row.preference_score is not None else 0.0),
                (row.acs if row.acs is not None else 100.0),
            )
        )
        total = len(rows)
        page = rows[offset : offset + limit]
        return RecommendationItemPage(
            items=[self._to_dto(row, details.get(row.plan_id, {})) for row in page],
            total=total,
            limit=limit,
            offset=offset,
            risk_distribution=self._risk_distribution(session_id),
            pool_size=self._pool_size(session_id),
        )

    def get_item(self, item_id: int) -> RecommendationItemDTO:
        row = self.session.get(RecommendationItem, item_id)
        if row is None:
            raise NotFoundError(f"推荐条目 {item_id} 不存在")
        details = self._plan_details([row.plan_id])
        return self._to_dto(row, details.get(row.plan_id, {}))

    # ---------------- internals ----------------

    def _risk_distribution(self, session_id: int) -> dict[str, int]:
        rows = self.session.execute(
            select(RecommendationItem.risk_bucket, func.count())
            .where(
                RecommendationItem.session_id == session_id,
                RecommendationItem.in_candidate_pool.is_(True),
            )
            .group_by(RecommendationItem.risk_bucket)
        ).all()
        return {str(bucket): int(count) for bucket, count in rows if bucket is not None}

    def _pool_size(self, session_id: int) -> int:
        return sum(self._risk_distribution(session_id).values())

    def _plan_details(self, plan_ids: list[int]) -> dict[int, dict[str, Any]]:
        if not plan_ids:
            return {}
        rows = self.session.execute(
            select(
                AdmissionPlan.id.label("plan_id"),
                AdmissionPlan.plan_name,
                AdmissionPlan.plan_count,
                AdmissionPlan.tuition_fee,
                AdmissionPlan.duration_years,
                AdmissionPlan.campus_name,
                AdmissionPlan.special_requirements,
                AdmissionPlan.series_match_quality,
                AdmissionPlan.is_demo,
                ProgramSeries.id.label("series_id"),
                ProgramSeries.cooperative,
                University.id.label("university_id"),
                University.name.label("university_name"),
                University.province_name,
                University.city_name,
                University.is_985,
                University.is_211,
                University.is_double_first_class,
            )
            .join(ProgramSeries, ProgramSeries.id == AdmissionPlan.series_id)
            .join(University, University.id == ProgramSeries.university_id)
            .where(AdmissionPlan.id.in_(plan_ids))
        ).mappings()
        details = {int(row["plan_id"]): dict(row) for row in rows}

        outcomes = self.plans.outcomes_for_series(
            sorted({int(row["series_id"]) for row in details.values()})
        )
        for row in details.values():
            row["history"] = outcomes.get(int(row["series_id"]), [])
        return details

    def _to_dto(self, row: RecommendationItem, detail: dict[str, Any]) -> RecommendationItemDTO:
        history_rows = detail.get("history", [])
        plan_history = [int(item.plan_count) for item in history_rows if item.plan_count]
        median_plan = float(statistics.median(plan_history)) if plan_history else None
        plan_current = int(detail.get("plan_count") or 0)
        change = None
        if median_plan and median_plan > 0:
            change = (plan_current - median_plan) / median_plan

        reasons = row.eligibility_reasons or []
        requirement_notes = [
            str(reason.get("message"))
            for reason in reasons
            if isinstance(reason, dict) and reason.get("message")
        ]

        return RecommendationItemDTO(
            item_id=row.id,
            plan_id=row.plan_id,
            program_label=f"{detail.get('plan_name', '')} · {detail.get('university_name', '')}".strip(" ·"),
            plan_name=detail.get("plan_name", ""),
            university_id=int(detail.get("university_id") or 0),
            university_name=detail.get("university_name", ""),
            province_name=detail.get("province_name"),
            city_name=detail.get("city_name"),
            is_985=bool(detail.get("is_985")),
            is_211=bool(detail.get("is_211")),
            is_double_first_class=bool(detail.get("is_double_first_class")),
            eligibility=row.eligibility,
            eligibility_reasons=list(reasons),
            excluded_by=row.excluded_by,
            acs=row.acs,
            base_acs=row.base_acs,
            confidence=row.confidence,
            confidence_label=None if row.confidence is None else confidence_label(row.confidence / 100.0),
            risk_bucket=row.risk_bucket,
            acs_explanation=row.acs_explanation,
            preference_score=row.preference_score,
            preference_dimensions=row.preference_dimensions,
            history=[
                HistoryYearDTO(
                    exam_year=item.exam_year,
                    min_rank=item.min_rank,
                    min_score=item.min_score,
                    plan_count=item.plan_count,
                    admitted_count=item.admitted_count,
                )
                for item in sorted(history_rows, key=lambda h: h.exam_year)
            ],
            plan_count=plan_current,
            plan_count_history=plan_history,
            plan_change_ratio=change,
            median_plan_history=median_plan,
            tuition_fee=int(detail.get("tuition_fee") or 0),
            duration_years=int(detail.get("duration_years") or 4),
            campus_name=detail.get("campus_name"),
            subject_requirement_text=None,
            special_requirements=detail.get("special_requirements"),
            requirement_notes=requirement_notes,
            cooperative=detail.get("cooperative") or "NONE",
            is_demo=bool(detail.get("is_demo")),
            in_candidate_pool=row.in_candidate_pool,
        )


def eligibility_counts(session: Session, session_id: int) -> dict[str, int]:
    """Funnel counters straight from the database (used by tests and reports)."""
    rows = session.execute(
        select(RecommendationItem.eligibility, func.count())
        .where(RecommendationItem.session_id == session_id)
        .group_by(RecommendationItem.eligibility)
    ).all()
    counts = Counter({str(status): int(count) for status, count in rows})
    return {
        EligibilityStatus.PASS.value: counts.get(EligibilityStatus.PASS.value, 0),
        EligibilityStatus.UNKNOWN.value: counts.get(EligibilityStatus.UNKNOWN.value, 0),
        EligibilityStatus.FAIL.value: counts.get(EligibilityStatus.FAIL.value, 0),
    }


__all__ = ["RecommendationService", "eligibility_counts"]
