"""Phase 7 acceptance: the eleven Health Checks.

Pure unit tests. Each check must fire on its own trigger and stay silent
otherwise, and no check may modify the scheme it inspects.
"""

from __future__ import annotations

import copy

import pytest

from app.common.enums import HealthSeverity, RiskBucket
from app.healthcheck.checks import (
    ApplicationLimitCheck,
    DuplicateCheck,
    EligibilityCheck,
    HardPreferenceCheck,
    LowConfidenceCheck,
    OrderingCheck,
    PlanReductionCheck,
    RiskDistributionCheck,
    SmallPlanCountCheck,
    StaleSnapshotCheck,
    UnknownRequirementCheck,
)
from app.healthcheck.engine import HEALTH_SCORE_DISCLAIMER, HealthCheckEngine
from app.healthcheck.types import (
    CheckResult,
    HealthCheckContext,
    PreferenceFact,
    SchemeFact,
    SnapshotFact,
    score_for,
)
from app.optimizer.types import DEFAULT_RISK_TARGETS


def fact(position: int, **overrides) -> SchemeFact:
    base = {
        "position": position,
        "plan_id": position,
        "program_label": f"专业{position} · 演示大学",
        "university_id": 1,
        "plan_name": f"专业{position}",
        "university_name": "演示大学",
        "eligibility": "PASS",
        "acs": 70.0,
        "confidence": 80.0,
        "preference_score": 60.0,
        "desire_band": 20,
        "risk_bucket": RiskBucket.STABLE.value,
        "plan_count": 30,
        "tuition_fee": 5000,
        "plan_count_history": (30, 30, 30),
        "min_rank_history": (30000, 31000, 29000),
    }
    base.update(overrides)
    return SchemeFact(**base)


def ctx(**overrides) -> HealthCheckContext:
    base = {
        "items": (fact(1), fact(2)),
        "application_limit": 96,
        "risk_targets": dict(DEFAULT_RISK_TARGETS["BALANCED"]),
        "preference": PreferenceFact(),
        "snapshot": SnapshotFact(id=1, code="DEMO-2026", status="PUBLISHED", published_year=2026),
        "latest_published_snapshot": SnapshotFact(
            id=1, code="DEMO-2026", status="PUBLISHED", published_year=2026
        ),
        "target_exam_year": 2026,
    }
    base.update(overrides)
    return HealthCheckContext(**base)


# ================= 1. EligibilityCheck =================


def test_eligibility_check_flags_fail_and_unknown() -> None:
    check = EligibilityCheck()
    assert check.run(ctx()).issues == ()

    fail = check.run(ctx(items=(fact(1, eligibility="FAIL"),)))
    assert len(fail.issues) == 1
    assert fail.issues[0].severity is HealthSeverity.ERROR
    assert fail.issues[0].code == "ELIGIBILITY_FAIL"

    unknown = check.run(ctx(items=(fact(1, eligibility="UNKNOWN"),)))
    assert unknown.issues[0].severity is HealthSeverity.WARNING


def test_eligibility_check_reports_a_clean_recheck() -> None:
    result = EligibilityCheck().run(ctx(recheck_enabled=True))
    assert len(result.issues) == 1
    assert result.issues[0].severity is HealthSeverity.INFO


# ================= 2. DuplicateCheck =================


def test_duplicate_check_detects_same_plan_twice() -> None:
    duplicated = (fact(1), fact(2, plan_id=1, program_label="专业1 · 演示大学"))
    result = DuplicateCheck().run(ctx(items=duplicated))
    assert len(result.issues) == 1
    assert result.issues[0].severity is HealthSeverity.ERROR
    assert result.issues[0].detail["positions"] == [1, 2]


def test_duplicate_check_passes_for_distinct_plans() -> None:
    assert DuplicateCheck().run(ctx()).issues == ()


# ================= 3. ApplicationLimitCheck =================


def test_application_limit_check_exceeds() -> None:
    items = tuple(fact(i) for i in range(1, 5))
    result = ApplicationLimitCheck().run(ctx(items=items, application_limit=3))
    assert result.issues[0].code == "LIMIT_EXCEEDED"
    assert result.issues[0].severity is HealthSeverity.ERROR
    assert result.issues[0].detail["application_limit"] == 3


def test_application_limit_check_notes_unused_slots() -> None:
    result = ApplicationLimitCheck().run(ctx(application_limit=96))
    assert result.issues[0].code == "LIMIT_UNUSED"
    assert result.issues[0].severity is HealthSeverity.INFO


