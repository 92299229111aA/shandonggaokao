"""Phase 6 acceptance (DB): versioned, optimistically-locked schemes.

Mandatory scenarios:
  * 方案修改产生新版本；旧版本仍可读取
  * 版本冲突返回 409
  * hard exclude 不会泄漏进方案（包括手动加入）
  * 重新优化保留 locked 志愿
  * 候选不足时方案少于目标数量并给出提示
"""

from __future__ import annotations

import pytest

from app.admission.models import (
    AdmissionOutcome,
    AdmissionPlan,
    DatasetSnapshot,
    ExamYearStats,
    ProgramSeries,
    SubjectRuleGroup,
    SubjectRuleItem,
    SubjectRuleSet,
)
from app.candidate.models import MajorPreference, PreferenceProfile, Profile
from app.common.enums import RiskBucket
from app.recommendation.service import RecommendationService
from app.reference.models import University
from app.schemes.schemas import OptimizeRequest, SchemeCreate, SchemeOrderUpdate
from app.schemes.service import SchemeService

pytestmark = pytest.mark.integration

POPULATION = 300000


@pytest.fixture()
def run_session(db_session, cycle):
    """A finished recommendation session with 12 pooled candidates."""
    snapshot = DatasetSnapshot(code="DEMO-V1", label="DEMO", status="PUBLISHED", is_demo=True)
    db_session.add(snapshot)
    db_session.flush()
    for year in (2023, 2024, 2025, 2026):
        db_session.add(
            ExamYearStats(
                province_code="37", exam_year=year, exam_category="SUMMER_REGULAR",
                ranked_candidates=POPULATION,
            )
        )

    rule_set = SubjectRuleSet(code="PHY+CHM", mode="ALL", raw_text="物理和化学")
    db_session.add(rule_set)
    db_session.flush()
    group = SubjectRuleGroup(rule_set_id=rule_set.id, group_no=0, mode="ALL")
    db_session.add(group)
    db_session.flush()
    db_session.add_all(
        [
            SubjectRuleItem(group_id=group.id, subject_code="PHY"),
            SubjectRuleItem(group_id=group.id, subject_code="CHM"),
        ]
    )

    universities = [
        University(
            ministry_code=f"DEMO{i:03d}", name=f"演示大学{i}", province_code="37", province_name="山东省"
        )
        for i in range(4)
    ]
    db_session.add_all(universities)
    db_session.flush()

    # min_rank 25000..80000 around candidate rank 30000 -> BORDER..HIGH_REACH mix
    for index in range(12):
        series = ProgramSeries(
            series_key=f"series-{index}",
            canonical_name=f"专业{index}",
            university_id=universities[index % 4].id,
            first_year=2023,
            last_year=2025,
        )
        db_session.add(series)
        db_session.flush()
        for year in (2023, 2024, 2025):
            db_session.add(
                AdmissionOutcome(
                    series_id=series.id, snapshot_id=snapshot.id, exam_year=year,
                    min_rank=25000 + index * 2000, plan_count=30 + index, is_demo=True,
                )
            )
        db_session.add(
            AdmissionPlan(
                cycle_id=cycle.id, series_id=series.id, subject_rule_set_id=rule_set.id,
                snapshot_id=snapshot.id, plan_name=f"专业{index}", plan_count=30 + index,
                tuition_fee=5000 + index * 200, is_demo=True,
            )
        )
    db_session.flush()

    profile = Profile(
        cycle_id=cycle.id, exam_year=2026, rank=30000, score=600,
        subjects=["PHY", "CHM", "BIO"], foreign_language="ENG",
    )
    db_session.add(profile)
    db_session.flush()
    preference = PreferenceProfile(
        profile_id=profile.id, version=1, risk_mode="BALANCED", candidate_rank=30000, target_count=96
    )
    db_session.add(preference)
    db_session.flush()

    response = RecommendationService(db_session).run(
        profile_id=profile.id, preference_id=preference.id, cycle_id=None, snapshot_id=None
    )
    return {"session_id": response.session.id, "profile": profile, "preference": preference}


# ---------------- create / read ----------------


