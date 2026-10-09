"""Calibrate the Health Check thresholds from the real distributions.

Method matters here. An earlier version of this script compared the current plan
count against the median of *all* history rows pooled together and concluded every
plan shrank by 100% — it had mixed years and compared against the wrong baseline.

This version measures what actually exists in the data:

  SmallPlanCount    distribution of plan sizes, and the share below candidate cut-offs
  PlanReduction     year-over-year swing of the SAME series' plan count, which is the
                    noise floor a threshold must sit above to be informative
  LowConfidence     distribution of the ACS confidence component under real data

Run: python scripts/calibrate_healthcheck.py
"""

from __future__ import annotations

import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.admission.models import (  # noqa: E402
    AdmissionCycle,
    AdmissionOutcome,
    AdmissionPlan,
    ExamYearStats,
)
from app.scoring.acs import ACSService, HistoryPoint  # noqa: E402

CANDIDATE_RANK = 60_000
TARGET_YEAR = 2025
HISTORY_YEARS = (2022, 2023, 2024)


def pct(values: list[float], fraction: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(len(ordered) * fraction)))]


def describe(label: str, values: list[float], unit: str = "") -> None:
    if not values:
        print(f"    {label:<16} （无数据）")
        return
    print(
        f"    {label:<16} n={len(values):>6,}  min={min(values):>7.1f}{unit}  "
        f"p10={pct(values,0.10):>7.1f}{unit}  p50={statistics.median(values):>7.1f}{unit}  "
        f"p90={pct(values,0.90):>7.1f}{unit}  p99={pct(values,0.99):>7.1f}{unit}"
    )


def main() -> int:
    engine = create_engine(os.environ["DATABASE_URL"])
    with Session(engine) as session:
        stats = dict(
            session.execute(
                select(ExamYearStats.exam_year, ExamYearStats.ranked_candidates)
            ).all()
        )
        population = stats.get(TARGET_YEAR)
        cycle = session.execute(
            select(AdmissionCycle.id).where(AdmissionCycle.exam_year == TARGET_YEAR)
        ).scalar_one()
        plans = session.execute(
            select(AdmissionPlan.series_id, AdmissionPlan.plan_count).where(
                AdmissionPlan.cycle_id == cycle
            )
        ).all()

        # one row per (series, year) — the natural key of an outcome
        outcome_rows = session.execute(
            select(
                AdmissionOutcome.series_id,
                AdmissionOutcome.exam_year,
                AdmissionOutcome.plan_count,
                AdmissionOutcome.min_rank,
            )
        ).all()

    plan_by_series_year: dict[int, dict[int, int]] = defaultdict(dict)
    rank_by_series_year: dict[int, dict[int, int]] = defaultdict(dict)
    for series_id, year, plan_count, min_rank in outcome_rows:
        if plan_count:
            plan_by_series_year[series_id][year] = int(plan_count)
        if min_rank:
            rank_by_series_year[series_id][year] = int(min_rank)

    # ---------------------------------------------------------------- 1
    sizes = [float(c) for _sid, c in plans if c]
    print("=" * 100)
    print("1) 招生计划规模（SMALL_PLAN_THRESHOLD 现状 = 5）")
    print("=" * 100)
    describe("计划数", sizes)
    print()
    print("    阈值候选 → 会触发 SmallPlanCount 的计划占比：")
    for threshold in (2, 3, 5, 8, 10, 15, 20, 30):
        share = sum(1 for s in sizes if s < threshold) / len(sizes) * 100
        mark = "   <-- 现状" if threshold == 5 else ""
        print(f"      计划数 < {threshold:>3} : {share:>5.1f}%{mark}")
    print()

    # ---------------------------------------------------------------- 2
    print("=" * 100)
    print("2) 同一专业序列的逐年计划数波动（PLAN_REDUCTION_THRESHOLD 现状 = 0.30）")
    print("=" * 100)
    swings: list[float] = []
    for series_id in plan_by_series_year:
        years = sorted(plan_by_series_year[series_id])
        for prev, curr in zip(years, years[1:], strict=False):
            before = plan_by_series_year[series_id][prev]
            after = plan_by_series_year[series_id][curr]
            if before <= 0:
                continue
            swings.append((after - before) / before)
    describe("逐年变化率", [s * 100 for s in swings], "%")
    print()
    print("    阈值候选 → 会触发 PlanReduction 的计划占比：")
    for threshold in (0.20, 0.30, 0.40, 0.50, 0.60, 0.70):
        share = sum(1 for s in swings if s <= -threshold) / len(swings) * 100
        mark = "   <-- 现状" if abs(threshold - 0.30) < 1e-9 else ""
        print(f"      缩减 ≥ {threshold*100:>4.0f}% : {share:>5.1f}%{mark}")
    print()

    # What the check ACTUALLY computes: current 2025 plan vs median of that series'
    # 2022-2024 plan counts. That is the real trigger population.
    actual_changes: list[float] = []
    for series_id, plan_count in plans:
        if not plan_count:
            continue
        history = [
            plan_by_series_year[series_id][year]
            for year in HISTORY_YEARS
            if plan_by_series_year[series_id].get(year)
        ]
        if not history:
            continue
        median = statistics.median(history)
        if median <= 0:
            continue
        actual_changes.append((plan_count - median) / median)
    print("    按检查的真实算法（今年计划 vs 2022–2024 计划中位数）计算：")
    describe("变化率", [c * 100 for c in actual_changes], "%")
    print()
    for threshold in (0.30, 0.40, 0.50, 0.60):
        share = sum(1 for c in actual_changes if c <= -threshold) / len(actual_changes) * 100
        mark = "   <-- 现状" if abs(threshold - 0.30) < 1e-9 else ""
        print(f"      缩减 ≥ {threshold*100:>4.0f}% : {share:>5.1f}%{mark}")
    print()

    # ---------------------------------------------------------------- 3
    print("=" * 100)
    print("3) 数据可信度（LOW_CONFIDENCE_THRESHOLD 现状 = 50）")
    print("=" * 100)
    service = ACSService()
    confidences: list[float] = []
    seen: set[int] = set()
    for series_id, _count in plans:
        if series_id in seen:
            continue
        seen.add(series_id)
        points = [
            HistoryPoint(
                exam_year=year,
                min_rank=rank_by_series_year[series_id][year],
                ranked_candidates=stats.get(year),
            )
            for year in sorted(rank_by_series_year[series_id])
            if stats.get(year)
        ]
        if not points:
            continue
        result = service.evaluate(
            candidate_rank=CANDIDATE_RANK,
            current_ranked_candidates=population,
            history=points,
        )
        confidences.append(result.confidence * 100)
    describe("可信度", confidences)
    print()
    print("    阈值候选 → 会触发 LowConfidence 的计划占比：")
    for threshold in (50, 55, 60, 65, 70):
        share = sum(1 for c in confidences if c < threshold) / len(confidences) * 100
        mark = "   <-- 现状（永不触发）" if threshold == 50 else ""
        print(f"      可信度 < {threshold:>3} : {share:>5.2f}%{mark}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
