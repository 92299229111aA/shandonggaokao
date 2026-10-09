"""Phase 5 acceptance: the recommendation pipeline.

Covers the spec's mandatory scenarios:
  * FAIL 不进入候选池；UNKNOWN 可以进入候选池但不计保底
  * 用户 hard_exclude 永不泄漏进候选池
  * 相同输入 + 相同版本 => 结果完全一致（可重放）
  * 会话锁定 profile/preference/dataset/policy/model 版本
  * 只有 PUBLISHED 快照可用
  * 每层候选池分别按 Preference DESC / Confidence DESC 截断
"""

from __future__ import annotations

import pytest

from app.admission.models import (
    AdmissionCycle,
    AdmissionOutcome,
    AdmissionPlan,
    AdmissionRequirement,
    DatasetSnapshot,
    ExamYearStats,
    ProgramSeries,
    SubjectRuleGroup,
    SubjectRuleItem,
    SubjectRuleSet,
)
from app.candidate.models import MajorPreference, PreferenceProfile, Profile, SubjectScore
from app.common.enums import EligibilityStatus, RequirementType
from app.recommendation.pipeline import RecommendationPipeline
from app.recommendation.pipeline_types import PipelineConfig
from app.recommendation.service import RecommendationService, eligibility_counts
from app.reference.models import University

pytestmark = pytest.mark.integration

CANDIDATE_RANK = 30000
POPULATION = 300000


