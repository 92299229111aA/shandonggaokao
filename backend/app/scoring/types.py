"""ACS parameters and pure input/output types.

Every constant here is published in docs/ALGORITHM.md and mirrored into
`recommendation.model_versions.parameters`, so a result can always be explained
and replayed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ACSParameters:
    """Formula constants. Changing any of these requires a new model_version."""

    version: str = "acs-v1"
    # 时间衰减半衰期（年）
    half_life_years: float = 1.5
    # d_y 的截断范围
    d_clip: float = 0.30
    # HistoryACS 的增益与尺度
    history_gain: float = 35.0
    history_scale: float = 0.07
    # 计划调整
    plan_gain: float = 7.0
    plan_scale: float = 0.40
    # 趋势调整
    trend_gain: float = 6.0
    trend_scale: float = 0.04

    # ---- Confidence 权重（和为 1）----
    w_history_coverage: float = 0.25
    w_freshness: float = 0.15
    w_series_match: float = 0.20
    w_source_quality: float = 0.15
    w_volatility: float = 0.15
    w_plan_quality: float = 0.10
    # 覆盖度饱和所需的年份数
    coverage_full_years: int = 3
    # 波动稳定项的特征尺度（以 ln(q) 的标准差计）
    volatility_scale: float = 0.10
    # 招生人数质量：达到该人数即视为满分
    plan_quality_reference: int = 30

    @property
    def decay_lambda(self) -> float:
        return math.log(2) / self.half_life_years

    def weights_sum(self) -> float:
        return (
            self.w_history_coverage
            + self.w_freshness
            + self.w_series_match
            + self.w_source_quality
            + self.w_volatility
            + self.w_plan_quality
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "half_life_years": self.half_life_years,
            "d_clip": self.d_clip,
            "history_gain": self.history_gain,
            "history_scale": self.history_scale,
            "plan_gain": self.plan_gain,
            "plan_scale": self.plan_scale,
            "trend_gain": self.trend_gain,
            "trend_scale": self.trend_scale,
            "weights": {
                "history_coverage": self.w_history_coverage,
                "freshness": self.w_freshness,
                "series_match": self.w_series_match,
                "source_quality": self.w_source_quality,
                "volatility": self.w_volatility,
                "plan_quality": self.w_plan_quality,
            },
            "coverage_full_years": self.coverage_full_years,
            "volatility_scale": self.volatility_scale,
            "plan_quality_reference": self.plan_quality_reference,
        }


@dataclass(frozen=True, slots=True)
class HistoryPoint:
    """One year of history for a program series.

    `min_rank` is the 投档/录取最低位次 and `ranked_candidates` the candidate
    population of that year — the pair is what makes years comparable.
    """

    exam_year: int
    min_rank: int | None
    ranked_candidates: int | None
    plan_count: int | None = None
    source_quality: str = "DEMO"
    # Series-match quality for this specific year's link to the series
    series_match_quality: str = "EXACT"
    min_score: int | None = None
    admitted_count: int | None = None

    @property
    def usable(self) -> bool:
        return bool(self.min_rank and self.min_rank > 0 and self.ranked_candidates and self.ranked_candidates > 0)

    def q(self) -> float | None:
        if not self.usable:
            return None
        assert self.min_rank is not None and self.ranked_candidates is not None
        return self.min_rank / self.ranked_candidates

    def d(self, q_c: float, clip: float) -> float | None:
        q_y = self.q()
        if q_y is None or q_c <= 0:
            return None
        return clip_value(math.log(q_y / q_c), clip)


@dataclass(frozen=True, slots=True)
class ConfidenceBreakdown:
    """Explainable components of the data-confidence score (0..1 each)."""

    history_coverage: float
    freshness: float
    series_match: float
    source_quality: float
    volatility: float
    plan_quality: float

    def weighted(self, params: ACSParameters) -> float:
        total = params.weights_sum()
        value = (
            params.w_history_coverage * self.history_coverage
            + params.w_freshness * self.freshness
            + params.w_series_match * self.series_match
            + params.w_source_quality * self.source_quality
            + params.w_volatility * self.volatility
            + params.w_plan_quality * self.plan_quality
        )
        return value / total if total > 0 else 0.0

    def as_dict(self) -> dict[str, float]:
        return {
            "history_coverage": round(self.history_coverage, 4),
            "freshness": round(self.freshness, 4),
            "series_match": round(self.series_match, 4),
            "source_quality": round(self.source_quality, 4),
            "volatility": round(self.volatility, 4),
            "plan_quality": round(self.plan_quality, 4),
        }


@dataclass(frozen=True, slots=True)
class ACSEvaluation:
    """Full scoring result plus the evidence needed to explain it."""

    acs: float
    base_acs: float
    confidence: float
    history_acs: float
    plan_adjustment: float
    trend_adjustment: float
    weighted_mean_d: float | None
    q_candidate: float
    confidence_parts: ConfidenceBreakdown
    years_used: tuple[int, ...] = ()
    history_points: int = 0
    warnings: tuple[str, ...] = field(default_factory=tuple)
    parameters_version: str = "acs-v1"

    def as_dict(self) -> dict[str, Any]:
        return {
            "acs": round(self.acs, 3),
            "base_acs": round(self.base_acs, 3),
            "confidence": round(self.confidence, 3),
            "history_acs": round(self.history_acs, 3),
            "plan_adjustment": round(self.plan_adjustment, 3),
            "trend_adjustment": round(self.trend_adjustment, 3),
            "weighted_mean_d": None if self.weighted_mean_d is None else round(self.weighted_mean_d, 5),
            "q_candidate": round(self.q_candidate, 8),
            "confidence_parts": self.confidence_parts.as_dict(),
            "years_used": list(self.years_used),
            "history_points": self.history_points,
            "warnings": list(self.warnings),
            "parameters_version": self.parameters_version,
            "disclaimer": "ACS 是录取安全指数（0-100），不是录取概率。",
        }


def clip_value(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
