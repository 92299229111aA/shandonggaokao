"""Verify whether the "843 out-of-range discipline codes" finding is real.

The audit flagged 843 majors with an invalid `discipline_code`, but its SQL used
`!~ '^([0-9]|1[0-4])$'`, which only matches a SINGLE character while the codes are
two-digit ('01'…'14'). That would make every valid code look invalid — a bug in the
check, not in the data.

This asks the two questions separately:
  1. what codes actually exist, and how many rows per code?
  2. do any rows genuinely fall outside 01–14?

Run: python scripts/audit_discipline_codes.py
"""

from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db.session import session_scope  # noqa: E402
from app.reference.models import Major  # noqa: E402

VALID = {f"{n:02d}" for n in range(1, 15)}


def main() -> int:
    with session_scope() as session:
        rows = list(session.execute(select(Major.code, Major.name, Major.discipline_code)).all())

    filled = [(code, name, disc) for code, name, disc in rows if disc]
    counts = Counter(disc for _c, _n, disc in filled)

    print(f"专业总数 {len(rows):,}   有门类 {len(filled):,}   无门类 {len(rows) - len(filled):,}")
    print()
    print("实际存在的门类代码分布:")
    for disc, count in sorted(counts.items()):
        mark = "" if disc in VALID else "   <<< 不在 01~14"
        print(f"    {disc!r:<8} {count:>5}{mark}")
    print()

    invalid = [(code, name, disc) for code, name, disc in filled if disc not in VALID]
    print(f"真正越界的行数: {len(invalid)}")
    if invalid:
        for code, name, disc in invalid[:20]:
            print(f"    {disc!r}  专业代码={code}  {name}")

    print()
    print("=" * 80)
    if not invalid:
        print("结论：门类代码全部合法（01~14）。此前审计脚本的正则只匹配 1 位字符，")
        print("      把两位合法代码误判为越界 —— 是检查写错了，不是数据错了。")
    else:
        print("结论：确实存在越界门类代码，需要修复映射逻辑。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