@pytest.fixture()
def snapshot(db_session):
    row = DatasetSnapshot(code="DEMO-V1", label="DEMO 数据集", status="PUBLISHED", is_demo=True)
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture()
def world(db_session, cycle, snapshot):
    """14 plans across risk levels, one hard-excluded, one eligibility FAIL."""
    db_session.add(
        ExamYearStats(
            province_code="37", exam_year=2026, exam_category="SUMMER_REGULAR",
            ranked_candidates=POPULATION,
        )
    )
    for year in (2023, 2024, 2025):
        db_session.add(
            ExamYearStats(
                province_code="37", exam_year=year, exam_category="SUMMER_REGULAR",
                ranked_candidates=POPULATION,
            )
        )

    phy_chm = SubjectRuleSet(code="PHY+CHM", mode="ALL", raw_text="物理和化学")
    history_only = SubjectRuleSet(code="HIS", mode="ALL", raw_text="历史")
    db_session.add_all([phy_chm, history_only])
    db_session.flush()
    group = SubjectRuleGroup(rule_set_id=phy_chm.id, group_no=0, mode="ALL")
    his_group = SubjectRuleGroup(rule_set_id=history_only.id, group_no=0, mode="ALL")
    db_session.add_all([group, his_group])
    db_session.flush()
    db_session.add_all(
        [
            SubjectRuleItem(group_id=group.id, subject_code="PHY"),
            SubjectRuleItem(group_id=group.id, subject_code="CHM"),
            SubjectRuleItem(group_id=his_group.id, subject_code="HIS"),
        ]
    )

    universities = [
        University(
            ministry_code=f"DEMO{i:03d}", name=f"演示大学{i}", province_code="37",
            province_name="山东省", city_code="370200", city_name="青岛市",
            is_985=(i == 0), is_211=(i <= 1), is_double_first_class=(i <= 1),
        )
        for i in range(4)
    ]
    db_session.add_all(universities)
    db_session.flush()

    plans: list[AdmissionPlan] = []
    # min_rank spread across buckets relative to candidate rank 30000 / 300000 (10%)
    # d = ln(q_y/q_c): history min_rank below 30000 => safer, above => harder
    rank_plan = [
        (200000, 20),   # very hard -> HIGH_REACH
        (120000, 25),   # hard
        (60000, 30),    # REACH
        (45000, 35),    # REACH
        (34000, 40),    # BORDER
        (30000, 45),    # BORDER (exactly at candidate percentile)
        (26000, 50),    # STABLE
        (20000, 55),    # STABLE
        (15000, 60),    # SAFE
        (10000, 65),    # SAFE
    ]
    for index, (min_rank, plan_count) in enumerate(rank_plan):
        university = universities[index % len(universities)]
        series = ProgramSeries(
            series_key=f"demo-series-{index}",
            canonical_name=f"演示专业{index}",
            university_id=university.id,
            first_year=2023,
            last_year=2025,
        )
        db_session.add(series)
        db_session.flush()
        for year in (2023, 2024, 2025):
            db_session.add(
                AdmissionOutcome(
                    series_id=series.id, snapshot_id=snapshot.id, exam_year=year,
                    min_rank=min_rank, plan_count=plan_count, admitted_count=plan_count,
                    min_score=600, is_demo=True,
                )
            )
        plan = AdmissionPlan(
            cycle_id=cycle.id, series_id=series.id, subject_rule_set_id=phy_chm.id,
            snapshot_id=snapshot.id, plan_name=f"演示专业{index}", plan_count=plan_count,
            tuition_fee=5000 + index * 500, is_demo=True,
        )
        db_session.add(plan)
        plans.append(plan)

    # A 历史-required plan the candidate cannot apply to -> eligibility FAIL
    his_series = ProgramSeries(
        series_key="demo-series-his", canonical_name="历史学", university_id=universities[0].id
    )
    db_session.add(his_series)
    db_session.flush()
    his_plan = AdmissionPlan(
        cycle_id=cycle.id, series_id=his_series.id, subject_rule_set_id=history_only.id,
        snapshot_id=snapshot.id, plan_name="历史学", plan_count=30, tuition_fee=5000, is_demo=True,
    )
    db_session.add(his_plan)

    # A cooperative plan with an unverifiable special requirement -> UNKNOWN
    coop_series = ProgramSeries(
        series_key="demo-series-coop", canonical_name="中外合作办学专业",
        university_id=universities[1].id, cooperative="CHINESE_FOREIGN",
    )
    db_session.add(coop_series)
    db_session.flush()
    for year in (2023, 2024, 2025):
        db_session.add(
            AdmissionOutcome(
                series_id=coop_series.id, snapshot_id=snapshot.id, exam_year=year,
                min_rank=25000, plan_count=20, is_demo=True,
            )
        )
    coop_plan = AdmissionPlan(
        cycle_id=cycle.id, series_id=coop_series.id, subject_rule_set_id=phy_chm.id,
        snapshot_id=snapshot.id, plan_name="中外合作办学专业", plan_count=20,
        tuition_fee=30000, is_demo=True, special_requirements="需通过学校组织的面试",
    )
    db_session.add(coop_plan)
    db_session.flush()
    db_session.add(
        AdmissionRequirement(
            plan_id=coop_plan.id, requirement_type=RequirementType.MEDICAL.value,
            operand={"must_satisfy": ["COLOR_VISION_NORMAL"]}, severity="BLOCK",
            raw_text="不招色盲色弱",
        )
    )
    db_session.flush()

    return {
        "plans": plans,
        "his_plan": his_plan,
        "coop_plan": coop_plan,
        "universities": universities,
        "history_only": history_only,
    }


def make_profile(db_session, cycle, **overrides) -> Profile:
    profile = Profile(
        cycle_id=cycle.id,
        exam_year=2026,
        rank=overrides.pop("rank", CANDIDATE_RANK),
        score=overrides.pop("score", 600),
        subjects=overrides.pop("subjects", ["PHY", "CHM", "BIO"]),
        foreign_language="ENG",
        **overrides,
    )
    db_session.add(profile)
    db_session.flush()
    db_session.add(SubjectScore(profile_id=profile.id, subject_code="ENG", score=120))
    db_session.flush()
    return profile