def test_application_limit_check_silent_when_exactly_full() -> None:
    items = tuple(fact(i) for i in range(1, 4))
    assert ApplicationLimitCheck().run(ctx(items=items, application_limit=3)).issues == ()


# ================= 4. UnknownRequirementCheck =================


def test_unknown_requirement_check_flags_unverified_text() -> None:
    items = (fact(1, special_requirements_text="需通过学校面试", has_unknown_requirement=True),)
    result = UnknownRequirementCheck().run(ctx(items=items))
    codes = [issue.code for issue in result.issues]
    assert "UNKNOWN_REQUIREMENT" in codes


def test_unknown_requirement_in_safety_net_is_flagged() -> None:
    """规格：UNKNOWN 项不能被算作保底。"""
    items = (
        fact(
            1,
            special_requirements_text="详见招生章程",
            has_unknown_requirement=True,
            risk_bucket=RiskBucket.SAFE.value,
        ),
    )
    result = UnknownRequirementCheck().run(ctx(items=items))
    codes = [issue.code for issue in result.issues]
    assert "UNKNOWN_IN_SAFETY_NET" in codes


def test_unknown_requirement_check_silent_for_clean_plans() -> None:
    assert UnknownRequirementCheck().run(ctx()).issues == ()


# ================= 5. RiskDistributionCheck =================


def test_risk_distribution_reports_target_shortfalls() -> None:
    result = RiskDistributionCheck().run(ctx())
    codes = [issue.code for issue in result.issues]
    assert "RISK_TARGET_SHORTFALL" in codes
    assert "NO_SAFETY_NET" in codes or "THIN_SAFETY_NET" in codes


def test_risk_distribution_errors_when_no_safety_net() -> None:
    items = tuple(
        fact(i, risk_bucket=RiskBucket.HIGH_REACH.value) for i in range(1, 4)
    )
    result = RiskDistributionCheck().run(ctx(items=items))
    assert any(issue.code == "NO_SAFETY_NET" and issue.severity is HealthSeverity.ERROR for issue in result.issues)


def test_risk_distribution_passes_when_targets_met() -> None:
    items = []
    position = 1
    for bucket, target in DEFAULT_RISK_TARGETS["BALANCED"].items():
        for _ in range(target):
            items.append(fact(position, risk_bucket=bucket))
            position += 1
    result = RiskDistributionCheck().run(ctx(items=tuple(items)))
    assert result.issues == ()


def test_risk_distribution_enforces_min_safe_count() -> None:
    items = (fact(1, risk_bucket=RiskBucket.BORDER.value),)
    result = RiskDistributionCheck().run(
        ctx(items=items, preference=PreferenceFact(min_safe_count=5))
    )
    assert any(issue.code == "MIN_SAFE_COUNT_UNMET" for issue in result.issues)


# ================= 6. OrderingCheck =================


def test_ordering_check_detects_desire_inversion() -> None:
    """更想去的志愿被排在后面。"""
    items = (
        fact(1, desire_band=10, preference_score=30.0, acs=70.0),
        fact(2, desire_band=25, preference_score=75.0, acs=70.0),
    )
    result = OrderingCheck().run(ctx(items=items))
    assert result.issues[0].code == "ORDER_DESIRE_INVERSION"
    assert result.issues[0].severity is HealthSeverity.WARNING


def test_ordering_check_detects_acs_inversion_within_band() -> None:
    items = (
        fact(1, desire_band=20, preference_score=60.0, acs=85.0),
        fact(2, desire_band=20, preference_score=60.0, acs=30.0),
    )
    result = OrderingCheck().run(ctx(items=items))
    assert result.issues[0].code == "ORDER_ACS_INVERSION"
    assert result.issues[0].severity is HealthSeverity.INFO


def test_ordering_check_passes_for_correct_order() -> None:
    items = (
        fact(1, desire_band=25, preference_score=75.0, acs=40.0),
        fact(2, desire_band=25, preference_score=75.0, acs=80.0),
        fact(3, desire_band=10, preference_score=30.0, acs=95.0),
    )
    assert OrderingCheck().run(ctx(items=items)).issues == ()


def test_ordering_check_ignores_fixed_positions() -> None:
    items = (
        fact(1, desire_band=5, preference_score=15.0, acs=90.0, fixed_position=True),
        fact(2, desire_band=30, preference_score=90.0, acs=50.0),
    )
    assert OrderingCheck().run(ctx(items=items)).issues == ()


