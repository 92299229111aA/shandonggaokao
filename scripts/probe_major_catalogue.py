"""Probe whether the MOE major catalogue PDF can be parsed reliably.

The catalogue has three levels:
    01         学科门类：哲学
    0101       哲学类                (专业类)
    010101     哲学                  (专业)
    010103K    宗教学                (K = 国家控制布点, T = 特设)

Text extraction from a PDF can interleave the code column and the name column, which
would silently attach the wrong name to a code — unacceptable for a catalogue that feeds
the 专业匹配 preference dimension. This measures the damage before loading anything.

Run: python scripts/probe_major_catalogue.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from pypdf import PdfReader

NEW_ROOT = Path(r"D:\BaiduNetdiskDownload\志愿高报资料")

CODE = re.compile(r"^(\d{6}[TK]?)\s*(.*)$")
MAJOR_CLASS = re.compile(r"^(\d{4})\s+(.+)$")
DISCIPLINE = re.compile(r"^(\d{2})\s*学科门类[:：]\s*(.+)$")


def find_pdf(fragment: str) -> Path | None:
    for path in NEW_ROOT.rglob("*.pdf"):
        if fragment in path.name:
            return path
    return None


def main() -> int:
    path = find_pdf("2026年普通高等学校本科专业目录") or find_pdf("普通高等学校本科专业目录（2025")
    if path is None:
        print("找不到专业目录 PDF")
        return 1
    print(f"文件: {path.name}")
    reader = PdfReader(str(path))
    print(f"页数: {len(reader.pages)}")
    print()

    disciplines: list[str] = []
    major_classes: list[str] = []
    majors: list[tuple[str, str]] = []
    orphan_codes: list[tuple[int, str]] = []

    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if (match := DISCIPLINE.match(line)):
                disciplines.append(f"{match.group(1)} {match.group(2)}")
                continue
            if (match := CODE.match(line)):
                code, name = match.group(1), match.group(2).strip()
                # A code with no inline name means the name was rendered elsewhere;
                # record it instead of guessing.
                if not name:
                    orphan_codes.append((page_number, code))
                else:
                    majors.append((code, name))
                continue
            if (match := MAJOR_CLASS.match(line)):
                major_classes.append(f"{match.group(1)} {match.group(2)}")

    print("=" * 100)
    print("解析结果")
    print("=" * 100)
    print(f"  学科门类 {len(disciplines)} 个")
    print(f"  专业类   {len(major_classes)} 个")
    print(f"  专业     {len(majors)} 个（含名称）")
    print(f"  只有代码没有名称 {len(orphan_codes)} 个")
    print()

    print("学科门类:")
    for item in disciplines:
        print(f"    {item}")
    print()
    print("专业样例（前 12）:")
    for code, name in majors[:12]:
        print(f"    {code:<10} {name[:44]}")
    print()
    if orphan_codes:
        print("无名称的代码（说明存在错位）:")
        for page_number, code in orphan_codes[:20]:
            print(f"    第 {page_number} 页  {code}")
        print()

    # Cross-check against codes already in the database.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    import os

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from app.reference.models import Major

    engine = create_engine(os.environ["DATABASE_URL"])
    with Session(engine) as session:
        stored = {
            code: name
            for code, name in session.execute(select(Major.code, Major.name)).all()
        }
    catalogue = dict(majors)
    exact = [c for c in stored if c in catalogue]
    print("=" * 100)
    print("与数据库专业代码的对照")
    print("=" * 100)
    print(f"  数据库专业 {len(stored):,}")
    print(f"  目录中能查到同代码 {len(exact):,}")
    agree = sum(1 for c in exact if stored[c] == catalogue[c])
    print(f"  其中名称也相同 {agree:,}")
    for code in exact[:8]:
        mark = "同" if stored[code] == catalogue[code] else "异"
        print(f"    [{mark}] {code}  库={stored[code][:20]:<22} 目录={catalogue[code][:20]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
