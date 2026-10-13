"""Measure the impact of storing a field label in `special_requirements`.

Every one of the 73,198 plans carries `special_requirements = '院校专业序号 XY'`, which I
wrote in a previous round to avoid a schema change. The eligibility engine treats any
non-empty `special_requirements` as an unverified admission-charter requirement and
answers UNKNOWN.

Consequences to measure:
  1. share of plans whose eligibility becomes UNKNOWN because of it;
  2. whether UNKNOWN plans are excluded from the 保底 (safety) count;
  3. what the health score and risk distribution look like as a result.

Read-only. Run: python scripts/audit_special_requirements.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db.session import session_scope  # noqa: E402


def main() -> int:
    with session_scope() as session:
        total = int(
            session.execute(
                text("select count(*) from admission.admission_plans where is_demo = false")
            ).scalar_one()
        )
        with_text = int(
            session.execute(
                text(
                    "select count(*) from admission.admission_plans "
                    "where is_demo = false and special_requirements is not null"
                )
            ).scalar_one()
        )
        sequence_only = int(
            session.execute(
                text(
                    "select count(*) from admission.admission_plans "
                    "where is_demo = false and special_requirements like '院校专业序号 %'"
                )
            ).scalar_one()
        )
        other_text = with_text - sequence_only

        print("=" * 100)
        print("1) special_requirements 字段的实际内容")
        print("=" * 100)
        print(f"  非 DEMO 计划            {total:,}")
        print(f"  有 special_requirements {with_text:,}  ({with_text / total * 100:.1f}%)")
        print(f"    其中只是「院校专业序号」 {sequence_only:,}")
        print(f"    其中是其它文本          {other_text:,}")
        print()

        samples = session.execute(
            text(
                "select distinct special_requirements from admission.admission_plans "
                "where special_requirements is not null limit 5"
            )
        ).scalars().all()
        print(f"  样例: {samples}")
        print()

        print("=" * 100)
        print("2) 资格结论的分布（最近一次推荐会话）")
        print("=" * 100)
        session_id = session.execute(
            text("select id from recommendation.sessions order by id desc limit 1")
        ).scalar_one()
        rows = session.execute(
            text(
                "select eligibility, count(*) from recommendation.recommendation_items "
                "where session_id = :sid group by 1 order by 2 desc"
            ),
            {"sid": session_id},
        ).all()
        for status, count in rows:
            print(f"  {status:<10} {count:>8,}")
        print()

        print("=" * 100)
        print("3) UNKNOWN 是否被排除在保底之外")
        print("=" * 100)
        print("  规则：counts_as_safe = (eligibility == 'PASS') and acs >= 60")
        print("        -> eligibility 全为 UNKNOWN 时，保底计数恒为 0")
        unknown_acs60 = session.execute(
            text(
                "select count(*) from recommendation.recommendation_items "
                "where session_id = :sid and eligibility = 'UNKNOWN' and acs >= 60"
            ),
            {"sid": session_id},
        ).scalar_one()
        print(f"  本次会话中 ACS>=60 但资格为 UNKNOWN 的条目: {unknown_acs60:,}")
        print("  这些本可计为保底，现在全部不计入。")
        print()

        print("=" * 100)
        print("4) 若该字段为空，会有多少计划变成 PASS")
        print("=" * 100)
        plans_with_real_requirements = int(
            session.execute(
                text(
                    "select count(*) from admission.admission_requirements where is_demo = false"
                )
            ).scalar_one()
        )
        print(f"  真正录入特殊要求的计划（admission_requirements 表）: {plans_with_real_requirements:,}")
        print(f"  仅因「院校专业序号」而判 UNKNOWN 的计划: {sequence_only:,}")
        print()
        print("  结论：该字段被当成了两件事用 —— 既存院校专业序号，又被资格引擎")
        print("        解读为招生章程特殊要求。这是本轮引入的缺陷。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