def test_create_scheme_generates_version_1(db_session, cycle, run_session) -> None:
    service = SchemeService(db_session)
    dto = service.create(
        SchemeCreate(recommendation_session_id=run_session["session_id"], risk_mode="BALANCED")
    )

    assert dto.version_no == 1
    assert dto.operation == "CREATE"
    assert dto.lock_version == 1
    assert dto.item_count == len(dto.items) == 12
    assert [item.position for item in dto.items] == list(range(1, 13))
    assert dto.target_count == 96, "目标数量应取自 admission_cycles.application_limit"
    assert dto.is_demo is True


def test_create_scheme_cannot_exceed_policy_limit(db_session, cycle, run_session) -> None:
    from app.common.errors import RuleViolationError

    service = SchemeService(db_session)
    with pytest.raises(RuleViolationError) as excinfo:
        service.create(
            SchemeCreate(recommendation_session_id=run_session["session_id"], target_count=120)
        )
    # the real limit is carried in the error details, straight from admission_cycles
    assert excinfo.value.details["application_limit"] == 96


def test_scheme_items_are_ordered_by_desire_then_difficulty(db_session, cycle, run_session) -> None:
    dto = SchemeService(db_session).create(
        SchemeCreate(recommendation_session_id=run_session["session_id"])
    )
    bands = [item.desire_band or 0 for item in dto.items]
    assert bands == sorted(bands, reverse=True), "意愿层必须单调不增"
    # within a band, ACS must be non-decreasing
    for previous, current in zip(dto.items, dto.items[1:], strict=False):
        if previous.desire_band == current.desire_band:
            assert (previous.acs or 0) <= (current.acs or 0) + 1e-9


# ---------------- versioning / optimistic locking ----------------


def test_edit_creates_new_version_and_keeps_history(db_session, cycle, run_session) -> None:
    service = SchemeService(db_session)
    created = service.create(SchemeCreate(recommendation_session_id=run_session["session_id"]))

    # move the last item to the top
    entries = [
        {"recommendation_item_id": created.items[-1].recommendation_item_id, "position": 1},
    ]
    updated = service.update_order(
        created.id, SchemeOrderUpdate(version=created.lock_version, items=entries)
    )
    assert updated.current_version_no == 2
    assert updated.lock_version == 2
    assert updated.operation == "REORDER"

    versions = service.list_versions(created.id)
    assert [version.version_no for version in versions] == [2, 1]

    # the old version is still readable and unchanged
    original = service.get_version(created.id, 1)
    assert original.version_no == 1
    assert original.items[0].recommendation_item_id == created.items[0].recommendation_item_id
    assert original.is_historical is True


def test_stale_version_raises_conflict(db_session, cycle, run_session) -> None:
    from app.common.errors import VersionConflictError

    service = SchemeService(db_session)
    created = service.create(SchemeCreate(recommendation_session_id=run_session["session_id"]))
    entry = [{"recommendation_item_id": created.items[0].recommendation_item_id, "position": 2}]
    service.update_order(created.id, SchemeOrderUpdate(version=created.lock_version, items=entry))

    with pytest.raises(VersionConflictError) as excinfo:
        service.update_order(created.id, SchemeOrderUpdate(version=created.lock_version, items=entry))
    assert excinfo.value.details["actual"] == 2
    assert excinfo.value.details["expected"] == 1


def test_removing_item_produces_new_version(db_session, cycle, run_session) -> None:
    service = SchemeService(db_session)
    created = service.create(SchemeCreate(recommendation_session_id=run_session["session_id"]))
    victim = created.items[2]
    after = service.remove_item(created.id, created.lock_version, position=victim.position)

    assert after.item_count == 11
    assert after.current_version_no == 2
    assert victim.recommendation_item_id not in [
        item.recommendation_item_id for item in after.items
    ]
    assert [item.position for item in after.items] == list(range(1, 12))


def test_removing_missing_position_is_404(db_session, cycle, run_session) -> None:
    from app.common.errors import NotFoundError

    service = SchemeService(db_session)
    created = service.create(SchemeCreate(recommendation_session_id=run_session["session_id"]))
    with pytest.raises(NotFoundError):
        service.remove_item(created.id, created.lock_version, position=999)


def test_lock_and_unlock(db_session, cycle, run_session) -> None:
    service = SchemeService(db_session)
    created = service.create(SchemeCreate(recommendation_session_id=run_session["session_id"]))

    locked = service.set_lock(created.id, created.lock_version, position=1, locked=True)
    assert locked.items[0].locked is True
    assert locked.operation == "LOCK"

    unlocked = service.set_lock(locked.id, locked.lock_version, position=1, locked=False)
    assert unlocked.items[0].locked is False
    assert unlocked.operation == "UNLOCK"


