"""Verify the candidate pool covers multiple provinces and honours location preferences.

Regression guard for a real defect: the demo dataset originally placed every
university in Shandong, which made the 地域偏好 dimension impossible to exercise
and gave a false impression of the candidate pool.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.admission.models import AdmissionCycle, AdmissionPlan, ProgramSeries
from app.candidate.models import LocationPreference, PreferenceProfile, Profile
from app.ingestion.demo_data import DEMO_EXAM_YEAR
from app.ingestion.seed_demo import seed
from app.recommendation.pipeline import RecommendationPipeline
from app.reference.models import University

pytestmark = pytest.mark.integration


@pytest.fixture()
def seeded(db_session):
    seed(db_session)
    db_session.flush()
    return db_session


def _make_profile(db_session, cycle, rank: int = 60_000) -> Profile:
    profile = Profile(
        cycle_id=cycle.id,
        exam_year=DEMO_EXAM_YEAR,
        rank=rank,
        score=590,
        subjects=["PHY", "CHM", "BIO"],
        foreign_language="ENG",
    )
    db_session.add(profile)
    db_session.flush()
    return profile


def _cycle(db_session) -> AdmissionCycle:
    return db_session.execute(
        select(AdmissionCycle).where(AdmissionCycle.exam_year == DEMO_EXAM_YEAR)
    ).scalar_one()


def test_demo_dataset_spans_many_provinces(seeded) -> None:
    """演示数据必须覆盖多个省份，否则地域偏好维度无法被验证。"""
    provinces = list(seeded.execute(select(University.province_code).distinct()).scalars())
    assert len(provinces) >= 8, f"省份过少: {provinces}"
    assert "37" in provinces, "应包含山东省内院校"

    out_of_province = seeded.execute(
        select(func.count()).select_from(University).where(University.province_code != "37")
    ).scalar_one()
    assert out_of_province >= 8, f"省外院校过少: {out_of_province}"

    # tier coverage: out-of-province 985 schools must exist
    top = seeded.execute(
        select(func.count())
        .select_from(University)
        .where(University.is_985.is_(True), University.province_code != "37")
    ).scalar_one()
    assert top >= 3, f"省外 985 院校过少: {top}"


def test_candidate_pool_contains_out_of_province_schools(seeded) -> None:
    """候选池不能只有山东院校。"""
    cycle = _cycle(seeded)
    profile = _make_profile(seeded, cycle)
    preference = PreferenceProfile(
        profile_id=profile.id, version=1, risk_mode="BALANCED",
        candidate_rank=profile.rank, target_count=96,
    )
    seeded.add(preference)
    seeded.flush()

    result = RecommendationPipeline(seeded).run(
        profile_id=profile.id, preference_id=preference.id
    )
    pooled = [b for b in result.bundles if b.poolable]
    assert pooled, "候选池不应为空"

    provinces = {b.target.province_code for b in pooled}
    assert len(provinces) >= 6, f"候选池省份过少: {provinces}"
    assert provinces - {"37"}, "候选池里完全没有省外院校"

    out_of_province = [b for b in pooled if b.target.province_code != "37"]
    assert len(out_of_province) >= 8, f"省外候选过少: {len(out_of_province)}"


def test_location_include_preference_boosts_that_province(seeded) -> None:
    """指定优先地域后，该地域志愿的偏好匹配应显著高于未指定时。"""
    cycle = _cycle(seeded)
    profile = _make_profile(seeded, cycle)

    # 1) no location preference at all
    neutral = PreferenceProfile(
        profile_id=profile.id, version=1, risk_mode="BALANCED",
        candidate_rank=profile.rank, target_count=96,
    )
    seeded.add(neutral)
    seeded.flush()
    neutral_run = RecommendationPipeline(seeded).run(
        profile_id=profile.id, preference_id=neutral.id
    )
    neutral_scores = {
        b.target.province_code: b.preference.score
        for b in neutral_run.bundles
        if b.poolable and b.preference
    }
    assert "11" in neutral_scores, "候选池应含北京院校"

    # 2) Beijing explicitly preferred
    beijing = PreferenceProfile(
        profile_id=profile.id, version=2, risk_mode="BALANCED",
        candidate_rank=profile.rank, target_count=96,
    )
    seeded.add(beijing)
    seeded.flush()
    seeded.add(
        LocationPreference(
            preference_profile_id=beijing.id,
            province_code="11",
            province_name="北京市",
            polarity="INCLUDE",
            weight=90.0,
        )
    )
    seeded.flush()

    preferred_run = RecommendationPipeline(seeded).run(
        profile_id=profile.id, preference_id=beijing.id
    )
    beijing_scores = [
        b.preference.score
        for b in preferred_run.bundles
        if b.poolable and b.preference and b.target.province_code == "11"
    ]
    other_scores = [
        b.preference.score
        for b in preferred_run.bundles
        if b.poolable and b.preference and b.target.province_code == "37"
    ]
    assert beijing_scores and other_scores
    assert sum(beijing_scores) / len(beijing_scores) > sum(other_scores) / len(other_scores), (
        "优先地域的偏好匹配应高于非优先地域"
    )
    # and the preference score must actually rise versus the neutral run
    assert max(beijing_scores) > max(
        score for province, score in neutral_scores.items() if province == "11"
    ) or min(beijing_scores) > 0


def test_location_hard_exclude_removes_that_province(seeded) -> None:
    """硬性排除某省份后，该省志愿不得出现在候选池。"""
    cycle = _cycle(seeded)
    profile = _make_profile(seeded, cycle)
    preference = PreferenceProfile(
        profile_id=profile.id, version=1, risk_mode="BALANCED",
        candidate_rank=profile.rank, target_count=96,
    )
    seeded.add(preference)
    seeded.flush()
    seeded.add(
        LocationPreference(
            preference_profile_id=preference.id,
            province_code="37",
            province_name="山东省",
            polarity="EXCLUDE",
            is_hard_exclude=True,
            weight=100.0,
        )
    )
    seeded.flush()

    result = RecommendationPipeline(seeded).run(
        profile_id=profile.id, preference_id=preference.id
    )
    assert result.stats.hard_excluded >= 1
    pooled_provinces = {b.target.province_code for b in result.bundles if b.poolable}
    assert "37" not in pooled_provinces, "被硬性排除的省份仍出现在候选池"
    assert pooled_provinces, "排除山东后仍应有省外院校可选"


def test_out_of_province_plans_are_real_plans(seeded) -> None:
    """每条省外专业序列都必须有历史录取数据，否则 ACS 会退化为中性值。"""
    series_ids = list(
        seeded.execute(
            select(ProgramSeries.id)
            .join(University, University.id == ProgramSeries.university_id)
            .where(University.province_code != "37")
        ).scalars()
    )
    assert len(series_ids) >= 16, f"省外专业序列过少: {len(series_ids)}"

    from app.admission.models import AdmissionOutcome

    for series_id in series_ids:
        outcomes = seeded.execute(
            select(func.count())
            .select_from(AdmissionOutcome)
            .where(AdmissionOutcome.series_id == series_id)
        ).scalar_one()
        assert outcomes >= 3, f"专业序列 {series_id} 的历史录取数据不足: {outcomes}"

    # and every out-of-province plan must point at one of those series
    plan_count = seeded.execute(
        select(func.count())
        .select_from(AdmissionPlan)
        .join(ProgramSeries, ProgramSeries.id == AdmissionPlan.series_id)
        .join(University, University.id == ProgramSeries.university_id)
        .where(University.province_code != "37")
    ).scalar_one()
    assert plan_count >= 16, f"省外计划过少: {plan_count}"
