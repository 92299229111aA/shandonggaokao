"""Phase 9 acceptance: the DEMO dataset seeds correctly and is idempotent.

Also guards the demo/real separation: every seeded row must be marked as DEMO so
it can never be mistaken for official admission data.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

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
    SubjectRuleSet,
)
from app.candidate.models import Profile
from app.common.enums import RiskBucket
from app.ingestion.demo_data import (
    DEMO_APPLICATION_LIMIT,
    DEMO_EXAM_YEAR,
    DEMO_HISTORY_YEARS,
    DEMO_PLANS,
    DEMO_SNAPSHOT_CODE,
)
from app.ingestion.seed_demo import reset_demo, seed
from app.recommendation.pipeline import RecommendationPipeline
from app.recommendation.service import RecommendationService
from app.reference.models import Major, University

pytestmark = pytest.mark.integration


@pytest.fixture()
def seeded(db_session):
    report = seed(db_session)
    db_session.flush()
    return report


def test_seed_creates_the_expected_dataset(db_session, seeded) -> None:
    assert seeded.universities == len({plan.university_code for plan in DEMO_PLANS})
    assert seeded.plans == len(DEMO_PLANS)
    assert seeded.outcomes == len(DEMO_PLANS) * len(DEMO_HISTORY_YEARS)
    assert seeded.series == len(DEMO_PLANS)
    assert seeded.segments > 0
    assert seeded.subject_rule_sets > 0


def test_seed_is_idempotent(db_session, seeded) -> None:
    second = seed(db_session)
    db_session.flush()
    # nothing new was created the second time
    assert second.universities == 0
    assert second.majors == 0
    assert second.plans == 0
    assert second.outcomes == 0
    assert second.segments == 0
    assert second.series == 0
    assert second.subject_rule_sets == 0
    assert second.requirements == 0


def test_seed_row_counts_match_after_double_seed(db_session, seeded) -> None:
    seed(db_session)
    db_session.flush()
    assert db_session.execute(select(func.count()).select_from(AdmissionPlan)).scalar_one() == len(
        DEMO_PLANS
    )
    assert db_session.execute(
        select(func.count()).select_from(ProgramSeries)
    ).scalar_one() == len(DEMO_PLANS)


def test_every_seeded_row_is_marked_as_demo(db_session, seeded) -> None:
    """DEMO 数据必须可被明确识别，绝不能冒充官方数据。"""
    snapshot = db_session.execute(
        select(DatasetSnapshot).where(DatasetSnapshot.code == DEMO_SNAPSHOT_CODE)
    ).scalar_one()
    assert snapshot.is_demo is True
    assert snapshot.code.startswith("DEMO-")
    assert snapshot.status == "PUBLISHED"
    assert "DEMO" in snapshot.label or "演示" in snapshot.label

    assert (
        db_session.execute(
            select(func.count()).select_from(AdmissionPlan).where(AdmissionPlan.is_demo.is_(False))
        ).scalar_one()
        == 0
    )
    assert (
        db_session.execute(
            select(func.count())
            .select_from(AdmissionOutcome)
            .where(AdmissionOutcome.is_demo.is_(False))
        ).scalar_one()
        == 0
    )

    qualities = set(
        db_session.execute(select(SourceDocument.quality).distinct()).scalars()
    )
    assert qualities == {"DEMO"}
    assert (
        db_session.execute(
            select(func.count()).select_from(SourceDocument).where(SourceDocument.is_demo.is_(False))
        ).scalar_one()
        == 0
    )


def test_seed_cycle_carries_the_policy_limit(db_session, seeded) -> None:
    cycle = db_session.execute(
        select(AdmissionCycle).where(AdmissionCycle.exam_year == DEMO_EXAM_YEAR)
    ).scalar_one()
    assert cycle.application_limit == DEMO_APPLICATION_LIMIT
    assert cycle.exam_category == "SUMMER_REGULAR"
    assert cycle.batch_code == "REGULAR_BATCH_1"
    assert cycle.policy_version


def test_seed_provides_every_year_needed_for_acs(db_session, seeded) -> None:
    """ACS 需要每一年的考生人数与一分一段表，否则流水线拒绝评分。"""
    years = set(
        db_session.execute(
            select(ExamYearStats.exam_year).where(ExamYearStats.exam_category == "SUMMER_REGULAR")
        ).scalars()
    )
    assert set(DEMO_HISTORY_YEARS).issubset(years)
    assert DEMO_EXAM_YEAR in years

    segment_years = set(
        db_session.execute(select(ScoreRankSegment.exam_year).distinct()).scalars()
    )
    assert set(DEMO_HISTORY_YEARS).issubset(segment_years)

    for year in DEMO_HISTORY_YEARS:
        assert (
            db_session.execute(
                select(func.count())
                .select_from(AdmissionOutcome)
                .where(AdmissionOutcome.exam_year == year)
            ).scalar_one()
            > 0
        )


def test_seed_creates_every_subject_rule_shape(db_session, seeded) -> None:
    rule_sets = list(db_session.execute(select(SubjectRuleSet)).scalars())
    assert len(rule_sets) >= 5
    codes = {rule_set.code for rule_set in rule_sets}
    assert {"UNRESTRICTED", "PHY_ONLY", "PHY_AND_CHM", "PHY_OR_HIS", "CHM_AND_BIO"} <= codes

    phy_chm = next(rule_set for rule_set in rule_sets if rule_set.code == "PHY_AND_CHM")
    groups = [[item.subject_code for item in group.items] for group in phy_chm.groups]
    assert groups == [["PHY", "CHM"]], "物理和化学必须是同一个 ALL 组"


def test_seed_creates_the_special_requirement_samples(db_session, seeded) -> None:
    """体检要求与自由文本特殊要求各有一个样本，用于覆盖 UNKNOWN/FAIL 路径。"""
    requirements = list(db_session.execute(select(AdmissionRequirement)).scalars())
    assert any(row.requirement_type == "MEDICAL" for row in requirements)
    assert any(row.requirement_type == "SINGLE_SUBJECT" for row in requirements)

    with_text = db_session.execute(
        select(func.count())
        .select_from(AdmissionPlan)
        .where(AdmissionPlan.special_requirements.is_not(None))
    ).scalar_one()
    assert with_text >= 1

    cooperative = db_session.execute(
        select(func.count())
        .select_from(ProgramSeries)
        .where(ProgramSeries.cooperative != "NONE")
    ).scalar_one()
    assert cooperative >= 1


def test_reset_then_seed_restores_the_same_dataset(db_session, seeded) -> None:
    reset_demo(db_session)
    db_session.flush()
    assert db_session.execute(select(func.count()).select_from(AdmissionPlan)).scalar_one() == 0

    again = seed(db_session)
    db_session.flush()
    assert again.plans == len(DEMO_PLANS)
    assert db_session.execute(select(func.count()).select_from(AdmissionPlan)).scalar_one() == len(
        DEMO_PLANS
    )


def test_reset_refuses_without_force_when_work_exists(db_session, seeded) -> None:
    """重置不得静默删除考生档案与推荐方案。"""
    from app.ingestion.seed_demo import reset_demo as do_reset

    cycle = db_session.execute(
        select(AdmissionCycle).where(AdmissionCycle.exam_year == DEMO_EXAM_YEAR)
    ).scalar_one()
    db_session.add(
        Profile(
            cycle_id=cycle.id, exam_year=DEMO_EXAM_YEAR, rank=60_000, score=590,
            subjects=["PHY", "CHM", "BIO"],
        )
    )
    db_session.flush()

    with pytest.raises(RuntimeError, match="考生档案"):
        do_reset(db_session)

    # with force it proceeds and removes the derived rows
    do_reset(db_session, force=True)
    db_session.flush()
    assert db_session.execute(select(func.count()).select_from(Profile)).scalar_one() == 0
    assert db_session.execute(select(func.count()).select_from(AdmissionPlan)).scalar_one() == 0


def test_reset_keeps_the_cycle_so_profiles_stay_valid(db_session, seeded) -> None:
    """周期承载政策数字，重置不应删除它。"""
    from app.ingestion.seed_demo import reset_demo as do_reset

    do_reset(db_session, force=True)
    db_session.flush()
    cycle = db_session.execute(
        select(AdmissionCycle).where(AdmissionCycle.policy_version == "sd-2026-demo-v1")
    ).scalar_one_or_none()
    assert cycle is not None
    assert cycle.application_limit == DEMO_APPLICATION_LIMIT


def test_seeded_data_drives_the_whole_pipeline(db_session, seeded) -> None:
    """规格要求：演示数据可生成候选池和志愿方案。"""
    cycle = db_session.execute(
        select(AdmissionCycle).where(AdmissionCycle.exam_year == DEMO_EXAM_YEAR)
    ).scalar_one()

    from app.candidate.models import PreferenceProfile

    profile = Profile(
        cycle_id=cycle.id,
        exam_year=DEMO_EXAM_YEAR,
        rank=60_000,
        score=590,
        subjects=["PHY", "CHM", "BIO"],
        foreign_language="ENG",
    )
    db_session.add(profile)
    db_session.flush()
    preference = PreferenceProfile(
        profile_id=profile.id,
        version=1,
        risk_mode="BALANCED",
        candidate_rank=profile.rank,
        target_count=DEMO_APPLICATION_LIMIT,
    )
    db_session.add(preference)
    db_session.flush()

    result = RecommendationPipeline(db_session).run(
        profile_id=profile.id, preference_id=preference.id
    )
    assert result.stats.plans_scanned == len(DEMO_PLANS)
    assert result.stats.pooled > 0, "演示数据必须能生成候选池"
    assert result.stats.scored == result.stats.pooled

    # A rank-60000 candidate must see a genuine spread, not one bucket
    buckets = {bundle.risk_bucket for bundle in result.bundles if bundle.risk_bucket}
    assert len(buckets) >= 3, f"风险层过于集中: {buckets}"
    assert RiskBucket.SAFE in buckets or RiskBucket.STABLE in buckets


def test_seeded_data_produces_a_scheme_via_the_service(db_session, seeded) -> None:
    from app.schemes.schemas import SchemeCreate
    from app.schemes.service import SchemeService

    cycle = db_session.execute(
        select(AdmissionCycle).where(AdmissionCycle.exam_year == DEMO_EXAM_YEAR)
    ).scalar_one()
    from app.candidate.models import PreferenceProfile

    profile = Profile(
        cycle_id=cycle.id,
        exam_year=DEMO_EXAM_YEAR,
        rank=60_000,
        score=590,
        subjects=["PHY", "CHM", "BIO"],
    )
    db_session.add(profile)
    db_session.flush()
    preference = PreferenceProfile(
        profile_id=profile.id, version=1, candidate_rank=profile.rank, target_count=DEMO_APPLICATION_LIMIT
    )
    db_session.add(preference)
    db_session.flush()

    run = RecommendationService(db_session).run(
        profile_id=profile.id, preference_id=preference.id, cycle_id=None, snapshot_id=None
    )
    scheme = SchemeService(db_session).create(
        SchemeCreate(recommendation_session_id=run.session.id)
    )
    assert scheme.item_count > 0
    assert scheme.item_count <= DEMO_APPLICATION_LIMIT
    assert scheme.is_demo is True
    assert [item.position for item in scheme.items] == list(range(1, scheme.item_count + 1))


def test_seed_creates_majors_in_the_catalogue(db_session, seeded) -> None:
    majors = list(db_session.execute(select(Major)).scalars())
    assert majors
    assert all(major.code and major.name for major in majors)
    assert {major.level for major in majors} <= {"MAJOR", "MAJOR_CLASS"}

    universities = list(db_session.execute(select(University)).scalars())
    assert all(row.ministry_code.startswith("DEMO") for row in universities)