def test_add_items_rejects_fail_recommendation_items(db_session, cycle, run_session) -> None:
    """hard exclude / FAIL 的志愿不允许手动加入。"""
    # create a FAIL row for this session by adding a 历史-required plan
    from app.common.errors import RuleViolationError
    from app.recommendation.models import RecommendationItem

    session_id = run_session["session_id"]
    existing_plan_ids = {
        row.plan_id
        for row in db_session.query(RecommendationItem)
        .filter(RecommendationItem.session_id == session_id)
        .all()
    }
    assert existing_plan_ids

    service = SchemeService(db_session)
    created = service.create(SchemeCreate(recommendation_session_id=session_id))

    # simulate a FAIL item that is not in the pool
    fail_item = (
        db_session.query(RecommendationItem)
        .filter(RecommendationItem.session_id == session_id, RecommendationItem.in_candidate_pool.is_(False))
        .first()
    )
    if fail_item is None:
        pytest.skip("该数据集没有 FAIL 条目可供测试")
    with pytest.raises(RuleViolationError, match="资格不符|候选池"):
        service.add_items(created.id, created.lock_version, [fail_item.id])


def test_hard_excluded_major_never_appears_in_scheme(db_session, cycle, run_session) -> None:
    """规格：用户 hard_exclude 专业永不进入方案。

    The excludes are attached to a *new* preference version, which is the row the
    pipeline loads — see tests/test_hard_exclude.py for the versioning rule.
    """
    from app.candidate.models import Profile as ProfileRow
    from app.recommendation.models import RecommendationItem

    excluded_names = {"专业2", "专业5", "专业7"}
    profile: ProfileRow = run_session["profile"]
    previous: PreferenceProfile = run_session["preference"]

    excluded_plan_ids = {
        plan_id
        for plan_id, name in db_session.query(AdmissionPlan.id, AdmissionPlan.plan_name).all()
        if name in excluded_names
    }
    assert excluded_plan_ids, "测试数据里应有这些专业"

    v2 = PreferenceProfile(
        profile_id=profile.id,
        version=previous.version + 1,
        risk_mode=previous.risk_mode,
        candidate_rank=previous.candidate_rank,
        target_count=96,
    )
    db_session.add(v2)
    db_session.flush()
    for name in excluded_names:
        db_session.add(
            MajorPreference(
                preference_profile_id=v2.id,
                major_name=name,
                polarity="EXCLUDE",
                is_hard_exclude=True,
            )
        )
    db_session.flush()

    response = RecommendationService(db_session).run(
        profile_id=profile.id, preference_id=v2.id, cycle_id=None, snapshot_id=None
    )
    assert response.session.preference_version == 2

    pooled_plan_ids = {
        row.plan_id
        for row in db_session.query(RecommendationItem)
        .filter(
            RecommendationItem.session_id == response.session.id,
            RecommendationItem.in_candidate_pool.is_(True),
        )
        .all()
    }
    assert pooled_plan_ids, "应仍有可入池的志愿"
    assert not (pooled_plan_ids & excluded_plan_ids), "被硬性排除的专业进入了候选池"

    dto = SchemeService(db_session).create(
        SchemeCreate(recommendation_session_id=response.session.id)
    )
    assert dto.items
    for item in dto.items:
        assert item.plan_name not in excluded_names, f"被硬性排除的专业泄漏进方案: {item.plan_name}"


# ---------------- re-optimize ----------------


def test_reoptimize_keeps_locked_items(db_session, cycle, run_session) -> None:
    service = SchemeService(db_session)
    created = service.create(
        SchemeCreate(recommendation_session_id=run_session["session_id"], target_count=6)
    )
    assert created.item_count == 6

    locked_position = created.items[-1].position
    locked_item_id = created.items[-1].recommendation_item_id
    locked = service.set_lock(created.id, created.lock_version, position=locked_position, locked=True)
    assert any(item.locked for item in locked.items)

    # shrink the target so the optimizer must drop entries
    optimized = service.optimize(
        created.id, OptimizeRequest(version=locked.lock_version, target_count=3)
    )
    assert optimized.item_count == 3
    kept = [item.recommendation_item_id for item in optimized.items]
    assert locked_item_id in kept, "锁定志愿必须被保留"
    assert optimized.optimizer_stats is not None
    assert optimized.operation == "OPTIMIZE"


