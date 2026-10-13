"""Sweep the Health Check thresholds and measure the effect on a real 96-item scheme.

The shipped thresholds were chosen against the DEMO fixtures. On the real dataset a
scoring model matters: the per-code grouping introduced in D-022 is what keeps the score
meaningful when a single check fires dozens of times.

This sweeps both thresholds over a real scheme and reports issue counts and the
resulting health score, so the choice is made on measured behaviour rather than taste.

Run: python scripts/sweep_healthcheck.py <scheme_id> <version_no>
"""

from __future__ import annotations

import os
import statistics
import sys
from collections import Counter
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.common.enums import HealthSeverity, RiskBucket  # noqa: E402
from app.healthcheck.assembly import build_context  # noqa: E402
from app.healthcheck.types import HealthIssue, score_for  # noqa: E402

SCHEME_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 18
VERSION_NO = int(sys.argv[2]) if len(sys.argv) > 2 else 1
CANDIDATE_RANK = 60_000



def main() -> int:
    engine = create_engine(os.environ["DATABASE_URL"])
    with Session(engine) as session:
        ctx = build_context(session, scheme_id=SCHEME_ID, version_no=VERSION_NO)
        items = list(ctx.items)

    print(f"方案 {SCHEME_ID} v{VERSION_NO}：{len(items)} 条志愿")
    print()

    sizes = [i.plan_count for i in items]
    print(f"招生人数：min={min(sizes)} p50={statistics.median(sizes):g} max={max(sizes)}")
    print(f"分布：<2 的 {sum(1 for s in sizes if s < 2)} 条，"
          f"<3 的 {sum(1 for s in sizes if s < 3)} 条，"
          f"<5 的 {sum(1 for s in sizes if s < 5)} 条，"
          f"<10 的 {sum(1 for s in sizes if s < 10)} 条")
    print()

    # ---- SmallPlanCount ----
    print("=" * 92)
    print("SMALL_PLAN_COUNT 阈值扫描（当前 = 5）")
    print("=" * 92)
    print(f"{'阈值':>5}{'触发':>7}{'其中 WARNING':>13}{'其中 INFO':>11}{'严重度分配依据':>22}")
    print("-" * 92)
    for threshold in (2, 3, 4, 5, 6, 8, 10):
        hits = [i for i in items if 0 < i.plan_count < threshold]
        # severity depends on whether the volunteer is a safety-net slot
        warn = sum(
            1 for i in hits
            if i.risk_bucket in {RiskBucket.STABLE.value, RiskBucket.SAFE.value}
        )
        info = len(hits) - warn
        mark = "   <-- 当前" if threshold == 5 else ""
        print(f"{threshold:>5}{len(hits):>7}{warn:>13}{info:>11}  稳/保位置计 WARNING{mark}")
    print()

    # ---- PlanReduction ----
    print("=" * 92)
    print("PLAN_REDUCTION 阈值扫描（当前 = 0.30，即缩减 ≥30%）")
    print("=" * 92)
    print(f"{'阈值':>7}{'缩减触发':>10}{'扩张触发':>10}{'历史年数<2 的噪音':>19}")
    print("-" * 92)
    for threshold in (0.30, 0.40, 0.50, 0.60, 0.70):
        reduced = 0
        expanded = 0
        noisy = 0
        for item in items:
            history = [c for c in item.plan_count_history if c > 0]
            if not history:
                continue
            if len(history) < 2:
                noisy += 1
            median = statistics.median(history)
            if median <= 0:
                continue
            change = (item.plan_count - median) / median
            if change <= -threshold:
                reduced += 1
            elif change >= threshold:
                expanded += 1
        mark = "  <-- 当前" if abs(threshold - 0.30) < 1e-9 else ""
        print(f"{threshold*100:>6.0f}%{reduced:>10}{expanded:>10}{noisy:>19}{mark}")
    print()

    # ---- combined health score ----
    #
    # Score through the engine's own scorer. An earlier version of this script
    # reimplemented the arithmetic as `100 - warn*5 - info*1`, which is a *second source
    # of truth* — it kept reporting 0 at every threshold long after the engine had moved
    # to per-code grouping with a repeat cap, so the sweep silently stopped describing
    # the shipped behaviour.
    print("=" * 92)
    print("组合效果（分数由 app.healthcheck.types.score_for 计算，不在此重复实现）")
    print("=" * 92)
    print(f"{'小计划阈值':>10}{'缩减阈值':>10}{'问题条数':>9}{'健康分':>8}")
    print("-" * 92)
    for small in (3, 5, 10):
        for reduction in (0.30, 0.50, 0.70):
            issues: list[HealthIssue] = []
            for item in items:
                if 0 < item.plan_count < small:
                    issues.append(
                        HealthIssue(
                            code="SMALL_PLAN_COUNT",
                            severity=(
                                HealthSeverity.WARNING
                                if item.risk_bucket in {RiskBucket.STABLE.value, RiskBucket.SAFE.value}
                                else HealthSeverity.INFO
                            ),
                            message="",
                        )
                    )
                history = [c for c in item.plan_count_history if c > 0]
                if not history:
                    continue
                median = statistics.median(history)
                if median <= 0:
                    continue
                change = (item.plan_count - median) / median
                if change <= -reduction:
                    issues.append(
                        HealthIssue(code="PLAN_REDUCED", severity=HealthSeverity.WARNING, message="")
                    )
                elif change >= reduction:
                    issues.append(
                        HealthIssue(code="PLAN_EXPANDED", severity=HealthSeverity.INFO, message="")
                    )
            score, _ = score_for(issues)
            print(f"{small:>10}{reduction*100:>9.0f}%{len(issues):>9}{score:>8}")
    print()

    # what the full check run actually produced, for reference
    with Session(engine) as session:
        from app.healthcheck.engine import HealthCheckEngine

        report = HealthCheckEngine().run(ctx)
    counts = Counter(issue.severity.value for issue in report.issues)
    print("参考：完整体检在当前阈值下的实际输出")
    print(f"  错误 {counts.get('ERROR', 0)}  警告 {counts.get('WARNING', 0)}  "
          f"提示 {counts.get('INFO', 0)}   健康分 {report.score}")
    by_code = Counter(issue.code for issue in report.issues)
    for code, count in by_code.most_common():
        print(f"    {code:<24} {count:>3}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