# ================= 7. LowConfidenceCheck =================


def test_low_confidence_check_flags_weak_evidence() -> None:
    items = (fact(1, confidence=20.0),)
    result = LowConfidenceCheck().run(ctx(items=items))
    assert result.issues[0].code == "LOW_CONFIDENCE"


def test_low_confidence_check_flags_missing_confidence() -> None:
    items = (fact(1, confidence=None),)
    result = LowConfidenceCheck().run(ctx(items=items))
    assert result.issues[0].severity is HealthSeverity.WARNING


def test_low_confidence_in_safety_net_is_a_warning_not_info() -> None:
    items = (fact(1, confidence=30.0, risk_bucket=RiskBucket.SAFE.value),)
    trusted = (fact(1, confidence=30.0, risk_bucket=RiskBucket.HIGH_REACH.value),)
    assert LowConfidenceCheck().run(ctx(items=items)).issues[0].severity is HealthSeverity.WARNING
    assert LowConfidenceCheck().run(ctx(items=trusted)).issues[0].severity is HealthSeverity.INFO


def test_low_confidence_check_silent_for_high_confidence() -> None:
    assert LowConfidenceCheck().run(ctx()).issues == ()


# ================= 8. SmallPlanCountCheck =================


def test_small_plan_count_check() -> None:
    small = SmallPlanCountCheck().run(ctx(items=(fact(1, plan_count=2),)))
    assert small.issues[0].code == "SMALL_PLAN_COUNT"

    zero = SmallPlanCountCheck().run(ctx(items=(fact(1, plan_count=0),)))
    assert zero.issues[0].code == "PLAN_COUNT_ZERO"
    assert zero.issues[0].severity is HealthSeverity.ERROR

    assert SmallPlanCountCheck().run(ctx()).issues == ()


# ================= 9. PlanReductionCheck =================


def test_plan_reduction_check_flags_shrinkage() -> None:
    """50 -> 5 的骤降必须被提示。"""
    items = (fact(1, plan_count=5, plan_count_history=(50, 50, 50)),)
    result = PlanReductionCheck().run(ctx(items=items))
    assert result.issues[0].code == "PLAN_REDUCED"
    assert result.issues[0].severity is HealthSeverity.WARNING
    assert result.issues[0].detail["change_ratio"] < 0


def test_plan_reduction_check_notes_expansion() -> None:
    items = (fact(1, plan_count=60, plan_count_history=(30, 30, 30)),)
    result = PlanReductionCheck().run(ctx(items=items))
    assert result.issues[0].code == "PLAN_EXPANDED"
    assert result.issues[0].severity is HealthSeverity.INFO


def test_plan_reduction_check_silent_without_history() -> None:
    items = (fact(1, plan_count=5, plan_count_history=()),)
    assert PlanReductionCheck().run(ctx(items=items)).issues == ()


# ================= 10. StaleSnapshotCheck =================


def test_stale_snapshot_flags_newer_dataset() -> None:
    """规格必测：数据快照更新后旧方案出现 STALE_DATASET warning。"""
    result = StaleSnapshotCheck().run(
        ctx(
            snapshot=SnapshotFact(id=1, code="DEMO-V1", status="PUBLISHED", published_year=2026),
            latest_published_snapshot=SnapshotFact(
                id=2, code="DEMO-V2", status="PUBLISHED", published_year=2026
            ),
        )
    )
    assert any(issue.code == "STALE_DATASET" and issue.severity is HealthSeverity.WARNING for issue in result.issues)


def test_stale_snapshot_flags_demo_data() -> None:
    result = StaleSnapshotCheck().run(
        ctx(
            snapshot=SnapshotFact(id=1, code="DEMO-V1", status="PUBLISHED", published_year=2026, is_demo=True),
            latest_published_snapshot=SnapshotFact(
                id=1, code="DEMO-V1", status="PUBLISHED", published_year=2026, is_demo=True
            ),
        )
    )
    assert any(issue.code == "DEMO_DATA" for issue in result.issues)


