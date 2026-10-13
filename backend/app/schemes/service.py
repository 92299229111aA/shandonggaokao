"""Scheme service: versioned, optimizable, optimistically-locked 志愿方案.

Invariants enforced here (not in the router):
  * every edit appends a new `scheme_versions` row — old versions stay readable
  * `schemes.lock_version` is the optimistic-locking token; a mismatch is a 409
  * hard-excluded / FAIL volunteers can never be added, even by hand
  * `target_count` never exceeds `admission_cycles.application_limit`
  * re-optimizing always keeps `locked` items
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.admission.models import AdmissionCycle, AdmissionPlan, ProgramSeries
from app.admission.repository import CycleRepository
from app.candidate.models import PreferenceProfile
from app.common.enums import RiskMode
from app.common.errors import NotFoundError, RuleViolationError, VersionConflictError
from app.optimizer.ordering import order_items
from app.optimizer.portfolio import PortfolioOptimizer
from app.optimizer.types import (
    DEFAULT_RISK_TARGETS,
    CandidateItem,
    OptimizationRequest,
    OptimizerConfig,
    SchemeOrderItem,
)
from app.recommendation.models import (
    RecommendationItem,
    Scheme,
    SchemeItem,
    SchemeVersion,
)
from app.recommendation.models import (
    Session as RunSession,
)
from app.reference.models import University
from app.schemes.schemas import (
    ExportResponse,
    ExportRow,
    OptimizeRequest,
    SchemeCreate,
    SchemeDTO,
    SchemeItemDTO,
    SchemeOrderUpdate,
    SchemeVersionDTO,
)


class SchemeService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.cycles = CycleRepository(session)

    # ---------------- create / read ----------------

    def create(self, payload: SchemeCreate) -> SchemeDTO:
        run = self.session.get(RunSession, payload.recommendation_session_id)
        if run is None:
            raise NotFoundError(f"推荐会话 {payload.recommendation_session_id} 不存在")
        if run.status != "SUCCEEDED":
            raise RuleViolationError(
                f"推荐会话状态为 {run.status}，只有成功的会话才能生成方案",
                details={"session_id": run.id, "status": run.status},
            )

        cycle = self.cycles.require(run.cycle_id)
        target_count = payload.target_count or cycle.application_limit
        if target_count > cycle.application_limit:
            raise RuleViolationError(
                f"志愿数量上限为 {cycle.application_limit}（来自 admission_cycles），"
                f"不能设置为 {target_count}",
                details={"application_limit": cycle.application_limit, "cycle_id": cycle.id},
            )

        scheme = Scheme(
            session_id=run.id,
            title=payload.title,
            target_count=target_count,
            risk_mode=payload.risk_mode,
            current_version_no=1,
            lock_version=1,
            is_demo=self._session_is_demo(run),
        )
        self.session.add(scheme)
        self.session.flush()

        selected_ids = payload.item_ids
        if not selected_ids:
            selected_ids = self._optimize_selection(
                run=run, cycle=cycle, risk_mode=payload.risk_mode,
                target_count=target_count, locked_item_ids=[], config=payload.optimizer_config(),
            ).selected_item_ids

        self._write_version(
            scheme,
            operation="CREATE",
            change_summary=f"初始方案：{len(selected_ids)} 个志愿",
            recommendation_item_ids=list(selected_ids),
            optimizer_stats=None,
        )
        return self.get(scheme.id)

    def get(self, scheme_id: int) -> SchemeDTO:
        scheme = self.session.get(Scheme, scheme_id)
        if scheme is None:
            raise NotFoundError(f"志愿方案 {scheme_id} 不存在")
        version = self._current_version(scheme)
        return self._to_dto(scheme, version)

    def get_version(self, scheme_id: int, version_no: int) -> SchemeDTO:
        scheme = self.session.get(Scheme, scheme_id)
        if scheme is None:
            raise NotFoundError(f"志愿方案 {scheme_id} 不存在")
        version = self._version_row(scheme_id, version_no)
        if version is None:
            raise NotFoundError(f"方案 {scheme_id} 的版本 {version_no} 不存在")
        return self._to_dto(scheme, version, historical=True)

    def list_versions(self, scheme_id: int) -> list[SchemeVersionDTO]:
        scheme = self.session.get(Scheme, scheme_id)
        if scheme is None:
            raise NotFoundError(f"志愿方案 {scheme_id} 不存在")
        rows = self.session.execute(
            select(SchemeVersion)
            .where(SchemeVersion.scheme_id == scheme_id)
            .order_by(desc(SchemeVersion.version_no))
        ).scalars()
        return [SchemeVersionDTO.model_validate(row) for row in rows]

    def export(self, scheme_id: int, version_no: int | None = None) -> ExportResponse:
        """Rows for CSV/Excel export. Never promises an admission probability."""
        dto = self.get(scheme_id) if version_no is None else self.get_version(scheme_id, version_no)
        run = self.session.get(RunSession, dto.session_id)
        cycle = self.cycles.get(run.cycle_id) if run else None
        return ExportResponse(
            scheme_id=dto.id,
            version_no=dto.version_no,
            target_count=dto.target_count,
            application_limit=cycle.application_limit if cycle else None,
            rows=[
                ExportRow(
                    position=item.position,
                    program_label=item.program_label,
                    university_name=item.university_name,
                    plan_name=item.plan_name,
                    risk_bucket=item.risk_bucket,
                    acs=item.acs,
                    confidence=item.confidence,
                    preference_score=item.preference_score,
                    tuition_fee=item.tuition_fee,
                    plan_count=item.plan_count,
                )
                for item in dto.items
            ],
        )

    # ---------------- edits ----------------

    def update_order(self, scheme_id: int, payload: SchemeOrderUpdate) -> SchemeDTO:
        scheme = self._require_unlocked(scheme_id, payload.version)
        current = self._current_version(scheme)
        by_item = {item.recommendation_item_id: item for item in current.items}

        missing = [
            entry.recommendation_item_id
            for entry in payload.items
            if entry.recommendation_item_id not in by_item
        ]
        if missing:
            raise RuleViolationError(
                "提交的排序中包含不在当前方案里的志愿，请刷新后重试",
                details={"unknown_item_ids": missing[:20]},
            )

        order_spec = {entry.recommendation_item_id: entry for entry in payload.items}
        entries: list[SchemeOrderItem] = []
        for item in current.items:
            item_id = item.recommendation_item_id or 0
            spec = order_spec.get(item_id)
            entries.append(
                SchemeOrderItem(
                    item_id=item_id,
                    plan_id=self._plan_id_of(item),
                    position=spec.position if spec and spec.position else None,
                    locked=spec.locked if spec and spec.locked is not None else item.locked,
                    fixed_position=bool(spec and spec.position),
                    acs=item.acs,
                    preference_score=item.preference_score,
                    confidence=item.confidence,
                    eligibility=item.eligibility or "PASS",
                )
            )
        ordered = order_items(entries)

        self._write_version(
            scheme,
            operation="REORDER",
            change_summary=payload.change_summary or "手动调整顺序",
            recommendation_item_ids=[entry.item_id for entry in ordered],
            ordered=ordered,
            carry_over=current,
        )
        return self.get(scheme_id)

    def add_items(self, scheme_id: int, version: int, recommendation_item_ids: list[int]) -> SchemeDTO:
        scheme = self._require_unlocked(scheme_id, version)
        current = self._current_version(scheme)

        if len(current.items) + len(recommendation_item_ids) > scheme.target_count:
            raise RuleViolationError(
                f"加入后志愿数将超过目标 {scheme.target_count} 个"
                f"（当前 {len(current.items)} 个），请先删除或调整目标数量",
                details={"target_count": scheme.target_count, "current": len(current.items)},
            )

        new_items = self._load_recommendation_items(scheme.session_id, recommendation_item_ids)
        blocked = [
            item.id
            for item in new_items
            if item.eligibility == "FAIL" or not item.in_candidate_pool
        ]
        if blocked:
            raise RuleViolationError(
                "不能加入报考资格不符或未进入候选池的志愿",
                details={"blocked_item_ids": blocked[:20]},
            )

        existing_ids = [item.recommendation_item_id for item in current.items if item.recommendation_item_id]
        ordered_ids = [*existing_ids, *[item.id for item in new_items]]
        self._write_version(
            scheme,
            operation="ADD",
            change_summary=f"新增 {len(new_items)} 个志愿",
            recommendation_item_ids=ordered_ids,
            carry_over=current,
        )
        return self.get(scheme_id)

    def remove_item(self, scheme_id: int, version: int, position: int) -> SchemeDTO:
        scheme = self._require_unlocked(scheme_id, version)
        current = self._current_version(scheme)
        victim = next((item for item in current.items if item.position == position), None)
        if victim is None:
            raise NotFoundError(f"方案 {scheme_id} 第 {position} 个志愿不存在")

        keep_ids = [
            item.recommendation_item_id
            for item in current.items
            if item.position != position and item.recommendation_item_id
        ]
        self._write_version(
            scheme,
            operation="REMOVE",
            change_summary=f"删除第 {position} 个志愿",
            recommendation_item_ids=keep_ids,
            carry_over=current,
            drop_recommendation_item_id=victim.recommendation_item_id,
        )
        return self.get(scheme_id)

    def set_lock(self, scheme_id: int, version: int, position: int, locked: bool) -> SchemeDTO:
        scheme = self._require_unlocked(scheme_id, version)
        current = self._current_version(scheme)
        target = next((item for item in current.items if item.position == position), None)
        if target is None:
            raise NotFoundError(f"方案 {scheme_id} 第 {position} 个志愿不存在")

        entries = [
            SchemeOrderItem(
                item_id=item.recommendation_item_id or 0,
                plan_id=self._plan_id_of(item),
                position=item.position,
                locked=locked if item.position == position else item.locked,
                fixed_position=item.fixed_position,
                acs=item.acs,
                preference_score=item.preference_score,
                confidence=item.confidence,
                eligibility=item.eligibility or "PASS",
            )
            for item in current.items
        ]
        ordered = order_items(entries)
        self._write_version(
            scheme,
            operation="LOCK" if locked else "UNLOCK",
            change_summary=f"{'锁定' if locked else '解锁'}第 {position} 个志愿",
            recommendation_item_ids=[entry.item_id for entry in ordered],
            ordered=ordered,
            carry_over=current,
        )
        return self.get(scheme_id)

    def optimize(self, scheme_id: int, payload: OptimizeRequest) -> SchemeDTO:
        scheme = self._require_unlocked(scheme_id, payload.version)
        run = self.session.get(RunSession, scheme.session_id)
        if run is None:  # pragma: no cover - FK guarantees it
            raise NotFoundError(f"推荐会话 {scheme.session_id} 不存在")
        cycle = self.cycles.require(run.cycle_id)

        current = self._current_version(scheme)
        # 重新优化必须保留 locked items
        locked_ids = [
            item.recommendation_item_id
            for item in current.items
            if item.locked and item.recommendation_item_id
        ]
        risk_mode = payload.risk_mode or scheme.risk_mode
        target_count = payload.target_count or scheme.target_count
        if target_count > cycle.application_limit:
            raise RuleViolationError(
                f"志愿数量上限为 {cycle.application_limit}（来自 admission_cycles）",
                details={"application_limit": cycle.application_limit},
            )

        result = self._optimize_selection(
            run=run,
            cycle=cycle,
            risk_mode=risk_mode,
            target_count=target_count,
            locked_item_ids=locked_ids,
            config=payload.optimizer_config(),
            min_safe_count=payload.min_safe_count,
        )

        selection = list(result.selected_item_ids)
        for locked_id in locked_ids:
            if locked_id not in selection:
                selection.append(locked_id)

        scheme.risk_mode = risk_mode
        scheme.target_count = target_count
        self._write_version(
            scheme,
            operation="OPTIMIZE",
            change_summary=(
                f"重新优化：选中 {len(selection)} 个志愿"
                f"（保留 {len(locked_ids)} 个锁定志愿）"
            ),
            recommendation_item_ids=selection,
            optimizer_stats=result.as_dict(),
            carry_over=current,
            keep_locked_positions=True,
        )
        return self.get(scheme_id)

    # ---------------- internals ----------------

    def _require_unlocked(self, scheme_id: int, expected_version: int) -> Scheme:
        # Lock the row for the rest of the transaction. Without it two editors can both
        # read lock_version = N, both pass this check, and both try to insert
        # version_no = N+1 — the loser hits uq_scheme_versions_scheme_id_version_no and
        # the API answers 500 instead of the promised 409.
        scheme = self.session.execute(
            select(Scheme).where(Scheme.id == scheme_id).with_for_update()
        ).scalar_one_or_none()
        if scheme is None:
            raise NotFoundError(f"志愿方案 {scheme_id} 不存在")
        if scheme.lock_version != expected_version:
            raise VersionConflictError(
                "志愿方案已经在其他位置被修改",
                details={
                    "expected": expected_version,
                    "actual": scheme.lock_version,
                    "current_version_no": scheme.current_version_no,
                },
            )
        return scheme

    def _current_version(self, scheme: Scheme) -> SchemeVersion:
        version = self._version_row(scheme.id, scheme.current_version_no)
        if version is None:  # pragma: no cover - created together with the scheme
            raise NotFoundError(f"方案 {scheme.id} 缺少当前版本数据")
        return version

    def _version_row(self, scheme_id: int, version_no: int) -> SchemeVersion | None:
        return self.session.execute(
            select(SchemeVersion)
            .where(SchemeVersion.scheme_id == scheme_id, SchemeVersion.version_no == version_no)
        ).scalar_one_or_none()

    def require_version_id(self, scheme_id: int, version_no: int) -> int:
        """Primary key of a scheme version, for rows that reference it by FK."""
        version = self._version_row(scheme_id, version_no)
        if version is None:
            raise NotFoundError(f"方案 {scheme_id} 的版本 {version_no} 不存在")
        return version.id

    def _session_is_demo(self, run: RunSession) -> bool:
        from app.admission.models import DatasetSnapshot

        snapshot = self.session.get(DatasetSnapshot, run.snapshot_id)
        return bool(snapshot and snapshot.is_demo)

    def _load_recommendation_items(
        self, session_id: int, item_ids: list[int]
    ) -> list[RecommendationItem]:
        if not item_ids:
            return []
        rows = list(
            self.session.execute(
                select(RecommendationItem).where(
                    RecommendationItem.session_id == session_id,
                    RecommendationItem.id.in_(item_ids),
                )
            ).scalars()
        )
        found = {row.id for row in rows}
        missing = [item_id for item_id in item_ids if item_id not in found]
        if missing:
            raise NotFoundError(
                "部分志愿不属于该推荐会话",
                details={"unknown_item_ids": missing[:20]},
            )
        by_id = {row.id: row for row in rows}
        return [by_id[item_id] for item_id in item_ids]

    def _optimize_selection(
        self,
        *,
        run: RunSession,
        cycle: AdmissionCycle,
        risk_mode: str,
        target_count: int,
        locked_item_ids: list[int],
        config: OptimizerConfig,
        min_safe_count: int | None = None,
    ):
        pool = list(
            self.session.execute(
                select(RecommendationItem).where(
                    RecommendationItem.session_id == run.id,
                    RecommendationItem.in_candidate_pool.is_(True),
                )
            ).scalars()
        )
        if not pool:
            raise RuleViolationError("该推荐会话没有候选志愿，无法生成方案")

        preference = self.session.get(PreferenceProfile, run.preference_profile_id)
        if min_safe_count is None and preference is not None:
            min_safe_count = preference.min_safe_count

        majors = self._major_ids_for([item.plan_id for item in pool])
        candidates = tuple(
            CandidateItem(
                item_id=item.id,
                plan_id=item.plan_id,
                university_id=majors.get(item.plan_id, (0, None))[0],
                major_id=majors.get(item.plan_id, (0, None))[1],
                eligibility=item.eligibility,
                acs=item.acs,
                confidence=item.confidence,
                preference_score=item.preference_score,
                risk_bucket=item.risk_bucket,
                locked=item.id in set(locked_item_ids),
                # UNKNOWN 不计入安全数量
                counts_as_safe=item.eligibility == "PASS",
            )
            for item in pool
        )

        request = OptimizationRequest(
            candidates=candidates,
            max_count=target_count,
            risk_mode=risk_mode,
            risk_targets=dict(
                DEFAULT_RISK_TARGETS.get(risk_mode, DEFAULT_RISK_TARGETS[RiskMode.BALANCED.value])
            ),
            config=config,
            min_safe_count=min_safe_count,
            # Locked volunteers are a hard constraint, so the cap still holds.
            required_item_ids=frozenset(locked_item_ids),
        )
        result = PortfolioOptimizer(config).optimize(request)

        if not result.selected_item_ids:
            raise RuleViolationError(
                "优化器没有选出任何志愿："
                + ("；".join(result.relax_suggestions) or "请检查候选池与最低安全数量设置"),
                details=result.diagnostics,
            )
        return result

    def _major_ids_for(self, plan_ids: list[int]) -> dict[int, tuple[int, int | None]]:
        if not plan_ids:
            return {}
        rows = self.session.execute(
            select(AdmissionPlan.id, ProgramSeries.university_id, ProgramSeries.major_id)
            .join(ProgramSeries, ProgramSeries.id == AdmissionPlan.series_id)
            .where(AdmissionPlan.id.in_(plan_ids))
        ).all()
        return {int(plan_id): (int(uni), major) for plan_id, uni, major in rows}

    def _write_version(
        self,
        scheme: Scheme,
        *,
        operation: str,
        change_summary: str,
        recommendation_item_ids: list[int],
        ordered: list | None = None,
        optimizer_stats: dict[str, Any] | None = None,
        carry_over: SchemeVersion | None = None,
        keep_locked_positions: bool = False,
        drop_recommendation_item_id: int | None = None,
    ) -> SchemeVersion:
        """Append an immutable version, then bump the optimistic-lock token."""
        items = self._load_recommendation_items(scheme.session_id, recommendation_item_ids)
        by_id = {item.id: item for item in items}

        carry_meta: dict[int, SchemeItem] = {}
        if carry_over is not None:
            carry_meta = {
                item.recommendation_item_id: item
                for item in carry_over.items
                if item.recommendation_item_id is not None
            }

        positions = {entry.item_id: entry for entry in ordered} if ordered is not None else {}

        entries: list[SchemeOrderItem] = []
        for item_id in recommendation_item_ids:
            previous = carry_meta.get(item_id)
            spec = positions.get(item_id)
            pinned = spec is not None and spec.fixed_position
            if pinned and spec is not None:
                position: int | None = spec.position
            elif keep_locked_positions and previous is not None and previous.fixed_position:
                position = previous.position
            else:
                position = None
            entries.append(
                SchemeOrderItem(
                    item_id=item_id,
                    plan_id=by_id[item_id].plan_id,
                    position=position,
                    locked=(
                        spec.locked
                        if spec is not None
                        else (bool(previous.locked) if previous is not None else False)
                    ),
                    fixed_position=pinned,
                    acs=by_id[item_id].acs,
                    preference_score=by_id[item_id].preference_score,
                    confidence=by_id[item_id].confidence,
                    eligibility=by_id[item_id].eligibility,
                )
            )
        final_order = order_items(entries)

        run = self.session.get(RunSession, scheme.session_id)
        next_no = 1 if carry_over is None else scheme.current_version_no + 1
        version = SchemeVersion(
            scheme_id=scheme.id,
            version_no=next_no,
            operation=operation,
            change_summary=change_summary,
            snapshot_id=run.snapshot_id if run else None,
            model_version=run.model_version if run else None,
            optimizer_stats=optimizer_stats,
            item_count=len(final_order),
        )
        self.session.add(version)
        self.session.flush()

        self.session.add_all(
            [
                SchemeItem(
                    scheme_version_id=version.id,
                    recommendation_item_id=entry.item_id,
                    position=entry.position,
                    locked=entry.locked,
                    fixed_position=entry.fixed_position,
                    eligibility=entry.eligibility,
                    acs=entry.acs,
                    confidence=entry.confidence,
                    preference_score=entry.preference_score,
                    desire_band=entry.desire_band,
                    risk_bucket=by_id[entry.item_id].risk_bucket,
                )
                for entry in final_order
            ]
        )
        scheme.current_version_no = next_no
        if carry_over is not None:
            scheme.lock_version += 1
        self.session.flush()
        return version

    @staticmethod
    def _plan_id_of(item: SchemeItem) -> int:
        if item.recommendation_item is not None:
            return item.recommendation_item.plan_id
        return 0

    def _to_dto(self, scheme: Scheme, version: SchemeVersion, *, historical: bool = False) -> SchemeDTO:
        items = self.session.execute(
            select(SchemeItem, RecommendationItem, AdmissionPlan, University)
            .join(RecommendationItem, RecommendationItem.id == SchemeItem.recommendation_item_id)
            .join(AdmissionPlan, AdmissionPlan.id == RecommendationItem.plan_id)
            .join(ProgramSeries, ProgramSeries.id == AdmissionPlan.series_id)
            .join(University, University.id == ProgramSeries.university_id)
            .where(SchemeItem.scheme_version_id == version.id)
            .order_by(SchemeItem.position)
        ).all()

        dto_items = [
            SchemeItemDTO(
                position=scheme_item.position,
                recommendation_item_id=scheme_item.recommendation_item_id or 0,
                plan_id=plan.id,
                program_label=f"{plan.plan_name} · {university.name}",
                plan_name=plan.plan_name,
                university_name=university.name,
                eligibility=scheme_item.eligibility or "PASS",
                acs=scheme_item.acs,
                confidence=scheme_item.confidence,
                preference_score=scheme_item.preference_score,
                desire_band=scheme_item.desire_band,
                risk_bucket=scheme_item.risk_bucket,
                locked=scheme_item.locked,
                fixed_position=scheme_item.fixed_position,
                tuition_fee=plan.tuition_fee,
                plan_count=plan.plan_count,
                is_demo=plan.is_demo,
            )
            for scheme_item, _recommendation_item, plan, university in items
        ]

        risk_counts: dict[str, int] = defaultdict(int)
        for item in dto_items:
            risk_counts[item.risk_bucket or "UNKNOWN"] += 1

        return SchemeDTO(
            id=scheme.id,
            session_id=scheme.session_id,
            title=scheme.title,
            target_count=scheme.target_count,
            risk_mode=scheme.risk_mode,
            current_version_no=scheme.current_version_no,
            lock_version=scheme.lock_version,
            is_demo=scheme.is_demo,
            version_no=version.version_no,
            operation=version.operation,
            change_summary=version.change_summary,
            item_count=version.item_count,
            optimizer_stats=version.optimizer_stats,
            risk_distribution=dict(risk_counts),
            items=dto_items,
            is_historical=historical,
        )


def count_scheme_items(session: Session, scheme_version_id: int) -> int:
    return int(
        session.execute(
            select(func.count())
            .select_from(SchemeItem)
            .where(SchemeItem.scheme_version_id == scheme_version_id)
        ).scalar_one()
    )


__all__ = ["SchemeService", "count_scheme_items"]
