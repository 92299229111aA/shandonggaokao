"""Pick the ACS sensitivity setting on evidence, using 2025 as a holdout year.

Confidence does not depend on history_scale, d_clip or the bucket boundaries — it is
a weighted sum of coverage / freshness / series-match / source-quality / volatility /
plan-quality, all of which are properties of the data. So "which setting gives the
highest confidence" cannot discriminate between them. This script uses criteria that
can:

1. **Holdout accuracy.** Compute ACS from 2022-2024 only, then ask whether 2025's
   actual cut-off rank fell at or below the candidate's rank (i.e. was the plan
   actually gettable). A useful safety index must separate "got in" from "did not".
   Reported as AUC over the candidate pool.
2. **Bucket calibration.** Of the plans bucketed SAFE, what share were actually
   gettable in 2025? A bucket named 保 should be near-certain, not a coin flip.
3. **Discrimination.** Does the 0-100 scale actually spread out, or does tanh
   saturate and collapse everything into two spikes? Measured by the share of items
   landing in the middle 60% of the scale, and by the bucket entropy.
4. **Target fit.** How close does the achievable bucket distribution get to the
   designed BALANCED targets (8/17/20/31/20)?

Run: python scripts/tune_acs.py [sample_size]
"""

from __future__ import annotations

import statistics
import sys
from collections import defaultdict
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.admission.models import (  # noqa: E402
    AdmissionOutcome,
    AdmissionPlan,
    ExamYearStats,
)
from app.admission.models import AdmissionCycle  # noqa: E402
from app.common.enums import RiskBucket, risk_bucket_for  # noqa: E402
from app.scoring.acs import ACSParameters, ACSService, HistoryPoint  # noqa: E402

import os  # noqa: E402

CANDIDATE_RANK = 60_000
HOLDOUT_YEAR = 2025
CONTROL_YEAR = 2026

# The designed BALANCED distribution (docs/ALGORITHM.md).
TARGETS = {
    RiskBucket.HIGH_REACH.value: 8,
    RiskBucket.REACH.value: 17,
    RiskBucket.BORDER.value: 20,
    RiskBucket.STABLE.value: 31,
    RiskBucket.SAFE.value: 20,
}
BUCKET_ORDER = ["HIGH_REACH", "REACH", "BORDER", "STABLE", "SAFE"]


def load(session: Session, sample: int | None) -> list[dict]:
    """One record per admission plan, with 2022-2024 history and the 2025 outcome."""
    stats = {
        year: value
        for year, value in session.execute(
            select(ExamYearStats.exam_year, ExamYearStats.ranked_candidates)
        ).all()
    }
    population_2025 = stats.get(HOLDOUT_YEAR)
    if not population_2025:
        raise SystemExit("缺少 2025 年考生人数")

    cycle_2025 = session.execute(
        select(AdmissionCycle.id).where(AdmissionCycle.exam_year == HOLDOUT_YEAR)
    ).scalar_one()
    plans = session.execute(
        select(AdmissionPlan.id, AdmissionPlan.series_id, AdmissionPlan.plan_count).where(
            AdmissionPlan.cycle_id == cycle_2025
        )
    ).all()

    outcomes: dict[int, dict[int, int]] = defaultdict(dict)
    for series_id, year, min_rank in session.execute(
        select(AdmissionOutcome.series_id, AdmissionOutcome.exam_year, AdmissionOutcome.min_rank)
    ).all():
        outcomes[series_id][year] = min_rank

    records: list[dict] = []
    for plan_id, series_id, plan_count in plans:
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
        records.append(
            {
                "plan_id": plan_id,
                "history": history,
                "holdout_rank": holdout,
                # Ground truth: could a candidate at CANDIDATE_RANK actually get in?
                "gettable": holdout >= CANDIDATE_RANK,
            }
        )
        if sample and len(records) >= sample:
            break
    return records


def auc(scored: list[tuple[float, bool]]) -> float:
    """Probability that a gettable plan scores higher than a non-gettable one.

    Ties count a half, which is the standard Mann-Whitney treatment.
    """
    positives = [s for s, label in scored if label]
    negatives = [s for s, label in scored if not label]
    if not positives or not negatives:
        return float("nan")
    wins = 0.0
    for value in positives:
        for other in negatives:
            wins += 1.0 if value > other else 0.5 if value == other else 0.0
    return wins / (len(positives) * len(negatives))


