"""Recommendation service: runs the pipeline and serves scored items for the UI."""

from __future__ import annotations

import statistics
from collections import Counter
from typing import Any

from sqlalchemy import Select, case, exists, func, or_, select
from sqlalchemy.orm import Session

from app.admission.models import AdmissionCycle, AdmissionPlan, ExamYearStats, ProgramSeries
from app.admission.repository import PlanRepository
from app.candidate.models import Profile
from app.common.enums import EligibilityStatus
from app.common.errors import NotFoundError
from app.recommendation.models import (
    RecommendationItem,
    Scheme,
    SchemeItem,
    SchemeVersion,
)
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
from app.recommendation.sorts import ItemSort
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
            input_warnings=self._input_warnings(run),
        )

    def _input_warnings(self, run: RunSession) -> list[str]:
        """Flag inputs that are accepted but make the output unrepresentative.

        A rank beyond the entire 一段 population is the case that matters: the rank
        percentile q_c = rank / ranked_candidates exceeds 1, so every historical entry
        looks easier than the candidate and the ACS skews high. The result is still
        returned — the candidate may be inputting a 位次 from a different category — but
        it must not be presented as a normal recommendation.
        """
        profile = self.session.get(Profile, run.profile_id)
        if profile is None or profile.rank is None:
            return []

        cycle = self.session.get(AdmissionCycle, run.cycle_id)
        if cycle is None:
            return []

        population = self.session.execute(
            select(ExamYearStats.ranked_candidates).where(
                ExamYearStats.province_code == cycle.province_code,
                ExamYearStats.exam_year == cycle.exam_year,
                ExamYearStats.program_level == cycle.program_level,
            )
        ).scalars().first()
        if not population or profile.rank <= population:
            return []

        return [
            f"你填写的位次 {profile.rank:,} 超出了 {cycle.exam_year} 年本省"
            f"本科批的达线人数 {population:,}。"
            "在本年度数据下该位次没有对应的历史参照，ACS 会系统性偏高，"
            "结果仅作参考，请核对位次口径（一分一段表使用的是累计人数）。"
        ]

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
        sort: str = ItemSort.PREFERENCE.value,
        province_name: str | None = None,
        is_985: bool | None = None,
        is_211: bool | None = None,
        is_double_first_class: bool | None = None,
        exclude_scheme_id: int | None = None,
    ) -> RecommendationItemPage:
        """One page of candidates, filtered, ordered and sliced **by the database**.

        The previous implementation read every item row, joined plan details for all of
        them, then filtered, sorted and paginated in Python. On a real session that is
        19,019 rows and cost ~7.7 s per request *regardless of `limit`* — so the UI could
        neither show more than a fraction of the pool nor filter it.

        `search` used to be applied after the slice, which meant a school sitting outside
        the first page could never be found. Both are fixed by letting SQL do the work.

        `exclude_scheme_id` drops candidates already present in that scheme's current
        version. The editor needs it: filtering the current page in the browser only works
        while the whole pool fits in one page, and `total` / page numbers would be wrong.
        """
        if self.session.get(RunSession, session_id) is None:
            raise NotFoundError(f"推荐会话 {session_id} 不存在")

        # `base_conditions` excludes the bucket filter: the bucket tabs need counts for
        # every bucket, and constraining by bucket would make the others read zero.
        base_conditions: list = [RecommendationItem.session_id == session_id]
        if only_pooled:
            base_conditions.append(RecommendationItem.in_candidate_pool.is_(True))
        if eligibility:
            base_conditions.append(RecommendationItem.eligibility == eligibility)
        if exclude_scheme_id is not None:
            base_conditions.append(self._not_in_scheme(exclude_scheme_id))

        if search and search.strip():
            needle = f"%{search.strip()}%"
            base_conditions.append(
                or_(
                    AdmissionPlan.plan_name.ilike(needle),
                    University.name.ilike(needle),
                )
            )
        if province_name:
            base_conditions.append(University.province_name == province_name)
        for flag, column in (
            (is_985, University.is_985),
            (is_211, University.is_211),
            (is_double_first_class, University.is_double_first_class),
        ):
            if flag is not None:
                base_conditions.append(column.is_(flag))

        conditions = list(base_conditions)
        if risk_bucket:
            conditions.append(RecommendationItem.risk_bucket == risk_bucket)

        # The name filters need the joins, so they are always present rather than added
        # only when a filter is used — one query shape is easier to reason about than four.
        def with_joins(stmt: Select) -> Select:
            return (
                stmt.join(AdmissionPlan, AdmissionPlan.id == RecommendationItem.plan_id)
                .join(ProgramSeries, ProgramSeries.id == AdmissionPlan.series_id)
                .join(University, University.id == ProgramSeries.university_id)
            )

        total = int(
            self.session.execute(
                with_joins(select(func.count()).select_from(RecommendationItem)).where(*conditions)
            ).scalar_one()
        )

        rows = self._page_rows(sort=sort, conditions=conditions, with_joins=with_joins,
                               limit=limit, offset=offset)

        # Only the current page needs its plan details and outcome history joined.
        details = self._plan_details([row.plan_id for row in rows])
        return RecommendationItemPage(
            items=[self._to_dto(row, details.get(row.plan_id, {})) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
            risk_distribution=self._risk_distribution(session_id),
            pool_size=self._pool_size(session_id),
            sort=sort,
            # Counts for the *current* filters, excluding the bucket filter itself (which
            # is the grouping dimension). The editor shows these on its bucket tabs so the
            # numbers match what the list will actually contain.
            bucket_counts=self._bucket_counts(base_conditions, with_joins),
        )

    def _page_rows(self, *, sort: str, conditions: list, with_joins, limit: int, offset: int):
        """Fetch one page of items, honouring the interleaved sort when requested."""
        if sort != ItemSort.RISK_MIX.value:
            stmt = (
                with_joins(select(RecommendationItem))
                .where(*conditions)
                .order_by(*self._order_for(sort))
                .limit(limit)
                .offset(offset)
            )
            return list(self.session.execute(stmt).scalars())

        # Round-robin across buckets: number each bucket's rows, then order by that number
        # so every bucket appears once per round. A plain ORDER BY cannot do this, hence
        # the window function.
        bucket_rank = case(
            (RecommendationItem.risk_bucket == "HIGH_REACH", 0),
            (RecommendationItem.risk_bucket == "REACH", 1),
            (RecommendationItem.risk_bucket == "BORDER", 2),
            (RecommendationItem.risk_bucket == "STABLE", 3),
            (RecommendationItem.risk_bucket == "SAFE", 4),
            else_=5,
        )
        inner = (
            with_joins(
                select(
                    RecommendationItem.id.label("id"),
                    func.row_number()
                    .over(
                        partition_by=RecommendationItem.risk_bucket,
                        order_by=(
                            RecommendationItem.preference_score.desc().nullslast(),
                            RecommendationItem.acs.desc().nullslast(),
                            RecommendationItem.id,
                        ),
                    )
                    .label("round_no"),
                    bucket_rank.label("bucket_rank"),
                )
            )
            .where(*conditions)
            .subquery()
        )
        ids = [
            row[0]
            for row in self.session.execute(
                select(inner.c.id)
                .order_by(inner.c.round_no, inner.c.bucket_rank, inner.c.id)
                .limit(limit)
                .offset(offset)
            ).all()
        ]
        if not ids:
            return []
        rows = list(
            self.session.execute(
                select(RecommendationItem).where(RecommendationItem.id.in_(ids))
            ).scalars()
        )
        # Preserve the interleaved order; `IN (...)` does not.
        by_id = {row.id: row for row in rows}
        return [by_id[item_id] for item_id in ids if item_id in by_id]

    def _not_in_scheme(self, scheme_id: int):
        """NOT EXISTS against the scheme's *current* version.

        Tied to the current version rather than the scheme row so that editing the scheme
        immediately changes what the pool offers, without any extra bookkeeping.
        """
        scheme = self.session.get(Scheme, scheme_id)
        if scheme is None:
            raise NotFoundError(f"志愿方案 {scheme_id} 不存在")
        return ~exists(
            select(1)
            .select_from(SchemeItem)
            .join(SchemeVersion, SchemeVersion.id == SchemeItem.scheme_version_id)
            .where(
                SchemeVersion.scheme_id == scheme_id,
                SchemeVersion.version_no == scheme.current_version_no,
                SchemeItem.recommendation_item_id == RecommendationItem.id,
            )
        )

    def _bucket_counts(self, conditions: list, with_joins) -> dict[str, int]:
        """Per-bucket counts under the current filters.

        `conditions` deliberately excludes the bucket filter: the question is "how many
        are there in each bucket", so constraining by bucket would make the others read
        zero.
        """
        rows = self.session.execute(
            with_joins(select(RecommendationItem.risk_bucket, func.count()))
            .where(*conditions)
            .group_by(RecommendationItem.risk_bucket)
        ).all()
        return {str(bucket): int(count) for bucket, count in rows if bucket is not None}

    @staticmethod
    def _order_for(sort: str) -> tuple:
        """SQL ordering per sort key, each with a stable tiebreaker.

        `NULLS LAST` matters: an item with no ACS must not sort as if it scored zero, and
        `RecommendationItem.id` keeps paging deterministic when the primary key ties.

        `RISK_MIX` is handled in the query, not here — it needs a window function, so it
        cannot be expressed as a plain ORDER BY on the base columns.
        """
        if sort == ItemSort.ACS_DESC.value:
            return (RecommendationItem.acs.desc().nullslast(), RecommendationItem.id)
        if sort == ItemSort.ACS_ASC.value:
            return (RecommendationItem.acs.asc().nullslast(), RecommendationItem.id)
        if sort == ItemSort.TUITION_ASC.value:
            # Tuition lives on the plan, which is already joined.
            return (AdmissionPlan.tuition_fee.asc(), RecommendationItem.id)
        if sort == ItemSort.PLAN_COUNT_DESC.value:
            return (
                AdmissionPlan.plan_count.desc(),
                RecommendationItem.acs.desc().nullslast(),
                RecommendationItem.id,
            )
        # Default: preference first, mirroring the two-step design (select by preference,
        # then order by risk).
        #
        # The tiebreaker is ACS **descending**, not ascending. `preference_score` collapses
        # to a three-value 院校层次 mapping whenever the candidate set no 专业/地域/学校
        # preferences, so the tiebreaker decides almost the entire order. Ascending put the
        # hardest programmes first and made the pool look like it contained only 高冲.
        return (
            RecommendationItem.preference_score.desc().nullslast(),
            RecommendationItem.acs.desc().nullslast(),
            RecommendationItem.id,
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
            major_sequence=detail.get("major_sequence"),
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