def test_stale_snapshot_flags_unpublished_and_old() -> None:
    draft = StaleSnapshotCheck().run(
        ctx(snapshot=SnapshotFact(id=1, code="X", status="DRAFT"), latest_published_snapshot=None)
    )
    assert any(issue.code == "SNAPSHOT_NOT_PUBLISHED" and issue.severity is HealthSeverity.ERROR for issue in draft.issues)

    old = StaleSnapshotCheck().run(
        ctx(
            snapshot=SnapshotFact(id=1, code="X", status="PUBLISHED", published_year=2023),
            latest_published_snapshot=SnapshotFact(id=1, code="X", status="PUBLISHED", published_year=2023),
            target_exam_year=2026,
        )
    )
    assert any(issue.code == "SNAPSHOT_TOO_OLD" for issue in old.issues)


def test_stale_snapshot_silent_when_current() -> None:
    assert StaleSnapshotCheck().run(ctx()).issues == ()


# ================= 11. HardPreferenceCheck =================


def test_hard_preference_check_catches_excluded_major_id() -> None:
    items = (fact(1, major_id=7),)
    result = HardPreferenceCheck().run(
        ctx(items=items, preference=PreferenceFact(hard_exclude_major_ids=frozenset({7})))
    )
    assert result.issues[0].code == "HARD_PREFERENCE_VIOLATED"
    assert result.issues[0].severity is HealthSeverity.ERROR


def test_hard_preference_check_catches_tuition_ceiling() -> None:
    items = (fact(1, tuition_fee=30000),)
    result = HardPreferenceCheck().run(
        ctx(items=items, preference=PreferenceFact(max_tuition=8000))
    )
    assert result.issues[0].code == "HARD_PREFERENCE_VIOLATED"


def test_hard_preference_check_catches_cooperative_and_university() -> None:
    items = (fact(1, cooperative="CHINESE_FOREIGN", university_id=9),)
    result = HardPreferenceCheck().run(
        ctx(
            items=items,
            preference=PreferenceFact(
                exclude_cooperative=True, hard_exclude_university_ids=frozenset({9})
            ),
        )
    )
    assert len(result.issues) == 2


def test_hard_preference_check_name_boundary() -> None:
    excluded = PreferenceFact(hard_exclude_major_names=("专业2",))
    assert HardPreferenceCheck().run(ctx(items=(fact(1, plan_name="专业2"),), preference=excluded)).issues
    assert (
        HardPreferenceCheck().run(
            ctx(items=(fact(1, plan_name="专业20"),), preference=excluded)
        ).issues == ()
    )


def test_hard_preference_check_silent_without_excludes() -> None:
    assert HardPreferenceCheck().run(ctx()).issues == ()


# ================= engine =================


def test_engine_runs_all_eleven_checks() -> None:
    report = HealthCheckEngine().run(ctx())
    assert len(report.results) == 11
    codes = [check.code for check in report.results]
    assert codes == [
        "ELIGIBILITY",
        "DUPLICATE",
        "APPLICATION_LIMIT",
        "UNKNOWN_REQUIREMENT",
        "RISK_DISTRIBUTION",
        "ORDERING",
        "LOW_CONFIDENCE",
        "SMALL_PLAN_COUNT",
        "PLAN_REDUCTION",
        "STALE_SNAPSHOT",
        "HARD_PREFERENCE",
    ]


def test_engine_score_groups_by_issue_class() -> None:
    """同一类问题只扣一次基础分，多出的条目按上限小额累加。

    真实数据下这是必须的：一份 96 条志愿的方案会触发 40 条 SMALL_PLAN_COUNT
    （山东 63% 的计划招生不足 5 人）。若逐条扣分，健康分恒为 0，0~100 的刻度
    就无法区分「方案有问题」和「方案正常」。
    """
    from app.healthcheck.types import (
        REPEAT_PENALTY,
        REPEAT_PENALTY_CAP,
        SEVERITY_PENALTY,
        HealthIssue,
    )

    # 三条同类：基础 15，外加 min(3, 2×1) = 2 → 83
    errors = [HealthIssue(code="E", severity=HealthSeverity.ERROR, message="x")] * 3
    expected = 100 - (SEVERITY_PENALTY[HealthSeverity.ERROR] + 2 * REPEAT_PENALTY)
    assert score_for(errors)[0] == expected

    # 累加封顶：超过 REPEAT_PENALTY_CAP 后，再多同类问题也不再额外扣分
    capped = 100 - (SEVERITY_PENALTY[HealthSeverity.ERROR] + REPEAT_PENALTY_CAP)
    many = [HealthIssue(code="E", severity=HealthSeverity.ERROR, message="x")] * 10
    assert score_for(many)[0] == capped, f"重复扣分应封顶在 {REPEAT_PENALTY_CAP}"
    lots = [HealthIssue(code="E", severity=HealthSeverity.ERROR, message="x")] * 40
    assert score_for(lots)[0] == capped, "第 40 条与第 10 条同类问题应同分"

    # 不同类各自计费
    mixed = [
        HealthIssue(code="A", severity=HealthSeverity.ERROR, message="x"),
        HealthIssue(code="B", severity=HealthSeverity.WARNING, message="y"),
    ]
    assert score_for(mixed)[0] == 100 - 15 - 5

    # 大量不同类的问题仍会触底，但不会为负
    flood = [
        HealthIssue(code=f"C{index}", severity=HealthSeverity.ERROR, message="x")
        for index in range(20)
    ]
    assert score_for(flood)[0] == 0, "健康分不得为负"


