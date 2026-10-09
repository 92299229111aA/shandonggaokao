"""Compare health-score aggregation models against real schemes.

The shipped model is `100 - sum(penalty per issue)`. On a real 96-item scheme that
floors at 0 because a single position-level check fires 40 times — 40 small-plan
programmes is normal for this dataset (63% of all plans have fewer than 5 seats), so
the score stops distinguishing a good scheme from a broken one.

This compares three aggregation models on the same real schemes:

  A. current       每 issue 扣一次
  B. per-code      每类问题扣一次（同 code 只扣一次），明细仍逐条列出
  C. capped        每类问题扣一次 + 每多一条加 1 分，单类封顶 3 分

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
from app.healthcheck.types import SEVERITY_PENALTY  # noqa: E402
from app.recommendation.models import Scheme, SchemeVersion  # noqa: E402

FULL_ITEM_MIN = 90  # only score schemes that are actually complete


def model_current(issues: list) -> int:
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
                    "per_code": model_per_code(issues),
                    "capped": model_capped(issues),
                }
            )

    print("=" * 104)
    print("三种计分模型在真实方案上的对比")
    print("=" * 104)
    print(f"{'方案':>6}{'版本':>6}{'条目':>6}{'问题数':>8}{'A 现状':>9}{'B 按类':>9}{'C 封顶':>9}   主要问题")
    print("-" * 104)
    for row in rows:
        top = ", ".join(f"{code}×{n}" for code, n in row["by_code"].most_common(3))
        print(
            f"{row['scheme']:>6}{row['version']:>6}{row['items']:>6}{row['issues']:>8}"
            f"{row['current']:>9}{row['per_code']:>9}{row['capped']:>9}   {top}"
        )

    print()
    print("A 现状：", [r["current"] for r in rows])
    print("B 按类：", [r["per_code"] for r in rows])
    print("C 封顶：", [r["capped"] for r in rows])
    print()

    def spread(values: list[int]) -> str:
        return f"范围 {min(values)}~{max(values)}，不同取值 {len(set(values))} 种"

    print(f"区分度（越大越能反映方案差异）：")
    print(f"  A 现状  ：{spread([r['current'] for r in rows])}")
    print(f"  B 按类  ：{spread([r['per_code'] for r in rows])}")
    print(f"  C 封顶  ：{spread([r['capped'] for r in rows])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