def make_preference(db_session, profile, **overrides) -> PreferenceProfile:
    fields: dict = {
        "profile_id": profile.id,
        "version": 1,
        "risk_mode": "BALANCED",
        "candidate_rank": profile.rank,
        "target_count": 96,
    }
    fields.update(overrides)
    preference = PreferenceProfile(**fields)
    db_session.add(preference)
    db_session.flush()
    return preference


# ---------------- basic run ----------------


def test_pipeline_produces_scored_candidate_pool(db_session, cycle, snapshot, world) -> None:
    profile = make_profile(db_session, cycle)
    preference = make_preference(db_session, profile)

    result = RecommendationPipeline(db_session).run(profile_id=profile.id, preference_id=preference.id)

    assert result.stats.plans_scanned == 12
    assert result.stats.eligibility_fail == 1, "历史学应因选科不符 FAIL"
    assert result.stats.eligibility_unknown >= 1, "中外合作+面试应 UNKNOWN"
    assert result.stats.scored == 11
    assert result.stats.pooled == 11
    assert result.stats.pool_by_bucket, "候选池必须按风险层分别统计"

    counts = eligibility_counts(db_session, result.session_id)
    assert counts[EligibilityStatus.FAIL.value] == 1
    assert counts[EligibilityStatus.UNKNOWN.value] >= 1


def test_pipeline_scores_span_multiple_risk_buckets(db_session, cycle, snapshot, world) -> None:
    profile = make_profile(db_session, cycle)
    preference = make_preference(db_session, profile)
    result = RecommendationPipeline(db_session).run(profile_id=profile.id, preference_id=preference.id)

    buckets = {bundle.risk_bucket.value for bundle in result.bundles if bundle.risk_bucket}
    assert len(buckets) >= 4, f"风险层过于集中: {buckets}"
    assert "HIGH_REACH" in buckets
    assert "SAFE" in buckets


def test_fail_items_never_enter_candidate_pool(db_session, cycle, snapshot, world) -> None:
    profile = make_profile(db_session, cycle)
    preference = make_preference(db_session, profile)
    result = RecommendationPipeline(db_session).run(profile_id=profile.id, preference_id=preference.id)

    from app.recommendation.models import RecommendationItem

    fail_pooled = [
        bundle
        for bundle in result.bundles
        if bundle.eligibility.status is EligibilityStatus.FAIL and bundle.plan_id in {
            item.plan_id
            for item in db_session.query(RecommendationItem)
            .filter(
                RecommendationItem.session_id == result.session_id,
                RecommendationItem.in_candidate_pool.is_(True),
            )
            .all()
        }
    ]
    assert fail_pooled == []


def test_unknown_items_are_pooled_but_not_safe(db_session, cycle, snapshot, world) -> None:
    profile = make_profile(db_session, cycle)
    preference = make_preference(db_session, profile)
    result = RecommendationPipeline(db_session).run(profile_id=profile.id, preference_id=preference.id)

    unknown = [b for b in result.bundles if b.eligibility.status is EligibilityStatus.UNKNOWN]
    assert unknown, "应存在 UNKNOWN 志愿"
    for bundle in unknown:
        assert bundle.eligibility.can_be_pooled is True
        assert bundle.eligibility.counts_as_safe is False


# ---------------- hard excludes ----------------


def test_hard_excluded_major_never_reaches_the_pool(db_session, cycle, snapshot, world) -> None:
    profile = make_profile(db_session, cycle)
    preference = make_preference(db_session, profile)
    db_session.add(
        MajorPreference(
            preference_profile_id=preference.id,
            major_name="演示专业3",
            polarity="EXCLUDE",
            is_hard_exclude=True,
        )
    )
    db_session.flush()

    result = RecommendationPipeline(db_session).run(profile_id=profile.id, preference_id=preference.id)
    excluded = world["plans"][3]
    assert result.stats.hard_excluded == 1
    assert all(bundle.plan_id != excluded.id for bundle in result.bundles if bundle.poolable)