def evaluate(records: list[dict], params: ACSParameters) -> dict:
    service = ACSService(params=params)
    scored: list[tuple[float, bool]] = []
    by_bucket: dict[str, list[bool]] = defaultdict(list)
    values: list[float] = []
    confidences: list[float] = []

    for record in records:
        result = service.evaluate(
            candidate_rank=CANDIDATE_RANK,
            current_ranked_candidates=_population,
            history=record["history"],
        )
        value = result.acs
        scored.append((value, record["gettable"]))
        values.append(value)
        by_bucket[risk_bucket_for(value).value].append(record["gettable"])
        confidences.append(result.confidence)

    values.sort()
    n = len(values)
    middle = sum(1 for v in values if 30 <= v < 80) / n

    return {
        "auc": auc(scored),
        "mean_acs": statistics.mean(values),
        "stdev_acs": statistics.pstdev(values),
        "p05": values[int(n * 0.05)],
        "p50": values[int(n * 0.50)],
        "p95": values[int(n * 0.95)],
        "share_middle": middle,
        "buckets": {b: len(by_bucket.get(b, [])) for b in BUCKET_ORDER},
        "bucket_share": {b: len(by_bucket.get(b, [])) / n for b in BUCKET_ORDER},
        # Calibration: of plans called SAFE, what fraction were really gettable?
        "safe_hit_rate": (
            sum(by_bucket["SAFE"]) / len(by_bucket["SAFE"]) if by_bucket.get("SAFE") else float("nan")
        ),
        "high_reach_miss_rate": (
            1 - sum(by_bucket["HIGH_REACH"]) / len(by_bucket["HIGH_REACH"])
            if by_bucket.get("HIGH_REACH")
            else float("nan")
        ),
        # Reported to show it is identical across settings — it depends only on data.
        "mean_confidence": statistics.mean(confidences),
    }


_population = 0


def main() -> int:
    global _population
    sample = int(sys.argv[1]) if len(sys.argv) > 1 else 4000
    engine = create_engine(os.environ["DATABASE_URL"])
    with Session(engine) as session:
        stats_2026 = session.execute(
            select(ExamYearStats.ranked_candidates).where(ExamYearStats.exam_year == CONTROL_YEAR)
        ).scalar_one_or_none()
        _population = stats_2026 or session.execute(
            select(ExamYearStats.ranked_candidates)
            .where(ExamYearStats.exam_year == HOLDOUT_YEAR)
        ).scalar_one()
        records = load(session, sample)

    print(f"样本 {len(records):,} 条计划；考生位次 {CANDIDATE_RANK:,}；考生人数 {_population:,}")
    gettable = sum(1 for r in records if r["gettable"])
    print(f"其中 2025 年真实可录取（投档位次 ≥ {CANDIDATE_RANK:,}）: {gettable:,} 条"
          f"（{gettable / len(records) * 100:.1f}%）")
    print()

    candidates = {
        "现状 λ=0.07 clip=0.30": ACSParameters(),
        "方案① λ=0.25 clip=0.30": ACSParameters(history_scale=0.25),
        "方案① λ=0.35 clip=0.30": ACSParameters(history_scale=0.35),
        "方案② λ=0.07 clip=0.60": ACSParameters(d_clip=0.60),
        "方案② λ=0.07 clip=0.80": ACSParameters(d_clip=0.80),
        "组合 λ=0.20 clip=0.60": ACSParameters(history_scale=0.20, d_clip=0.60),
        "组合 λ=0.25 clip=0.80": ACSParameters(history_scale=0.25, d_clip=0.80),
    }

    header = (
        f"{'方案':<24}{'AUC':>7}{'标准分':>8}{'中间占比':>9}"
        f"{'SAFE命中':>9}{'高冲落空':>9}  分桶占比（高冲/冲/边界/稳/保）"
    )
    print(header)
    print("-" * len(header))

    results: dict[str, dict] = {}
    for label, params in candidates.items():
        outcome = evaluate(records, params)
        results[label] = outcome
        shares = "/".join(f"{outcome['bucket_share'][b] * 100:.0f}%" for b in BUCKET_ORDER)
        print(
            f"{label:<24}{outcome['auc']:>7.4f}{outcome['stdev_acs']:>8.1f}"
            f"{outcome['share_middle'] * 100:>8.0f}%"
            f"{outcome['safe_hit_rate'] * 100:>8.0f}%"
            f"{outcome['high_reach_miss_rate'] * 100:>8.0f}%  {shares}"
        )

    print()
    print("目标分布（BALANCED）:", "/".join(f"{TARGETS[b] / 96 * 100:.0f}%" for b in BUCKET_ORDER))
    print()
    best = max(results.items(), key=lambda kv: (kv[1]["auc"], kv[1]["stdev_acs"]))
    print(f"AUC 最高：{best[0]}  AUC={best[1]['auc']:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
