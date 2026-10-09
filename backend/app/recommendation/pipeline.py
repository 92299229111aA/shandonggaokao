"""The recommendation pipeline.

    锁定数据快照 → 标准化考生信息 → Eligibility → 用户硬排除
    → Program Series 历史匹配 → 位次百分位标准化 → Base ACS → Confidence
    → Final ACS → Preference Score → 分层 Candidate Pool → 持久化

The pipeline is a plain class taking a Session: the HTTP layer only validates
DTOs. Scoring and ranking internals are pure and separately unit-tested.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.admission.models import AdmissionCycle, DatasetSnapshot, ExamYearStats
from app.admission.repository import CycleRepository, PlanRepository, SnapshotRepository
from app.candidate.models import PreferenceProfile, Profile
from app.common.enums import EligibilityStatus, SessionStatus
from app.common.errors import NotFoundError, RuleViolationError
from app.recommendation.models import ModelVersion, RecommendationItem
from app.recommendation.models import Session as RunSession
from app.recommendation.pipeline_types import (
    PipelineConfig,
    PipelineResult,
    PipelineStats,
    PlanBundle,
)
from app.rules.mapping import build_candidate_context, build_target_plan
from app.rules.service import RuleEngine, RuleEngineConfig
from app.scoring.acs import ACSService
from app.scoring.assembly import (
    evaluate_acs,
    evaluate_preference,
    history_from_outcomes,
    preference_weights_from,
)
from app.scoring.preference import PreferenceService
from app.scoring.types import ACSParameters

logger = logging.getLogger(__name__)


class RecommendationPipeline:
    def __init__(
        self,
        session: Session,
        *,
        rule_engine: RuleEngine | None = None,
        acs_service: ACSService | None = None,
        preference_service: PreferenceService | None = None,
        pipeline_config: PipelineConfig | None = None,
    ) -> None:
        self.session = session
        self.rule_engine = rule_engine or RuleEngine(config=RuleEngineConfig())
        self.acs_service = acs_service or ACSService(ACSParameters())
        self.preference_service = preference_service or PreferenceService()
        self.config = pipeline_config or PipelineConfig()

        self.plans = PlanRepository(session)
        self.cycles = CycleRepository(session)
        self.snapshots = SnapshotRepository(session)
        self.years = YearStatsRepository(session)

    # ---------------- entry point ----------------

    def run(
        self,
        *,
        profile_id: int,
        preference_id: int | None = None,
        cycle_id: int | None = None,
        snapshot_id: int | None = None,
        locked_plan_ids: set[int] | None = None,
    ) -> PipelineResult:
        locked_plan_ids = locked_plan_ids or set()

        profile = self._load_profile(profile_id)
        cycle = self._resolve_cycle(cycle_id or profile.cycle_id)
        snapshot = self._resolve_snapshot(snapshot_id)
        preference = self._load_preference(profile_id, preference_id)
        model = self._get_or_create_model_version()

        run = RunSession(
            profile_id=profile.id,
            preference_profile_id=preference.id,
            cycle_id=cycle.id,
            snapshot_id=snapshot.id,
            model_version_id=model.id,
            # ---- the replay contract ----
            profile_version=profile.version,
            preference_version=preference.version,
            policy_version=cycle.policy_version,
            model_version=model.code,
            algorithm_config_version=self.acs_service.params.version,
            status=SessionStatus.RUNNING.value,
            stage="ELIGIBILITY",
            started_at=datetime.now(UTC),
        )
        self.session.add(run)
        self.session.flush()

        stats = PipelineStats(pool_limits=dict(self.config.pool_limits))
        bundles: list[PlanBundle] = []
        try:
            candidate_rank = preference.candidate_rank or profile.rank
            ranked_candidates = self._current_ranked_candidates(cycle.exam_year)
            if ranked_candidates is None:
                stats.notes.append(
                    f"{cycle.exam_year} 年缺少考生人数（exam_year_stats），"
                    "无法做位次百分位标准化，本次不产出 ACS 评分"
                )

            rows = self._load_plan_rows(cycle.id, snapshot.id)
            stats.plans_scanned = len(rows)
            if not rows:
                stats.notes.append("该数据快照下没有任何招生计划，请检查 seed/ETL 是否已执行")

            bundles = self._stage_eligibility(
                rows=rows, profile=profile, preference=preference,
                ranked_candidates=ranked_candidates, stats=stats,
            )

            if ranked_candidates is not None:
                run.stage = "SCORING"
                self.session.flush()
                self._stage_scoring(
                    bundles=bundles, profile=profile, preference=preference, cycle=cycle,
                    candidate_rank=candidate_rank, ranked_candidates=ranked_candidates,
                    stats=stats, locked_plan_ids=locked_plan_ids,
                )

                run.stage = "POOLING"
                self.session.flush()
                # Pool membership is recorded per item via `in_candidate_pool`;
                # the per-bucket counts land in stats.
                self._stage_pool(bundles, locked_plan_ids=locked_plan_ids, stats=stats)

                run.stage = "PERSISTING"
                self.session.flush()
                self._persist(run, bundles, stats)

            run.status = SessionStatus.SUCCEEDED.value
            run.stage = "DONE"
            run.stats = stats.as_dict()
            run.finished_at = datetime.now(UTC)
            self.session.flush()
        except Exception as exc:
            run.status = SessionStatus.FAILED.value
            run.error_message = f"{type(exc).__name__}: {exc}"
            run.stats = stats.as_dict()
            run.finished_at = datetime.now(UTC)
            self.session.flush()
            raise

        return PipelineResult(session_id=run.id, stats=stats, bundles=bundles)

    # ---------------- stage 1: lock versions ----------------

    def _load_profile(self, profile_id: int) -> Profile:
        profile = self.session.get(
            Profile,
            profile_id,
            options=[selectinload(Profile.subject_scores), selectinload(Profile.attributes)],
        )
        if profile is None:
            raise NotFoundError(f"考生档案 {profile_id} 不存在")
        return profile

    def _load_preference(self, profile_id: int, preference_id: int | None) -> PreferenceProfile:
        stmt = (
            select(PreferenceProfile)
            .where(PreferenceProfile.profile_id == profile_id)
            .options(
                selectinload(PreferenceProfile.major_preferences),
                selectinload(PreferenceProfile.location_preferences),
                selectinload(PreferenceProfile.university_preferences),
                selectinload(PreferenceProfile.other_preferences),
            )
        )
        if preference_id is not None:
            stmt = stmt.where(PreferenceProfile.id == preference_id)
        else:
            stmt = stmt.order_by(PreferenceProfile.version.desc()).limit(1)
        preference = self.session.execute(stmt).scalar_one_or_none()
        if preference is None:
            raise NotFoundError(
                f"考生 {profile_id} 没有可用偏好档案，"
                f"请先 POST /api/v1/profiles/{profile_id}/preferences"
            )
        return preference

    def _resolve_cycle(self, cycle_id: int) -> AdmissionCycle:
        cycle = self.cycles.get(cycle_id)
        if cycle is None:
            raise NotFoundError(f"招生周期 {cycle_id} 不存在")
        if cycle.exam_category != "SUMMER_REGULAR" or cycle.batch_code != "REGULAR_BATCH_1":
            raise RuleViolationError(
                "V1 仅支持山东夏季高考普通类常规批第1次志愿",
                details={"cycle_id": cycle.id, "batch_code": cycle.batch_code},
            )
        return cycle

    def _resolve_snapshot(self, snapshot_id: int | None) -> DatasetSnapshot:
        """Only PUBLISHED snapshots may feed the engine (invariant #9)."""
        if snapshot_id is not None:
            snapshot = self.snapshots.get(snapshot_id)
            if snapshot is None:
                raise NotFoundError(f"数据快照 {snapshot_id} 不存在")
            if snapshot.status != "PUBLISHED":
                raise RuleViolationError(
                    f"数据快照 {snapshot.code} 状态为 {snapshot.status}，"
                    "只有 PUBLISHED 快照可用于推荐",
                    details={"snapshot_id": snapshot.id, "status": snapshot.status},
                )
            return snapshot
        latest = self.snapshots.latest_published()
        if latest is None:
            raise RuleViolationError("没有已发布（PUBLISHED）的数据快照，无法生成推荐")
        return latest

    def _get_or_create_model_version(self) -> ModelVersion:
        code = self.config.model_version
        model = self.session.execute(
            select(ModelVersion).where(ModelVersion.code == code)
        ).scalar_one_or_none()
        if model is not None:
            return model
        model = ModelVersion(
            code=code,
            label=f"ACS {self.acs_service.params.version} + 分层候选池",
            parameters={
                "acs": self.acs_service.params.as_dict(),
                "pipeline": self.config.as_dict(),
            },
            is_active=True,
            notes=(
                "ACS 是录取安全指数（0-100），不是录取概率。"
                "本记录固定公式常量与候选池上限，用于历史重放。"
            ),
        )
        self.session.add(model)
        self.session.flush()
        return model

    def _current_ranked_candidates(self, exam_year: int) -> int | None:
        return self.years.ranked_candidates(exam_year)

    def _load_plan_rows(self, cycle_id: int, snapshot_id: int) -> list[dict[str, Any]]:
        total = self.plans.count_candidates(cycle_id=cycle_id, snapshot_id=snapshot_id)
        if total == 0:
            return []
        return self.plans.list_candidates(
            cycle_id=cycle_id, snapshot_id=snapshot_id, limit=total, offset=0
        )

    # ---------------- stage 2: eligibility ----------------

    def _stage_eligibility(
        self,
        *,
        rows: list[dict[str, Any]],
        profile: Profile,
        preference: PreferenceProfile,
        ranked_candidates: int | None,
        stats: PipelineStats,
    ) -> list[PlanBundle]:
        ctx = build_candidate_context(
            profile=profile,
            preference=preference,
            subject_scores=list(profile.subject_scores),
            attributes=list(profile.attributes),
            current_ranked_candidates=ranked_candidates,
        )
        rule_map = self.plans.subject_rule_groups(
            [row["subject_rule_set_id"] for row in rows if row["subject_rule_set_id"]]
        )
        requirement_map = self.plans.requirements_for([row["plan_id"] for row in rows])

        bundles: list[PlanBundle] = []
        for row in rows:
            target = build_target_plan(
                plan_row=row,
                subject_groups=rule_map.get(row["subject_rule_set_id"] or 0),
                requirements=requirement_map.get(row["plan_id"], []),
            )
            result = self.rule_engine.evaluate(ctx, target)
            if result.status is EligibilityStatus.PASS:
                stats.eligibility_pass += 1
            elif result.status is EligibilityStatus.UNKNOWN:
                stats.eligibility_unknown += 1
            else:
                stats.eligibility_fail += 1
            if result.hard_excluded:
                stats.hard_excluded += 1
            bundles.append(PlanBundle(plan_row=row, target=target, eligibility=result))
        return bundles

    # ---------------- stage 3: history + scoring ----------------

    def _stage_scoring(
        self,
        *,
        bundles: list[PlanBundle],
        profile: Profile,
        preference: PreferenceProfile,
        cycle: AdmissionCycle,
        candidate_rank: int | None,
        ranked_candidates: int,
        stats: PipelineStats,
        locked_plan_ids: set[int],
    ) -> None:
        # Locked items are scored even when the rule engine would exclude them,
        # because the user explicitly asked to keep them in the scheme.
        poolable = [
            bundle
            for bundle in bundles
            if bundle.eligibility.can_be_pooled or bundle.plan_id in locked_plan_ids
        ]
        if not poolable:
            return

        outcomes_by_series = self.plans.outcomes_for_series(
            sorted({int(bundle.plan_row["series_id"]) for bundle in poolable})
        )
        ranked_by_year = self.years.ranked_candidates_by_year()
        weights = preference_weights_from(preference)

        for bundle in poolable:
            row = bundle.plan_row
            history = history_from_outcomes(
                outcomes_by_series.get(int(row["series_id"]), []),
                series_match_quality=row.get("series_match_quality") or "EXACT",
                ranked_candidates_by_year=ranked_by_year,
                is_demo=bool(row.get("is_demo")),
            )
            evaluation = evaluate_acs(
                service=self.acs_service,
                candidate_rank=candidate_rank,
                current_ranked_candidates=ranked_candidates,
                history=history,
                plan_current=row.get("plan_count"),
                series_match_quality=row.get("series_match_quality") or "EXACT",
                data_year=cycle.exam_year,
            )
            preference_result = evaluate_preference(
                service=self.preference_service,
                weights=weights,
                major_id=row.get("major_id"),
                discipline_code=row.get("discipline_code"),
                major_names=(
                    row.get("plan_name") or "",
                    row.get("series_name") or "",
                    row.get("major_name") or "",
                ),
                university_id=int(row["university_id"]),
                university_name=row["university_name"],
                university_traits={
                    "IS_985": bool(row.get("is_985")),
                    "IS_211": bool(row.get("is_211")),
                    "IS_DOUBLE_FIRST_CLASS": bool(row.get("is_double_first_class")),
                },
                province_code=row.get("province_code"),
                city_code=row.get("city_code"),
                tuition_fee=int(row.get("tuition_fee") or 0),
                tuition_budget=preference.max_tuition,
                tuition_floor=preference.min_tuition,
            )
            bundle.acs = evaluation
            bundle.preference = preference_result
            bundle.history = tuple(history)
            stats.scored += 1
            if evaluation.history_points == 0:
                stats.no_history += 1

    # ---------------- stage 4: layered candidate pool ----------------

    def _stage_pool(
        self,
        bundles: list[PlanBundle],
        *,
        locked_plan_ids: set[int],
        stats: PipelineStats,
    ) -> list[PlanBundle]:
        by_bucket: dict[str, list[PlanBundle]] = defaultdict(list)
        for bundle in bundles:
            if not bundle.poolable:
                continue
            bucket = bundle.risk_bucket
            assert bucket is not None  # poolable implies scored
            by_bucket[bucket.value].append(bundle)

        pooled: list[PlanBundle] = []
        for bucket_name, items in by_bucket.items():
            # 每层优先按 Preference Score DESC、Confidence DESC 截取
            items.sort(
                key=lambda bundle: (
                    -(bundle.preference.score if bundle.preference else 0.0),
                    -(bundle.acs.confidence if bundle.acs else 0.0),
                )
            )
            limit = self.config.pool_limits.get(bucket_name, 120)
            # 用户手动锁定的志愿必须无条件加入候选池
            locked = [item for item in items if item.plan_id in locked_plan_ids]
            rest = [item for item in items if item.plan_id not in locked_plan_ids]
            keep = locked + rest[: max(0, limit - len(locked))]
            stats.pool_by_bucket[bucket_name] = len(keep)
            stats.locked_kept += len(locked)
            if len(items) > len(keep):
                stats.truncated_by_bucket[bucket_name] = len(items) - len(keep)
            pooled.extend(keep)

        stats.pooled = len(pooled)
        return pooled

    # ---------------- stage 5: persist ----------------

    def _persist(
        self, run: RunSession, bundles: list[PlanBundle], stats: PipelineStats
    ) -> None:
        to_persist = [bundle for bundle in bundles if bundle.poolable]
        to_persist += [
            bundle
            for bundle in bundles
            if bundle.eligibility.status is EligibilityStatus.FAIL
            and not bundle.poolable
        ][: self.config.persist_fail_sample]

        self.session.add_all(
            [
                self._to_item(run, bundle, in_pool=bundle.poolable)
                for bundle in to_persist
            ]
        )
        self.session.flush()

    @staticmethod
    def _to_item(run: RunSession, bundle: PlanBundle, *, in_pool: bool) -> RecommendationItem:
        return RecommendationItem(
            session_id=run.id,
            plan_id=bundle.plan_id,
            eligibility=bundle.eligibility.status.value,
            eligibility_reasons=bundle.eligibility.as_reasons(),
            base_acs=bundle.acs.base_acs if bundle.acs else None,
            confidence=(bundle.acs.confidence * 100.0) if bundle.acs else None,
            acs=bundle.acs.acs if bundle.acs else None,
            risk_bucket=(bundle.risk_bucket.value if bundle.risk_bucket else None),
            acs_explanation=bundle.acs.as_dict() if bundle.acs else None,
            preference_score=bundle.preference.score if bundle.preference else None,
            preference_dimensions=(bundle.preference.as_dict() if bundle.preference else None),
            in_candidate_pool=in_pool,
            excluded_by=bundle.eligibility.excluded_by,
            source_plan_snapshot_id=run.snapshot_id,
        )


class YearStatsRepository:
    """Per-year candidate population, needed for 位次百分位标准化."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def ranked_candidates(self, exam_year: int) -> int | None:
        value = self.session.execute(
            select(ExamYearStats.ranked_candidates).where(
                ExamYearStats.exam_year == exam_year,
                ExamYearStats.exam_category == "SUMMER_REGULAR",
                ExamYearStats.program_level == "UNDERGRAD",
            )
        ).scalar_one_or_none()
        return int(value) if value is not None else None

    def ranked_candidates_by_year(self) -> dict[int, int]:
        rows = self.session.execute(
            select(ExamYearStats.exam_year, ExamYearStats.ranked_candidates).where(
                ExamYearStats.exam_category == "SUMMER_REGULAR",
                ExamYearStats.program_level == "UNDERGRAD",
            )
        ).all()
        return {int(year): int(count) for year, count in rows}


def count_pooled_items(session: Session, session_id: int) -> int:
    return int(
        session.execute(
            select(func.count())
            .select_from(RecommendationItem)
            .where(
                RecommendationItem.session_id == session_id,
                RecommendationItem.in_candidate_pool.is_(True),
            )
        ).scalar_one()
    )


__all__ = ["RecommendationPipeline", "YearStatsRepository", "count_pooled_items"]
