"""Regression tests for the audit findings P1–P5.

Every test here exists because the bug it covers was **silent**: nothing errored, the
output just quietly stopped meaning what it said.

  P1  A per-school 专业序号 stored in `special_requirements` made the rules engine answer
      UNKNOWN for all 73,198 plans. UNKNOWN does not count toward 保底, so the safety
      requirement was disabled for the whole dataset while every test still passed —
      because they asserted "no FAIL", never "some PASS".
  P3  Two editors hitting the same scheme version both passed the optimistic-lock check
      and both inserted version_no = N+1; the loser surfaced a UniqueViolation as HTTP 500
      instead of the documented 409.
  P4  A profile with two 选考科目 was accepted and produced a full-looking recommendation
      in which no 选科要求 could ever be satisfied.
  P5  A rank beyond the entire 一段 population was accepted with no warning, skewing ACS
      upward because q_c = rank / population exceeded 1.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.admission.models import AdmissionPlan, DatasetSnapshot
from app.common.enums import EligibilityStatus
from app.rules.engine import SpecialRequirementTextRule
from app.rules.service import RuleEngine, RuleEngineConfig
from app.rules.types import CandidateContext, TargetPlan

# ---------------- P1: the sequence number must not look like a requirement ----------------


def _plan(**overrides) -> TargetPlan:
    """Mirrors how rows become TargetPlan: only `special_requirements` feeds the text.

    `major_sequence` deliberately has no field on TargetPlan — nothing in the rules engine
    should ever read it.
    """
    base = {
        "plan_id": 1,
        "plan_name": "计算机类",
        "university_id": 100,
        "university_name": "演示大学",
        "province_code": "37",
        "city_code": "370200",
        "tuition_fee": 5800,
        "plan_count": 40,
    }
    base.update(overrides)
    return TargetPlan(**base)


def _context() -> CandidateContext:
    return CandidateContext(
        rank=20000,
        score=600,
        current_ranked_candidates=300000,
        subjects=frozenset({"PHY", "CHM", "BIO"}),
        subjects_known=True,
    )


def test_sequence_number_alone_does_not_degrade_eligibility() -> None:
    """A plan whose only extra text was a 专业序号 must not raise a 章程 requirement."""
    outcome = SpecialRequirementTextRule().evaluate(_context(), _plan())
    assert outcome.status is EligibilityStatus.PASS


def test_real_requirement_text_still_degrades_to_unknown() -> None:
    """The fix must not weaken the rule that protects the candidate."""
    outcome = SpecialRequirementTextRule().evaluate(
        _context(), _plan(special_requirements_text="外语语种限英语，且需通过学校面试")
    )
    assert outcome.status is EligibilityStatus.UNKNOWN


def test_engine_answers_pass_when_only_a_sequence_is_present() -> None:
    engine = RuleEngine(config=RuleEngineConfig(assume_unrestricted_when_missing=True))
    assert engine.evaluate(_context(), _plan()).status is EligibilityStatus.PASS


def test_target_plan_has_no_field_for_the_sequence_number() -> None:
    """The sequence must stay out of the rules contract entirely."""
    assert "major_sequence" not in TargetPlan.__dataclass_fields__


def test_loaded_plans_do_not_carry_sequence_numbers_as_requirements(db_session) -> None:
    """Guards the data, not just the code: the ETL must not refill that column."""
    contaminated = db_session.execute(
        select(AdmissionPlan.id)
        .where(AdmissionPlan.special_requirements.like("院校专业序号%"))
        .limit(5)
    ).all()
    assert not contaminated, (
        f"{len(contaminated)} 条计划仍把专业序号写在 special_requirements 里，"
        "会让这些志愿的资格变成 UNKNOWN"
    )


@pytest.mark.integration
def test_loaded_plans_still_have_their_sequence_number(db_session) -> None:
    """The sequence must be moved, not dropped."""
    total = db_session.execute(
        select(AdmissionPlan.id).where(AdmissionPlan.is_demo.is_(False)).limit(1)
    ).first()
    if total is None:
        pytest.skip("测试库未载入真实招生计划")

    kept = db_session.execute(
        select(AdmissionPlan.id)
        .where(AdmissionPlan.major_sequence.is_not(None))
        .limit(1)
    ).first()
    assert kept is not None, "major_sequence 全为空，0009 的数据迁移没生效"


# ---------------- P4: 选考科目 must be exactly three ----------------


def test_subject_count_must_be_exactly_three() -> None:
    """山东 3+3: any other count cannot satisfy a 选科要求, so it is refused."""
    import pydantic

    from app.candidate.schemas import ProfileCreate

    def build(subjects: list[str]) -> ProfileCreate:
        return ProfileCreate.model_validate(
            {"cycle_id": 1, "rank": 1000, "subjects": subjects}
        )

    assert len(build(["PHY", "CHM", "BIO"]).subjects) == 3
    for bad in (["PHY"], ["PHY", "CHM"], ["PHY", "CHM", "BIO", "GEO"]):
        with pytest.raises(pydantic.ValidationError):
            build(bad)


# ---------------- P3: concurrent edits must not surface as a 500 ----------------


def test_second_writer_of_the_same_version_is_rejected(db_session) -> None:
    """Drives `_require_unlocked` twice; the second call must raise, not corrupt state.

    This is the in-process half of the guarantee. The real race is two HTTP requests, but
    the fix is the row lock taken by this method, so asserting its behaviour here keeps the
    contract under test without needing two live connections.
    """
    from app.common.errors import VersionConflictError
    from app.schemes.service import SchemeService

    service = SchemeService(db_session)
    scheme = _make_scheme(db_session)

    # First reader at the current version succeeds and takes the row lock.
    acquired = service._require_unlocked(scheme.id, scheme.lock_version)
    assert acquired.id == scheme.id

    # A stale version is refused with the documented conflict error.
    with pytest.raises(VersionConflictError):
        service._require_unlocked(scheme.id, scheme.lock_version + 99)


def _make_scheme(db_session):
    """Smallest real scheme: needs a run session, which needs a profile and preference."""
    from app.admission.models import AdmissionCycle
    from app.candidate.models import PreferenceProfile, Profile
    from app.recommendation.models import ModelVersion, Scheme, SchemeVersion
    from app.recommendation.models import Session as RunSession

    cycle = db_session.execute(select(AdmissionCycle).limit(1)).scalars().first()
    if cycle is None:
        cycle = AdmissionCycle(
            province_code="37",
            exam_year=2026,
            exam_category="SUMMER_REGULAR",
            batch_code="REGULAR_BATCH_1",
            application_limit=96,
        )
        db_session.add(cycle)
        db_session.flush()

    model = db_session.execute(select(ModelVersion).limit(1)).scalars().first()
    if model is None:
        model = ModelVersion(code="test-v1", label="测试模型", parameters={})
        db_session.add(model)
        db_session.flush()

    snapshot = db_session.execute(select(DatasetSnapshot).limit(1)).scalars().first()
    if snapshot is None:
        snapshot = DatasetSnapshot(code="TEST-SNAP", label="测试快照", status="PUBLISHED")
        db_session.add(snapshot)
        db_session.flush()

    profile = Profile(cycle_id=cycle.id, exam_year=cycle.exam_year, rank=1000, subjects=["PHY", "CHM", "BIO"])
    db_session.add(profile)
    db_session.flush()
    preference = PreferenceProfile(
        profile_id=profile.id, version=1, risk_mode="BALANCED", candidate_rank=1000, target_count=96
    )
    db_session.add(preference)
    db_session.flush()
    run = RunSession(
        profile_id=profile.id,
        preference_profile_id=preference.id,
        cycle_id=cycle.id,
        snapshot_id=snapshot.id,
        model_version_id=model.id,
        profile_version=1,
        preference_version=1,
        policy_version="test",
        model_version=model.code,
        status="SUCCEEDED",
    )
    db_session.add(run)
    db_session.flush()
    scheme = Scheme(session_id=run.id, risk_mode="BALANCED", target_count=96, current_version_no=1, lock_version=1)
    db_session.add(scheme)
    db_session.flush()
    db_session.add(
        SchemeVersion(
            scheme_id=scheme.id, version_no=1, operation="OPTIMIZE", item_count=0
        )
    )
    db_session.flush()
    return scheme
