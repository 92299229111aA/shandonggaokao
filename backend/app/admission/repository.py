"""Data access for the admission schema."""

from __future__ import annotations

from sqlalchemy import Select, desc, or_, select

from app.admission.models import (
    AdmissionCycle,
    AdmissionOutcome,
    AdmissionPlan,
    AdmissionRequirement,
    DatasetSnapshot,
    ExamYearStats,
    ProgramSeries,
    ScoreRankSegment,
    SubjectRuleGroup,
    SubjectRuleItem,
    SubjectRuleSet,
)
from app.common.repository import Repository
from app.reference.models import Major, University


class CycleRepository(Repository[AdmissionCycle]):
    model = AdmissionCycle

    def active_cycles(self) -> list[AdmissionCycle]:
        return list(
            self.session.execute(
                select(AdmissionCycle)
                .where(AdmissionCycle.is_active.is_(True))
                .order_by(desc(AdmissionCycle.exam_year))
            ).scalars()
        )

    def by_year(self, exam_year: int) -> AdmissionCycle | None:
        return self.session.execute(
            select(AdmissionCycle)
            .where(AdmissionCycle.exam_year == exam_year, AdmissionCycle.is_active.is_(True))
            .order_by(desc(AdmissionCycle.round_no))
            .limit(1)
        ).scalar_one_or_none()


class SnapshotRepository(Repository[DatasetSnapshot]):
    model = DatasetSnapshot

    def published(self, *, is_demo: bool | None = None) -> list[DatasetSnapshot]:
        stmt = select(DatasetSnapshot).where(DatasetSnapshot.status == "PUBLISHED")
        if is_demo is not None:
            stmt = stmt.where(DatasetSnapshot.is_demo.is_(is_demo))
        return list(
            self.session.execute(stmt.order_by(desc(DatasetSnapshot.published_at))).scalars()
        )

    def latest_published(self, *, is_demo: bool | None = None) -> DatasetSnapshot | None:
        rows = self.published(is_demo=is_demo)
        return rows[0] if rows else None


class ScoreRankRepository(Repository[ScoreRankSegment]):
    model = ScoreRankSegment

    def segment_for(self, *, exam_year: int, score: int) -> ScoreRankSegment | None:
        return self.session.execute(
            select(ScoreRankSegment)
            .where(
                ScoreRankSegment.exam_year == exam_year,
                ScoreRankSegment.exam_category == "SUMMER_REGULAR",
                ScoreRankSegment.score == score,
            )
            .limit(1)
        ).scalar_one_or_none()

    def nearest_segment_for(self, *, exam_year: int, score: int) -> ScoreRankSegment | None:
        """Closest published row at or above the queried score (cumulative rank <=)."""
        return self.session.execute(
            select(ScoreRankSegment)
            .where(
                ScoreRankSegment.exam_year == exam_year,
                ScoreRankSegment.exam_category == "SUMMER_REGULAR",
                ScoreRankSegment.score >= score,
            )
            .order_by(ScoreRankSegment.score)
            .limit(1)
        ).scalar_one_or_none()

    def year_stats(self, *, exam_year: int) -> ExamYearStats | None:
        return self.session.execute(
            select(ExamYearStats)
            .where(
                ExamYearStats.exam_year == exam_year,
                ExamYearStats.exam_category == "SUMMER_REGULAR",
                ExamYearStats.program_level == "UNDERGRAD",
            )
            .limit(1)
        ).scalar_one_or_none()


