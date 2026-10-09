"""Phase 3 acceptance: ORM rows → Rule Engine dataclasses → verdict.

Proves the bridge is faithful: subject-rule groups, requirement severities,
special-condition attributes and user hard excludes all survive the mapping.
"""

from __future__ import annotations

import pytest

from app.admission.models import (
    AdmissionPlan,
    AdmissionRequirement,
    DatasetSnapshot,
    ProgramSeries,
    SubjectRuleGroup,
    SubjectRuleItem,
    SubjectRuleSet,
)
from app.candidate.models import Attribute, PreferenceProfile, Profile, SubjectScore
from app.common.enums import EligibilityStatus, RequirementSeverity, RequirementType, SubjectRuleMode
from app.reference.models import Major, University
from app.rules.mapping import build_candidate_context, build_target_plan
from app.rules.service import RuleEngine

pytestmark = pytest.mark.integration


@pytest.fixture()
def world(db_session, cycle):
    """One university, two majors, plans with 选科 + 单科 + 体检 requirements."""
    snapshot = DatasetSnapshot(code="DEMO-V1", label="DEMO", status="PUBLISHED", is_demo=True)
    university = University(
        ministry_code="DEMO1042", name="演示海洋大学", province_code="37", province_name="山东省"
    )
    cs = Major(code="0809", name="计算机类", level="MAJOR_CLASS", discipline_code="08")
    clinical = Major(code="100201", name="临床医学", level="MAJOR", discipline_code="10")
    db_session.add_all([snapshot, university, cs, clinical])
    db_session.flush()

    phy_chm = SubjectRuleSet(code="PHY+CHM", mode=SubjectRuleMode.ALL.value, raw_text="物理和化学")
    db_session.add(phy_chm)
    db_session.flush()
    group = SubjectRuleGroup(rule_set_id=phy_chm.id, group_no=0, mode=SubjectRuleMode.ALL.value)
    db_session.add(group)
    db_session.flush()
    db_session.add_all(
        [SubjectRuleItem(group_id=group.id, subject_code="PHY"), SubjectRuleItem(group_id=group.id, subject_code="CHM")]
    )

    cs_series = ProgramSeries(
        series_key="demo-cs", canonical_name="计算机类", university_id=university.id, major_id=cs.id
    )
    med_series = ProgramSeries(
        series_key="demo-med", canonical_name="临床医学", university_id=university.id, major_id=clinical.id
    )
    db_session.add_all([cs_series, med_series])
    db_session.flush()

    cs_plan = AdmissionPlan(
        cycle_id=cycle.id, series_id=cs_series.id, subject_rule_set_id=phy_chm.id, snapshot_id=snapshot.id,
        plan_name="计算机类", plan_count=40, tuition_fee=5800, is_demo=True,
    )
    med_plan = AdmissionPlan(
        cycle_id=cycle.id, series_id=med_series.id, snapshot_id=snapshot.id,
        plan_name="临床医学", plan_count=30, tuition_fee=7500, is_demo=True,
        special_requirements="不招色盲色弱考生",
    )
    db_session.add_all([cs_plan, med_plan])
    db_session.flush()

    db_session.add_all(
        [
            AdmissionRequirement(
                plan_id=med_plan.id,
                requirement_type=RequirementType.MEDICAL.value,
                operand={"must_satisfy": ["COLOR_VISION_NORMAL"]},
                severity=RequirementSeverity.BLOCK.value,
                raw_text="不招色盲色弱",
            ),
            AdmissionRequirement(
                plan_id=cs_plan.id,
                requirement_type=RequirementType.SINGLE_SUBJECT.value,
                operator="GTE",
                operand={"subject_code": "ENG", "min_score": 110},
                raw_text="英语不低于110分",
            ),
            AdmissionRequirement(
                plan_id=cs_plan.id,
                requirement_type=RequirementType.OTHER.value,
                operand={},
                severity=RequirementSeverity.WARN.value,
                is_deterministic=False,
                raw_text="详见招生章程",
            ),
        ]
    )
    db_session.flush()
    return {"university": university, "cs": cs, "clinical": clinical, "cs_plan": cs_plan, "med_plan": med_plan}


def _plans(db_session, plans):
    from app.admission.repository import PlanRepository

    repo = PlanRepository(db_session)
    rows = [repo.get_candidate(p.id) for p in plans]
    requirement_map = repo.requirements_for([p.id for p in plans])
    rule_map = repo.subject_rule_groups(
        [r["subject_rule_set_id"] for r in rows if r["subject_rule_set_id"]]
    )
    return [
        build_target_plan(
            plan_row=row,
            subject_groups=rule_map.get(row["subject_rule_set_id"] or 0),
            requirements=requirement_map.get(row["plan_id"], []),
        )
        for row in rows
    ]


def test_mapping_preserves_subject_rule_dnf(db_session, cycle, world) -> None:
    target = _plans(db_session, [world["cs_plan"]])[0]
    assert target.subject_requirement is not None
    assert target.subject_requirement.mode == SubjectRuleMode.ALL
    assert target.subject_requirement.groups == (("PHY", "CHM"),)
    assert target.discipline_code == "08"
    assert target.major_id == world["cs"].id


