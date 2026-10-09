"""What granularity are the major codes that came from the Shandong plan files?

The plan workbooks carry 专业代码 values of differing length. Some are 专业类 codes
(4 digits, e.g. 0809 计算机类) and some are 专业 codes (6 digits, e.g. 080901
计算机科学与技术). That decides whether the MOE catalogue can be joined at all, and
whether the 专业匹配 preference dimension is comparing like with like.

Run: python scripts/audit_major_codes.py
"""

from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.reference.models import Major  # noqa: E402


def main() -> int:
    engine = create_engine(os.environ["DATABASE_URL"])
    with Session(engine) as session:
        rows = list(session.execute(select(Major.code, Major.name, Major.level)).all())

    print("=" * 100)
    print(f"数据库专业条目: {len(rows):,}")
    print("=" * 100)

    lengths = Counter(len(code) for code, _name, _level in rows)
    print("代码长度分布:")
    for length, count in sorted(lengths.items()):
        print(f"    {length} 位: {count:,}")

    levels = Counter(level for _code, _name, level in rows)
    print(f"\nlevel 分布: {dict(levels)}")

    print("\n各长度样例:")
    for length in sorted(lengths):
        samples = [(c, n) for c, n, _l in rows if len(c) == length][:6]
        print(f"    {length} 位:")
        for code, name in samples:
            print(f"        {code:<10} {name[:34]}")

    # How do the plan files actually reference majors?
    from app.admission.models import AdmissionPlan, ProgramSeries

    print()
    print("=" * 100)
    print("招生计划里的专业名称层级")
    print("=" * 100)
    with Session(engine) as session:
        names = list(
            session.execute(
                select(ProgramSeries.canonical_name)
                .join(AdmissionPlan, AdmissionPlan.series_id == ProgramSeries.id)
                .distinct()
            ).scalars()
        )
    class_like = [n for n in names if n.endswith("类")]
    print(f"  专业序列 {len(names):,}")
    print(f"    以「类」结尾（专业类）: {len(class_like):,}  ({len(class_like)/len(names)*100:.1f}%)")
    print(f"    其余（具体专业）      : {len(names) - len(class_like):,}")
    print("\n  专业类样例:", class_like[:10])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
