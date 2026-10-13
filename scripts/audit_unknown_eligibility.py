"""Trace where UNKNOWN eligibility comes from, and whether the inputs that cause it
should have been refused or flagged.

Three observations from the edge-input run:
  1. A profile with only TWO 选考科目 yields UNKNOWN eligibility for every plan.
  2. A rank of 600,000 (population 333,469) produces a pool with no SAFE entries and no
     warning in `stats.notes`.
  3. Even the normal control shows `资格=UNKNOWN` on the first items returned.

(3) is the important one: if UNKNOWN appears on ordinary candidates too, the cause is not
the odd input, and the reason string will say which rule produced it.

Run: python scripts/audit_unknown_eligibility.py
"""

from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import select, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db.session import session_scope  # noqa: E402
from app.recommendation.models import RecommendationItem  # noqa: E402


def main() -> int:
    with session_scope() as session:
        latest = session.execute(
            text("select id from recommendation.sessions order by id desc limit 6")
        ).scalars().all()
        print(f"最近会话: {latest}")
        print()

        for session_id in latest:
            rows = session.execute(
                select(
                    RecommendationItem.eligibility,
                    RecommendationItem.eligibility_reasons,
                    RecommendationItem.in_candidate_pool,
                ).where(RecommendationItem.session_id == session_id)
            ).all()
            if not rows:
                continue
            counts = Counter(r[0] for r in rows)
            pooled = sum(1 for r in rows if r[2])
            print(f"会话 {session_id}: 共 {len(rows):,} 条，入池 {pooled:,}")
            for status, count in counts.most_common():
                print(f"    {status:<10} {count:>7,}")

            unknown = [r for r in rows if r[0] == "UNKNOWN" and r[1]]
            if unknown:
                reasons = Counter()
                for _status, reason_list, _pooled in unknown[:2000]:
                    if isinstance(reason_list, list):
                        for reason in reason_list:
                            if isinstance(reason, dict):
                                reasons[reason.get("code") or reason.get("rule") or str(reason)[:40]] += 1
                            else:
                                reasons[str(reason)[:60]] += 1
                print(f"    UNKNOWN 原因（抽样 {min(len(unknown), 2000)} 条）:")
                for reason, count in reasons.most_common(8):
                    print(f"      {count:>6}  {reason}")
            print()

    print("=" * 100)
    print("判断要点")
    print("=" * 100)
    print("  · 若普通考生也有大量 UNKNOWN，说明原因与异常输入无关，需要看具体原因码。")
    print("  · 若 UNKNOWN 全部来自「未解析的特殊要求」，那是已知设计（不猜测为可报）。")
    print("  · 2 科档案与超出人数的位次都不会被拒绝，需要确认这是否是有意为之。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