def test_profile_maps_to_candidate_context(db_session, cycle, world) -> None:
    profile = Profile(
        cycle_id=cycle.id, exam_year=2026, rank=20000, score=600,
        subjects=["PHY", "CHM", "BIO"], foreign_language="ENG", sex="MALE",
    )
    db_session.add(profile)
    db_session.flush()
    db_session.add_all(
        [
            SubjectScore(profile_id=profile.id, subject_code="ENG", score=120),
            Attribute(profile_id=profile.id, attr_key="COLOR_VISION_NORMAL", value_type="BOOL", value_bool=True),
        ]
    )
    db_session.flush()

    ctx = build_candidate_context(
        profile=profile,
        subject_scores=list(profile.subject_scores) if hasattr(profile, "subject_scores") else [],
        attributes=list(profile.attributes) if hasattr(profile, "attributes") else [],
        current_ranked_candidates=300000,
    )
    # relationship loading is lazy in this fixture; reload explicitly
    from sqlalchemy import select

    scores = list(db_session.execute(select(SubjectScore).where(SubjectScore.profile_id == profile.id)).scalars())
    attrs = list(db_session.execute(select(Attribute).where(Attribute.profile_id == profile.id)).scalars())
    ctx = build_candidate_context(
        profile=profile, subject_scores=scores, attributes=attrs, current_ranked_candidates=300000
    )

    assert ctx.subjects_known is True
    assert ctx.subjects == frozenset({"PHY", "CHM", "BIO"})
    assert ctx.subject_scores["ENG"] == 120
    assert ctx.attribute("COLOR_VISION_NORMAL").value is True
    assert ctx.foreign_language.value == "ENG"


def test_established_requirement_does_not_decide_alone(db_session, cycle, world) -> None:
    """An is_deterministic=False requirement forces UNKNOWN, never PASS."""
    from sqlalchemy import select

    profile = Profile(
        cycle_id=cycle.id, exam_year=2026, rank=20000, score=600,
        subjects=["PHY", "CHM", "BIO"], foreign_language="ENG",
    )
    db_session.add(profile)
    db_session.flush()
    db_session.add(SubjectScore(profile_id=profile.id, subject_code="ENG", score=120))
    db_session.flush()

    scores = list(db_session.execute(select(SubjectScore).where(SubjectScore.profile_id == profile.id)).scalars())
    ctx = build_candidate_context(profile=profile, subject_scores=scores)
    target = _plans(db_session, [world["cs_plan"]])[0]

    result = RuleEngine().evaluate(ctx, target)
    assert result.status == EligibilityStatus.UNKNOWN
    assert result.can_be_pooled is True
    assert result.counts_as_safe is False


def test_medical_requirement_fails_with_verified_attribute(db_session, cycle, world) -> None:
    from sqlalchemy import select

    profile = Profile(
        cycle_id=cycle.id, exam_year=2026, rank=20000, score=600, subjects=["PHY", "CHM", "BIO"]
    )
    db_session.add(profile)
    db_session.flush()
    db_session.add(
        Attribute(profile_id=profile.id, attr_key="COLOR_VISION_NORMAL", value_type="BOOL", value_bool=False)
    )
    db_session.flush()

    attrs = list(db_session.execute(select(Attribute).where(Attribute.profile_id == profile.id)).scalars())
    ctx = build_candidate_context(profile=profile, attributes=attrs)
    target = _plans(db_session, [world["med_plan"]])[0]

    result = RuleEngine().evaluate(ctx, target)
    assert result.status == EligibilityStatus.FAIL
    assert any("体检不符" in v.message for v in result.failures)


def test_hard_exclude_from_preference_profile(db_session, cycle, world) -> None:
    from sqlalchemy import select

    from app.candidate.models import MajorPreference

    profile = Profile(
        cycle_id=cycle.id, exam_year=2026, rank=20000, score=600, subjects=["PHY", "CHM", "BIO"]
    )
    db_session.add(profile)
    db_session.flush()
    preference = PreferenceProfile(profile_id=profile.id, version=1, exclude_cooperative=True, max_tuition=6000)
    db_session.add(preference)
    db_session.flush()
    db_session.add(
        MajorPreference(
            preference_profile_id=preference.id,
            major_id=world["clinical"].id,
            polarity="EXCLUDE",
            is_hard_exclude=True,
        )
    )
    db_session.flush()

    loaded = db_session.execute(
        select(PreferenceProfile).where(PreferenceProfile.id == preference.id)
    ).scalar_one()
    ctx = build_candidate_context(profile=profile, preference=loaded)
    assert world["clinical"].id in ctx.hard_excludes.major_ids
    assert ctx.hard_excludes.max_tuition == 6000
    assert ctx.hard_excludes.exclude_cooperative is True

    engine = RuleEngine()
    med_target = _plans(db_session, [world["med_plan"]])[0]
    medical = engine.evaluate(ctx, med_target)
    assert medical.status == EligibilityStatus.FAIL
    assert medical.hard_excluded is True

    # 学费 7500 > 6000 also triggers the tuition hard exclude
    assert any(v.code.value == "USER_HARD_EXCLUDE" for v in medical.failures)
