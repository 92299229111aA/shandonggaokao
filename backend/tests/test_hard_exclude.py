"""Phase 3/6 acceptance: hard exclude applies to the preference actually used.

Regression guard for a real bug: the pipeline loads the newest
`preference_profiles` row by version, so a hard exclude added to an older version
is correctly ignored. This test pins that behaviour down.
"""

from __future__ import annotations

import pytest

from app.admission.models import AdmissionPlan, DatasetSnapshot, ProgramSeries
from app.candidate.models import MajorPreference, PreferenceProfile, Profile
from app.reference.models import University
from app.rules.types import TargetPlan, UserHardExcludes

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("plan_name", "excluded", "expected"),
    [
        ("专业2", "专业2", True),
        ("专业20", "专业2", False),
        ("计算机类", "计算机类", True),
        ("临床医学(5+3一体化)", "临床医学", True),
        ("计算机类[校企合作]", "计算机类", True),
        ("软件工程", "计算机", False),
        ("计算机", "计算机类", False),
    ],
)
def test_major_name_matching_requires_a_boundary(
    plan_name: str, excluded: str, expected: bool
) -> None:
    """Prefix collisions must not silently swallow unrelated majors."""
    plan = TargetPlan(plan_id=1, plan_name=plan_name, university_id=1, university_name="U")
    assert plan.matches_major_name((excluded,)) is expected


def test_older_preference_version_hard_exclude_is_not_applied(db_session, cycle) -> None:
    from app.recommendation.pipeline import RecommendationPipeline

    snapshot = DatasetSnapshot(code="DEMO-HE", label="DEMO", status="PUBLISHED", is_demo=True)
    university = University(
        ministry_code="DEMO900", name="演示大学", province_code="37", province_name="山东省"
    )
    db_session.add_all([snapshot, university])
    db_session.flush()
    series = ProgramSeries(series_key="he-1", canonical_name="专业2", university_id=university.id)
    db_session.add(series)
    db_session.flush()
    db_session.add(
        AdmissionPlan(
            cycle_id=cycle.id, series_id=series.id, snapshot_id=snapshot.id,
            plan_name="专业2", plan_count=10, tuition_fee=5000, is_demo=True,
        )
    )
    profile = Profile(
        cycle_id=cycle.id, exam_year=2026, rank=30000, score=600, subjects=["PHY", "CHM", "BIO"]
    )
    db_session.add(profile)
    db_session.flush()

    # v1 excludes 专业2, v2 does not: v2 is the one the pipeline must use
    v1 = PreferenceProfile(profile_id=profile.id, version=1, candidate_rank=30000, target_count=96)
    db_session.add(v1)
    db_session.flush()
    db_session.add(
        MajorPreference(
            preference_profile_id=v1.id, major_name="专业2", polarity="EXCLUDE", is_hard_exclude=True
        )
    )
    v2 = PreferenceProfile(profile_id=profile.id, version=2, candidate_rank=30000, target_count=96)
    db_session.add(v2)
    db_session.flush()

    v2_result = RecommendationPipeline(db_session).run(profile_id=profile.id, preference_id=v2.id)
    assert v2_result.stats.hard_excluded == 0

    v1_result = RecommendationPipeline(db_session).run(profile_id=profile.id, preference_id=v1.id)
    assert v1_result.stats.hard_excluded == 1
    assert all(
        not bundle.poolable
        for bundle in v1_result.bundles
        if bundle.target.plan_name == "专业2"
    )


def test_user_hard_exclude_direct_filter() -> None:
    from app.rules.service import HardExcludeFilter
    from app.rules.types import CandidateContext

    plan = TargetPlan(plan_id=1, plan_name="专业2", university_id=1, university_name="U")
    ctx = CandidateContext(hard_excludes=UserHardExcludes(major_names=("专业2",)))
    outcome = HardExcludeFilter().evaluate(ctx, plan)
    assert outcome.failures
    assert "明确排除" in outcome.failures[0].message
    assert outcome.failures[0].code.value == "USER_HARD_EXCLUDE"