def test_plan_expansion_is_not_penalised() -> None:
    """招生计划扩张对考生有利，只应展示，不应扣分。"""
    from app.healthcheck.types import HealthIssue

    expanded = [
        HealthIssue(code="PLAN_EXPANDED", severity=HealthSeverity.INFO, message="扩招") for _ in range(29)
    ]
    assert score_for(expanded)[0] == 100
    assert score_for(expanded)[1]["infos"] == 29, "明细仍要如实计数，只是不计入扣分"

    # 缩减仍然扣分，两个方向不对称是刻意的
    reduced = [HealthIssue(code="PLAN_REDUCED", severity=HealthSeverity.WARNING, message="缩招")]
    assert score_for(reduced)[0] < 100


def test_engine_score_is_100_for_a_perfect_scheme() -> None:
    items = []
    position = 1
    for bucket, target in DEFAULT_RISK_TARGETS["BALANCED"].items():
        for _ in range(target):
            items.append(fact(position, risk_bucket=bucket))
            position += 1
    report = HealthCheckEngine().run(
        ctx(items=tuple(items), application_limit=96, target_exam_year=2026)
    )
    assert report.score == 100, report.as_dict()
    assert report.passed is True


def test_engine_report_has_disclaimer_and_no_probability() -> None:
    import json

    report = HealthCheckEngine().run(ctx())
    payload = report.as_dict()
    assert payload["disclaimer"] == HEALTH_SCORE_DISCLAIMER
    assert "不是录取概率" in payload["disclaimer"]
    assert "录取概率" in payload["disclaimer"]  # only inside the negation
    serialised = json.dumps(payload, ensure_ascii=False)
    assert "probability" not in serialised.lower()


def test_engine_report_lists_every_check() -> None:
    report = HealthCheckEngine().run(ctx())
    payload = report.as_dict()
    assert len(payload["checks"]) == 11
    assert payload["error_count"] == report.error_count
    assert payload["summary"]["note"].startswith("本报告只做诊断")


def test_engine_never_mutates_the_context() -> None:
    """体检只报告问题，不得擅自修改方案。"""
    original = ctx()
    snapshot_before = copy.deepcopy(original)
    report = HealthCheckEngine().run(original)
    assert original == snapshot_before
    assert report.results, "应当产生报告"
    # items are frozen dataclasses: mutation is impossible by construction
    import dataclasses

    with pytest.raises(dataclasses.FrozenInstanceError):
        original.items[0].acs = 1.0  # type: ignore[misc]


def test_engine_is_deterministic() -> None:
    first = HealthCheckEngine().run(ctx()).as_dict()
    second = HealthCheckEngine().run(ctx()).as_dict()
    assert first == second


def test_engine_handles_empty_scheme() -> None:
    report = HealthCheckEngine().run(ctx(items=()))
    assert report.summary["item_count"] == 0
    # an empty scheme is allowed and simply reports unused slots
    assert any(issue.code == "LIMIT_UNUSED" for issue in report.issues)


def test_check_result_counts_and_worst_severity() -> None:
    from app.healthcheck.types import HealthIssue

    result = CheckResult(
        code="X",
        title="T",
        issues=(
            HealthIssue(code="a", severity=HealthSeverity.INFO, message="i"),
            HealthIssue(code="b", severity=HealthSeverity.ERROR, message="e"),
        ),
    )
    assert result.worst_severity is HealthSeverity.ERROR
    assert result.passed is False
    assert result.counts() == {"errors": 1, "warnings": 0, "infos": 1}