class PlanRepository(Repository[AdmissionPlan]):
    model = AdmissionPlan

    def base_query(self) -> Select:
        """Plan rows joined with their series, university and 选科要求."""
        return (
            select(
                AdmissionPlan.id.label("plan_id"),
                AdmissionPlan.cycle_id,
                AdmissionPlan.plan_name,
                AdmissionPlan.plan_count,
                AdmissionPlan.tuition_fee,
                AdmissionPlan.duration_years,
                AdmissionPlan.special_requirements,
                AdmissionPlan.major_sequence,
                AdmissionPlan.campus_name,
                AdmissionPlan.series_match_quality,
                AdmissionPlan.is_demo,
                ProgramSeries.id.label("series_id"),
                ProgramSeries.canonical_name.label("series_name"),
                ProgramSeries.cooperative,
                ProgramSeries.first_year,
                ProgramSeries.last_year,
                University.id.label("university_id"),
                University.name.label("university_name"),
                University.province_code,
                University.province_name,
                University.city_code,
                University.city_name,
                University.is_985,
                University.is_211,
                University.is_double_first_class,
                Major.id.label("major_id"),
                Major.code.label("major_code"),
                Major.name.label("major_name"),
                Major.discipline_code,
                Major.discipline_name,
                SubjectRuleSet.id.label("subject_rule_set_id"),
                SubjectRuleSet.mode.label("subject_rule_mode"),
                SubjectRuleSet.raw_text.label("subject_rule_raw_text"),
                SubjectRuleSet.is_authoritative.label("subject_rule_is_authoritative"),
            )
            .join(ProgramSeries, ProgramSeries.id == AdmissionPlan.series_id)
            .join(University, University.id == ProgramSeries.university_id)
            .outerjoin(Major, Major.id == ProgramSeries.major_id)
            .outerjoin(SubjectRuleSet, SubjectRuleSet.id == AdmissionPlan.subject_rule_set_id)
        )

    def list_candidates(
        self,
        *,
        cycle_id: int,
        snapshot_id: int,
        limit: int,
        offset: int,
        search: str | None = None,
        university_id: int | None = None,
        province_code: str | None = None,
        city_code: str | None = None,
        max_tuition: int | None = None,
    ) -> list[dict]:
        stmt = self.base_query().where(
            AdmissionPlan.cycle_id == cycle_id, AdmissionPlan.snapshot_id == snapshot_id
        )
        if search:
            pattern = f"%{search}%"
            stmt = stmt.where(
                or_(AdmissionPlan.plan_name.ilike(pattern), University.name.ilike(pattern))
            )
        if university_id is not None:
            stmt = stmt.where(University.id == university_id)
        if province_code:
            stmt = stmt.where(University.province_code == province_code)
        if city_code:
            stmt = stmt.where(University.city_code == city_code)
        if max_tuition is not None:
            stmt = stmt.where(AdmissionPlan.tuition_fee <= max_tuition)
        rows = self.session.execute(
            stmt.order_by(University.name, AdmissionPlan.plan_name).limit(limit).offset(offset)
        ).mappings()
        return [dict(row) for row in rows]

    def count_candidates(
        self,
        *,
        cycle_id: int,
        snapshot_id: int,
        search: str | None = None,
        university_id: int | None = None,
        province_code: str | None = None,
        city_code: str | None = None,
        max_tuition: int | None = None,
    ) -> int:
        from sqlalchemy import func

        stmt = (
            select(func.count())
            .select_from(AdmissionPlan)
            .join(ProgramSeries, ProgramSeries.id == AdmissionPlan.series_id)
            .join(University, University.id == ProgramSeries.university_id)
            .where(AdmissionPlan.cycle_id == cycle_id, AdmissionPlan.snapshot_id == snapshot_id)
        )
        if search:
            pattern = f"%{search}%"
            stmt = stmt.where(
                or_(AdmissionPlan.plan_name.ilike(pattern), University.name.ilike(pattern))
            )
        if university_id is not None:
            stmt = stmt.where(University.id == university_id)
        if province_code:
            stmt = stmt.where(University.province_code == province_code)
        if city_code:
            stmt = stmt.where(University.city_code == city_code)
        if max_tuition is not None:
            stmt = stmt.where(AdmissionPlan.tuition_fee <= max_tuition)
        return int(self.session.execute(stmt).scalar_one())

    def get_candidate(self, plan_id: int) -> dict | None:
        row = self.session.execute(self.base_query().where(AdmissionPlan.id == plan_id)).mappings().first()
        return dict(row) if row else None

    def subject_rule_groups(self, rule_set_ids: list[int]) -> dict[int, list[list[str]]]:
        """rule_set_id -> [[subject, ...], ...] where the outer list is OR-ed."""
        if not rule_set_ids:
            return {}
        rows = self.session.execute(
            select(SubjectRuleGroup.rule_set_id, SubjectRuleItem.subject_code)
            .join(SubjectRuleItem, SubjectRuleItem.group_id == SubjectRuleGroup.id)
            .where(SubjectRuleGroup.rule_set_id.in_(rule_set_ids))
            .order_by(SubjectRuleGroup.rule_set_id, SubjectRuleGroup.group_no, SubjectRuleItem.id)
        ).all()
        grouped: dict[int, list[list[str]]] = {}
        for rule_set_id, subject_code in rows:
            grouped.setdefault(rule_set_id, [[]])
            grouped[rule_set_id][-1].append(subject_code)
        return grouped

    def requirements_for(self, plan_ids: list[int]) -> dict[int, list[AdmissionRequirement]]:
        if not plan_ids:
            return {}
        rows = self.session.execute(
            select(AdmissionRequirement)
            .where(AdmissionRequirement.plan_id.in_(plan_ids))
            .order_by(AdmissionRequirement.plan_id, AdmissionRequirement.id)
        ).scalars()
        out: dict[int, list[AdmissionRequirement]] = {}
        for row in rows:
            out.setdefault(row.plan_id, []).append(row)
        return out

    def outcomes_for_series(self, series_ids: list[int]) -> dict[int, list[AdmissionOutcome]]:
        if not series_ids:
            return {}
        rows = self.session.execute(
            select(AdmissionOutcome)
            .where(AdmissionOutcome.series_id.in_(series_ids))
            .order_by(AdmissionOutcome.series_id, AdmissionOutcome.exam_year)
        ).scalars()
        out: dict[int, list[AdmissionOutcome]] = {}
        for row in rows:
            out.setdefault(row.series_id, []).append(row)
        return out
