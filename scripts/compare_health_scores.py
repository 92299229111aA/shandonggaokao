"""Compare health-score aggregation models against real schemes.

`100 - sum(penalty per issue)` floors at 0 on a real 96-item scheme, because a single
position-level check fires dozens of times — small-plan programmes are the norm here
(63.3% of all plans have fewer than 5 seats, median 3), so the score stops distinguishing
a good scheme from a broken one. That is why D-022 grouped penalties by issue code.

This compares four aggregation models on the same real schemes:

  引擎(现状)       shipped model, read from app.healthcheck.types.score_for
  旧-每条扣一次    每 issue 扣一次（D-022 之前的行为，仅作对照）
  仅按类不封顶     每类问题扣一次（同 code 只扣一次）
  按类+封顶        每类扣一次 + 每多一条加 1 分，单类封顶 3 分

Run: python scripts/compare_health_scores.py
"""

from __future__ import annotations

import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.common.enums import HealthSeverity  # noqa: E402
from app.healthcheck.assembly import build_context  # noqa: E402
from app.healthcheck.engine import HealthCheckEngine  # noqa: E402
from app.healthcheck.types import SEVERITY_PENALTY, score_for  # noqa: E402
from app.recommendation.models import SchemeVersion  # noqa: E402

FULL_ITEM_MIN = 90  # only score schemes that are actually complete


def model_current(issues: list) -> int:
    """The *shipped* model, taken from the engine rather than re-implemented here.

    This function used to recompute `100 - sum(penalty per issue)` inline. When D-022
    moved the engine to per-code grouping with a repeat cap, the copy did not move with
    it, so the column labelled 现状 kept reporting 0 while the real check reported 83.
    Delegating to `score_for` removes the second source of truth.
    """
    score, _ = score_for(issues)
    return score


def model_per_issue(issues: list) -> int:
    """The original one-penalty-per-issue model, kept for comparison only."""
    return max(0, 100 - sum(SEVERITY_PENALTY[i.severity] for i in issues))


def model_per_code(issues: list) -> int:
    worst: dict[str, HealthSeverity] = {}
    for issue in issues:
        if issue.code not in worst or SEVERITY_PENALTY[issue.severity] > SEVERITY_PENALTY[worst[issue.code]]:
            worst[issue.code] = issue.severity
    return max(0, 100 - sum(SEVERITY_PENALTY[s] for s in worst.values()))


def model_capped(issues: list, per_extra: int = 1, cap: int = 3) -> int:
    grouped: dict[str, list] = defaultdict(list)
    for issue in issues:
        grouped[issue.code].append(issue)
    penalty = 0
    for _code, group in grouped.items():
        severity = max((i.severity for i in group), key=lambda s: SEVERITY_PENALTY[s])
        base = SEVERITY_PENALTY[severity]
        extra = min(cap, (len(group) - 1) * per_extra)
        penalty += base + extra
    return max(0, 100 - penalty)


def main() -> int:
    engine = create_engine(os.environ["DATABASE_URL"])
    with Session(engine) as session:
        versions = session.execute(
            select(SchemeVersion.scheme_id, SchemeVersion.version_no, SchemeVersion.item_count)
            .where(SchemeVersion.item_count >= FULL_ITEM_MIN)
            .order_by(SchemeVersion.id.desc())
            .limit(8)
        ).all()
        if not versions:
            print(f"没有条目数 ≥ {FULL_ITEM_MIN} 的方案版本可评估")
            return 1

        rows = []
        for scheme_id, version_no, item_count in versions:
            ctx = build_context(session, scheme_id=scheme_id, version_no=version_no)
            report = HealthCheckEngine().run(ctx)
            issues = list(report.issues)
            by_code = Counter(i.code for i in issues)
            rows.append(
                {
                    "scheme": scheme_id,
                    "version": version_no,
                    "items": item_count,
                    "issues": len(issues),
                    "by_code": by_code,
                    "current": model_current(issues),
                    "per_issue": model_per_issue(issues),
                    "per_code": model_per_code(issues),
                    "capped": model_capped(issues),
                }
            )

    print("=" * 104)
    print("各计分模型在真实方案上的对比（以引擎为准）")
    print("=" * 104)
    print(f"{'方案':>6}{'版本':>6}{'条目':>6}{'问题数':>8}{'引擎(现状)':>11}{'旧-每条':>9}{'仅按类':>8}{'封顶':>7}   主要问题")
    print("-" * 104)
    for row in rows:
        top = ", ".join(f"{code}×{n}" for code, n in row["by_code"].most_common(3))
        print(
            f"{row['scheme']:>6}{row['version']:>6}{row['items']:>6}{row['issues']:>8}"
            f"{row['current']:>11}{row['per_issue']:>9}{row['per_code']:>8}{row['capped']:>7}   {top}"
        )

    print()
    print("引擎(现状)   ：", [r["current"] for r in rows])
    print("旧-每条扣一次：", [r["per_issue"] for r in rows])
    print("仅按类不封顶 ：", [r["per_code"] for r in rows])
    print("按类+封顶    ：", [r["capped"] for r in rows])
    print()

    def spread(values: list[int]) -> str:
        return f"范围 {min(values)}~{max(values)}，不同取值 {len(set(values))} 种"

    print("区分度（越大越能反映方案差异）：")
    for label, key in (
        ("引擎(现状)", "current"),
        ("旧-每条扣一次", "per_issue"),
        ("仅按类不封顶", "per_code"),
        ("按类+封顶", "capped"),
    ):
        print(f"  {label:<14}：{spread([r[key] for r in rows])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