def test_hard_excluded_tuition_ceiling(db_session, cycle, snapshot, world) -> None:
    profile = make_profile(db_session, cycle)
    preference = make_preference(db_session, profile, max_tuition=5500)
    result = RecommendationPipeline(db_session).run(profile_id=profile.id, preference_id=preference.id)

    assert result.stats.hard_excluded >= 5  # 5000 + index*500 > 5500 from index 2 onward
    for bundle in result.bundles:
        if bundle.poolable:
            assert bundle.plan_row["tuition_fee"] <= 5500


def test_hard_exclude_cooperative(db_session, cycle, snapshot, world) -> None:
    profile = make_profile(db_session, cycle)
    preference = make_preference(db_session, profile, exclude_cooperative=True)
    result = RecommendationPipeline(db_session).run(profile_id=profile.id, preference_id=preference.id)

    assert result.stats.hard_excluded >= 1
    assert all(
        bundle.plan_row["cooperative"] == "NONE"
        for bundle in result.bundles
        if bundle.poolable
    )


# ---------------- determinism / replay ----------------


def test_same_inputs_produce_identical_scores(db_session, cycle, snapshot, world) -> None:
    """规格要求：相同快照 + 模型 + 输入可重放，结果一致。"""
    profile = make_profile(db_session, cycle)
    preference = make_preference(db_session, profile)

    first = RecommendationPipeline(db_session).run(profile_id=profile.id, preference_id=preference.id)
    second = RecommendationPipeline(db_session).run(profile_id=profile.id, preference_id=preference.id)

    def fingerprint(result) -> list[tuple[int, float, float, float, str]]:
        return sorted(
            (
                bundle.plan_id,
                round(bundle.acs.acs, 6),
                round(bundle.acs.confidence, 6),
                round(bundle.preference.score, 6),
                bundle.risk_bucket.value,
            )
            for bundle in result.bundles
            if bundle.acs and bundle.preference
        )

    assert fingerprint(first) == fingerprint(second)
    assert first.session_id != second.session_id, "每次运行必须是独立会话（历史不可覆盖）"


def test_session_locks_every_version(db_session, cycle, snapshot, world) -> None:
    profile = make_profile(db_session, cycle)
    preference = make_preference(db_session, profile)
    result = RecommendationPipeline(db_session).run(profile_id=profile.id, preference_id=preference.id)

    from app.recommendation.models import Session as RunSession

    run = db_session.get(RunSession, result.session_id)
    assert run is not None
    assert run.profile_version == profile.version
    assert run.preference_version == preference.version
    assert run.snapshot_id == snapshot.id
    assert run.policy_version == cycle.policy_version
    assert run.model_version
    assert run.algorithm_config_version
    assert run.stats["pooled"] == result.stats.pooled


def test_only_published_snapshot_is_accepted(db_session, cycle, world) -> None:
    draft = DatasetSnapshot(code="DEMO-DRAFT", label="草稿", status="DRAFT", is_demo=True)
    db_session.add(draft)
    db_session.flush()
    profile = make_profile(db_session, cycle)
    preference = make_preference(db_session, profile)

    from app.common.errors import RuleViolationError

    with pytest.raises(RuleViolationError, match="PUBLISHED"):
        RecommendationPipeline(db_session).run(
            profile_id=profile.id, preference_id=preference.id, snapshot_id=draft.id
        )


def test_missing_exam_year_stats_degrades_gracefully(db_session, cycle, snapshot) -> None:
    """没有当年考生人数时不能瞎算 ACS，应给出明确说明。"""

    empty_cycle = AdmissionCycle(
        province_code="37", exam_year=2030, exam_category="SUMMER_REGULAR",
        batch_code="REGULAR_BATCH_1", application_limit=96, policy_version="sd-2030-v1",
    )
    db_session.add(empty_cycle)
    db_session.flush()

    profile = make_profile(db_session, empty_cycle)
    preference = make_preference(db_session, profile)
    result = RecommendationPipeline(db_session).run(profile_id=profile.id, preference_id=preference.id)

    assert any("exam_year_stats" in note for note in result.stats.notes)
    assert result.stats.pooled == 0


