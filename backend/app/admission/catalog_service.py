"""Catalog service: read-only public data (universities, majors, plans, meta)."""

from __future__ import annotations

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.admission.models import (
    AdmissionCycle,
    AdmissionPlan,
    DatasetSnapshot,
    ProgramSeries,
    SourceDocument,
)
from app.admission.repository import CycleRepository, PlanRepository, SnapshotRepository
from app.admission.schemas import (
    AdmissionCycleDTO,
    DatasetMetaDTO,
    MajorDTO,
    PlanDetail,
    PlanDTO,
    RequirementDTO,
    SnapshotDTO,
    SubjectRuleDTO,
    UniversityDTO,
)
from app.common.errors import NotFoundError
from app.reference.models import Major, University


def _program_label(university_name: str, plan_name: str) -> str:
    """Display form of a volunteer unit: 专业（专业类）+ 学校."""
    return f"{plan_name} · {university_name}"


class CatalogService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.plans = PlanRepository(session)
        self.cycles = CycleRepository(session)
        self.snapshots = SnapshotRepository(session)

    # ---------------- reference data ----------------

    def list_universities(
        self,
        *,
        limit: int,
        offset: int,
        search: str | None = None,
        province_code: str | None = None,
        is_985: bool | None = None,
    ) -> tuple[list[UniversityDTO], int]:
        stmt: Select = select(University)
        if search:
            pattern = f"%{search}%"
            stmt = stmt.where(University.name.ilike(pattern) | University.short_name.ilike(pattern))
        if province_code:
            stmt = stmt.where(University.province_code == province_code)
        if is_985 is not None:
            stmt = stmt.where(University.is_985.is_(is_985))
        total = int(
            self.session.execute(
                select(func.count()).select_from(stmt.subquery())
            ).scalar_one()
        )
        rows = self.session.execute(
            stmt.order_by(University.province_code, University.name).limit(limit).offset(offset)
        ).scalars()
        return [UniversityDTO.model_validate(row) for row in rows], total

    def list_majors(
        self,
        *,
        limit: int,
        offset: int,
        search: str | None = None,
        discipline_code: str | None = None,
        level: str | None = None,
    ) -> tuple[list[MajorDTO], int]:
        stmt: Select = select(Major)
        if search:
            stmt = stmt.where(Major.name.ilike(f"%{search}%") | Major.code.ilike(f"{search}%"))
        if discipline_code:
            stmt = stmt.where(Major.discipline_code == discipline_code)
        if level:
            stmt = stmt.where(Major.level == level)
        total = int(self.session.execute(select(func.count()).select_from(stmt.subquery())).scalar_one())
        rows = self.session.execute(
            stmt.order_by(Major.code).limit(limit).offset(offset)
        ).scalars()
        return [MajorDTO.model_validate(row) for row in rows], total

    # ---------------- admission plans ----------------

    def list_plans(
        self,
        *,
        cycle_id: int,
        snapshot_id: int | None,
        limit: int,
        offset: int,
        search: str | None = None,
        university_id: int | None = None,
        province_code: str | None = None,
        city_code: str | None = None,
        max_tuition: int | None = None,
    ) -> tuple[list[PlanDTO], int]:
        snapshot_id = self._resolve_snapshot_id(snapshot_id)
        rows = self.plans.list_candidates(
            cycle_id=cycle_id,
            snapshot_id=snapshot_id,
            limit=limit,
            offset=offset,
            search=search,
            university_id=university_id,
            province_code=province_code,
            city_code=city_code,
            max_tuition=max_tuition,
        )
        total = self.plans.count_candidates(
            cycle_id=cycle_id,
            snapshot_id=snapshot_id,
            search=search,
            university_id=university_id,
            province_code=province_code,
            city_code=city_code,
            max_tuition=max_tuition,
        )
        subjects = self.plans.subject_rule_groups(
            [row["subject_rule_set_id"] for row in rows if row["subject_rule_set_id"]]
        )
        return [self._to_plan_dto(row, subjects) for row in rows], total

    def get_plan(self, plan_id: int) -> PlanDetail:
        row = self.plans.get_candidate(plan_id)
        if row is None:
            raise NotFoundError(f"招生计划 {plan_id} 不存在")
        subjects = self.plans.subject_rule_groups(
            [row["subject_rule_set_id"]] if row["subject_rule_set_id"] else []
        )
        dto = self._to_plan_dto(row, subjects)
        requirements = self.plans.requirements_for([plan_id]).get(plan_id, [])
        return PlanDetail(
            **dto.model_dump(),
            requirements=[RequirementDTO.model_validate(item) for item in requirements],
        )

    # ---------------- meta ----------------

    def list_cycles(self) -> list[AdmissionCycleDTO]:
        return [AdmissionCycleDTO.model_validate(row) for row in self.cycles.active_cycles()]

    def dataset_meta(self, *, cycle_id: int | None = None) -> DatasetMetaDTO:
        from app.recommendation.models import ModelVersion

        cycle: AdmissionCycle | None = self.cycles.get(cycle_id) if cycle_id else None
        if cycle is None:
            active = self.cycles.active_cycles()
            cycle = active[0] if active else None

        snapshot: DatasetSnapshot | None = self.snapshots.latest_published()
        model = self.session.execute(
            select(ModelVersion).where(ModelVersion.is_active.is_(True)).order_by(ModelVersion.id.desc()).limit(1)
        ).scalar_one_or_none()

        warnings: list[str] = []
        plan_count = 0
        university_count = 0
        if cycle is not None and snapshot is not None:
            plan_count = self.plans.count_candidates(cycle_id=cycle.id, snapshot_id=snapshot.id)
            university_count = int(
                self.session.execute(
                    select(func.count(func.distinct(University.id)))
                    .select_from(University)
                    .join(ProgramSeries, ProgramSeries.university_id == University.id)
                    .join(AdmissionPlan, AdmissionPlan.series_id == ProgramSeries.id)
                    .where(
                        AdmissionPlan.cycle_id == cycle.id,
                        AdmissionPlan.snapshot_id == snapshot.id,
                    )
                ).scalar_one()
            )
        if snapshot is None:
            warnings.append("没有已发布的可用数据快照，推荐功能不可用")
        elif snapshot.is_demo:
            warnings.append("当前使用 DEMO 演示数据，不是官方招生数据，禁止用于真实填报决策")
        else:
            # Real data can still be third-party. Saying so is the difference between
            # "this is real" and "this is authoritative", and users act on the latter.
            third_party = self.session.execute(
                select(func.count())
                .select_from(SourceDocument)
                .where(SourceDocument.quality == "THIRD_PARTY", SourceDocument.is_demo.is_(False))
            ).scalar_one()
            if third_party:
                warnings.append(
                    "当前数据来自第三方整理包（非省考试院官方发布），已标记 quality=THIRD_PARTY。"
                    "可作参考，但真实填报前请以官方《填报志愿指南》核对。"
                )
        if model is None:
            warnings.append("没有激活的模型版本，推荐功能不可用")

        return DatasetMetaDTO(
            cycle=AdmissionCycleDTO.model_validate(cycle) if cycle else None,
            snapshot=SnapshotDTO.model_validate(snapshot) if snapshot else None,
            model_version=model.code if model else None,
            model_label=model.label if model else None,
            data_year_label=f"{cycle.exam_year} 年" if cycle else None,
            is_demo=bool(snapshot and snapshot.is_demo),
            plan_count=plan_count,
            university_count=university_count,
            warnings=warnings,
        )

    # ---------------- internals ----------------

    def _resolve_snapshot_id(self, snapshot_id: int | None) -> int:
        if snapshot_id is not None:
            snapshot = self.snapshots.get(snapshot_id)
            if snapshot is None:
                raise NotFoundError(f"数据快照 {snapshot_id} 不存在")
            return snapshot.id
        latest = self.snapshots.latest_published()
        if latest is None:
            raise NotFoundError("没有已发布的数据快照（dataset_snapshots.status='PUBLISHED'）")
        return latest.id

    def _to_plan_dto(self, row: dict, subjects: dict[int, list[list[str]]]) -> PlanDTO:
        rule_set_id = row["subject_rule_set_id"]
        rule = None
        if rule_set_id is not None:
            rule = SubjectRuleDTO(
                rule_set_id=rule_set_id,
                mode=row["subject_rule_mode"],
                groups=subjects.get(rule_set_id, []),
                raw_text=row["subject_rule_raw_text"],
                is_authoritative=row["subject_rule_is_authoritative"],
            )
        return PlanDTO(
            plan_id=row["plan_id"],
            cycle_id=row["cycle_id"],
            plan_name=row["plan_name"],
            program_label=_program_label(row["university_name"], row["plan_name"]),
            university_id=row["university_id"],
            university_name=row["university_name"],
            province_code=row["province_code"],
            province_name=row["province_name"],
            city_code=row["city_code"],
            city_name=row["city_name"],
            is_985=row["is_985"],
            is_211=row["is_211"],
            is_double_first_class=row["is_double_first_class"],
            series_id=row["series_id"],
            series_name=row["series_name"],
            cooperative=row["cooperative"],
            first_year=row["first_year"],
            last_year=row["last_year"],
            plan_count=row["plan_count"],
            tuition_fee=row["tuition_fee"],
            duration_years=row["duration_years"],
            campus_name=row["campus_name"],
            special_requirements=row["special_requirements"],
            series_match_quality=row["series_match_quality"],
            is_demo=row["is_demo"],
            subject_rule=rule,
        )
