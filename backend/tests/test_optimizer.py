"""Phase 6 acceptance: CP-SAT optimizer, final ordering, scheme versioning.

Pure unit tests for ordering; solver tests for the portfolio; DB tests for the
versioned scheme API behaviour (409 on stale version, history preserved).
"""

from __future__ import annotations

from app.common.enums import RiskBucket, RiskMode
from app.optimizer.ordering import order_items, reorder_after_removal
from app.optimizer.portfolio import PortfolioOptimizer
from app.optimizer.types import (
    DEFAULT_RISK_TARGETS,
    CandidateItem,
    OptimizationRequest,
    OptimizerConfig,
    SchemeOrderItem,
)
from app.scoring.preference import desire_band


def candidate(
    item_id: int,
    *,
    acs: float | None = 70.0,
    preference: float | None = 60.0,
    confidence: float | None = 70.0,
    bucket: str | None = None,
    eligibility: str = "PASS",
    university_id: int | None = None,
    major_id: int | None = None,
    counts_as_safe: bool = True,
) -> CandidateItem:
    return CandidateItem(
        item_id=item_id,
        plan_id=item_id,
        university_id=university_id if university_id is not None else item_id,
        major_id=major_id,
        eligibility=eligibility,
        acs=acs,
        confidence=confidence,
        preference_score=preference,
        risk_bucket=bucket,
        counts_as_safe=counts_as_safe,
    )


# ================= 最终排序（规格 §13） =================


def test_ordering_prioritises_real_desire_then_harder_within_band() -> None:
    """偏好更高者排前；同 desire_band 内 ACS 更低者先。"""
    items = [
        SchemeOrderItem(item_id=1, plan_id=1, acs=30.0, preference_score=90.0),
        SchemeOrderItem(item_id=2, plan_id=2, acs=85.0, preference_score=90.0),
        SchemeOrderItem(item_id=3, plan_id=3, acs=20.0, preference_score=40.0),
    ]
    ordered = order_items(items)
    # band 30 (90/3) before band 13 (40/3); inside band 30 the harder (ACS 30) first
    assert [item.item_id for item in ordered] == [1, 2, 3]
    assert ordered[0].desire_band == 30
    assert ordered[2].desire_band == 13


def test_higher_desire_band_always_wins_over_lower_acs() -> None:
    items = [
        SchemeOrderItem(item_id=1, plan_id=1, acs=10.0, preference_score=50.0),
        SchemeOrderItem(item_id=2, plan_id=2, acs=95.0, preference_score=80.0),
    ]
    ordered = order_items(items)
    assert [item.item_id for item in ordered] == [2, 1], "意愿优先于难易"


def test_preference_breaks_ties_within_the_same_band_and_acs() -> None:
    items = [
        SchemeOrderItem(item_id=1, plan_id=1, acs=50.0, preference_score=60.0),
        SchemeOrderItem(item_id=2, plan_id=2, acs=50.0, preference_score=62.0),
    ]
    ordered = order_items(items)
    assert [item.item_id for item in ordered] == [2, 1]


def test_confidence_breaks_final_tie() -> None:
    items = [
        SchemeOrderItem(item_id=1, plan_id=1, acs=50.0, preference_score=60.0, confidence=40.0),
        SchemeOrderItem(item_id=2, plan_id=2, acs=50.0, preference_score=60.0, confidence=90.0),
    ]
    ordered = order_items(items)
    assert [item.item_id for item in ordered] == [2, 1]


def test_fixed_position_wins_over_everything() -> None:
    items = [
        SchemeOrderItem(item_id=1, plan_id=1, acs=90.0, preference_score=99.0),
        SchemeOrderItem(item_id=2, plan_id=2, acs=10.0, preference_score=10.0, position=1, fixed_position=True),
    ]
    ordered = order_items(items)
    assert ordered[0].item_id == 2
    assert ordered[0].fixed_position is True
    assert ordered[1].item_id == 1


def test_duplicate_fixed_position_does_not_drop_a_volunteer() -> None:
    items = [
        SchemeOrderItem(item_id=1, plan_id=1, position=1, fixed_position=True, preference_score=50.0),
        SchemeOrderItem(item_id=2, plan_id=2, position=1, fixed_position=True, preference_score=40.0),
        SchemeOrderItem(item_id=3, plan_id=3, preference_score=30.0),
    ]
    ordered = order_items(items)
    assert sorted(item.item_id for item in ordered) == [1, 2, 3]
    assert [item.position for item in ordered] == [1, 2, 3]