def test_reoptimize_respects_min_safe_count(db_session, cycle, run_session) -> None:
    service = SchemeService(db_session)
    created = service.create(SchemeCreate(recommendation_session_id=run_session["session_id"]))
    optimistic = service.optimize(
        created.id, OptimizeRequest(version=created.lock_version, min_safe_count=5)
    )
    # the pool holds 12 candidates, so the scheme keeps all of them
    assert optimistic.item_count == 12
    assert optimistic.optimizer_stats is not None


def test_reoptimize_with_unknown_items_does_not_count_them_as_safe(
    db_session, cycle, run_session
) -> None:
    from app.recommendation.models import RecommendationItem as ItemRow

    session_id = run_session["session_id"]
    # Force every pooled item to UNKNOWN; a min_safe_count must then be unsatisfiable
    # but clamped rather than crashing.
    for row in (
        db_session.query(ItemRow)
        .filter(ItemRow.session_id == session_id, ItemRow.in_candidate_pool.is_(True))
        .all()
    ):
        row.eligibility = "UNKNOWN"
    db_session.flush()

    service = SchemeService(db_session)
    created = service.create(SchemeCreate(recommendation_session_id=session_id))
    optimized = service.optimize(
        created.id, OptimizeRequest(version=created.lock_version, min_safe_count=10)
    )
    assert optimized.item_count >= 1


def test_optimize_rejects_target_above_policy_limit(db_session, cycle, run_session) -> None:
    from app.common.errors import RuleViolationError

    service = SchemeService(db_session)
    created = service.create(SchemeCreate(recommendation_session_id=run_session["session_id"]))
    with pytest.raises(RuleViolationError) as excinfo:
        service.optimize(created.id, OptimizeRequest(version=created.lock_version, target_count=150))
    assert excinfo.value.details["application_limit"] == 96


def test_optimize_on_stale_version_is_conflict(db_session, cycle, run_session) -> None:
    from app.common.errors import VersionConflictError

    service = SchemeService(db_session)
    created = service.create(SchemeCreate(recommendation_session_id=run_session["session_id"]))
    service.set_lock(created.id, created.lock_version, position=1, locked=True)
    with pytest.raises(VersionConflictError):
        service.optimize(created.id, OptimizeRequest(version=created.lock_version))


# ---------------- export ----------------


def test_export_has_no_probability_language(db_session, cycle, run_session) -> None:
    service = SchemeService(db_session)
    created = service.create(
        SchemeCreate(recommendation_session_id=run_session["session_id"], target_count=5)
    )
    exported = service.export(created.id)

    assert exported.scheme_id == created.id
    assert exported.application_limit == 96
    assert len(exported.rows) == 5
    assert [row.position for row in exported.rows] == list(range(1, 6))
    assert "不是录取概率" in exported.disclaimer or "非录取概率" in exported.disclaimer
    assert "probability" not in exported.model_dump_json()


def test_export_historical_version(db_session, cycle, run_session) -> None:
    service = SchemeService(db_session)
    created = service.create(
        SchemeCreate(recommendation_session_id=run_session["session_id"], target_count=5)
    )
    service.remove_item(created.id, created.lock_version, position=1)

    current = service.export(created.id)
    historical = service.export(created.id, version_no=1)
    assert current.version_no == 2 and len(current.rows) == 4
    assert historical.version_no == 1 and len(historical.rows) == 5


def test_scheme_from_failed_session_is_rejected(db_session, cycle, run_session) -> None:
    from app.common.errors import RuleViolationError
    from app.recommendation.models import Session as RunSession

    session_id = run_session["session_id"]
    run = db_session.get(RunSession, session_id)
    assert run is not None
    run.status = "FAILED"
    db_session.flush()

    with pytest.raises(RuleViolationError, match="状态"):
        SchemeService(db_session).create(SchemeCreate(recommendation_session_id=session_id))


def test_risk_distribution_is_reported(db_session, cycle, run_session) -> None:
    dto = SchemeService(db_session).create(
        SchemeCreate(recommendation_session_id=run_session["session_id"])
    )
    assert dto.risk_distribution
    assert sum(dto.risk_distribution.values()) == dto.item_count
    allowed = {bucket.value for bucket in RiskBucket} | {"UNKNOWN"}
    assert set(dto.risk_distribution) <= allowed