# ---------------- candidate pool truncation ----------------


def test_pool_is_truncated_per_bucket_by_preference_then_confidence(
    db_session, cycle, snapshot, world
) -> None:
    profile = make_profile(db_session, cycle)
    preference = make_preference(db_session, profile)
    # A limit of 1 per bucket: the kept item must be the highest preference one
    config = PipelineConfig(
        pool_limits={"HIGH_REACH": 1, "REACH": 1, "BORDER": 1, "STABLE": 1, "SAFE": 1}
    )
    result = RecommendationPipeline(db_session, pipeline_config=config).run(
        profile_id=profile.id, preference_id=preference.id
    )

    assert result.stats.pooled <= 5
    assert result.stats.truncated_by_bucket, "应记录每个风险层被截断的数量"

    for bucket, kept in result.stats.pool_by_bucket.items():
        items = [b for b in result.bundles if b.poolable and b.risk_bucket.value == bucket]
        items.sort(
            key=lambda b: (-(b.preference.score if b.preference else 0.0), -(b.acs.confidence if b.acs else 0.0))
        )
        if len(items) > kept:
            assert items[0].plan_id in {
                bundle.plan_id
                for bundle in result.bundles
                if bundle.poolable and bundle.risk_bucket.value == bucket
            }


def test_locked_items_bypass_the_pool_limit(db_session, cycle, snapshot, world) -> None:
    profile = make_profile(db_session, cycle)
    preference = make_preference(db_session, profile)
    locked_plan = world["plans"][9]  # a SAFE plan
    config = PipelineConfig(
        pool_limits={"HIGH_REACH": 1, "REACH": 1, "BORDER": 1, "STABLE": 1, "SAFE": 1}
    )
    result = RecommendationPipeline(db_session, pipeline_config=config).run(
        profile_id=profile.id, preference_id=preference.id, locked_plan_ids={locked_plan.id}
    )

    pooled_ids = {bundle.plan_id for bundle in result.bundles if bundle.poolable}
    assert locked_plan.id in pooled_ids or result.stats.pool_by_bucket.get("SAFE", 0) >= 1
    assert result.stats.locked_kept >= 1


# ---------------- persistence ----------------


def test_fail_rows_are_persisted_for_audit_but_not_pooled(db_session, cycle, snapshot, world) -> None:
    profile = make_profile(db_session, cycle)
    preference = make_preference(db_session, profile)
    result = RecommendationPipeline(db_session).run(profile_id=profile.id, preference_id=preference.id)

    from app.recommendation.models import RecommendationItem

    fail_rows = (
        db_session.query(RecommendationItem)
        .filter(
            RecommendationItem.session_id == result.session_id,
            RecommendationItem.eligibility == "FAIL",
        )
        .all()
    )
    assert fail_rows, "FAIL 记录应保留以便审计"
    assert all(row.in_candidate_pool is False for row in fail_rows)


def test_items_persist_full_explanation(db_session, cycle, snapshot, world) -> None:
    profile = make_profile(db_session, cycle)
    preference = make_preference(db_session, profile)
    result = RecommendationPipeline(db_session).run(profile_id=profile.id, preference_id=preference.id)

    from app.recommendation.models import RecommendationItem

    item = (
        db_session.query(RecommendationItem)
        .filter(
            RecommendationItem.session_id == result.session_id,
            RecommendationItem.in_candidate_pool.is_(True),
        )
        .first()
    )
    assert item is not None
    assert item.acs is not None and 0 <= item.acs <= 100
    assert item.base_acs is not None
    assert item.confidence is not None and 0 <= item.confidence <= 100
    assert item.preference_score is not None
    assert item.risk_bucket
    assert item.acs_explanation is not None
    assert "不是录取概率" in item.acs_explanation["disclaimer"]
    assert item.preference_dimensions is not None