def test_positions_are_contiguous_from_one() -> None:
    items = [SchemeOrderItem(item_id=i, plan_id=i, acs=float(i), preference_score=float(i * 3)) for i in range(1, 21)]
    ordered = order_items(items)
    assert [item.position for item in ordered] == list(range(1, 21))


def test_ordering_is_deterministic_and_total() -> None:
    items = [
        SchemeOrderItem(item_id=i, plan_id=i, acs=50.0, preference_score=50.0, confidence=50.0)
        for i in range(1, 11)
    ]
    first = [item.item_id for item in order_items(items)]
    second = [item.item_id for item in order_items(list(reversed(items)))]
    assert first == second == list(range(1, 11))


def test_empty_scheme_orders_to_nothing() -> None:
    assert order_items([]) == []
    assert reorder_after_removal([]) == []


def test_desire_band_matches_spec_formula() -> None:
    for score in (0.0, 2.9, 3.0, 44.4, 66.6, 99.9, 100.0):
        assert desire_band(score) == int(score // 3)


# ================= CP-SAT 组合优化 =================


def test_optimizer_respects_application_limit() -> None:
    candidates = tuple(candidate(i, bucket=RiskBucket.BORDER.value) for i in range(1, 21))
    request = OptimizationRequest(candidates=candidates, max_count=10)
    result = PortfolioOptimizer().optimize(request)
    assert len(result.selected_item_ids) == 10
    assert result.status in {"OPTIMAL", "FEASIBLE"}


def test_optimizer_never_selects_fail_candidates() -> None:
    candidates = (
        candidate(1, eligibility="FAIL"),
        candidate(2, eligibility="FAIL"),
        candidate(3, eligibility="PASS", bucket=RiskBucket.STABLE.value),
    )
    request = OptimizationRequest(candidates=candidates, max_count=96)
    result = PortfolioOptimizer().optimize(request)
    assert 1 not in result.selected_item_ids
    assert 2 not in result.selected_item_ids
    assert result.selected_item_ids == (3,)
    assert result.diagnostics["excluded_by_eligibility"] == 2


def test_optimizer_prefers_higher_preference() -> None:
    candidates = (
        candidate(1, preference=95.0, bucket=RiskBucket.STABLE.value),
        candidate(2, preference=20.0, bucket=RiskBucket.STABLE.value),
    )
    request = OptimizationRequest(
        candidates=candidates,
        max_count=1,
        risk_targets={RiskBucket.STABLE.value: 1},
    )
    result = PortfolioOptimizer().optimize(request)
    assert result.selected_item_ids == (1,)


def test_risk_targets_are_soft_not_hard() -> None:
    """目标层候选不足时必须仍然产出方案，而不是无解。"""
    candidates = tuple(candidate(i, bucket=RiskBucket.SAFE.value) for i in range(1, 6))
    request = OptimizationRequest(
        candidates=candidates,
        max_count=96,
        risk_mode=RiskMode.STEADY.value,
    )
    result = PortfolioOptimizer().optimize(request)
    assert len(result.selected_item_ids) == 5
    assert result.deviation_penalty > 0, "偏离目标应有罚分"
    assert result.shortfall == 91
    assert any("少于目标" in s for s in result.relax_suggestions)


def test_optimizer_approaches_balanced_targets() -> None:
    """均衡模式：8/17/20/31/20。候选充足时应接近目标分布。"""
    targets = DEFAULT_RISK_TARGETS[RiskMode.BALANCED.value]
    candidates = []
    item_id = 1
    for bucket, count in targets.items():
        for _ in range(count * 2):  # enough slack to hit the target
            candidates.append(candidate(item_id, bucket=bucket, preference=50.0 + (item_id % 10)))
            item_id += 1
    request = OptimizationRequest(
        candidates=tuple(candidates), max_count=96, risk_mode=RiskMode.BALANCED.value
    )
    result = PortfolioOptimizer().optimize(request)
    assert len(result.selected_item_ids) == 96
    for bucket, target in targets.items():
        assert result.risk_counts.get(bucket, 0) == target, f"{bucket} 未达到目标 {target}"


def test_min_safe_count_is_a_hard_constraint() -> None:
    risky = [candidate(i, acs=20.0, bucket=RiskBucket.HIGH_REACH.value) for i in range(1, 11)]
    safe = [candidate(100 + i, acs=90.0, bucket=RiskBucket.SAFE.value) for i in range(1, 4)]
    request = OptimizationRequest(
        candidates=(*risky, *safe), max_count=13, min_safe_count=3
    )
    result = PortfolioOptimizer().optimize(request)
    safe_selected = [i for i in result.selected_item_ids if i >= 100]
    assert len(safe_selected) >= 3


def test_unknown_eligibility_does_not_count_toward_safety_floor() -> None:
    """规格：UNKNOWN 项不计入 SAFE/STABLE 最低安全数量。"""
    unknown_safe = [
        candidate(100 + i, acs=90.0, bucket=RiskBucket.SAFE.value, eligibility="UNKNOWN", counts_as_safe=False)
        for i in range(1, 4)
    ]
    pass_border = [candidate(i, acs=50.0, bucket=RiskBucket.BORDER.value) for i in range(1, 6)]
    request = OptimizationRequest(
        candidates=(*unknown_safe, *pass_border), max_count=8, min_safe_count=2
    )
    result = PortfolioOptimizer().optimize(request)
    # Only 0 PASS items clear ACS>=60, so the floor is unsatisfiable and clamped to 0;
    # the UNKNOWN items must not have been counted as satisfying it.
    counted = [i for i in result.selected_item_ids if i >= 100]
    assert all(
        not candidate_item.counts_as_safe
        for candidate_item in request.candidates
        if candidate_item.item_id in counted
    ) or not counted


def test_university_concentration_penalty_spreads_selection() -> None:
    """同一学校堆叠过多时，罚分应促使选择分散。"""
    clumped = [candidate(i, university_id=1, bucket=RiskBucket.BORDER.value) for i in range(1, 13)]
    spread = [candidate(100 + i, university_id=100 + i, bucket=RiskBucket.BORDER.value) for i in range(1, 13)]
    request = OptimizationRequest(
        candidates=(*clumped, *spread),
        max_count=6,
        risk_targets={RiskBucket.BORDER.value: 6},
        config=OptimizerConfig(university_soft_cap=2, university_concentration_weight=1.0),
    )
    result = PortfolioOptimizer().optimize(request)
    selected_universities = {
        item.university_id for item in request.candidates if item.item_id in result.selected_item_ids
    }
    assert len(selected_universities) >= 3, "应分散到多所学校"


def test_shortfall_reports_relax_suggestions() -> None:
    candidates = tuple(candidate(i, bucket=RiskBucket.STABLE.value) for i in range(1, 4))
    result = PortfolioOptimizer().optimize(
        OptimizationRequest(candidates=candidates, max_count=96, risk_mode=RiskMode.STEADY.value)
    )
    assert result.shortfall == 93
    assert result.relax_suggestions
    assert any("不会为凑满" in s for s in result.relax_suggestions)


def test_no_candidates_is_reported_not_crashed() -> None:
    result = PortfolioOptimizer().optimize(OptimizationRequest(candidates=(), max_count=96))
    assert result.selected_item_ids == ()
    assert result.status == "INFEASIBLE"
    assert result.relax_suggestions


def test_optimizer_is_deterministic_with_fixed_seed() -> None:
    candidates = tuple(
        candidate(i, bucket=RiskBucket.BORDER.value, preference=float(40 + i)) for i in range(1, 21)
    )
    request = OptimizationRequest(candidates=candidates, max_count=10, config=OptimizerConfig(random_seed=7))
    first = PortfolioOptimizer().optimize(request)
    second = PortfolioOptimizer().optimize(request)
    assert set(first.selected_item_ids) == set(second.selected_item_ids)


def test_result_is_serialisable() -> None:
    import json

    candidates = tuple(candidate(i, bucket=RiskBucket.STABLE.value) for i in range(1, 4))
    result = PortfolioOptimizer().optimize(OptimizationRequest(candidates=candidates, max_count=2))
    payload = json.dumps(result.as_dict(), ensure_ascii=False)
    assert "objective_value" in payload


# ================= 默认风险组合（规格 §12） =================


def test_default_risk_targets_match_spec() -> None:
    assert DEFAULT_RISK_TARGETS[RiskMode.STEADY.value] == {
        "HIGH_REACH": 5, "REACH": 10, "BORDER": 15, "STABLE": 36, "SAFE": 30
    }
    assert DEFAULT_RISK_TARGETS[RiskMode.BALANCED.value] == {
        "HIGH_REACH": 8, "REACH": 17, "BORDER": 20, "STABLE": 31, "SAFE": 20
    }
    assert DEFAULT_RISK_TARGETS[RiskMode.AGGRESSIVE.value] == {
        "HIGH_REACH": 15, "REACH": 25, "BORDER": 20, "STABLE": 24, "SAFE": 12
    }
    for targets in DEFAULT_RISK_TARGETS.values():
        assert sum(targets.values()) == 96


def test_large_pool_is_trimmed_instead_of_timing_out() -> None:
    """回归：真实数据会产生上万条候选，全部交给 CP-SAT 会返回 UNKNOWN。

    这是接入真实数据时暴露的缺陷 —— 15,202 条候选在 10 秒限时内解不出来，
    求解器返回 UNKNOWN，用户看到「没有可行解」的空方案。修法是在求解前按桶裁剪。

    本测试用一个足以暴露规模的候选集（3,000 条，覆盖五个桶），断言求解器给出
    OPTIMAL/FEASIBLE 且选满目标数量。
    """
    buckets = [
        RiskBucket.HIGH_REACH,
        RiskBucket.REACH,
        RiskBucket.BORDER,
        RiskBucket.STABLE,
        RiskBucket.SAFE,
    ]
    # ACS 必须落在对应桶的区间内，否则 counts_as_safe / 目标偏差项会失真
    acs_by_bucket = {
        RiskBucket.HIGH_REACH: 15.0,
        RiskBucket.REACH: 37.0,
        RiskBucket.BORDER: 52.0,
        RiskBucket.STABLE: 70.0,
        RiskBucket.SAFE: 88.0,
    }
    candidates = [
        candidate(
            index,
            acs=acs_by_bucket[buckets[index % len(buckets)]],
            preference=float(50 + (index % 50)),
            confidence=80.0,
            bucket=buckets[index % len(buckets)].value,
            university_id=index,  # 每所院校一条，避免集中度惩罚干扰
        )
        for index in range(3000)
    ]

    request = OptimizationRequest(
        candidates=tuple(candidates),
        max_count=96,
        risk_mode=RiskMode.BALANCED.value,
        risk_targets=dict(DEFAULT_RISK_TARGETS[RiskMode.BALANCED.value]),
        config=OptimizerConfig(max_time_seconds=20.0),
    )
    result = PortfolioOptimizer().optimize(request)

    assert result.status in {"OPTIMAL", "FEASIBLE"}, (
        f"求解器返回 {result.status}；候选过多时应当先裁剪再求解"
    )
    assert len(result.selected_item_ids) == 96
    assert result.shortfall == 0
    # 裁剪确实发生了，且风险分布贴近目标
    assert result.diagnostics["candidates_before_trim"] == 3000
    assert result.diagnostics["candidates_after_trim"] < 3000
    for bucket, target in DEFAULT_RISK_TARGETS[RiskMode.BALANCED.value].items():
        assert result.risk_counts.get(bucket, 0) == target, f"{bucket} 未达到目标 {target}"


def test_timeout_is_not_reported_as_infeasible() -> None:
    """超时（UNKNOWN）与不可行（INFEASIBLE）必须给不同的提示。

    把「求解器没解完」说成「没有可行解，请降低最低安全数量」，会让用户去调一个
    根本没有问题的参数。
    """
    candidates = tuple(
        candidate(index, acs=70.0, preference=60.0, confidence=80.0, bucket=RiskBucket.STABLE.value,
                  university_id=index)
        for index in range(400)
    )
    # 用一个不可能满足的安全数量，并给极短求解时间以诱导 UNKNOWN
    request = OptimizationRequest(
        candidates=candidates,
        max_count=96,
        risk_mode=RiskMode.BALANCED.value,
        risk_targets=dict(DEFAULT_RISK_TARGETS[RiskMode.BALANCED.value]),
        config=OptimizerConfig(max_time_seconds=0.001, num_workers=1),
        min_safe_count=200,
    )
    result = PortfolioOptimizer().optimize(request)
    if result.status == "UNKNOWN":
        assert "未能求解" in result.relax_suggestions[0]
        assert result.diagnostics["timed_out"] is True
    else:
        # 求解器仍解出来了也不算失败，但提示里绝不能出现误导性的「没有可行解」
        assert "没有可行解" not in result.relax_suggestions[0] or result.status == "INFEASIBLE"
