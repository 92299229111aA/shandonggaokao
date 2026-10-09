"""Recommendation pipeline input/output types (pure data)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.common.enums import RiskBucket, risk_bucket_for
from app.rules.result import EligibilityResult
from app.rules.types import TargetPlan
from app.scoring.preference import PreferenceEvaluation
from app.scoring.types import ACSEvaluation, HistoryPoint

# Candidate-pool caps per risk bucket (docs/ALGORITHM.md §10)
DEFAULT_POOL_LIMITS: dict[str, int] = {
    RiskBucket.HIGH_REACH.value: 120,
    RiskBucket.REACH.value: 120,
    RiskBucket.BORDER.value: 120,
    RiskBucket.STABLE.value: 150,
    RiskBucket.SAFE.value: 150,
}


@dataclass(slots=True)
class PlanBundle:
    """A plan plus its rule input, eligibility verdict, and history.

    Deliberately mutable: each pipeline stage fills in one more field
    (history -> acs -> preference) for the same plan.
    """

    plan_row: dict[str, Any]
    target: TargetPlan
    eligibility: EligibilityResult
    history: tuple[HistoryPoint, ...] = ()
    acs: ACSEvaluation | None = None
    preference: PreferenceEvaluation | None = None

    @property
    def plan_id(self) -> int:
        return int(self.plan_row["plan_id"])

    @property
    def risk_bucket(self) -> RiskBucket | None:
        return None if self.acs is None else risk_bucket_for(self.acs.acs)

    @property
    def poolable(self) -> bool:
        return self.eligibility.can_be_pooled and self.acs is not None


@dataclass(slots=True)
class PipelineStats:
    """Funnel counters persisted on the session so a run can be audited."""

    plans_scanned: int = 0
    eligibility_pass: int = 0
    eligibility_unknown: int = 0
    eligibility_fail: int = 0
    hard_excluded: int = 0
    scored: int = 0
    no_history: int = 0
    pooled: int = 0
    truncated_by_bucket: dict[str, int] = field(default_factory=dict)
    pool_by_bucket: dict[str, int] = field(default_factory=dict)
    pool_limits: dict[str, int] = field(default_factory=dict)
    locked_kept: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "plans_scanned": self.plans_scanned,
            "eligibility_pass": self.eligibility_pass,
            "eligibility_unknown": self.eligibility_unknown,
            "eligibility_fail": self.eligibility_fail,
            "hard_excluded": self.hard_excluded,
            "scored": self.scored,
            "no_history": self.no_history,
            "pooled": self.pooled,
            "truncated_by_bucket": dict(self.truncated_by_bucket),
            "pool_by_bucket": dict(self.pool_by_bucket),
            "pool_limits": dict(self.pool_limits),
            "locked_kept": self.locked_kept,
            "notes": list(self.notes),
        }


@dataclass(slots=True)
class PipelineResult:
    session_id: int
    stats: PipelineStats
    bundles: list[PlanBundle] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    """Knobs that must be part of the recorded model version, not hidden."""

    model_version: str = "acs-v1"
    pool_limits: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_POOL_LIMITS))
    # Safety valve for the persisted audit trail of FAIL rows
    persist_fail_sample: int = 200

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_version": self.model_version,
            "pool_limits": dict(self.pool_limits),
            "persist_fail_sample": self.persist_fail_sample,
        }


__all__ = [
    "DEFAULT_POOL_LIMITS",
    "PipelineConfig",
    "PipelineResult",
    "PipelineStats",
    "PlanBundle",
]
