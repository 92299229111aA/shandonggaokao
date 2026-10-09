"""Pure types for the portfolio optimizer and the final ordering step.

Kept free of OR-Tools and ORM so ordering can be unit-tested on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.common.enums import RiskBucket, RiskMode

# Default risk targets (docs/ALGORITHM.md §12): 高冲 / 冲 / 边界 / 稳 / 保
DEFAULT_RISK_TARGETS: dict[str, dict[str, int]] = {
    RiskMode.STEADY.value: {
        RiskBucket.HIGH_REACH.value: 5,
        RiskBucket.REACH.value: 10,
        RiskBucket.BORDER.value: 15,
        RiskBucket.STABLE.value: 36,
        RiskBucket.SAFE.value: 30,
    },
    RiskMode.BALANCED.value: {
        RiskBucket.HIGH_REACH.value: 8,
        RiskBucket.REACH.value: 17,
        RiskBucket.BORDER.value: 20,
        RiskBucket.STABLE.value: 31,
        RiskBucket.SAFE.value: 20,
    },
    RiskMode.AGGRESSIVE.value: {
        RiskBucket.HIGH_REACH.value: 15,
        RiskBucket.REACH.value: 25,
        RiskBucket.BORDER.value: 20,
        RiskBucket.STABLE.value: 24,
        RiskBucket.SAFE.value: 12,
    },
}

# 最低 ACS 门槛背后的桶划分（用于"至少 N 个 ACS≥60 / ≥80"）
ACS_STABLE_FLOOR = 60.0
ACS_SAFE_FLOOR = 80.0


@dataclass(frozen=True, slots=True)
class OptimizerConfig:
    """Weights and hard floors. Every value is recorded on the model version."""

    version: str = "optimizer-v1"
    # 目标函数权重
    preference_weight: float = 1.0
    confidence_weight: float = 0.10
    deviation_weight: float = 0.60
    university_concentration_weight: float = 0.05
    major_concentration_weight: float = 0.05
    # 硬约束
    min_acs_stable: int | None = None
    min_acs_safe: int | None = None
    # 同一学校/专业的软上限（只影响集中度罚分，不阻止入选）
    university_soft_cap: int = 12
    major_soft_cap: int = 8
    # 求解预算
    max_time_seconds: float = 10.0
    num_workers: int = 4
    random_seed: int = 0
    # 每个风险桶保留「目标数量 × 该倍数」个候选交给求解器。
    # 真实数据会产生约 1.5 万个合格候选，全部作为布尔变量交给 CP-SAT 在限时内解不出来
    # （返回 UNKNOWN，用户看到空方案）。所以按桶裁剪后再求解。
    # 想要更宽的搜索空间就调大它，代价是求解时间。
    pool_trim_headroom: int = 8

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "preference_weight": self.preference_weight,
            "confidence_weight": self.confidence_weight,
            "deviation_weight": self.deviation_weight,
            "university_concentration_weight": self.university_concentration_weight,
            "major_concentration_weight": self.major_concentration_weight,
            "min_acs_stable": self.min_acs_stable,
            "min_acs_safe": self.min_acs_safe,
            "university_soft_cap": self.university_soft_cap,
            "major_soft_cap": self.major_soft_cap,
            "max_time_seconds": self.max_time_seconds,
            "random_seed": self.random_seed,
        }


@dataclass(frozen=True, slots=True)
class CandidateItem:
    """A poolable volunteer as the optimizer sees it."""

    item_id: int
    plan_id: int
    university_id: int
    major_id: int | None
    eligibility: str
    acs: float | None
    confidence: float | None
    preference_score: float | None
    risk_bucket: str | None
    locked: bool = False
    # UNKNOWN eligibility can be pooled but never counts toward a safety floor
    counts_as_safe: bool = True

    @property
    def is_fail(self) -> bool:
        return self.eligibility == "FAIL"

    def normalized_preference(self) -> float:
        value = self.preference_score if self.preference_score is not None else 50.0
        return max(0.0, min(1.0, value / 100.0))

    def normalized_confidence(self) -> float:
        value = self.confidence if self.confidence is not None else 0.0
        return max(0.0, min(1.0, value / 100.0))

    def acs_value(self) -> float:
        return self.acs if self.acs is not None else 50.0


@dataclass(frozen=True, slots=True)
class OptimizationRequest:
    candidates: tuple[CandidateItem, ...]
    max_count: int
    risk_mode: str = RiskMode.BALANCED.value
    risk_targets: dict[str, int] = field(default_factory=dict)
    config: OptimizerConfig = field(default_factory=OptimizerConfig)
    # 最低安全数量（硬约束）：ACS>=60 且 eligibility 可计入安全
    min_safe_count: int | None = None
    # 用户锁定 / 必须保留的条目：作为硬约束进入模型，而不是求解后再追加
    required_item_ids: frozenset[int] = frozenset()

    def targets_for(self, mode: str | None = None) -> dict[str, int]:
        if self.risk_targets:
            return dict(self.risk_targets)
        return dict(DEFAULT_RISK_TARGETS.get(mode or self.risk_mode, DEFAULT_RISK_TARGETS[RiskMode.BALANCED.value]))


@dataclass(slots=True)
class OptimizationResult:
    """Mutable so the caller can add user-locked items and re-check the shortfall."""

    selected_item_ids: tuple[int, ...]
    objective_value: float
    status: str
    risk_counts: dict[str, int] = field(default_factory=dict)
    target_counts: dict[str, int] = field(default_factory=dict)
    deviation_penalty: float = 0.0
    university_penalty: float = 0.0
    major_penalty: float = 0.0
    preference_utility: float = 0.0
    confidence_bonus: float = 0.0
    shortfall: int = 0
    relax_suggestions: tuple[str, ...] = ()
    solver_wall_time: float = 0.0
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "selected_count": len(self.selected_item_ids),
            "objective_value": round(self.objective_value, 3),
            "status": self.status,
            "risk_counts": dict(self.risk_counts),
            "target_counts": dict(self.target_counts),
            "deviation_penalty": round(self.deviation_penalty, 3),
            "university_penalty": round(self.university_penalty, 3),
            "major_penalty": round(self.major_penalty, 3),
            "preference_utility": round(self.preference_utility, 3),
            "confidence_bonus": round(self.confidence_bonus, 3),
            "shortfall": self.shortfall,
            "relax_suggestions": list(self.relax_suggestions),
            "solver_wall_time": round(self.solver_wall_time, 3),
            "diagnostics": dict(self.diagnostics),
        }


@dataclass(frozen=True, slots=True)
class SchemeOrderItem:
    """One volunteer as the ordering step sees it."""

    item_id: int
    plan_id: int
    position: int | None = None
    locked: bool = False
    fixed_position: bool = False
    acs: float | None = None
    preference_score: float | None = None
    confidence: float | None = None
    eligibility: str = "PASS"


@dataclass(frozen=True, slots=True)
class OrderedItem:
    item_id: int
    plan_id: int
    position: int
    fixed_position: bool
    locked: bool
    desire_band: int
    acs: float
    preference_score: float
    confidence: float
    eligibility: str = "PASS"

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "plan_id": self.plan_id,
            "position": self.position,
            "fixed_position": self.fixed_position,
            "locked": self.locked,
            "desire_band": self.desire_band,
            "acs": round(self.acs, 3),
            "preference_score": round(self.preference_score, 3),
            "confidence": round(self.confidence, 3),
            "eligibility": self.eligibility,
        }


__all__ = [
    "ACS_SAFE_FLOOR",
    "ACS_STABLE_FLOOR",
    "DEFAULT_RISK_TARGETS",
    "CandidateItem",
    "OptimizationRequest",
    "OptimizationResult",
    "OptimizerConfig",
    "OrderedItem",
    "SchemeOrderItem",
]
