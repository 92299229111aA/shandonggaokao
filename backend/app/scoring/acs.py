"""ACS (录取安全指数) and data Confidence.

ACS answers exactly one question: *how safe is this plan given the historical
record?* It is a 0-100 index. It is **not** a calibrated admission probability and
must never be presented as one.

Formula (docs/ALGORITHM.md §1-§8):

    q_y = historical_min_rank / historical_ranked_candidates
    q_c = candidate_rank / current_ranked_candidates
    d_y = clip(ln(q_y / q_c), -d_clip, d_clip)
    w_y = exp(-lambda * age_y), lambda = ln(2)/1.5
    M   = weighted_mean(d_y)
    HistoryACS = 50 + 35 * tanh(M / 0.07)
    P   = ln((plan_current+1) / (median(plan_history)+1));  PlanAdj = 7 * tanh(P/0.40)
    T   = weighted regression slope of ln(q_y) over year;   TrendAdj = 6 * tanh(T/0.04)
    BaseACS = clamp(HistoryACS + PlanAdj + TrendAdj, 0, 100)
    ACS = 50 + (BaseACS - 50) * Confidence
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence

from app.common.enums import RiskBucket, risk_bucket_for
from app.scoring.types import (
    ACSEvaluation,
    ACSParameters,
    ConfidenceBreakdown,
    HistoryPoint,
    clamp,
)

# Series-match quality -> trust in the cross-year link
SERIES_MATCH_SCORES: dict[str, float] = {
    "EXACT": 1.0,
    "RENAMED": 0.85,
    "MERGED": 0.70,
    "SPLIT": 0.60,
    "FUZZY": 0.40,
    "NONE": 0.10,
}

# Provenance quality -> trust in the numbers themselves
SOURCE_QUALITY_SCORES: dict[str, float] = {
    "OFFICIAL": 1.0,
    "UNIVERSITY": 0.85,
    "THIRD_PARTY": 0.55,
    "DEMO": 0.50,
}


class ACSService:
    """Stateless: the same inputs always produce the same ACS."""

    def __init__(self, params: ACSParameters | None = None) -> None:
        self.params = params or ACSParameters()

    # ---------------- public API ----------------

    def evaluate(
        self,
        *,
        candidate_rank: int | None,
        current_ranked_candidates: int | None,
        history: Sequence[HistoryPoint],
        plan_current: int | None = None,
        series_match_quality: str = "EXACT",
        data_year: int | None = None,
    ) -> ACSEvaluation:
        """`data_year` is the year of the snapshot the history was taken from.

        It is used for the 新鲜度 component so that "how current is this dataset"
        is a property of the data, not of the wall clock — the same inputs must
        always give the same ACS (replayability).
        """
        params = self.params
        warnings: list[str] = []

        if not candidate_rank or candidate_rank <= 0:
            raise ValueError("candidate_rank 必须是正整数：ACS 依赖考生位次")
        if not current_ranked_candidates or current_ranked_candidates <= 0:
            raise ValueError("current_ranked_candidates 必须是正整数：跨年比较必须做位次百分位标准化")

        q_c = candidate_rank / current_ranked_candidates
        usable = [point for point in history if point.usable]
        if len(usable) < len(history):
            warnings.append(
                f"{len(history) - len(usable)} 条历史记录缺少位次或当年考生人数，已从 ACS 计算中剔除"
            )
        if not usable:
            # No evidence at all: returns a neutral 50 rather than inventing risk.
            warnings.append("没有任何可用的历史录取位次数据，ACS 取中性值 50，请谨慎参考")
            breakdown = ConfidenceBreakdown(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
            return ACSEvaluation(
                acs=50.0,
                base_acs=50.0,
                confidence=0.0,
                history_acs=50.0,
                plan_adjustment=0.0,
                trend_adjustment=0.0,
                weighted_mean_d=None,
                q_candidate=q_c,
                confidence_parts=breakdown,
                years_used=(),
                history_points=0,
                warnings=tuple(warnings),
                parameters_version=params.version,
            )

        latest_year = max(point.exam_year for point in usable)
        weighted_d, total_weight = self._weighted_mean_d(usable, latest_year, q_c)
        if total_weight <= 0:
            raise ValueError("历史记录权重之和为 0，无法计算 ACS")
        mean_d = weighted_d / total_weight

        history_acs = 50.0 + params.history_gain * math.tanh(mean_d / params.history_scale)
        plan_adjustment = self._plan_adjustment(plan_current, usable)
        trend_slope = self._trend_slope(usable, latest_year)
        trend_adjustment = params.trend_gain * math.tanh(trend_slope / params.trend_scale)

        base_acs = clamp(history_acs + plan_adjustment + trend_adjustment, 0.0, 100.0)
        confidence_parts = self._confidence_parts(
            usable,
            latest_year,
            plan_current=plan_current,
            data_year=data_year,
            series_match_quality=series_match_quality,
        )
        confidence = confidence_parts.weighted(params)
        acs = clamp(50.0 + (base_acs - 50.0) * confidence, 0.0, 100.0)

        if len(usable) == 1:
            warnings.append("只有 1 年历史数据，Confidence 已自动下调，结果仅供参考")
        # The per-year series link is the ground truth; the parameter is the fallback.
        effective_match = self._worst_series_match(usable) or series_match_quality
        if effective_match in {"FUZZY", "NONE"}:
            warnings.append(f"专业序列匹配质量较低（{effective_match}），历史数据的可比性存疑")
        if plan_current is not None and usable:
            median_plan = self._median_plan(usable)
            if median_plan and plan_current < median_plan * 0.5:
                warnings.append(
                    f"今年招生计划 {plan_current} 人，明显少于历史中位数 {median_plan:g} 人，竞争预计加剧"
                )

        return ACSEvaluation(
            acs=acs,
            base_acs=base_acs,
            confidence=confidence,
            history_acs=history_acs,
            plan_adjustment=plan_adjustment,
            trend_adjustment=trend_adjustment,
            weighted_mean_d=mean_d,
            q_candidate=q_c,
            confidence_parts=confidence_parts,
            years_used=tuple(sorted({point.exam_year for point in usable})),
            history_points=len(usable),
            warnings=tuple(warnings),
            parameters_version=params.version,
        )

    @staticmethod
    def risk_bucket(acs: float) -> RiskBucket:
        return risk_bucket_for(acs)

    # ---------------- components ----------------

    def _weighted_mean_d(
        self, points: Sequence[HistoryPoint], latest_year: int, q_c: float
    ) -> tuple[float, float]:
        """Sum(w_y * d_y) and Sum(w_y)."""
        weighted = 0.0
        total = 0.0
        for point in points:
            d_y = point.d(q_c, self.params.d_clip)
            if d_y is None:
                continue
            age = max(0, latest_year - point.exam_year)
            weight = math.exp(-self.params.decay_lambda * age)
            weighted += weight * d_y
            total += weight
        return weighted, total

    def _plan_adjustment(self, plan_current: int | None, points: Sequence[HistoryPoint]) -> float:
        params = self.params
        if plan_current is None or plan_current < 0:
            return 0.0
        median_plan = self._median_plan(points)
        if median_plan is None:
            return 0.0
        # The +1 avoids log(0) and keeps tiny plans from exploding
        ratio = math.log((plan_current + 1) / (median_plan + 1))
        return params.plan_gain * math.tanh(ratio / params.plan_scale)

    @staticmethod
    def _median_plan(points: Sequence[HistoryPoint]) -> float | None:
        plans = [point.plan_count for point in points if point.plan_count and point.plan_count > 0]
        if not plans:
            return None
        return float(statistics.median(plans))

    def _trend_slope(self, points: Sequence[HistoryPoint], latest_year: int) -> float:
        """Weighted least-squares slope of ln(q_y) against year.

        Points are first averaged per year so a duplicated year cannot make the
        regression degenerate. Fewer than two distinct years -> no trend.
        """
        params = self.params
        by_year: dict[int, list[tuple[float, float]]] = {}
        for point in points:
            q_y = point.q()
            if q_y is None or q_y <= 0:
                continue
            age = max(0, latest_year - point.exam_year)
            weight = math.exp(-params.decay_lambda * age)
            by_year.setdefault(point.exam_year, []).append((math.log(q_y), weight))

        observations: list[tuple[float, float, float]] = []
        for year, entries in sorted(by_year.items()):
            weight_sum = sum(weight for _, weight in entries)
            if weight_sum <= 0:
                continue
            mean_ln = sum(ln_q * weight for ln_q, weight in entries) / weight_sum
            observations.append((float(year), mean_ln, weight_sum))

        if len(observations) < 2:
            return 0.0

        total_weight = sum(weight for _, _, weight in observations)
        if total_weight <= 0:
            return 0.0
        mean_year = sum(year * weight for year, _, weight in observations) / total_weight
        mean_value = sum(value * weight for _, value, weight in observations) / total_weight
        numerator = sum(
            weight * (year - mean_year) * (value - mean_value) for year, value, weight in observations
        )
        denominator = sum(weight * (year - mean_year) ** 2 for year, _, weight in observations)
        if denominator == 0:
            return 0.0
        return numerator / denominator

    @staticmethod
    def _worst_series_match(points: Sequence[HistoryPoint]) -> str | None:
        order = list(SERIES_MATCH_SCORES)
        worst: str | None = None
        for point in points:
            quality = point.series_match_quality
            if quality not in SERIES_MATCH_SCORES:
                continue
            if worst is None or order.index(quality) > order.index(worst):
                worst = quality
        return worst

    def _confidence_parts(
        self,
        points: Sequence[HistoryPoint],
        latest_year: int,
        *,
        plan_current: int | None,
        data_year: int | None,
        series_match_quality: str,
    ) -> ConfidenceBreakdown:
        params = self.params

        # 历史覆盖: 3 年及以上趋近 1
        distinct_years = len({point.exam_year for point in points})
        history_coverage = min(1.0, distinct_years / params.coverage_full_years)

        # 新鲜度: 快照年份距最新历史年份越久越低（半衰期 1.5 年）。
        # data_year 缺省时视为与最新历史同年 —— 不依赖系统时钟，保证可重放。
        reference_year = data_year if data_year is not None else latest_year
        staleness = max(0, reference_year - latest_year)
        freshness = math.exp(-params.decay_lambda * staleness)

        # 专业序列匹配: 取历史中最差的匹配质量（短板原则）
        rank_order = list(SERIES_MATCH_SCORES)
        worst_match = "EXACT"
        for point in points:
            if rank_order.index(point.series_match_quality) > rank_order.index(worst_match):
                worst_match = point.series_match_quality
        series_match = SERIES_MATCH_SCORES.get(worst_match, 0.1)

        # 数据源质量: 同样取最差
        source_order = list(SOURCE_QUALITY_SCORES)
        worst_source = "OFFICIAL"
        for point in points:
            if source_order.index(point.source_quality) > source_order.index(worst_source):
                worst_source = point.source_quality
        source_quality = SOURCE_QUALITY_SCORES.get(worst_source, 0.3)

        # 波动稳定: ln(q_y) 的标准差越大越低
        ln_qs = []
        for point in points:
            q_y = point.q()
            if q_y and q_y > 0:
                ln_qs.append(math.log(q_y))
        if len(ln_qs) >= 2:
            stdev = statistics.pstdev(ln_qs)
            volatility = math.exp(-stdev / params.volatility_scale)
        else:
            # A single year cannot demonstrate stability, it only lacks evidence
            volatility = 0.5

        # 招生人数质量: 小样本降低可信度
        plan_quality = self._plan_quality(points, plan_current)

        return ConfidenceBreakdown(
            history_coverage=clamp(history_coverage, 0.0, 1.0),
            freshness=clamp(freshness, 0.0, 1.0),
            series_match=clamp(series_match, 0.0, 1.0),
            source_quality=clamp(source_quality, 0.0, 1.0),
            volatility=clamp(volatility, 0.0, 1.0),
            plan_quality=clamp(plan_quality, 0.0, 1.0),
        )

    def _plan_quality(self, points: Sequence[HistoryPoint], plan_current: int | None) -> float:
        counts = [p.plan_count for p in points if p.plan_count and p.plan_count > 0]
        if plan_current is not None and plan_current > 0:
            counts.append(plan_current)
        if not counts:
            return 0.0
        reference = self.params.plan_quality_reference
        # median keeps one huge year from masking a generally small program
        median_plan = statistics.median(counts)
        return min(1.0, math.sqrt(median_plan / reference))


def confidence_label(confidence: float) -> str:
    """Coarse label for UI display (高/中/低)."""
    if confidence >= 0.75:
        return "HIGH"
    if confidence >= 0.5:
        return "MEDIUM"
    return "LOW"


__all__ = [
    "SERIES_MATCH_SCORES",
    "SOURCE_QUALITY_SCORES",
    "ACSEvaluation",
    "ACSParameters",
    "ACSService",
    "confidence_label",
]
