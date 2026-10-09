"""Load the DEMO dataset into PostgreSQL.

Idempotent by design: running it twice leaves one copy of the data. Every write is
keyed by a natural key (snapshot code, series key, plan scope, …) so re-running
after a schema change or a partial failure converges instead of duplicating.

    python -m app.ingestion.seed_demo            # create or update
    python -m app.ingestion.seed_demo --reset    # delete DEMO data first
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.admission.models import (
    AdmissionCycle,
    AdmissionOutcome,
    AdmissionPlan,
    AdmissionRequirement,
    DatasetSnapshot,
    ExamYearStats,
    ProgramSeries,
    ScoreRankSegment,
    SourceDocument,
    SubjectRuleGroup,
    SubjectRuleItem,
    SubjectRuleSet,
)
from app.db.session import session_scope
from app.ingestion.demo_data import (
    DEMO_APPLICATION_LIMIT,
    DEMO_EXAM_YEAR,
    DEMO_EXAM_YEAR_STATS,
    DEMO_HISTORY_YEARS,
    DEMO_MAJORS,
    DEMO_PLANS,
    DEMO_POLICY_VERSION,
    DEMO_PROVINCE_CODE,
    DEMO_SCORE_LINES,
    DEMO_SCORE_SEGMENTS,
    DEMO_SNAPSHOT_CODE,
    DEMO_SOURCE_DOCUMENTS,
    DEMO_UNIVERSITIES,
    SUBJECT_RULES,
)
from app.reference.models import Major, University

# Deterministic checksums: the demo has no real files, but the schema requires a
# traceable digest. Derived from the title so re-seeding produces the same value.
DEMO_CHECKSUM_SALT = "shandong-gaokao-demo-v1"


@dataclass
class SeedReport:
    universities: int = 0
    majors: int = 0
    subject_rule_sets: int = 0
    series: int = 0
    plans: int = 0
    outcomes: int = 0
    segments: int = 0
    requirements: int = 0

    def as_lines(self) -> list[str]:
        return [
            f"院校 {self.universities}",
            f"专业 {self.majors}",
            f"选科要求 {self.subject_rule_sets}",
            f"专业序列 {self.series}",
            f"招生计划 {self.plans}",
            f"历史录取 {self.outcomes}",
            f"一分一段 {self.segments}",
            f"特殊要求 {self.requirements}",
        ]


def _checksum(title: str) -> str:
    return hashlib.sha256(f"{DEMO_CHECKSUM_SALT}:{title}".encode()).hexdigest()


def reset_demo(session: Session, *, force: bool = False) -> None:
    """Delete DEMO admission data, in FK order. Real data is never touched.

    Candidate profiles reference the demo cycle, and recommendation sessions
    reference the demo snapshot. Rather than deleting a candidate's work silently,
    `force=True` is required; without it the caller gets a clear explanation of
    exactly what would be removed.
    """
    from app.candidate.models import Profile
    from app.recommendation.models import Session as RunSession

    snapshot_ids = list(
        session.execute(
            select(DatasetSnapshot.id).where(DatasetSnapshot.code == DEMO_SNAPSHOT_CODE)
        ).scalars()
    )
    cycle_ids = list(
        session.execute(
            select(AdmissionCycle.id).where(AdmissionCycle.policy_version == DEMO_POLICY_VERSION)
        ).scalars()
    )

    blocked: list[str] = []
    dependent_sessions: list[int] = []
    if snapshot_ids:
        dependent_sessions = list(
            session.execute(
                select(RunSession.id).where(RunSession.snapshot_id.in_(snapshot_ids))
            ).scalars()
        )
        if dependent_sessions:
            blocked.append(f"{len(dependent_sessions)} 个推荐会话（含方案与体检记录）")

    dependent_profiles: list[int] = []
    if cycle_ids:
        dependent_profiles = list(
            session.execute(select(Profile.id).where(Profile.cycle_id.in_(cycle_ids))).scalars()
        )
        if dependent_profiles:
            blocked.append(f"{len(dependent_profiles)} 份考生档案（含偏好画像）")

    if blocked and not force:
        raise RuntimeError(
            "重置 DEMO 数据会连带删除：" + "、".join(blocked) + "。"
            "如确认要重建演示数据，请加 --force。"
        )

    if dependent_sessions:
        _delete_recommendation_data(session, dependent_sessions)
    if dependent_profiles:
        _delete_profile_data(session, dependent_profiles)

    if snapshot_ids:
        plan_ids = list(
            session.execute(
                select(AdmissionPlan.id).where(AdmissionPlan.snapshot_id.in_(snapshot_ids))
            ).scalars()
        )
        if plan_ids:
            session.execute(
                delete(AdmissionRequirement).where(AdmissionRequirement.plan_id.in_(plan_ids))
            )
        session.execute(
            delete(AdmissionOutcome).where(AdmissionOutcome.snapshot_id.in_(snapshot_ids))
        )
        session.execute(delete(AdmissionPlan).where(AdmissionPlan.snapshot_id.in_(snapshot_ids)))
        session.flush()
        session.execute(delete(DatasetSnapshot).where(DatasetSnapshot.id.in_(snapshot_ids)))

    # Series created by the demo are only referenced by demo plans, now gone.
    demo_university_ids = list(
        session.execute(select(University.id).where(University.ministry_code.like("DEMO%"))).scalars()
    )
    if demo_university_ids:
        leftover_series = list(
            session.execute(
                select(ProgramSeries.id).where(ProgramSeries.university_id.in_(demo_university_ids))
            ).scalars()
        )
        if leftover_series:
            session.execute(delete(ProgramSeries).where(ProgramSeries.id.in_(leftover_series)))
        session.flush()

        # Now drop the DEMO universities themselves. Only rows with the DEMO code
        # prefix and no remaining plans are removed, so real reference data and any
        # profile that still points at a live plan are never touched. Without this a
        # stale university from an earlier dataset shape lingers forever, because a
        # university with no series is invisible to the query above.
        still_referenced = select(ProgramSeries.id).where(
            ProgramSeries.university_id == University.id
        )
        session.execute(
            delete(University).where(
                University.ministry_code.like("DEMO%"),
                ~still_referenced.exists(),
            )
        )

    # The cycle is kept: it carries the policy numbers and is idempotent to re-seed.
    # Deleting it would break any profile that legitimately points at it.
    #
    # Source documents are kept as well. They are few, `seed` is idempotent on their
    # checksum, and deleting them while their plans survive would leave those plans
    # pointing at a removed source — which silently loses the provenance link and
    # makes the next seed skip the requirement rows it thinks already exist.
    session.flush()


def _delete_profile_data(session: Session, profile_ids: list[int]) -> None:
    from app.candidate.models import (
        Attribute,
        LocationPreference,
        MajorPreference,
        OtherPreference,
        PreferenceProfile,
        Profile,
        SubjectScore,
        UniversityPreference,
    )

    preference_ids = list(
        session.execute(
            select(PreferenceProfile.id).where(PreferenceProfile.profile_id.in_(profile_ids))
        ).scalars()
    )
    if preference_ids:
        for table in (
            MajorPreference,
            LocationPreference,
            UniversityPreference,
            OtherPreference,
        ):
            session.execute(delete(table).where(table.preference_profile_id.in_(preference_ids)))
        session.execute(
            delete(PreferenceProfile).where(PreferenceProfile.id.in_(preference_ids))
        )
    session.execute(delete(SubjectScore).where(SubjectScore.profile_id.in_(profile_ids)))
    session.execute(delete(Attribute).where(Attribute.profile_id.in_(profile_ids)))
    session.execute(delete(Profile).where(Profile.id.in_(profile_ids)))
    session.flush()


def _delete_recommendation_data(session: Session, session_ids: list[int]) -> None:
    """Drop recommendation sessions and everything derived from them."""
    from app.recommendation.models import (
        HealthCheck,
        HealthCheckIssue,
        RecommendationItem,
        Scheme,
        SchemeItem,
        SchemeVersion,
    )
    from app.recommendation.models import (
        Session as RunSession,
    )

    scheme_ids = list(
        session.execute(select(Scheme.id).where(Scheme.session_id.in_(session_ids))).scalars()
    )
    if scheme_ids:
        check_ids = list(
            session.execute(
                select(HealthCheck.id).where(HealthCheck.scheme_id.in_(scheme_ids))
            ).scalars()
        )
        if check_ids:
            session.execute(
                delete(HealthCheckIssue).where(HealthCheckIssue.health_check_id.in_(check_ids))
            )
            session.execute(delete(HealthCheck).where(HealthCheck.id.in_(check_ids)))
        version_ids = list(
            session.execute(
                select(SchemeVersion.id).where(SchemeVersion.scheme_id.in_(scheme_ids))
            ).scalars()
        )
        if version_ids:
            session.execute(delete(SchemeItem).where(SchemeItem.scheme_version_id.in_(version_ids)))
            session.execute(delete(SchemeVersion).where(SchemeVersion.id.in_(version_ids)))
        session.execute(delete(Scheme).where(Scheme.id.in_(scheme_ids)))

    session.execute(delete(RecommendationItem).where(RecommendationItem.session_id.in_(session_ids)))
    session.execute(delete(RunSession).where(RunSession.id.in_(session_ids)))
    session.flush()


def seed(session: Session) -> SeedReport:
    report = SeedReport()

    # ---- 1. source documents (provenance first) ----
    sources: dict[str, SourceDocument] = {}
    for spec in DEMO_SOURCE_DOCUMENTS:
        checksum = _checksum(spec["title"])
        row = session.execute(
            select(SourceDocument).where(SourceDocument.checksum_sha256 == checksum)
        ).scalar_one_or_none()
        if row is None:
            row = SourceDocument(
                title=spec["title"],
                publisher=spec["publisher"],
                quality=spec["quality"],
                archive_path=spec["archive_path"],
                checksum_sha256=checksum,
                is_demo=True,
                notes=spec["notes"],
                published_on=date(DEMO_EXAM_YEAR, 6, 20),
                retrieved_at=datetime(DEMO_EXAM_YEAR, 6, 20, 12, 0),
            )
            session.add(row)
            session.flush()
        sources[spec["archive_path"]] = row

    plan_source = sources["data/raw/demo/demo_plans_2026.csv"]
    outcome_source = sources["data/raw/demo/demo_outcomes.csv"]
    segment_source = sources["data/raw/demo/demo_score_rank.csv"]

    # ---- 2. snapshot ----
    snapshot = session.execute(
        select(DatasetSnapshot).where(DatasetSnapshot.code == DEMO_SNAPSHOT_CODE)
    ).scalar_one_or_none()
    if snapshot is None:
        snapshot = DatasetSnapshot(
            code=DEMO_SNAPSHOT_CODE,
            label=f"DEMO 数据集 {DEMO_EXAM_YEAR}（虚构，非官方）",
            status="PUBLISHED",
            policy_version=DEMO_POLICY_VERSION,
            is_demo=True,
            published_at=datetime(DEMO_EXAM_YEAR, 6, 25, 9, 0),
            source_document_id=plan_source.id,
            notes=(
                "本快照全部为 DEMO 虚构数据，仅用于功能开发与回归测试。"
                "禁止用于真实填报决策。接入官方数据后请新建快照并置为 PUBLISHED。"
            ),
        )
        session.add(snapshot)
        session.flush()
    # A snapshot used by recommendations must be PUBLISHED; re-seeding repairs drift.
    snapshot.status = "PUBLISHED"

    # ---- 3. cycle ----
    cycle = session.execute(
        select(AdmissionCycle).where(
            AdmissionCycle.exam_year == DEMO_EXAM_YEAR,
            AdmissionCycle.province_code == DEMO_PROVINCE_CODE,
            AdmissionCycle.exam_category == "SUMMER_REGULAR",
            AdmissionCycle.batch_code == "REGULAR_BATCH_1",
            AdmissionCycle.round_no == 1,
            AdmissionCycle.program_level == "UNDERGRAD",
        )
    ).scalar_one_or_none()
    if cycle is None:
        cycle = AdmissionCycle(
            province_code=DEMO_PROVINCE_CODE,
            exam_year=DEMO_EXAM_YEAR,
            exam_category="SUMMER_REGULAR",
            batch_code="REGULAR_BATCH_1",
            round_no=1,
            program_level="UNDERGRAD",
            application_limit=DEMO_APPLICATION_LIMIT,
            filling_rule="分数优先、遵循志愿（平行志愿）",
            policy_version=DEMO_POLICY_VERSION,
            published_on=date(DEMO_EXAM_YEAR, 6, 25),
            is_active=True,
            notes="DEMO 招生周期。志愿数量上限来自本行，前后端均不得写死。",
        )
        session.add(cycle)
        session.flush()
    cycle.application_limit = DEMO_APPLICATION_LIMIT

    # ---- 4. exam year stats + 一分一段表 ----
    for year, ranked in DEMO_EXAM_YEAR_STATS.items():
        stats_row = session.execute(
            select(ExamYearStats).where(
                ExamYearStats.exam_year == year,
                ExamYearStats.exam_category == "SUMMER_REGULAR",
                ExamYearStats.program_level == "UNDERGRAD",
            )
        ).scalar_one_or_none()
        special, regular = DEMO_SCORE_LINES.get(year, (None, None))
        if stats_row is None:
            session.add(
                ExamYearStats(
                    province_code=DEMO_PROVINCE_CODE,
                    exam_year=year,
                    exam_category="SUMMER_REGULAR",
                    program_level="UNDERGRAD",
                    special_line=special,
                    regular_line=regular,
                    ranked_candidates=ranked,
                    source_document_id=segment_source.id,
                )
            )
        else:
            stats_row.ranked_candidates = ranked
            stats_row.special_line = special
            stats_row.regular_line = regular

    cumulative = 0
    for score, same_score_count in DEMO_SCORE_SEGMENTS:
        cumulative += same_score_count
        for year in sorted(DEMO_EXAM_YEAR_STATS):
            # Older years had fewer candidates: scale the sample so the demo stays
            # internally consistent when comparing across years.
            scale = DEMO_EXAM_YEAR_STATS[year] / DEMO_EXAM_YEAR_STATS[DEMO_EXAM_YEAR]
            year_cumulative = max(1, int(cumulative * scale))
            segment_row = session.execute(
                select(ScoreRankSegment).where(
                    ScoreRankSegment.exam_year == year,
                    ScoreRankSegment.exam_category == "SUMMER_REGULAR",
                    ScoreRankSegment.score == score,
                )
            ).scalar_one_or_none()
            if segment_row is None:
                session.add(
                    ScoreRankSegment(
                        exam_year=year,
                        exam_category="SUMMER_REGULAR",
                        program_level="UNDERGRAD",
                        score=score,
                        same_score_count=max(1, int(same_score_count * scale)),
                        cumulative_rank=year_cumulative,
                        source_document_id=segment_source.id,
                    )
                )
                report.segments += 1
            else:
                segment_row.cumulative_rank = year_cumulative
    session.flush()

    # ---- 5. subject rule sets ----
    rule_sets: dict[str, SubjectRuleSet] = {}
    for code, rule_spec in SUBJECT_RULES.items():
        rule_row = session.execute(
            select(SubjectRuleSet).where(SubjectRuleSet.code == code)
        ).scalar_one_or_none()
        if rule_row is None:
            rule_row = SubjectRuleSet(
                code=code,
                mode=rule_spec["mode"],
                raw_text=rule_spec["raw_text"],
                source_document_id=plan_source.id,
                is_authoritative=True,
            )
            session.add(rule_row)
            session.flush()
            for group_index, group in enumerate(rule_spec["groups"]):
                group_row = SubjectRuleGroup(
                    rule_set_id=rule_row.id, group_no=group_index, mode=rule_spec["mode"]
                )
                session.add(group_row)
                session.flush()
                for subject_code in group:
                    session.add(SubjectRuleItem(group_id=group_row.id, subject_code=subject_code))
            report.subject_rule_sets += 1
        rule_sets[code] = rule_row
    session.flush()

    # ---- 6. universities + majors ----
    universities: dict[str, University] = {}
    for university_spec in DEMO_UNIVERSITIES:
        university_row = session.execute(
            select(University).where(University.ministry_code == university_spec.ministry_code)
        ).scalar_one_or_none()
        if university_row is None:
            university_row = University(
                ministry_code=university_spec.ministry_code,
                name=university_spec.name,
                short_name=university_spec.short_name,
                province_code=university_spec.province_code,
                province_name=university_spec.province_name,
                city_code=university_spec.city_code,
                city_name=university_spec.city_name,
                is_985=university_spec.is_985,
                is_211=university_spec.is_211,
                is_double_first_class=university_spec.is_double_first_class,
                is_public=university_spec.is_public,
                remark="DEMO 演示院校（非真实招生数据）",
            )
            session.add(university_row)
            session.flush()
            report.universities += 1
        universities[university_spec.ministry_code] = university_row

    majors: dict[str, Major] = {}
    for major_spec in DEMO_MAJORS:
        major_row = session.execute(
            select(Major).where(Major.code == major_spec.code)
        ).scalar_one_or_none()
        if major_row is None:
            major_row = Major(
                code=major_spec.code,
                name=major_spec.name,
                level=major_spec.level,
                discipline_code=major_spec.discipline_code,
                discipline_name=major_spec.discipline_name,
                category_code=major_spec.category_code,
                category_name=major_spec.category_name,
                degree_awarded=major_spec.degree_awarded,
            )
            session.add(major_row)
            session.flush()
            report.majors += 1
        majors[major_spec.code] = major_row
    session.flush()

    # ---- 7. series + plans + outcomes + requirements ----
    for plan_spec in DEMO_PLANS:
        university = universities[plan_spec.university_code]
        major = majors.get(plan_spec.major_code) if plan_spec.major_code else None
        series_key = (
            f"DEMO|{plan_spec.university_code}|{plan_spec.major_code or 'NA'}|"
            f"{plan_spec.plan_name}|{plan_spec.campus_name or 'MAIN'}|{plan_spec.cooperative}"
        )
        series_row = session.execute(
            select(ProgramSeries).where(ProgramSeries.series_key == series_key)
        ).scalar_one_or_none()
        if series_row is None:
            series_row = ProgramSeries(
                series_key=series_key,
                canonical_name=plan_spec.plan_name,
                university_id=university.id,
                major_id=major.id if major else None,
                cooperative=plan_spec.cooperative,
                match_quality="EXACT",
                first_year=DEMO_HISTORY_YEARS[0],
                last_year=DEMO_HISTORY_YEARS[-1],
                is_active=True,
            )
            session.add(series_row)
            session.flush()
            report.series += 1

        rule_set = rule_sets[plan_spec.subject_rule]
        plan_row = session.execute(
            select(AdmissionPlan).where(
                AdmissionPlan.cycle_id == cycle.id,
                AdmissionPlan.series_id == series_row.id,
                AdmissionPlan.subject_rule_set_id == rule_set.id,
            )
        ).scalar_one_or_none()
        if plan_row is None:
            plan_row = AdmissionPlan(
                cycle_id=cycle.id,
                series_id=series_row.id,
                subject_rule_set_id=rule_set.id,
                snapshot_id=snapshot.id,
                source_document_id=plan_source.id,
                plan_name=plan_spec.plan_name,
                plan_count=plan_spec.plan_count,
                tuition_fee=plan_spec.tuition_fee,
                duration_years=plan_spec.duration_years,
                special_requirements=plan_spec.special_requirements,
                campus_name=plan_spec.campus_name,
                study_mode="普通全日制",
                series_match_quality="EXACT",
                is_demo=True,
            )
            session.add(plan_row)
            session.flush()
            report.plans += 1
        else:
            plan_row.plan_count = plan_spec.plan_count
            plan_row.tuition_fee = plan_spec.tuition_fee

        for offset, year in enumerate(DEMO_HISTORY_YEARS):
            outcome_row = session.execute(
                select(AdmissionOutcome).where(
                    AdmissionOutcome.series_id == series_row.id,
                    AdmissionOutcome.exam_year == year,
                )
            ).scalar_one_or_none()
            min_rank = plan_spec.history_min_ranks[offset]
            history_plan_count = plan_spec.history_plans[offset]
            if outcome_row is None:
                session.add(
                    AdmissionOutcome(
                        series_id=series_row.id,
                        plan_id=plan_row.id,
                        snapshot_id=snapshot.id,
                        source_document_id=outcome_source.id,
                        exam_year=year,
                        batch_code="REGULAR_BATCH_1",
                        round_no=1,
                        plan_count=history_plan_count,
                        admitted_count=history_plan_count,
                        min_rank=min_rank,
                        is_demo=True,
                    )
                )
                report.outcomes += 1
            else:
                outcome_row.min_rank = min_rank
                outcome_row.plan_count = history_plan_count

        for requirement_spec in plan_spec.requirement_specs:
            requirement_type = requirement_spec["requirement_type"]
            operator = requirement_spec.get("operator", "EQ")
            requirement_row = session.execute(
                select(AdmissionRequirement).where(
                    AdmissionRequirement.plan_id == plan_row.id,
                    AdmissionRequirement.requirement_type == requirement_type,
                    AdmissionRequirement.operator == operator,
                )
            ).scalar_one_or_none()
            if requirement_row is None:
                session.add(
                    AdmissionRequirement(
                        plan_id=plan_row.id,
                        requirement_type=requirement_type,
                        operator=operator,
                        operand=requirement_spec.get("operand"),
                        severity=requirement_spec.get("severity", "BLOCK"),
                        is_deterministic=requirement_spec.get("is_deterministic", True),
                        raw_text=requirement_spec.get("raw_text"),
                        source_document_id=plan_source.id,
                    )
                )
                report.requirements += 1

    session.flush()
    return report


def verify_postgres(session: Session) -> None:
    session.execute(text("SELECT 1"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the DEMO dataset (never official data).")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="先删除已有 DEMO 数据再重建（不影响真实数据）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="配合 --reset：连同基于 DEMO 快照生成的推荐会话/方案/体检一起删除",
    )
    args = parser.parse_args(argv)

    with session_scope() as session:
        verify_postgres(session)
        if args.reset:
            reset_demo(session, force=args.force)
        report = seed(session)

    print("DEMO 数据已就绪（虚构数据，禁止用于真实填报）：")
    for line in report.as_lines():
        print(f"  - {line}")
    print(f"  快照代码 {DEMO_SNAPSHOT_CODE} · 政策版本 {DEMO_POLICY_VERSION}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
