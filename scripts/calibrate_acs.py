"""Calibrate ACS against the 2025 holdout year, then derive bucket boundaries.

The bucket names are promises: 保 (SAFE) should be near-certain, 高冲 (HIGH_REACH)
should mostly fail. This measures whether the current boundaries keep that promise,
or whether they were arbitrary cut-points on a scale with a different shape.

Method:
  1. Compute ACS from 2022-2024 history only, for a candidate at rank 60,000.
  2. Check against the real 2025 cut-off: gettable = 2025 min_rank >= 60,000.
  3. Bin by ACS and report the empirical gettable rate per bin.
  4. Derive boundaries at chosen reliability levels and compare with the shipped ones.

Run: python scripts/calibrate_acs.py [lambda] [sample]
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
from app.scoring.acs import ACSParameters, ACSService, HistoryPoint  # noqa: E402

CANDIDATE_RANK = 60_000
HOLDOUT_YEAR = 2025
SHIPPED_BOUNDARIES = [(0, 30, "HIGH_REACH"), (30, 45, "REACH"), (45, 60, "BORDER"),
                      (60, 80, "STABLE"), (80, 101, "SAFE")]


def main() -> int:
    history_scale = float(sys.argv[1]) if len(sys.argv) > 1 else 0.07
    d_clip = float(sys.argv[2]) if len(sys.argv) > 2 else 0.30
    sample = int(sys.argv[3]) if len(sys.argv) > 3 else 25_000

    engine = create_engine(os.environ["DATABASE_URL"])
    with Session(engine) as session:
        stats = dict(
            session.execute(
                select(ExamYearStats.exam_year, ExamYearStats.ranked_candidates)
            ).all()
        )
        population = stats.get(2026) or stats.get(HOLDOUT_YEAR)
        cycle = session.execute(
            select(AdmissionCycle.id).where(AdmissionCycle.exam_year == HOLDOUT_YEAR)
        ).scalar_one()
        plans = session.execute(
            select(AdmissionPlan.series_id).where(AdmissionPlan.cycle_id == cycle)
        ).scalars().all()
        outcomes: dict[int, dict[int, int]] = defaultdict(dict)
        for series_id, year, min_rank in session.execute(
            select(AdmissionOutcome.series_id, AdmissionOutcome.exam_year, AdmissionOutcome.min_rank)
        ).all():
            outcomes[series_id][year] = min_rank

    records: list[tuple[float, bool]] = []
    service = ACSService(params=ACSParameters(history_scale=history_scale, d_clip=d_clip))
    seen: set[int] = set()
    for series_id in plans:
        if series_id in seen:
            continue
        seen.add(series_id)
        by_year = outcomes.get(series_id)
        if not by_year:
            continue
        history = [
            HistoryPoint(exam_year=year, min_rank=rank, ranked_candidates=stats.get(year))
            for year, rank in sorted(by_year.items())
            if year != HOLDOUT_YEAR and stats.get(year)
        ]
        holdout = by_year.get(HOLDOUT_YEAR)
        if not history or holdout is None:
            continue
        result = service.evaluate(
            candidate_rank=CANDIDATE_RANK,
            current_ranked_candidates=population,
            history=history,
        )
        records.append((result.acs, holdout >= CANDIDATE_RANK))
        if len(records) >= sample:
            break

    total = len(records)
    base_rate = sum(1 for _, ok in records if ok) / total
    print(f"λ={history_scale}  clip={d_clip}   样本 {total:,}   考生位次 {CANDIDATE_RANK:,}")
    print(f"整体真实可录取率: {base_rate * 100:.1f}%")
    print()

    # ---- empirical reliability curve ----
    print("ACS 分档 → 2025 年真实可录取率（这就是校准曲线）")
    print(f"{'ACS 区间':<14}{'条数':>8}{'占比':>7}{'真实可录取率':>13}  可靠性")
    print("-" * 62)
    bins = [(0, 20), (20, 30), (30, 45), (45, 60), (60, 70), (70, 80), (80, 90), (90, 101)]
    curve: list[tuple[int, float, int]] = []
    for low, high in bins:
        group = [ok for value, ok in records if low <= value < high]
        if not group:
            print(f"{f'{low}-{high-1}':<14}{0:>8}{'0%':>7}{'—':>13}")
            continue
        rate = sum(group) / len(group)
        curve.append((low, rate, len(group)))
        bar = "█" * int(rate * 30)
        print(f"{f'{low}-{high-1}':<14}{len(group):>8,}{len(group)/total*100:>6.0f}%{rate*100:>12.1f}%  {bar}")
    print()

    # ---- derive boundaries at reliability levels ----
    def boundary_for(level: float) -> int | None:
        """Smallest ACS threshold whose bin is at least `level` reliable."""
        for low, rate, _count in sorted(curve, key=lambda c: -c[0]):
            if rate < level:
                return low + 20 if low < 80 else low
        return None

    def threshold_meeting(level: float) -> float | None:
        """Lowest ACS at which the *cumulative* rate from there up reaches `level`."""
        ordered = sorted(records, key=lambda r: -r[0])
        ok = 0
        for index, (value, gettable) in enumerate(ordered, start=1):
            ok += gettable
            if ok / index >= level and index >= 10:
                return value
        return None

    print("按「累计可录取率」反推的桶边界（自上而下累计）")
    print(f"{'目标可靠性':<12}{'对应 ACS 下界':>15}{'该档及以上真实可录取率':>24}")
    print("-" * 54)
    for level in (0.98, 0.95, 0.90, 0.80, 0.60):
        threshold = threshold_meeting(level)
        if threshold is None:
            print(f"{level*100:>9.0f}%   {'（无法达到）':>15}")
            continue
        above = [ok for value, ok in records if value >= threshold]
        rate = sum(above) / len(above) if above else float("nan")
        print(f"{level*100:>9.0f}%   {threshold:>15.1f}{rate*100:>23.1f}%")
    print()

    print("当前生效的桶边界 vs 其实际含义：")
    print(f"{'桶':<12}{'ACS 区间':<14}{'条数':>8}{'占比':>7}{'真实可录取率':>13}")
    print("-" * 56)
    rates: list[float] = []
    for low, high, name in SHIPPED_BOUNDARIES:
        group = [ok for value, ok in records if low <= value < high]
        if not group:
            print(f"{name:<12}{f'{low}-{high-1}':<14}{0:>8}{'0%':>7}{'—':>13}")
            continue
        rate = sum(group) / len(group)
        rates.append(rate)
        print(f"{name:<12}{f'{low}-{high-1}':<14}{len(group):>8,}{len(group)/total*100:>6.0f}%{rate*100:>12.1f}%")
    print()

    # Separation: how far apart are the buckets that are supposed to differ?
    # STABLE vs SAFE is the pair the bucket names promise is distinct.
    stable = [ok for value, ok in records if 60 <= value < 80]
    safe = [ok for value, ok in records if value >= 80]
    stable_rate = sum(stable) / len(stable) if stable else float("nan")
    safe_rate = sum(safe) / len(safe) if safe else float("nan")
    gap = (safe_rate - stable_rate) * 100 if stable and safe else float("nan")
    n_buckets = sum(1 for low, high, _n in SHIPPED_BOUNDARIES
                    if any(low <= value < high for value, _ in records))
    print("可区分度指标：")
    print(f"  STABLE 真实录取率 {stable_rate*100:.1f}%   SAFE {safe_rate*100:.1f}%   "
          f"差值 {gap:.1f} 个百分点（越大越说明两个桶确实不同）")
    print(f"  非空桶数量 {n_buckets}/5")
    print("目标分布（BALANCED 8/17/20/31/20 = 8%/18%/21%/32%/21%）")
    return {
        "lambda": history_scale,
        "clip": d_clip,
        "stable_rate": stable_rate,
        "safe_rate": safe_rate,
        "gap": gap,
        "n_buckets": n_buckets,
        "auc_proxy": base_rate,
    }


if __name__ == "__main__":
    sys.exit(main())