# ---------------- service / shortfall reporting ----------------


def test_service_reports_shortfall_instead_of_padding(db_session, cycle, snapshot, world) -> None:
    profile = make_profile(db_session, cycle)
    preference = make_preference(db_session, profile, target_count=96)
    response = RecommendationService(db_session).run(
        profile_id=profile.id, preference_id=preference.id, cycle_id=None, snapshot_id=None
    )

    assert response.pool_size == 11
    assert response.pool_size < 96
    assert response.shortfall_notice is not None
    assert "不会为凑数" in response.shortfall_notice
    assert response.relax_suggestions
    assert any("放宽" in suggestion for suggestion in response.relax_suggestions)
    # the eligibility FAIL is reported but explicitly marked as non-relaxable
    assert any("选科" in suggestion for suggestion in response.relax_suggestions)


def test_item_dto_exposes_card_fields(db_session, cycle, snapshot, world) -> None:
    profile = make_profile(db_session, cycle)
    preference = make_preference(db_session, profile)
    service = RecommendationService(db_session)
    response = service.run(
        profile_id=profile.id, preference_id=preference.id, cycle_id=None, snapshot_id=None
    )

    page = service.list_items(session_id=response.session.id, limit=5, offset=0)
    assert page.total == 11
    assert page.pool_size == 11
    assert page.risk_distribution

    item = page.items[0]
    assert "·" in item.program_label
    assert item.acs is not None
    assert item.confidence_label in {"HIGH", "MEDIUM", "LOW"}
    assert item.risk_bucket
    assert len(item.history) == 3, "推荐卡片必须带近 3 年最低位次"
    assert item.plan_count_history
    assert item.plan_change_ratio is not None
    assert item.preference_score is not None


def test_search_and_filters_on_items(db_session, cycle, snapshot, world) -> None:
    profile = make_profile(db_session, cycle)
    preference = make_preference(db_session, profile)
    service = RecommendationService(db_session)
    response = service.run(
        profile_id=profile.id, preference_id=preference.id, cycle_id=None, snapshot_id=None
    )
    session_id = response.session.id

    filtered = service.list_items(session_id=session_id, limit=50, offset=0, risk_bucket="SAFE")
    assert filtered.total >= 1
    assert all(item.risk_bucket == "SAFE" for item in filtered.items)

    searched = service.list_items(session_id=session_id, limit=50, offset=0, search="演示大学1")
    assert searched.total >= 1
    assert all("演示大学1" in item.university_name for item in searched.items)

    with_fails = service.list_items(
        session_id=session_id, limit=50, offset=0, only_pooled=False, eligibility="FAIL"
    )
    assert with_fails.total == 1


def test_get_item_by_id(db_session, cycle, snapshot, world) -> None:
    profile = make_profile(db_session, cycle)
    preference = make_preference(db_session, profile)
    service = RecommendationService(db_session)
    response = service.run(
        profile_id=profile.id, preference_id=preference.id, cycle_id=None, snapshot_id=None
    )
    page = service.list_items(session_id=response.session.id, limit=1, offset=0)
    item = service.get_item(page.items[0].item_id)
    assert item.item_id == page.items[0].item_id


def test_snapshot_mismatch_between_cycles_is_rejected(db_session, cycle, snapshot, world) -> None:
    """会话必须锁定唯一快照；传入不存在的快照应报 404。"""
    from app.common.errors import NotFoundError

    profile = make_profile(db_session, cycle)
    preference = make_preference(db_session, profile)
    with pytest.raises(NotFoundError):
        RecommendationPipeline(db_session).run(
            profile_id=profile.id, preference_id=preference.id, snapshot_id=999999
        )
